import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.models import User
from upload_module.models import UploadFile

#: One impression earns a fraction of a cent, so counting in cents rounds
#: everything to zero. Earnings accumulate in micro-cents and only reach the
#: wallet at a whole cent; the remainder is kept in EarningCarry.
MICRO_PER_CENT = 10_000


class AdSlot(models.Model):
    """An ad slot on a download page.

    The network script is read from the database rather than the template, so
    swapping networks or A/B testing two needs no deploy.
    """

    class Position(models.TextChoices):
        STEP1 = 'download_step1', _('صفحه اول دانلود')
        STEP2 = 'download_step2', _('صفحه دوم دانلود')
        LINK_INTERSTITIAL = 'link_interstitial', _('صفحه میانی کوتاه‌کننده')

    position = models.CharField(max_length=40, choices=Position.choices,
                                verbose_name=_('جایگاه'))
    network = models.CharField(max_length=50, verbose_name=_('شبکه تبلیغاتی'),
                               help_text=_('مثلاً یکتانت یا تپسل'))
    script = models.TextField(
        verbose_name=_('کد تبلیغ'),
        help_text=_('کد HTML/JS که شبکه می‌دهد. عیناً در صفحه قرار می‌گیرد.'),
    )
    # With several active slots for one position the pick is weighted, which
    # allows running two networks side by side.
    weight = models.PositiveIntegerField(default=1, verbose_name=_('وزن'))
    is_active = models.BooleanField(default=True, verbose_name=_('فعال'))

    class Meta:
        verbose_name = _('جایگاه تبلیغ')
        verbose_name_plural = _('جایگاه های تبلیغ')

    def __str__(self):
        return f'{self.get_position_display()} / {self.network}'


