"""Files kept in a user's own Telegram channel, through the user's own bot.

Every user brings a bot they created themselves at BotFather, and adds it to a
private channel they own. We store the token, encrypted, and use it only to put
their files in that channel and read them back.

Nothing here is shared between users. There is no site-wide bot, so a token
that leaks reaches one person's channel rather than everybody's, and Telegram's
per-bot rate limits are per user instead of one ceiling for the whole site.

We never ask for a phone number, a login code, or a two-step password. A bot
token is not an account: it cannot read the owner's chats, contacts, or saved
messages, and revoking it is one message to BotFather.

Four tables:

  TelegramSetting     one row, whether the feature is offered at all
  TelegramConnection  one user, their bot, and their channel
  TelegramFile        one upload, split across messages in that channel
  TelegramChunk       one message holding one part

The split exists because the Bot API refuses to hand back more than 20 MB in a
single getFile, so a large upload becomes a run of parts streamed back in
order.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from upload_module.models import UploadFile

User = settings.AUTH_USER_MODEL

#: How long an unused link code stays valid.
LINK_CODE_LIFETIME = timezone.timedelta(minutes=30)


def _new_link_code():
    # Short enough to retype off a screen, long enough that guessing one is
    # not a way into someone else's account.
    return f'DUP-{secrets.token_hex(6).upper()}'


class TelegramSetting(models.Model):
    """Whether the feature is offered, one row.

    It holds no credentials. Every token belongs to a user and lives on their
    own connection row; there is nothing site-wide to keep here but the switch.

    The switch exists so the feature can be withdrawn without a deploy, which
    matters for something that depends on a third party staying reachable.
    """

    is_enabled = models.BooleanField(
        default=True, verbose_name=_('ارائه به کاربران'),
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('آخرین تغییر'))

    class Meta:
        verbose_name = _('تنظیمات تلگرام')
        verbose_name_plural = _('تنظیمات تلگرام')

    def __str__(self):
        return 'Telegram settings'

    @classmethod
    def load(cls):
        """The settings row, creating it if needed.

        Only for the staff page, which is about to write. Read paths use
        feature.is_enabled, which never writes: a GET that can insert a row is
        a GET that can be made to fill a table.
        """
        setting, _created = cls.objects.get_or_create(pk=1)
        return setting

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # The switch is cached and read on every request, so a change has to
        # take effect now rather than within the hour.
        from . import feature

        feature.forget()

    @staticmethod
    def mask(secret):
        """A hint that this is the right value, without printing it.

        Anything short enough to be guessed from a hint is hidden completely.
        """
        secret = secret or ''
        if len(secret) < 12:
            return '•' * len(secret)
        return f'{secret[:6]}{"•" * 8}{secret[-4:]}'


class TelegramConnection(models.Model):
    """One user's own bot, and the channel it puts their files in."""

    class Status(models.TextChoices):
        NO_BOT = 'no_bot', _('ربات ثبت نشده')
        PENDING = 'pending', _('در انتظار اتصال کانال')
        ACTIVE = 'active', _('متصل')
        BROKEN = 'broken', _('دسترسی قطع شده')

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='telegram_connection',
        verbose_name=_('کاربر'),
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.NO_BOT,
        verbose_name=_('وضعیت'),
    )

    # Encrypted at rest. This is the user's credential, not ours: a database
    # dump that leaked it would hand over control of their bot. See crypto.py
    # for what that does and does not protect against.
    #
    # Never read directly. Use the token property, which decrypts.
    bot_token_encrypted = models.TextField(
        blank=True, verbose_name=_('توکن ربات (رمزشده)'),
    )
    # Cached from getMe so the page can show which bot this is without a call
    # to Telegram on every render.
    bot_username = models.CharField(
        max_length=64, blank=True, verbose_name=_('نام ربات'),
    )
    # Telegram's own id for the bot. Used to ask whether it is an admin of the
    # channel, which needs an id rather than a name.
    bot_id = models.BigIntegerField(
        null=True, blank=True, verbose_name=_('شناسه ربات'),
    )

    # Null until the code is found in a channel. Telegram channel ids are large
    # and negative, hence BigInteger.
    chat_id = models.BigIntegerField(
        null=True, blank=True, unique=True, verbose_name=_('شناسه کانال'),
    )
    chat_title = models.CharField(
        max_length=200, blank=True, verbose_name=_('نام کانال'),
    )

    link_code = models.CharField(
        max_length=32, default=_new_link_code, db_index=True,
        verbose_name=_('کد اتصال'),
    )
    code_expires_at = models.DateTimeField(verbose_name=_('انقضای کد'))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))
    linked_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('تاریخ اتصال'),
    )
    last_error = models.TextField(blank=True, verbose_name=_('آخرین خطا'))
    last_error_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('زمان آخرین خطا'),
    )

    class Meta:
        verbose_name = _('اتصال تلگرام')
        verbose_name_plural = _('اتصال های تلگرام')

    def __str__(self):
        return f'{self.user} -> {self.chat_title or self.chat_id or "unlinked"}'

    @property
    def token(self):
        """The decrypted bot token.

        Raises crypto.DecryptionFailed if the encryption key changed, which is
        deliberately loud: silently treating it as absent would disconnect
        every user at once with no explanation anywhere.
        """
        from . import crypto

        return crypto.decrypt(self.bot_token_encrypted)

    def set_token(self, raw_token):
        from . import crypto

        self.bot_token_encrypted = crypto.encrypt(raw_token)

    def has_bot(self):
        return bool(self.bot_token_encrypted)

    def is_usable(self):
        return (self.status == self.Status.ACTIVE
                and self.chat_id is not None
                and self.has_bot())

    def code_is_valid(self):
        return timezone.now() < self.code_expires_at

    def rotate_code(self):
        """Issue a fresh code. Used when the old one expired unused."""
        self.link_code = _new_link_code()
        self.code_expires_at = timezone.now() + LINK_CODE_LIFETIME
        self.save(update_fields=['link_code', 'code_expires_at'])

    def mark_broken(self, message):
        """Record that Telegram turned us away.

        The row is kept rather than deleted. The chunk rows still describe
        where every file sits, so restoring the bot's admin rights brings
        everything back without a re-upload.
        """
        self.status = self.Status.BROKEN
        self.last_error = str(message)[:2000]
        self.last_error_at = timezone.now()
        self.save(update_fields=['status', 'last_error', 'last_error_at'])

    def clear_error(self):
        if self.last_error:
            self.last_error = ''
            self.last_error_at = None
            self.save(update_fields=['last_error', 'last_error_at'])

    def save(self, *args, **kwargs):
        if not self.code_expires_at:
            self.code_expires_at = timezone.now() + LINK_CODE_LIFETIME
        super().save(*args, **kwargs)


