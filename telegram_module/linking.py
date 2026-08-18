"""Registering a user's bot, then finding their channel with it.

Two steps, in this order:

1. The user creates a bot at BotFather and pastes its token here. We verify it
   with getMe and remember who the bot is.
2. They add that bot to a private channel of theirs as an admin, and post a
   one-time code in it. They press a button, we call getUpdates with their own
   token, find the code, and record the channel.

No webhook anywhere. Because the token belongs to the user rather than to the
site, updates can be fetched on demand with it. That removes a public endpoint,
a shared secret, and a running daemon from the feature.

Posting the code in the channel is itself the proof of ownership: a private
channel only accepts posts from its own admins. The code is short-lived and is
replaced the moment it is used, so one read over a shoulder is worth nothing a
moment later.
"""
import logging
import re

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import api, crypto
from .models import TelegramConnection

logger = logging.getLogger(__name__)

CODE_PREFIX = 'DUP-'

#: BotFather tokens look like 123456789:AA... . Checked before spending a
#: round trip on something that is plainly not a token, and to catch the
#: common mistake of pasting the bot's @name instead.
TOKEN_SHAPE = re.compile(r'^\d{6,}:[A-Za-z0-9_-]{30,}$')


class LinkError(Exception):
    """Something the user can fix. The message is shown to them."""


def connection_for(user, create=True):
    """The user's connection row, creating an empty one on demand."""
    connection = TelegramConnection.objects.filter(user=user).first()
    if connection is None and create:
        connection = TelegramConnection.objects.create(user=user)
    if connection is not None and not connection.code_is_valid():
        connection.rotate_code()
    return connection


def register_bot(user, raw_token):
    """Store a user's bot token after checking it works.

    Returns the connection. Raises LinkError with something actionable.
    """
    token = (raw_token or '').strip()
    if not token:
        raise LinkError(_('توکن ربات را وارد کن.'))

    if not TOKEN_SHAPE.match(token):
        raise LinkError(
            _('این شکل یک توکن نیست. BotFather چیزی شبیه '
              '123456789:AAE... می‌دهد. نام ربات (@yourbot) توکن نیست.')
        )

    try:
        identity = api.get_me(token)
    except api.TelegramError as exc:
        raise LinkError(
            _('تلگرام این توکن را نپذیرفت: %(reason)s') % {'reason': exc}
        )

    if not identity or not identity.get('is_bot'):
        raise LinkError(_('این توکن به یک ربات تعلق ندارد.'))

    bot_id = identity.get('id')
    clash = TelegramConnection.objects.filter(bot_id=bot_id).exclude(
        user=user,
    ).first()
    if clash is not None:
        # Two accounts sharing one bot means either could read the other's
        # files, since a bot sees every chat it is in.
        raise LinkError(_('این ربات قبلا به حساب دیگری وصل شده است.'))

    # A bot that already points somewhere cannot answer getUpdates, and the
    # user has no way to guess that from the error Telegram returns.
    try:
        api.delete_webhook(token)
    except api.TelegramError as exc:
        logger.info('could not clear the webhook for bot %s: %s', bot_id, exc)

    connection = connection_for(user)
    connection.set_token(token)
    connection.bot_username = identity.get('username', '')[:64]
    connection.bot_id = bot_id
    if connection.status == TelegramConnection.Status.NO_BOT:
        connection.status = TelegramConnection.Status.PENDING
    connection.last_error = ''
    connection.last_error_at = None
    connection.save()
    return connection


def forget_bot(user):
    """Remove the stored token, keeping every file record.

    The rows stay because the files are still in the channel. Deleting them
    would orphan real data and make coming back useless.
    """
    connection = TelegramConnection.objects.filter(user=user).first()
    if connection is None:
        return None

    connection.bot_token_encrypted = ''
    connection.bot_username = ''
    connection.bot_id = None
    connection.status = TelegramConnection.Status.NO_BOT
    connection.last_error = 'the user removed their bot'
    connection.last_error_at = timezone.now()
    connection.save()
    return connection


