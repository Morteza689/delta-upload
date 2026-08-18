"""ZarinPal gateway (API v4).

Amounts are held in cents internally and converted to toman with the rate set
in the admin panel.
"""
# Lazy: this is evaluated when the class is defined, at import time,
# when no language is active yet. gettext would freeze the default one.
from django.utils.translation import gettext_lazy as _

from ..models import ExchangeRate
from .base import BaseGateway, GatewayError, PaymentDeclined
from .http import post_json


class ZarinPalGateway(BaseGateway):
    slug = 'zarinpal'
    label = _('زرین‌پال')
    settles_on_return = True

    LIVE_API = 'https://payment.zarinpal.com/pg/v4/payment/'
    LIVE_START = 'https://payment.zarinpal.com/pg/StartPay/'
    SANDBOX_API = 'https://sandbox.zarinpal.com/pg/v4/payment/'
    SANDBOX_START = 'https://sandbox.zarinpal.com/pg/StartPay/'

    # After a successful verify, ZarinPal answers a second attempt with code 101,
    # meaning already verified. That is also success: otherwise refreshing the
    # return page would record a good payment as failed.
    SUCCESS_CODES = (100, 101)

    @property
    def api_base(self):
        return self.SANDBOX_API if self.config.get('sandbox') else self.LIVE_API

    @property
    def start_base(self):
        return self.SANDBOX_START if self.config.get('sandbox') else self.LIVE_START

    def is_configured(self):
        return bool(self.config.get('merchant_id'))

    def toman_amount(self, cents):
        """Cents to toman, using the rate recorded in the admin."""
        rate = ExchangeRate.current()
        if rate is None:
            raise GatewayError(
                _('نرخ ارز ثبت نشده است. از پنل ادمین یک نرخ فعال ثبت کنید.')
            )
        return round(cents * rate.usd_to_toman / 100), rate.usd_to_toman

    def start(self, payment, return_url, ipn_url=None):
        self.check_configured()
        amount, rate = self.toman_amount(payment.amount)

        result = post_json(self.api_base + 'request.json', {
            'merchant_id': self.config['merchant_id'],
            'amount': amount,
            'currency': 'IRT',
            'description': _('شارژ کیف پول دی‌آپ'),
            'callback_url': return_url,
        })

        data = result.get('data') or {}
        if data.get('code') not in self.SUCCESS_CODES or not data.get('authority'):
            errors = result.get('errors') or {}
            raise GatewayError(
                errors.get('message') or _('درگاه پرداخت را ایجاد نکرد')
            )

        payment.gateway_ref = data['authority']
        payment.rate_snapshot = rate
        payment.save(update_fields=['gateway_ref', 'rate_snapshot'])

        return self.start_base + data['authority']

    def verify(self, payment, data):
        self.check_configured()

        if data.get('Status') != 'OK':
            raise PaymentDeclined(_('پرداخت توسط شما لغو شد'))

        authority = data.get('Authority') or ''
        # The authority comes from the URL and must match what we recorded, or one
        # person's payment could be credited to another account.
        if authority != payment.gateway_ref:
            raise GatewayError(_('شناسه پرداخت با رکورد ما مطابقت ندارد'))

        # The amount is read from our own record, not from the returned parameters.
        amount = round(payment.amount * (payment.rate_snapshot or 0) / 100)

        result = post_json(self.api_base + 'verify.json', {
            'merchant_id': self.config['merchant_id'],
            'amount': amount,
            'authority': authority,
        })

        verified = result.get('data') or {}
        code = verified.get('code')
        if code not in self.SUCCESS_CODES:
            errors = result.get('errors') or {}
            raise PaymentDeclined(
                errors.get('message') or _('پرداخت تأیید نشد')
            )

        return str(verified.get('ref_id') or '')
