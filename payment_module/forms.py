from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from account_module.forms import INPUT_CLASS

from .gateways import available_gateways


class TopUpForm(forms.Form):
    """Amount entered in dollars, stored in cents."""

    amount = forms.DecimalField(
        label=_('مبلغ (دلار)'),
        max_digits=9, decimal_places=2,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS, 'step': '0.01', 'min': '1'}),
    )
    gateway = forms.ChoiceField(
        label=_('روش پرداخت'),
        widget=forms.RadioSelect,
        choices=(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only configured gateways are selectable, so the user never picks a method
        # that ends in an error.
        self.fields['gateway'].choices = [
            (gateway.slug, gateway.label) for gateway in available_gateways()
        ]

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        cents = int(round(amount * 100))

        minimum = getattr(settings, 'WALLET_MIN_TOPUP_CENTS', 100)
        maximum = getattr(settings, 'WALLET_MAX_TOPUP_CENTS', 100_000_00)

        if cents < minimum:
            raise forms.ValidationError(
                _('حداقل مبلغ شارژ %(min)s دلار است') % {'min': minimum / 100}
            )
        if cents > maximum:
            raise forms.ValidationError(
                _('حداکثر مبلغ شارژ %(max)s دلار است') % {'max': maximum / 100}
            )
        return cents


class ExchangeRateForm(forms.Form):
    """Exchange rate settings for the admin panel.

    The manual rate and the automatic setting share one form because in
    practice they are decided together.
    """

    usd_to_toman = forms.IntegerField(
        label=_('نرخ هر دلار به تومان'),
        min_value=1_000, max_value=10_000_000,
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS, 'step': '500'}),
        help_text=_('نرخی که همین حالا ملاک تبدیل است.'),
    )
    auto_update = forms.BooleanField(
        label=_('نرخ را خودکار از API بگیر'),
        required=False,
        help_text=_('با فعال بودن این گزینه، نرخ بالا با هر بار اجرای به‌روزرسانی بازنویسی می‌شود.'),
    )
    provider = forms.ChoiceField(
        label=_('سرویس نرخ'),
        choices=(),
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
    )
    api_key = forms.CharField(
        label=_('کلید API'), required=False, max_length=200,
        widget=forms.TextInput(attrs={'class': INPUT_CLASS, 'dir': 'ltr',
                                      'autocomplete': 'off'}),
        help_text=_('فقط برای سرویس‌هایی که کلید می‌خواهند.'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from .rates import PROVIDER_CHOICES
        self.fields['provider'].choices = PROVIDER_CHOICES

    def clean(self):
        cleaned = super().clean()

        # Automatic mode with a provider that needs a key but has none means the
        # rate never updates and nobody finds out.
        if cleaned.get('auto_update'):
            from .rates import PROVIDERS

            entry = PROVIDERS.get(cleaned.get('provider'))
            if entry and entry[2] and not cleaned.get('api_key'):
                self.add_error(
                    'api_key',
                    _('این سرویس بدون کلید کار نمی‌کند.'),
                )

        return cleaned