def disconnect(user):
    """Forget the channel but keep the bot registered."""
    connection = TelegramConnection.objects.filter(user=user).first()
    if connection is None:
        return None

    if connection.chat_id and connection.has_bot():
        # Best effort. If the bot was already removed this fails, which is the
        # outcome we wanted anyway.
        try:
            api.send_message(
                connection.token, connection.chat_id,
                _('اتصال این کانال به دلتا آپلود قطع شد. '
                  'فایل‌های موجود همچنان اینجا هستند.'),
            )
        except (api.TelegramError, crypto.DecryptionFailed):
            pass

    connection.status = TelegramConnection.Status.BROKEN
    connection.last_error = 'disconnected by the user'
    connection.last_error_at = timezone.now()
    connection.save()
    return connection


def _extract_code(text):
    if not text:
        return None
    for word in text.split():
        candidate = word.strip().upper()
        if candidate.startswith(CODE_PREFIX) and len(candidate) > len(CODE_PREFIX):
            return candidate
    return None


def bot_can_post(token, bot_id, chat_id):
    """Is the bot an admin that may actually post here?

    Being added to a channel is not the same as being able to write in it. If
    this is not checked at link time, the first thing the user notices is a
    failed upload an hour later.
    """
    member = api.get_chat_member(token, chat_id, bot_id)
    if member.get('status') != 'administrator':
        return False, _('ربات باید ادمین کانال باشد.')
    # A channel admin without this right cannot send anything.
    if member.get('can_post_messages') is False:
        return False, _('ربات اجازه‌ی ارسال پیام در کانال را ندارد.')
    return True, ''


def find_channel(user):
    """Look through the bot's pending updates for the user's link code.

    Called when the user presses the button, so the work happens while they are
    watching and any failure is something they can act on straight away.
    """
    connection = connection_for(user, create=False)
    if connection is None or not connection.has_bot():
        raise LinkError(_('اول توکن ربات را ثبت کن.'))

    try:
        token = connection.token
    except crypto.DecryptionFailed:
        raise LinkError(
            _('توکن ذخیره‌شده قابل خواندن نیست. دوباره ثبتش کن.')
        )

    if not connection.code_is_valid():
        connection.rotate_code()

    try:
        updates = api.get_updates(token) or []
    except api.TelegramError as exc:
        raise LinkError(
            _('نشد از تلگرام آپدیت گرفت: %(reason)s') % {'reason': exc}
        )

    for update in updates:
        post = update.get('channel_post') or update.get('message') or {}
        code = _extract_code(post.get('text') or post.get('caption'))
        if code != connection.link_code:
            continue

        chat = post.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue
        return _claim(connection, token, chat_id, chat.get('title') or '')

    raise LinkError(
        _('کد در هیچ کانالی پیدا نشد. مطمئن شو ربات ادمین کانال است و کد را '
          'همان‌جا فرستاده‌ای، بعد دوباره امتحان کن.')
    )


def _claim(connection, token, chat_id, chat_title):
    allowed, reason = bot_can_post(token, connection.bot_id, chat_id)
    if not allowed:
        raise LinkError(reason)

    try:
        with transaction.atomic():
            connection.chat_id = chat_id
            connection.chat_title = (chat_title or '')[:200]
            connection.status = TelegramConnection.Status.ACTIVE
            connection.linked_at = timezone.now()
            connection.last_error = ''
            connection.last_error_at = None
            connection.save()
    except IntegrityError:
        # chat_id is unique. Two accounts sharing one channel means either can
        # delete the other's files, so refusing is the point.
        raise LinkError(_('این کانال قبلا به حساب دیگری متصل شده است.'))

    # Burn the code straight away. It has done its job, and every extra minute
    # it stays valid is a minute somebody else could use it.
    connection.rotate_code()

    try:
        api.send_message(
            token, chat_id,
            _('اتصال برقرار شد. از این پس فایل‌های شما اینجا ذخیره می‌شوند.'),
        )
    except api.TelegramError:
        # The confirmation is a courtesy. A link that worked stays worked.
        pass
    return connection
