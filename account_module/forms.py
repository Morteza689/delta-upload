from decimal import Decimal

from captcha.fields import CaptchaTextInput
from django import forms
from django.core import validators
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: Form input class. The appearance is defined once in the design system so
#: it is not scattered across Python widgets.
INPUT_CLASS = 'field'


class DollarField(forms.DecimalField):
    """Accepts dollars, returns integer cents.

    Money is stored in whole cents to keep floating point out of it, but a
    person typing 5 means five dollars.
    """

    def __init__(self, *args, min_cents=0, max_cents=None, **kwargs):
        self.min_cents = min_cents
        self.max_cents = max_cents
        kwargs.setdefault('max_digits', 12)
        kwargs.setdefault('decimal_places', 2)
        kwargs.setdefault('widget', forms.NumberInput(attrs={
            'class': INPUT_CLASS, 'step': '0.01', 'inputmode': 'decimal',
        }))
        super().__init__(*args, **kwargs)

    def clean(self, value):
        amount = super().clean(value)
        if amount is None:
            return None

        cents = int((amount * 100).to_integral_value())

        if cents < self.min_cents:
            raise ValidationError(
                _('مبلغ نمی‌تواند کمتر از %(min)s دلار باشد.')
                % {'min': f'{self.min_cents / 100:.2f}'}
            )
        if self.max_cents is not None and cents > self.max_cents:
            raise ValidationError(
                _('مبلغ نمی‌تواند بیشتر از %(max)s دلار باشد.')
                % {'max': f'{self.max_cents / 100:,.2f}'}
            )
        return cents

    def prepare_value(self, value):
        """Convert the stored cents back to dollars for display."""
        if isinstance(value, int):
            return Decimal(value) / 100
        return value


class StyledCaptchaInput(CaptchaTextInput):
    """Captcha widget styled like the rest of the forms."""

    template_name = 'captcha/styled_widget.html'


def captcha_field(remote_ip=None):
    """A captcha field for the active provider.

    A thin wrapper over account_module.captcha. The import is local because
    that module imports back into this one for the local captcha.
    """
    from .captcha import captcha_field as build

    return build(remote_ip)


class RegisterForm(forms.Form):
    username = forms.CharField(
        label=_('نام کاربری'),
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )
    email = forms.EmailField(
        label=_('ایمیل'),
        widget=forms.EmailInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100), validators.EmailValidator],
    )
    password = forms.CharField(
        label=_('کلمه عبور'),
        widget=forms.PasswordInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )
    confirm_password = forms.CharField(
        label=_('تکرار کلمه عبور'),
        widget=forms.PasswordInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )

    def __init__(self, *args, remote_ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        # In __init__, not at class level: a class-level field is built once
        # at import and the provider switch would need a restart.
        self.fields['captcha'] = captcha_field(remote_ip)

    def clean_confirm_password(self):
        password = self.cleaned_data.get('password')
        confirm_password = self.cleaned_data.get('confirm_password')

        if password == confirm_password:
            return confirm_password

        raise ValidationError(_('کلمه عبور و تکرار کلمه عبور مغایرت دارند'))


class LoginForm(forms.Form):
    """Login form. The captcha appears only after repeated failures.

    The view turns it on with with_captcha=True; the field is added in
    __init__ because the decision is per request.
    """

    email = forms.EmailField(
        label=_('ایمیل'),
        widget=forms.EmailInput(attrs={'id': 'loginEmail', 'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100), validators.EmailValidator],
    )
    password = forms.CharField(
        label=_('کلمه عبور'),
        widget=forms.PasswordInput(attrs={'id': 'loginPass', 'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )

    def __init__(self, *args, with_captcha=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.with_captcha = with_captcha
        if with_captcha:
            self.fields['captcha'] = captcha_field()


class ForgetPasswordForm(forms.Form):
    email = forms.EmailField(
        label=_('ایمیل'),
        widget=forms.EmailInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100), validators.EmailValidator],
    )

    def __init__(self, *args, remote_ip=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['captcha'] = captcha_field(remote_ip)


class ResetPasswordForm(forms.Form):
    password = forms.CharField(
        label=_('کلمه عبور جدید'),
        widget=forms.PasswordInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )
    confirm_password = forms.CharField(
        label=_('تکرار کلمه عبور'),
        widget=forms.PasswordInput(attrs={'class': INPUT_CLASS}),
        validators=[validators.MaxLengthValidator(100)],
    )

    def clean_confirm_password(self):
        password = self.cleaned_data.get('password')
        confirm_password = self.cleaned_data.get('confirm_password')

        if password == confirm_password:
            return confirm_password

        raise ValidationError(_('کلمه عبور و تکرار کلمه عبور مغایرت دارند'))