class AdSetting(models.Model):
    """Revenue and anti-fraud settings.

    In the database rather than settings.py because these numbers are tuned
    against the ad network's real reports, without a deploy.
    """

    # Network loader: once per page, not once per slot.
    #
    # Most networks ship one shared loader plus an anchor div per slot. Putting
    # the loader inside each slot means a two-slot page loads it twice.
    #
    # Rendered only on pages that carry a slot; the user panel and login pages
    # should not run the network's analytics script.
    loader_script = models.TextField(
        blank=True, verbose_name=_('اسکریپت لودر شبکه'),
        help_text=_('اسکریپت مشترکی که شبکه می‌دهد. عیناً و بدون تغییر بگذار.'),
    )

    # The rate is entered the way the network states it.
    #
    # Yektanet pays per click, not per impression: native 50 to 1000 toman a
    # click, banner 300 to 3000. We count impressions, because a click happens
    # on the network's side and is never reported back to us per view.
    #
    # So the impression rate is derived: clicks per thousand views, times what
    # a click pays. Both are numbers an operator can read straight off the
    # Yektanet panel, which a cents-per-mille figure is not.
    cpc_toman = models.PositiveIntegerField(
        default=0, verbose_name=_('درآمد هر کلیک (تومان)'),
        help_text=_('از پنل یکتانت. همسان ۵۰ تا ۱۰۰۰، بنری ۳۰۰ تا ۳۰۰۰.'),
    )
    clicks_per_mille = models.PositiveIntegerField(
        default=10, verbose_name=_('کلیک به ازای هزار نمایش'),
        help_text=_('نرخ کلیک واقعی از گزارش شبکه. ۱۰ یعنی یک درصد.'),
    )

    # Kept as an override for a network that really does pay per impression,
    # and as the value seeded installs already carry. Zero means derive it.
    cpm_cents = models.PositiveIntegerField(
        default=0, verbose_name=_('نرخ هر ۱۰۰۰ نمایش (سنت)'),
        help_text=_('فقط اگر شبکه به ازای نمایش پرداخت می‌کند. صفر بگذار تا از نرخ کلیک حساب شود.'),
    )
    uploader_share_percent = models.PositiveIntegerField(
        default=60, verbose_name=_('سهم آپلودکننده (درصد)'),
    )
    confirm_after_days = models.PositiveIntegerField(
        default=7, verbose_name=_('تأیید درآمد پس از (روز)'),
        help_text=_('شبکه تبلیغاتی هم ممکن است نمایش‌ها را رد کند؛ این فاصله فرصت اصلاح می‌دهد.'),
    )

    # --- Anti-fraud ---
    min_dwell_seconds = models.PositiveIntegerField(
        default=3, verbose_name=_('حداقل فاصله بین دو صفحه (ثانیه)'),
        help_text=_('عبور سریع‌تر از این یعنی ربات، نه بازدید واقعی.'),
    )
    max_dwell_seconds = models.PositiveIntegerField(
        default=600, verbose_name=_('حداکثر فاصله بین دو صفحه (ثانیه)'),
    )
    dedup_hours = models.PositiveIntegerField(
        default=24, verbose_name=_('فاصله شمارش مجدد یک بازدیدکننده (ساعت)'),
        help_text=_('یک بازدیدکننده برای یک فایل، در این بازه فقط یک بار شمرده می‌شود.'),
    )
    visitor_cooldown_seconds = models.PositiveIntegerField(
        default=60, verbose_name=_('فاصله بین دو درآمد یک بازدیدکننده (ثانیه)'),
        help_text=_('یک نفر نباید با رفتن سراغ فایل‌های مختلف، پشت‌سرهم درآمد بسازد.'),
    )
    max_earnings_per_file_per_day = models.PositiveIntegerField(
        default=500, verbose_name=_('سقف درآمد روزانه هر فایل (تعداد)'),
        help_text=_('حتی اگر همه‌ی قواعد دیگر رد شوند، یک فایل بیش از این در روز درآمد نمی‌سازد.'),
    )
    max_earnings_per_user_per_day = models.PositiveIntegerField(
        default=2000, verbose_name=_('سقف درآمد روزانه هر کاربر (تعداد)'),
    )
    require_cookies = models.BooleanField(
        default=True, verbose_name=_('نیاز به پشتیبانی از کوکی'),
        help_text=_('مرورگر واقعی کوکی نگه می‌دارد؛ بیشتر اسکریپت‌های ساده نه.'),
    )

    is_active = models.BooleanField(default=True, verbose_name=_('فعال'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('آخرین تغییر'))

    class Meta:
        verbose_name = _('تنظیمات تبلیغات')
        verbose_name_plural = _('تنظیمات تبلیغات')

    def __str__(self):
        return (f'{self.effective_cpm_cents():.3f}c/1000 '
                f'/ share {self.uploader_share_percent}%')

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).order_by('-updated_at').first()

    def effective_cpm_cents(self):
        """What a thousand impressions actually bring in, in cents.

        From the click rate when there is one, because that is what the network
        pays on. An explicit cpm_cents wins, for a network that really does pay
        per impression.

        Returns a float: the true figure is often well under one cent per
        thousand, and rounding it to an integer here would silently become zero.
        """
        if self.cpm_cents:
            return float(self.cpm_cents)
        if not self.cpc_toman or not self.clicks_per_mille:
            return 0.0

        from payment_module.models import ExchangeRate

        rate = ExchangeRate.current()
        toman_per_dollar = rate.usd_to_toman if rate else 0
        if not toman_per_dollar:
            # No rate, no defensible conversion. Accruing at a guessed rate
            # would promise money in a currency we cannot price.
            return 0.0

        toman = self.cpc_toman * self.clicks_per_mille
        return toman / toman_per_dollar * 100

    def micro_per_impression(self):
        """The uploader's share of one impression, in micro-cents.

        Micro-cents exist for exactly this: a single impression is worth a tiny
        fraction of a cent, and anything coarser rounds every one of them to
        nothing.
        """
        cents_per_mille = self.effective_cpm_cents()
        return int(
            cents_per_mille * MICRO_PER_CENT * self.uploader_share_percent
            / (1000 * 100)
        )


class DownloadSession(models.Model):
    """One pass through the download funnel: step 1 to step 2.

    This row is what makes step 2 reachable only via step 1. Without it anyone
    could jump straight to step 2 and the first ad would never be seen.
    """

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    file = models.ForeignKey(UploadFile, on_delete=models.CASCADE,
                             related_name='download_sessions')
    # Salted hash of IP and User-Agent. The IP itself is never stored.
    visitor_hash = models.CharField(max_length=64, db_index=True)

    step1_at = models.DateTimeField(auto_now_add=True)
    step2_at = models.DateTimeField(null=True, blank=True)
    counted = models.BooleanField(default=False)
    reject_reason = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = _('نشست دانلود')
        verbose_name_plural = _('نشست های دانلود')
        indexes = [
            models.Index(fields=['file', 'visitor_hash', 'step2_at']),
        ]

    def __str__(self):
        return f'{self.file_id} / {self.visitor_hash[:8]}'


