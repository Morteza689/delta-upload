import uuid

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.models import User


class UploadFile(models.Model):
    class ChoiceType(models.TextChoices):
        Folder = 'پوشه', _('پوشه')
        File = 'فایل', _('فایل')

    class Visibility(models.TextChoices):
        PUBLIC = 'public', _('عمومی')
        PRIVATE = 'private', _('خصوصی')

    class StorageBackend(models.TextChoices):
        LOCAL = 'local', _('سرور دلتا')
        TELEGRAM = 'telegram', _('تلگرام کاربر')

    name = models.CharField(max_length=100, verbose_name=_('نام فایل'), blank=True, null=True)
    description = models.TextField(verbose_name=_('توضیحات'), blank=True, null=True)
    # max_length 255, not the default 100. The stored path is
    # dl/files/<name>, and Django truncates the name to make the whole thing
    # fit, so on the default a long filename came back mangled.
    file = models.FileField(upload_to='dl/files', max_length=255,
                            verbose_name=_('فایل'), blank=True, null=True)

    # The filename as the browser sent it, before storage touched it.
    #
    # Django sanitises and, when a name is long, truncates it and splices in a
    # random suffix for uniqueness. That is right for a path on disk and wrong
    # for the name a download arrives under, which should be what was uploaded.
    #
    # db_default because migrations run before the service restarts: for that
    # window old workers insert rows without this column.
    original_filename = models.CharField(
        max_length=255, blank=True, db_default='',
        verbose_name=_('نام اصلی فایل'),
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name=_('اپلودر'), null=True, blank=True)
    number_of_download = models.IntegerField(verbose_name=_('تعداد دانلود'), default=0)
    slug = models.SlugField(verbose_name=_('لینک دانلود'), null=True, blank=True)
    is_active = models.BooleanField(verbose_name=_('فعال / غیر فعال'), default=True)
    created_deta = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))

    type_file = models.CharField(max_length=200, choices=ChoiceType.choices, verbose_name=_('نوع'), blank=True, null=True)
    dir_id = models.ForeignKey("UploadFile", on_delete=models.CASCADE, verbose_name=_('ایدی پوشه'), blank=True, null=True)

    # Private by default. A freshly uploaded file must not become public by accident.
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PRIVATE,
        verbose_name=_('وضعیت انتشار'),
    )
    # Empty means no expiry. Once past, the file leaves the public listing but
    # stays available in the owner's panel.
    public_until = models.DateTimeField(
        null=True, blank=True, verbose_name=_('عمومی تا تاریخ'),
    )
    # Zero means free.
    price = models.IntegerField(default=0, verbose_name=_('قیمت'))

    # number_of_download is the public total; these two split it into free and paid.
    free_downloads = models.IntegerField(default=0, verbose_name=_('دانلود رایگان'))
    paid_downloads = models.IntegerField(default=0, verbose_name=_('دانلود پولی'))

    # Guest files are deleted at this moment; member files have no such deadline.
    # Distinct from public_until, which only closes the public link.
    auto_delete_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        verbose_name=_('حذف خودکار در'),
    )

    # Recorded at save time. Quota maths cannot use file.size, which hits storage
    # on every access; on S3 that is a network round trip per file.
    size_bytes_cached = models.BigIntegerField(
        default=0, verbose_name=_('حجم (بایت)'),
    )

    # Set when the owner deletes the file. The row itself stays.
    #
    # A real delete cascades into Purchase, FileDownload and AdEarning, so it
    # would wipe the purchase records of people who paid for this file and the
    # owner's own unconfirmed earnings along with it. The bytes are what the
    # owner wants gone; the history is what everyone else needs kept.
    deleted_at = models.DateTimeField(
        null=True, blank=True, db_index=True, verbose_name=_('حذف شده در'),
    )

    # Where the bytes actually live. LOCAL covers both the local disk and the
    # S3 bucket, since both are reached through the file field. TELEGRAM means
    # the file field is empty and the content sits in the owner's own channel,
    # in pieces, described by telegram_module.TelegramFile.
    #
    # It is a field rather than a lookup because the download path checks it on
    # every request and a join per download is not worth it.
    storage_backend = models.CharField(
        max_length=20, choices=StorageBackend.choices,
        default=StorageBackend.LOCAL, db_index=True,
        verbose_name=_('محل ذخیره'),
    )

    def save(self, *args, **kwargs):
        if self.file and not self.size_bytes_cached:
            try:
                self.size_bytes_cached = self.file.size
            except (OSError, ValueError):
                self.size_bytes_cached = 0
        super().save(*args, **kwargs)

    @property
    def size_bytes(self):
        """File size, or None when there is no file.

        Templates must use this rather than file.size: reading .size on an empty
        field raises ValueError and turns the page into a 500. Rows without a
        file do exist (folders, legacy records).
        """
        if self.storage_backend == self.StorageBackend.TELEGRAM:
            # Nothing to stat: the bytes are in Telegram and the cached value
            # was written before they left this server.
            return self.size_bytes_cached or None
        if not self.file:
            return None
        try:
            return self.file.size
        except (OSError, ValueError):
            # Not on storage: a legacy row or an interrupted migration.
            return None

    def on_telegram(self):
        return self.storage_backend == self.StorageBackend.TELEGRAM

    def is_deleted(self):
        return self.deleted_at is not None

    def has_content(self):
        """Is there anything to download?

        Views must ask this rather than testing the file field, which is empty
        both for a file that finished moving to Telegram and for one the owner
        deleted.
        """
        if self.is_deleted():
            return False
        if self.on_telegram():
            return True
        return bool(self.file)

    def soft_delete(self):
        """Destroy the bytes and keep the row.

        The owner wants the file gone; everyone else needs the history kept. A
        real delete cascades into Purchase, FileDownload and AdEarning, which
        would erase the records of people who paid for this file and the
        owner's own unconfirmed earnings with them.

        The size is zeroed because nothing is stored any more, which is also
        what releases the space against the owner's quota.
        """
        from django.utils import timezone

        if self.is_deleted():
            return

        if self.on_telegram():
            self._remove_from_telegram()
        elif self.file:
            try:
                self.file.storage.delete(self.file.name)
            except OSError:
                # The row is about to say the file is gone, and it is gone as
                # far as the site is concerned. Orphaned bytes are the cleanup
                # command's problem, not a reason to fail a delete.
                pass

        self.file = ''
        self.size_bytes_cached = 0
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=['file', 'size_bytes_cached', 'is_active',
                                 'deleted_at'])

    def _remove_from_telegram(self):
        telegram_file = getattr(self, 'telegram_copy', None)
        if telegram_file is None:
            return
        from telegram_module import storage as telegram_storage

        try:
            telegram_storage.delete_from_telegram(telegram_file)
        except Exception:
            # Same reasoning as above: messages left in the user's own channel
            # are something they can remove by hand.
            pass
        telegram_file.delete()

    def is_free(self):
        return self.price <= 0

    def is_public_now(self):
        """Is this file publicly reachable right now?

        Single source of truth for the download pages.
        """
        if self.is_deleted():
            return False
        if not self.is_active or self.visibility != self.Visibility.PUBLIC:
            return False
        return self.public_until is None or timezone.now() <= self.public_until

    def is_purchased_by(self, user):
        if not user or not user.is_authenticated:
            return False
        if self.author_id == user.id:
            return True
        return self.purchases.filter(buyer_id=user.id).exists()

    def delete(self, using=None, keep_parents=False):
        if self.file:
            self.file.storage.delete(self.file.name)
        super().delete()

    def __str__(self):
        return self.name or (self.file.name if self.file else f'file #{self.pk}')

    def get_absolute_url(self):
        return reverse('download_page', args=[self.slug])

    class Meta:
        verbose_name = _('فایل')
        verbose_name_plural = _('فایل ها')


