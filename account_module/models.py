from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    balance = models.IntegerField(verbose_name=_('موجودی'), default=0)
    email_active_code = models.CharField(max_length=100, verbose_name=_('کد فعالسازی ایمیل'))

    # When the activation link was first opened.
    #
    # The activation code is no longer rotated after use, so that a mail
    # scanner fetching the link does not turn the visitor's own click into a
    # 404. This is what keeps that safe: once set, the code can no longer
    # change is_active, so an account an operator disabled cannot be switched
    # back on with an old activation email.
    activated_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_('زمان فعال‌سازی'),
    )

    # Which language to write to this person in.
    #
    # The site itself reads the language from a cookie, which is right for a
    # page: it follows whichever browser is in front of you. Mail has no
    # browser and no cookie, so without this every message would go out in the
    # default language regardless of what the reader picked.
    #
    # Set when a signed-in user changes the language, so nobody has to fill in
    # a preference form to be written to correctly.
    language = models.CharField(
        max_length=10, choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE, verbose_name=_('زبان'),
    )

    class Meta:
        verbose_name = _('کاربر')
        verbose_name_plural = _('کاربران')

    def __str__(self):
        if self.last_name != '' and self.first_name != '':
            return self.get_full_name()
        return self.email


class PayoutAccount(models.Model):
    """Where withdrawn money is sent.

    Not called Wallet: in this project the wallet is the internal balance on
    User. Keeping a bank IBAN inside something named Wallet invites mistakes.

    Destinations are stored normalised, or the same IBAN with and without
    spaces becomes two rows and duplicate detection stops working.
    """

    class Method(models.TextChoices):
        SHEBA = 'sheba', _('حساب بانکی — شبا')
        USDT_TRC20 = 'usdt-trc20', _('تتر (USDT) — شبکه TRC20')
        USDT_BEP20 = 'usdt-bep20', _('تتر (USDT) — شبکه BEP20')

    #: Methods that pay out to a crypto wallet.
    CRYPTO_METHODS = {Method.USDT_TRC20, Method.USDT_BEP20}

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='payout_accounts', verbose_name=_('کاربر'))
    method = models.CharField(max_length=30, choices=Method.choices,
                              verbose_name=_('روش برداشت'))
    destination = models.CharField(max_length=200,
                                   verbose_name=_('شبا یا آدرس کیف پول'))
    #: Banks reverse a transfer whose holder name does not match. Meaningless
    #: for crypto, left empty there.
    holder_name = models.CharField(max_length=120, blank=True,
                                   verbose_name=_('نام صاحب حساب'))
    created_at = models.DateTimeField(auto_now_add=True, null=True,
                                      verbose_name=_('تاریخ ثبت'))

    class Meta:
        verbose_name = _('مقصد برداشت')
        verbose_name_plural = _('مقصدهای برداشت')
        ordering = ['-created_at']
        constraints = [
            # The same destination twice is useless and clutters the select.
            models.UniqueConstraint(fields=['user', 'method', 'destination'],
                                    name='unique_payout_destination'),
        ]

    def __str__(self):
        return f'{self.get_method_display()} — {self.short}'

    @property
    def is_crypto(self):
        return self.method in self.CRYPTO_METHODS

    @property
    def short(self):
        """Short display form for listings."""
        from . import payout
        return payout.mask(self.method, self.destination)

    @property
    def network(self):
        """Network name; empty for bank destinations."""
        if self.method == self.Method.USDT_TRC20:
            return 'TRC20'
        if self.method == self.Method.USDT_BEP20:
            return 'BEP20'
        return ''


