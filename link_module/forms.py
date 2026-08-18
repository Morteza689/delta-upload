from datetime import timedelta
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.forms import INPUT_CLASS, captcha_field

from .models import ShortLink

#: Only these two schemes. Without the check, javascript: or data: URLs could
#: be shortened and turn our domain into an XSS vector.
ALLOWED_SCHEMES = {'http', 'https'}

#: Expiry choices; empty means never.
EXPIRY_CHOICES = [
    ('', _('بدون انقضا')),
    ('1', _('۱ ساعت')),
    ('24', _('۱ روز')),
    ('168', _('۷ روز')),
    ('720', _('۳۰ روز')),
]


def _is_internal_host(host):
    """Does this host point into a private network?"""
    import ipaddress

    if host in ('localhost', 'localhost.localdomain', '') or host.endswith('.local'):
        return True

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False

    return (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast
            or address.is_unspecified)


def validate_target(url, request=None):
    """Validate the target URL or raise ValidationError."""
    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise forms.ValidationError(_('فقط آدرس‌های http و https پذیرفته می‌شوند.'))

    if not parsed.netloc:
        raise forms.ValidationError(_('آدرس معتبر نیست.'))

    # Shortening our own URLs makes a loop and serves no purpose.
    host = parsed.netloc.split(':')[0].lower()
    own_hosts = {h.lower() for h in settings.ALLOWED_HOSTS if h != '*'}
    if request is not None:
        own_hosts.add(request.get_host().split(':')[0].lower())
    if host in own_hosts:
        raise forms.ValidationError(_('نمی‌توان آدرس همین سایت را کوتاه کرد.'))

    # Internal and loopback addresses. The server never fetches these, only
    # the visitor's browser is redirected, so this is not SSRF. But a shortener
    # pointing into a private network is a reconnaissance and phishing tool and
    # gets the domain blacklisted.
    if _is_internal_host(host):
        raise forms.ValidationError(
            _('آدرس‌های داخلی و لوکال قابل کوتاه کردن نیستند.'))

    return url


class ShortLinkForm(forms.Form):
    """Public shortener form.

    Guests get a captcha: without one this is a free bulk anonymous link
    generator and the domain lands on blacklists quickly.
    """

    target = forms.URLField(
        label=_('آدرس بلند'),
        assume_scheme='https',
        widget=forms.URLInput(attrs={
            'class': INPUT_CLASS,
            'placeholder': 'https://example.com/a/very/long/link',
            'dir': 'ltr',
            'autocomplete': 'off',
        }),
    )

    with_ads = forms.BooleanField(
        required=False,
        label=_('نمایش تبلیغ قبل از هدایت'),
        help_text=_('بازدیدکننده اول یک صفحه‌ی کوتاه می‌بیند و بعد به مقصد می‌رود.'),
    )

    expires_in = forms.ChoiceField(
        required=False, choices=EXPIRY_CHOICES, initial='',
        label=_('انقضا'),
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
    )

    max_clicks = forms.IntegerField(
        required=False, min_value=1, max_value=1_000_000,
        label=_('حداکثر تعداد استفاده'),
        help_text=_('خالی یعنی بدون سقف. مثلاً ۱۰ یعنی بعد از ۱۰ بار باز شدن، لینک می‌بندد.'),
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS, 'placeholder': '10'}),
    )

    def __init__(self, *args, request=None, is_authenticated=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.is_authenticated = is_authenticated

        if not is_authenticated:
            self.fields['captcha'] = captcha_field()
            # The interstitial earns money and needs an owner, so guests never
            # see the option.
            del self.fields['with_ads']

    def clean_target(self):
        return validate_target(self.cleaned_data['target'], self.request)

    def clean(self):
        cleaned = super().clean()
        if not self.is_authenticated:
            cleaned['with_ads'] = False
        return cleaned

    @property
    def mode(self):
        if self.cleaned_data.get('with_ads'):
            return ShortLink.Mode.INTERSTITIAL
        return ShortLink.Mode.DIRECT

    @property
    def expires_at(self):
        hours = self.cleaned_data.get('expires_in')
        if not hours:
            return None
        return timezone.now() + timedelta(hours=int(hours))
