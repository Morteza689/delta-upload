"""Captcha provider selection: local image or Google reCAPTCHA.

This is a switch, not a fallback, and the difference is a security one. If the
server accepted either a Google token or a local answer, an attacker would
always take the weaker path and the effective strength would be the weaker of
the two. Exactly one provider is active at a time, chosen server side.

The secret comes from the environment so a database dump cannot leak it. The
switch itself is in the database so reverting it is one click, not a deploy.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

VERIFY_URL = 'https://www.google.com/recaptcha/api/siteverify'
VERIFY_TIMEOUT = 8

LOCAL = 'local'
RECAPTCHA = 'recaptcha'

PROVIDER_CHOICES = [
    (LOCAL, _('کپچای تصویری محلی')),
    (RECAPTCHA, _('reCAPTCHA گوگل')),
]


def recaptcha_is_configured():
    return bool(getattr(settings, 'RECAPTCHA_SITE_KEY', '')
                and getattr(settings, 'RECAPTCHA_SECRET_KEY', ''))


def active_provider():
    """The active provider.

    Falls back to local when reCAPTCHA is selected without keys. That is a
    configuration error no visitor can influence, and the result is a working
    captcha rather than none.
    """
    from .models import CaptchaSetting

    try:
        provider = CaptchaSetting.load().provider
    except Exception:  # noqa: BLE001
        # Database unavailable, for example mid-migration. The captcha must
        # still work.
        return LOCAL

    if provider == RECAPTCHA and not recaptcha_is_configured():
        logger.warning('reCAPTCHA انتخاب شده ولی کلید ندارد؛ کپچای محلی استفاده شد')
        return LOCAL

    return provider if provider in (LOCAL, RECAPTCHA) else LOCAL


# ─────────────────────────── reCAPTCHA ───────────────────────────

class ReCaptchaWidget(forms.Widget):
    """reCAPTCHA v2 checkbox widget.

    The template lives in an app directory, not the project one: the form
    renderer uses a separate engine that only sees app template dirs.
    """

    template_name = 'captcha/recaptcha_widget.html'

    def value_from_datadict(self, data, files, name):
        # Google posts under its own fixed name, not the form field name.
        return data.get('g-recaptcha-response')

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['site_key'] = getattr(settings, 'RECAPTCHA_SITE_KEY', '')
        return context


def verify_token(token, remote_ip=None):
    """Verify a token with Google.

    Returns (valid, reason).

    A network failure rejects rather than accepts. Failing open would let
    anyone bypass the captcha by cutting the server off from Google. The cost
    is that forms stop working while Google is unreachable; the answer then is
    to switch back to the local captcha.
    """
    if not token:
        return False, 'empty-token'

    payload = {
        'secret': getattr(settings, 'RECAPTCHA_SECRET_KEY', ''),
        'response': token,
    }
    if remote_ip:
        payload['remoteip'] = remote_ip

    request = urllib.request.Request(
        VERIFY_URL, data=urllib.parse.urlencode(payload).encode(),
    )
    request.add_header('User-Agent', 'DeltaUpload/1.0')

    try:
        with urllib.request.urlopen(request, timeout=VERIFY_TIMEOUT) as response:
            body = json.loads(response.read().decode('utf-8', 'replace'))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning('reCAPTCHA verify failed: %s', exc)
        return False, 'unreachable'

    if body.get('success'):
        return True, ''

    return False, ','.join(body.get('error-codes') or ['rejected'])


class ReCaptchaField(forms.Field):
    widget = ReCaptchaWidget
    #: Views fill this in so Google can weigh the address too.
    remote_ip = None

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('label', '')
        kwargs.setdefault('required', True)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        valid, reason = verify_token(value, self.remote_ip)
        if valid:
            return value

        if reason == 'unreachable':
            raise ValidationError(
                _('بررسی کپچا ممکن نشد. چند لحظه بعد دوباره تلاش کن.'),
                code='unreachable',
            )
        if reason == 'empty-token':
            raise ValidationError(_('لطفاً تیک «من ربات نیستم» را بزن.'),
                                  code='required')

        raise ValidationError(_('تأیید کپچا ناموفق بود. دوباره تلاش کن.'),
                              code='invalid')


# --- Selection ---

def captcha_field(remote_ip=None):
    """A captcha field for the active provider.

    Call this in __init__, never at class level: a class-level field is built
    once at import and the switch would then need a restart to take effect.
    """
    if active_provider() == RECAPTCHA:
        field = ReCaptchaField()
        field.remote_ip = remote_ip
        return field

    from captcha.fields import CaptchaField

    from .forms import StyledCaptchaInput

    return CaptchaField(
        label=_('عبارت داخل تصویر'),
        widget=StyledCaptchaInput,
        error_messages={'invalid': _('عبارت واردشده با تصویر مطابقت ندارد')},
    )
