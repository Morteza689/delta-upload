from django.db import models
from django.utils.translation import gettext_lazy as _

from account_module.models import Transaction, User


class ExchangeRate(models.Model):
    """USD to toman conversion for the rial gateways.

    Each change is a new row rather than an edit, so history survives and any
    payment can be explained. Without it a disputed amount cannot be checked.

    The rate can be entered by hand or fetched from an API (RateSetting).
    Even on automatic, the last successful rate stands: if the provider goes
    down, payments continue at yesterday's rate instead of stopping.
    """

    class Source(models.TextChoices):
        MANUAL = 'manual', _('دستی')
        API = 'api', _('خودکار از API')

    usd_to_toman = models.PositiveIntegerField(
        verbose_name=_('نرخ هر دلار به تومان'),
        help_text=_('مثلاً ۹۵۰۰۰'),
    )
    is_active = models.BooleanField(default=True, verbose_name=_('فعال'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ثبت'))

    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL,
        verbose_name=_('منبع نرخ'),
    )
    #: Provider name when the rate came from an API. Empty means manual.
    provider = models.CharField(max_length=30, blank=True,
                                verbose_name=_('ارائه‌دهنده'))

    class Meta:
        verbose_name = _('نرخ ارز')
        verbose_name_plural = _('نرخ های ارز')
        ordering = ['-created_at']

    def __str__(self):
        return f'1 USD = {self.usd_to_toman} T'

    @classmethod
    def current(cls):
        """The active rate, or None if none has been set."""
        return cls.objects.filter(is_active=True).order_by('-created_at').first()

    @classmethod
    def set_rate(cls, toman, source=Source.MANUAL, provider=''):
        """Record a new rate and deactivate the previous one.

        An unchanged rate creates no row: an hourly cron would otherwise fill
        the history with duplicates.
        """
        current = cls.current()
        if current and current.usd_to_toman == toman:
            return current

        cls.objects.filter(is_active=True).update(is_active=False)
        return cls.objects.create(
            usd_to_toman=toman, source=source, provider=provider, is_active=True,
        )


class RateSetting(models.Model):
    """Where the exchange rate comes from.

    Single row. Separate from ExchangeRate because this is configuration and
    that is history; merging them would copy the settings on every rate
    change.
    """

    auto_update = models.BooleanField(
        default=False, verbose_name=_('دریافت خودکار نرخ از API'),
    )
    # A literal default rather than rates.DEFAULT_PROVIDER: migrations store the
    # value inside themselves, and a code reference makes that history brittle.
    provider = models.CharField(
        max_length=30, default='tgju', verbose_name=_('سرویس نرخ'),
    )
    #: Some providers need a key. Empty means the provider needs none.
    api_key = models.CharField(max_length=200, blank=True,
                               verbose_name=_('کلید API'))

    last_checked_at = models.DateTimeField(null=True, blank=True,
                                           verbose_name=_('آخرین تلاش'))
    last_success_at = models.DateTimeField(null=True, blank=True,
                                           verbose_name=_('آخرین دریافت موفق'))
    #: Error from the last attempt. Empty means the last attempt succeeded.
    last_error = models.CharField(max_length=300, blank=True,
                                  verbose_name=_('آخرین خطا'))

    class Meta:
        verbose_name = _('تنظیم نرخ ارز')
        verbose_name_plural = _('تنظیم نرخ ارز')

    def __str__(self):
        return 'تنظیم نرخ ارز'

    @classmethod
    def load(cls):
        """The current setting, created with defaults if absent."""
        setting, _created = cls.objects.get_or_create(pk=1)
        return setting


class Payment(models.Model):
    """One attempt to top up a wallet.

    Everything from pressing the button to the gateway's final confirmation is
    recorded here. The amount credited always comes from this row, never from
    the parameters the gateway sends back, which the user can edit in a URL.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('در انتظار پرداخت')
        AWAITING_CONFIRMATION = 'awaiting', _('در انتظار تأیید شبکه')
        PAID = 'paid', _('پرداخت شده')
        FAILED = 'failed', _('ناموفق')
        CANCELED = 'canceled', _('لغو شده')

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='payments', verbose_name=_('کاربر'))
    gateway = models.CharField(max_length=30, verbose_name=_('درگاه'))
    # What will be added to the wallet, in cents.
    amount = models.PositiveIntegerField(verbose_name=_('مبلغ (سنت)'))
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.PENDING, verbose_name=_('وضعیت'))

    # The reference the gateway issues before payment.
    gateway_ref = models.CharField(max_length=200, blank=True, db_index=True,
                                   verbose_name=_('شناسه درگاه'))
    # Tracking reference after a successful payment.
    tracking_code = models.CharField(max_length=200, blank=True,
                                     verbose_name=_('کد پیگیری'))
    # The rate at payment time, so the conversion can be explained later.
    rate_snapshot = models.PositiveIntegerField(null=True, blank=True,
                                                verbose_name=_('نرخ در لحظه پرداخت'))
    transaction = models.OneToOneField(
        Transaction, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payment', verbose_name=_('تراکنش کیف پول'),
    )
    error = models.CharField(max_length=300, blank=True, verbose_name=_('خطا'))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name=_('تاریخ پرداخت'))

    class Meta:
        verbose_name = _('پرداخت')
        verbose_name_plural = _('پرداخت ها')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} / {self.amount} / {self.get_status_display()}'

    @property
    def is_settled(self):
        return self.status == self.Status.PAID