class TelegramFile(models.Model):
    """One upload on its way to, or living in, a Telegram channel."""

    class Status(models.TextChoices):
        PENDING = 'pending', _('در صف انتقال')
        UPLOADING = 'uploading', _('در حال انتقال')
        STORED = 'stored', _('ذخیره شده')
        FAILED = 'failed', _('ناموفق')

    upload_file = models.OneToOneField(
        UploadFile, on_delete=models.CASCADE, related_name='telegram_copy',
        verbose_name=_('فایل'),
    )
    connection = models.ForeignKey(
        TelegramConnection, on_delete=models.PROTECT, related_name='files',
        verbose_name=_('اتصال'),
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
        db_index=True, verbose_name=_('وضعیت'),
    )
    total_bytes = models.BigIntegerField(default=0, verbose_name=_('حجم (بایت)'))
    chunk_bytes = models.IntegerField(default=0, verbose_name=_('اندازه هر تکه'))

    # The filename as uploaded, kept because the transfer clears the file field
    # and takes the only other copy of it with it. Without this, a download
    # from Telegram arrives named after the display title, which usually has no
    # extension and sometimes is not set at all.
    #
    # db_default rather than only a model default: migrations run before the
    # service restarts, so for that window old workers insert rows without
    # this column. Without a default in the database, every one of those
    # inserts fails on the NOT NULL constraint.
    original_name = models.CharField(
        max_length=255, blank=True, db_default='',
        verbose_name=_('نام اصلی فایل'),
    )

    attempts = models.IntegerField(default=0, verbose_name=_('تعداد تلاش'))
    last_error = models.TextField(blank=True, verbose_name=_('آخرین خطا'))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))
    stored_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('زمان ذخیره'),
    )

    class Meta:
        verbose_name = _('فایل تلگرامی')
        verbose_name_plural = _('فایل های تلگرامی')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.upload_file_id} ({self.status})'

    def is_ready(self):
        return self.status == self.Status.STORED

    def in_flight(self):
        return self.status in (self.Status.PENDING, self.Status.UPLOADING)

    @property
    def sent_bytes(self):
        """How much has actually reached the channel.

        Summed from the recorded parts rather than kept in a counter. A counter
        drifts from reality the first time a transfer dies mid-part, and then
        the bar shows a number nothing in the database agrees with.
        """
        if self.status == self.Status.STORED:
            return self.total_bytes
        return sum(chunk.size_bytes for chunk in self.chunks.all())

    @property
    def progress_percent(self):
        if self.status == self.Status.STORED:
            return 100
        if not self.total_bytes:
            return 0
        # Floored, so a bar only reads 100 once the file really is stored.
        return min(99, int(self.sent_bytes * 100 / self.total_bytes))


class TelegramChunk(models.Model):
    """One part of a file, as one message in the channel."""

    telegram_file = models.ForeignKey(
        TelegramFile, on_delete=models.CASCADE, related_name='chunks',
        verbose_name=_('فایل'),
    )
    index = models.IntegerField(verbose_name=_('شماره تکه'))
    size_bytes = models.IntegerField(verbose_name=_('حجم (بایت)'))

    # message_id is the durable handle: it survives, and it is what deletes the
    # part later. file_id is a cache that lets us skip a lookup, but Telegram
    # does not promise it stays valid forever, so nothing depends on it alone.
    message_id = models.BigIntegerField(verbose_name=_('شناسه پیام'))
    file_id = models.CharField(max_length=200, verbose_name=_('شناسه فایل'))
    file_unique_id = models.CharField(
        max_length=100, blank=True, verbose_name=_('شناسه یکتا'),
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))

    class Meta:
        verbose_name = _('تکه فایل')
        verbose_name_plural = _('تکه های فایل')
        ordering = ['index']
        constraints = [
            models.UniqueConstraint(
                fields=['telegram_file', 'index'],
                name='telegram_chunk_index_unique',
            ),
        ]

    def __str__(self):
        return f'{self.telegram_file_id}#{self.index}'