class Purchase(models.Model):
    """A paid file purchase. Access is permanent; no repeat purchase."""

    buyer = models.ForeignKey(User, on_delete=models.CASCADE,
                              related_name='purchases', verbose_name=_('خریدار'))
    file = models.ForeignKey(UploadFile, on_delete=models.CASCADE,
                             related_name='purchases', verbose_name=_('فایل'))
    # Frozen at purchase time so a later price change cannot rewrite history.
    amount = models.IntegerField(verbose_name=_('مبلغ پرداختی'))
    seller_share = models.IntegerField(verbose_name=_('سهم فروشنده'))
    # Recorded rather than derived from the percentage in settings, which
    # changes. Without it, last year's sales get recomputed at this year's rate
    # and the books stop matching what actually moved.
    #
    # site_commission plus seller_share always equals amount.
    site_commission = models.IntegerField(
        default=0, db_default=0, verbose_name=_('کمیسیون سایت'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ خرید'))

    class Meta:
        verbose_name = _('خرید')
        verbose_name_plural = _('خریدها')
        constraints = [
            models.UniqueConstraint(fields=['buyer', 'file'], name='unique_purchase_per_buyer'),
        ]

    def __str__(self):
        return f'{self.buyer} / {self.file}'


class DownloadLink(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True)
    file = models.ForeignKey('UploadFile', on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    # Decided when the link is issued, since that is where we know whether the
    # file was free or already purchased.
    kind = models.CharField(max_length=20, default='free', verbose_name=_('نوع دانلود'))

    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at


class FileDownload(models.Model):
    """One completed download.

    The counters on UploadFile are for quick display; this table is for
    reporting: free versus paid, trend over time, unique visitors.
    """

    class Kind(models.TextChoices):
        FREE = 'free', _('رایگان')
        PAID = 'paid', _('پولی')
        OWNER = 'owner', _('توسط صاحب فایل')

    file = models.ForeignKey(UploadFile, on_delete=models.CASCADE,
                             related_name='downloads', verbose_name=_('فایل'))
    kind = models.CharField(max_length=20, choices=Kind.choices,
                            default=Kind.FREE, verbose_name=_('نوع'))
    # Salted hash rather than the IP itself; enough to count unique visitors.
    visitor_hash = models.CharField(max_length=64, blank=True, db_index=True)
    bytes_sent = models.BigIntegerField(default=0, verbose_name=_('حجم منتقل‌شده'))
    created_at = models.DateTimeField(auto_now_add=True, db_index=True,
                                      verbose_name=_('تاریخ'))

    class Meta:
        verbose_name = _('دانلود')
        verbose_name_plural = _('دانلودها')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['file', 'kind', 'created_at'])]

    def __str__(self):
        return f'{self.file_id} / {self.get_kind_display()}'