class Transaction(models.Model):
    """Wallet ledger.

    User.balance is a single number with no history. Every change to it must
    write a row here so the money can be traced, both for the user and for the
    day the balance disagrees with reality.

    Do not create these directly; use account_module.wallet.
    """

    class Kind(models.TextChoices):
        TOPUP = 'topup', _('شارژ حساب')
        PURCHASE = 'purchase', _('خرید فایل')
        SALE = 'sale', _('فروش فایل')
        AD_REVENUE = 'ad_revenue', _('درآمد تبلیغات')
        WITHDRAWAL = 'withdrawal', _('برداشت')

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='transactions', verbose_name=_('کاربر'))
    kind = models.CharField(max_length=20, choices=Kind.choices, verbose_name=_('نوع'))
    # Positive credits the wallet, negative debits it.
    amount = models.IntegerField(verbose_name=_('مبلغ'))
    balance_after = models.IntegerField(verbose_name=_('موجودی پس از تراکنش'))
    file = models.ForeignKey('upload_module.UploadFile', on_delete=models.SET_NULL,
                             null=True, blank=True, verbose_name=_('فایل مرتبط'))
    description = models.CharField(max_length=300, blank=True, verbose_name=_('توضیح'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ'))

    class Meta:
        verbose_name = _('تراکنش')
        verbose_name_plural = _('تراکنش ها')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_kind_display()} / {self.amount}'


class Withdrawal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name=_('کاربر'))
    withdrawal_amount = models.IntegerField(verbose_name=_('مقدار برداشت'))
    # PROTECT, not CASCADE: deleting a destination must not erase payment
    # history. Otherwise a user could take the money and remove the record.
    account = models.ForeignKey(PayoutAccount, on_delete=models.PROTECT,
                                verbose_name=_('مقصد برداشت'))
    # Recorded at request time. If the method's fee changes later, the record
    # must still show what the user agreed to.
    fee = models.IntegerField(default=0, verbose_name=_('کارمزد انتقال'))
    is_paid = models.BooleanField(verbose_name=_('پراخت شده / نشده'), default=False)
    created_deta = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ برداشت'))
    deta_is_paid = models.DateTimeField(blank=True, null=True, verbose_name=_('تاریخ واریز'))

    @property
    def net_amount(self):
        """What the user actually receives.

        withdrawal_amount is what left the balance; this is what to send. The
        operator must always see this figure, not that one.
        """
        return max(0, self.withdrawal_amount - (self.fee or 0))

    def __str__(self):
        return f'{self.account}/{self.withdrawal_amount}'

    class Meta:
        verbose_name = _('برداشت')
        verbose_name_plural = _('برداشتی ها')


class CaptchaSetting(models.Model):
    """Which captcha service is active.

    Single row (pk=1). The switch lives in the database rather than settings so
    that reverting it, when Google becomes unreachable, is one click.

    The reCAPTCHA secret is not here; it comes from the environment so a
    database dump cannot leak it.

    There is no off option: disabling the captcha entirely would be one click
    away from turning every form into a bulk signup tool.
    """

    class Provider(models.TextChoices):
        LOCAL = 'local', _('کپچای تصویری محلی')
        RECAPTCHA = 'recaptcha', _('reCAPTCHA گوگل')

    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.LOCAL,
        verbose_name=_('سرویس کپچا'),
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('آخرین تغییر'))

    class Meta:
        verbose_name = _('تنظیم کپچا')
        verbose_name_plural = _('تنظیم کپچا')

    def __str__(self):
        return self.get_provider_display()

    @classmethod
    def load(cls):
        setting, _created = cls.objects.get_or_create(pk=1)
        return setting


class StoragePackage(models.Model):
    """A storage package on sale.

    StoragePackage is what is currently offered; StoragePlan is what a user
    bought, with its size and price frozen. That split is why editing a price
    or withdrawing a package leaves existing purchases alone.

    In the database rather than settings: pricing is a business decision and
    should not need a deploy.
    """

    bonus_bytes = models.BigIntegerField(verbose_name=_('فضا (بایت)'))
    price_cents = models.PositiveIntegerField(verbose_name=_('قیمت (سنت)'))
    days = models.PositiveIntegerField(default=30, verbose_name=_('مدت (روز)'))
    is_active = models.BooleanField(default=True, verbose_name=_('برای فروش'))
    sort_order = models.IntegerField(default=0, verbose_name=_('ترتیب نمایش'))

    class Meta:
        verbose_name = _('بسته فضا')
        verbose_name_plural = _('بسته های فضا')
        ordering = ['sort_order', 'bonus_bytes']

    def __str__(self):
        return f'{self.bonus_bytes} bytes / {self.price_cents}c / {self.days}d'

    @classmethod
    def for_sale(cls):
        return cls.objects.filter(is_active=True)


class StoragePlan(models.Model):
    """A purchased storage add-on.

    Holds only the extra space, not the total, so changing the free allowance
    moves every existing plan with it and no rows need rewriting.

    Size and price are frozen at purchase time.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='storage_plans', verbose_name=_('کاربر'))
    bonus_bytes = models.BigIntegerField(verbose_name=_('فضای اضافه (بایت)'))
    price_paid = models.IntegerField(verbose_name=_('مبلغ پرداختی (سنت)'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ خرید'))
    expires_at = models.DateTimeField(verbose_name=_('انقضا'))

    class Meta:
        verbose_name = _('اشتراک فضا')
        verbose_name_plural = _('اشتراک های فضا')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} +{self.bonus_bytes}'

    def is_active(self):
        return timezone.now() < self.expires_at

    @classmethod
    def active_bonus_for(cls, user):
        """Total extra space from active plans.

        Plans stack: two packages give both allowances, not the larger one.
        """
        if not getattr(user, 'pk', None):
            return 0

        return cls.objects.filter(
            user=user, expires_at__gt=timezone.now(),
        ).aggregate(total=models.Sum('bonus_bytes'))['total'] or 0


# A separate model rather than a field on User. The activation code used to
# serve this purpose and was never rotated, leaving a permanent reset link.
from .tokens import PasswordResetToken  # noqa: E402, F401
