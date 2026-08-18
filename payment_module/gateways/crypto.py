"""Crypto gateway (NOWPayments).

Settlement happens through the IPN webhook, never on the user's return: a
chain confirmation can take minutes and the browser will not wait.
"""
import hashlib
import hmac
import json

# Two forms on purpose: the class attribute is built at import time,
# before any language is active, so it needs the lazy one. Messages
# raised inside a request use the immediate one.
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from .base import BaseGateway, GatewayError
from .http import post_json


class CryptoGateway(BaseGateway):
    slug = 'crypto'
    label = _lazy('ارز دیجیتال')
    settles_on_return = False

    LIVE_API = 'https://api.nowpayments.io/v1'
    SANDBOX_API = 'https://api-sandbox.nowpayments.io/v1'

    # Statuses that mean the money has genuinely arrived.
    SETTLED_STATUSES = ('finished', 'confirmed')
    FAILED_STATUSES = ('failed', 'refunded', 'expired')

    @property
    def API(self):
        # Sandbox and live keys are separate; mixing them means a test payment
        # against a real account, or the reverse.
        return self.SANDBOX_API if self.config.get('sandbox') else self.LIVE_API

    def is_configured(self):
        return bool(self.config.get('api_key') and self.config.get('ipn_secret'))

    def start(self, payment, return_url, ipn_url=None):
        self.check_configured()

        result = post_json(
            f'{self.API}/invoice',
            {
                # NOWPayments takes a decimal dollar amount.
                'price_amount': round(payment.amount / 100, 2),
                'price_currency': 'usd',
                'order_id': str(payment.pk),
                'order_description': _('شارژ کیف پول دی‌آپ'),
                'ipn_callback_url': ipn_url,
                'success_url': return_url,
                'cancel_url': return_url,
            },
            headers={'x-api-key': self.config['api_key']},
        )

        invoice_url = result.get('invoice_url')
        if not invoice_url:
            raise GatewayError(result.get('message') or _('درگاه فاکتور را ایجاد نکرد'))

        payment.gateway_ref = str(result.get('id') or '')
        payment.save(update_fields=['gateway_ref'])
        return invoice_url

    def verify(self, payment, data):
        """This gateway does not settle on the user's return."""
        raise GatewayError(_('پرداخت کریپتو فقط با تأیید شبکه نهایی می‌شود'))

    # --- webhook ---

    def check_signature(self, raw_body, signature):
        """Verify the IPN signature.

        Without this anyone could POST and credit their own wallet.
        NOWPayments serialises the body as JSON with sorted keys and takes an
        HMAC-SHA512, so we do exactly the same.
        """
        if not signature:
            return False

        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            return False

        normalized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        expected = hmac.new(
            self.config['ipn_secret'].encode(),
            normalized.encode(),
            hashlib.sha512,
        ).hexdigest()

        # Constant-time compare so the signature cannot be guessed by timing.
        return hmac.compare_digest(expected, signature)

    def read_ipn(self, payload):
        """Pull the order id and status out of the webhook body.

        Returns (order_id, settled, failed, tracking_code).
        """
        status = (payload.get('payment_status') or '').lower()
        return (
            payload.get('order_id'),
            status in self.SETTLED_STATUSES,
            status in self.FAILED_STATUSES,
            str(payload.get('payment_id') or ''),
        )
