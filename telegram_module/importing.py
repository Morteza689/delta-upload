"""Registering a file the user posted to their channel themselves.

They paste the post's link and we record it, instead of downloading the file
and uploading the same bytes back into the same channel.

The awkward part is that the Bot API has no way to fetch an arbitrary message
by id. What it does have is forwardMessage, which returns the forwarded message
in full, attachment metadata included. So the post is forwarded into the same
channel, read, and the copy deleted immediately. One message appears and
disappears; the original is untouched.
"""
import logging
import re

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _

from upload_module.models import UploadFile

from . import api, crypto
from .models import TelegramChunk, TelegramFile

logger = logging.getLogger(__name__)

#: getFile will not return more than 20 MB, so anything larger can be recorded
#: but never served. Recording it would produce a file that 404s on download.
MAX_IMPORT_BYTES = 20 * 1024 * 1024

#: Attachment kinds that carry a file_id, in the order Telegram nests them.
ATTACHMENT_FIELDS = ('document', 'video', 'audio', 'animation', 'voice',
                     'video_note')

#: https://t.me/c/<internal id>/<message id>
#: Forum topics add a thread id in between, so the last number is the message.
PRIVATE_LINK = re.compile(r'(?:https?://)?t\.me/c/(\d+)(?:/\d+)*/(\d+)/?$')
#: https://t.me/<username>/<message id>, for a channel with a public name.
PUBLIC_LINK = re.compile(r'(?:https?://)?t\.me/([A-Za-z][\w]{3,})/(\d+)/?$')


class ImportError_(Exception):
    """The post could not be imported. The message is shown to the user."""


def parse_link(raw, connection):
    """Turn a t.me link into (chat_id, message_id).

    The chat is taken from the user's own connection rather than from the link,
    so a link to somebody else's channel cannot be imported into this account
    even if the bot happens to be an admin there too.
    """
    link = (raw or '').strip()
    if not link:
        raise ImportError_(_('لینک پست را وارد کن.'))

    match = PRIVATE_LINK.match(link)
    if match:
        internal, message_id = match.groups()
        # t.me/c/ strips the -100 prefix that the API uses.
        chat_id = int(f'-100{internal}')
        if chat_id != connection.chat_id:
            raise ImportError_(
                _('این لینک به کانال متصلِ تو اشاره نمی‌کند.')
            )
        return chat_id, int(message_id)

    match = PUBLIC_LINK.match(link)
    if match:
        _username, message_id = match.groups()
        # A public link gives a name, not an id, and resolving it would let
        # someone point at any public channel. The connected chat is used
        # instead; if the link is for a different channel, the forward fails
        # with a message id that is not there.
        return connection.chat_id, int(message_id)

    raise ImportError_(
        _('لینک معتبر نیست. روی پست در تلگرام «Copy Post Link» بزن و همان را '
          'اینجا بگذار.')
    )


def _attachment(message):
    """The file part of a message, whatever kind it is."""
    for field in ATTACHMENT_FIELDS:
        item = message.get(field)
        if item:
            return field, item

    # A photo arrives as a list of sizes, largest last.
    photos = message.get('photo')
    if photos:
        return 'photo', photos[-1]
    return None, None


def _fetch_message(token, chat_id, message_id):
    """Read a message by forwarding it and deleting the copy."""
    try:
        forwarded = api.call(token, 'forwardMessage', {
            'chat_id': chat_id,
            'from_chat_id': chat_id,
            'message_id': message_id,
            'disable_notification': 'true',
        })
    except api.TelegramError as exc:
        raise ImportError_(
            _('تلگرام این پست را برنگرداند: %(reason)s') % {'reason': exc}
        )

    try:
        return forwarded
    finally:
        # Always, even when the caller rejects what it found. Leaving the copy
        # behind would litter the channel a little more on every attempt.
        try:
            api.delete_message(token, chat_id, forwarded['message_id'])
        except (api.TelegramError, KeyError) as exc:
            logger.info('could not remove the forwarded copy: %s', exc)


def _name_for(kind, item, message, fallback):
    name = item.get('file_name')
    if name:
        return name[:100]
    caption = (message.get('caption') or '').strip().splitlines()
    if caption:
        return caption[0][:100]
    return f'{kind}-{fallback}'[:100]


@transaction.atomic
def import_post(user, connection, link, description=''):
    """Register a post as one of the user's files.

    Returns the new UploadFile.
    """
    if not connection or not connection.is_usable():
        raise ImportError_(_('اول کانالت را وصل کن.'))

    try:
        token = connection.token
    except crypto.DecryptionFailed:
        raise ImportError_(_('توکن ربات قابل خواندن نیست. دوباره ثبتش کن.'))

    chat_id, message_id = parse_link(link, connection)

    if TelegramChunk.objects.filter(
        telegram_file__connection=connection, message_id=message_id,
    ).exists():
        raise ImportError_(_('این پست قبلا اضافه شده است.'))

    message = _fetch_message(token, chat_id, message_id)
    kind, item = _attachment(message)
    if item is None:
        raise ImportError_(
            _('این پست فایلی ندارد. لینک پستی را بده که در آن فایل فرستاده‌ای.')
        )

    size = int(item.get('file_size') or 0)
    if not size:
        raise ImportError_(_('حجم این فایل مشخص نیست و قابل اضافه کردن نیست.'))
    if size > MAX_IMPORT_BYTES:
        # Honest about the cause: the limit is Telegram's on reading a file
        # back, not ours on storing one. Uploading through the site splits the
        # file into parts and has no such ceiling.
        raise ImportError_(
            _('این فایل %(size)s است. تلگرام اجازه نمی‌دهد رباتی فایل بزرگ‌تر '
              'از ۲۰ مگابایت را برای دانلود بردارد، پس این پست قابل استفاده '
              'نیست. برای فایل بزرگ‌تر، از همین سایت آپلودش کن تا تکه‌تکه '
              'ذخیره شود.') % {'size': _human(size)}
        )

    upload_file = UploadFile.objects.create(
        name=_name_for(kind, item, message, message_id),
        description=description or '',
        author=user,
        type_file=UploadFile.ChoiceType.File,
        # Unguessable: it is the only thing protecting the public page.
        slug=get_random_string(12),
        size_bytes_cached=size,
        storage_backend=UploadFile.StorageBackend.TELEGRAM,
        visibility=UploadFile.Visibility.PRIVATE,
    )

    telegram_file = TelegramFile.objects.create(
        upload_file=upload_file,
        connection=connection,
        status=TelegramFile.Status.STORED,
        total_bytes=size,
        chunk_bytes=size,
        stored_at=timezone.now(),
        # Telegram knows the real filename for a document; for a video or a
        # photo there is none, so the download falls back to the title.
        original_name=(item.get('file_name') or '')[:255],
    )
    TelegramChunk.objects.create(
        telegram_file=telegram_file,
        index=0,
        size_bytes=size,
        # The original message, not the forwarded copy that was just deleted.
        message_id=message_id,
        file_id=item.get('file_id', ''),
        file_unique_id=item.get('file_unique_id', ''),
    )
    logger.info('telegram import: user=%s message=%s size=%s',
                user.pk, message_id, size)
    return upload_file


def _human(size):
    from account_module.quota import human_size

    return human_size(size)