class LinkView(models.Model):
    """One view of the shortener interstitial.

    The DownloadSession equivalent for the shortener funnel, kept separate
    because the funnel shape differs: two pages there, one here.
    """

    link = models.ForeignKey('link_module.ShortLink', on_delete=models.CASCADE,
                             related_name='views', verbose_name=_('لینک'))
    visitor_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    counted = models.BooleanField(default=False)
    reject_reason = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = _('بازدید لینک کوتاه')
        verbose_name_plural = _('بازدیدهای لینک کوتاه')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['link', 'visitor_hash', 'created_at'])]

    def __str__(self):
        return f'{self.link_id} / {self.visitor_hash[:8]}'


class AdEarning(models.Model):
    """Ad revenue from one valid pass through a funnel.

    Earnings do not reach the balance immediately: they sit pending and are
    moved by confirm_ad_earnings days later. The network may still rule an
    impression invalid, and clawing back withdrawable money is hard.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('در انتظار تأیید')
        CONFIRMED = 'confirmed', _('تأیید شده')
        REJECTED = 'rejected', _('رد شده')

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='ad_earnings', verbose_name=_('کاربر'))

    # Earnings come from two funnels and each row carries exactly one:
    #   download funnel      -> file + session
    #   shortener interstitial -> link + link_view
    # Both are nullable; the constraint below enforces exactly one.
    file = models.ForeignKey(UploadFile, on_delete=models.CASCADE, null=True,
                             blank=True, related_name='ad_earnings',
                             verbose_name=_('فایل'))
    session = models.OneToOneField(DownloadSession, on_delete=models.CASCADE,
                                   null=True, blank=True, related_name='earning')

    link = models.ForeignKey('link_module.ShortLink', on_delete=models.CASCADE,
                             null=True, blank=True, related_name='ad_earnings',
                             verbose_name=_('لینک کوتاه'))
    link_view = models.OneToOneField('ads_module.LinkView',
                                     on_delete=models.CASCADE, null=True,
                                     blank=True, related_name='earning')
    micro_cents = models.PositiveIntegerField(verbose_name=_('درآمد (میکروسنت)'))
    impressions = models.PositiveIntegerField(default=2, verbose_name=_('تعداد نمایش'))
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING, verbose_name=_('وضعیت'))
    available_at = models.DateTimeField(verbose_name=_('قابل تأیید از'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ'))

    @property
    def source_label(self):
        """What earned this, in words the owner recognises.

        A slug is a random twelve characters and tells nobody which file it
        was. The uploaded filename does, and is there even when the owner left
        the title blank, which is the usual case.
        """
        if self.file_id and self.file:
            from upload_module.downloads import download_filename

            return self.file.name or download_filename(self.file)
        if self.link_id and self.link:
            return self.link.code
        # A row with neither is refused by the constraint below, so this is
        # only reachable for a source deleted out from under it.
        return ''

    @property
    def source_url(self):
        """Where to go for more about it, or empty.

        The file's own report rather than its public page: this is the owner
        looking at their earnings, and the report is the page that breaks the
        number down. The public page also 404s once a file goes private.
        """
        from django.urls import reverse

        if self.file_id and self.file and not self.file.is_deleted():
            return reverse('file_report', args=[self.file_id])
        if self.link_id and self.link:
            return reverse('shorten_page')
        return ''

    @property
    def source_kind(self):
        return 'file' if self.file_id else 'link'

    class Meta:
        verbose_name = _('درآمد تبلیغات')
        verbose_name_plural = _('درآمدهای تبلیغات')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'available_at'])]
        constraints = [
            # A row with no source is untraceable revenue; a row with two is
            # one impression counted twice.
            models.CheckConstraint(
                condition=(
                    models.Q(file__isnull=False, link__isnull=True)
                    | models.Q(file__isnull=True, link__isnull=False)
                ),
                name='ad_earning_has_exactly_one_source',
            ),
        ]

    def __str__(self):
        return f'{self.user} / {self.micro_cents} micro'

    def is_ready(self):
        return self.status == self.Status.PENDING and timezone.now() >= self.available_at


class EarningCarry(models.Model):
    """Leftover sub-cent revenue per user.

    An impression earns a fraction of a cent. Rounding each one down would
    quietly discard most of what a user is owed, so the remainder is carried
    here until it adds up to a whole cent.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE,
                                related_name='earning_carry')
    micro_cents = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('باقیمانده درآمد')
        verbose_name_plural = _('باقیمانده های درآمد')

    def __str__(self):
        return f'{self.user} / {self.micro_cents}'
