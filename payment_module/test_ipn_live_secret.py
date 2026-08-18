"""Crypto webhook tests with a real signature.

Signatures are produced exactly as NOWPayments produces them, rather than
mocked into agreeing with themselves.

The scenario that matters: one payment sends two IPNs, confirmed then
finished, because both count as settled. If both credited, the wallet would be
topped up twice with real money.
"""
import hashlib
import hmac
import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Payment

User = get_user_model()

GATEWAYS = dict(settings.PAYMENT_GATEWAYS)
SECRET = GATEWAYS['crypto']['ipn_secret'] or 'test-ipn-secret'
GATEWAYS['crypto'] = dict(GATEWAYS['crypto'], api_key='k', ipn_secret=SECRET)


def sign(payload):
    """The same algorithm NOWPayments uses."""
    normalized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hmac.new(SECRET.encode(), normalized.encode(), hashlib.sha512).hexdigest()


@override_settings(PAYMENT_GATEWAYS=GATEWAYS)
class CryptoWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='payer', email='payer@example.com', password='pw',
            email_active_code='payer12345', is_active=True,
        )
        self.payment = Payment.objects.create(
            user=self.user, gateway='crypto', amount=500,
            status=Payment.Status.PENDING,
        )

    def body(self, status='finished', **extra):
        payload = {
            'payment_id': 5745459419,
            'payment_status': status,
            'pay_address': 'TXn9YkhDD95J42FQtQTdwVVRt7oXQTEST',
            'price_amount': 5,
            'price_currency': 'usd',
            'pay_amount': 5.02,
            'actually_paid': 5.02,
            'pay_currency': 'usdttrc20',
            'order_id': str(self.payment.pk),
            'outcome_amount': 4.98,
            'outcome_currency': 'usdttrc20',
        }
        payload.update(extra)
        return payload

    def post(self, payload, signature=None):
        raw = json.dumps(payload)
        return self.client.post(
            reverse('payment_ipn'), data=raw,
            content_type='application/json',
            HTTP_X_NOWPAYMENTS_SIG=signature if signature is not None else sign(payload),
        )

    def balance(self):
        self.user.refresh_from_db()
        return self.user.balance

    # --- Signature ---

    def test_a_correctly_signed_webhook_settles(self):
        self.post(self.body())

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.balance(), 500)

    def test_an_unsigned_webhook_is_refused(self):
        """Without this anyone tops up their own wallet with a plain POST."""
        self.post(self.body(), signature='')

        self.payment.refresh_from_db()
        self.assertNotEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.balance(), 0)

    def test_a_wrongly_signed_webhook_is_refused(self):
        self.post(self.body(), signature='f' * 128)
        self.assertEqual(self.balance(), 0)

    def test_a_tampered_amount_is_refused(self):
        """The signature covers the original body; changing the amount voids it."""
        payload = self.body()
        signature = sign(payload)
        payload['price_amount'] = 5000

        self.post(payload, signature=signature)
        self.assertEqual(self.balance(), 0)

    def test_pointing_at_someone_elses_payment_is_refused(self):
        other = User.objects.create_user(
            username='victim', email='victim@example.com', password='pw',
            email_active_code='victim1234', is_active=True,
        )
        theirs = Payment.objects.create(
            user=other, gateway='crypto', amount=9999,
            status=Payment.Status.PENDING,
        )

        payload = self.body()
        signature = sign(payload)
        payload['order_id'] = str(theirs.pk)

        self.post(payload, signature=signature)

        theirs.refresh_from_db()
        self.assertNotEqual(theirs.status, Payment.Status.PAID)

    # --- Status lifecycle ---

    def test_confirmed_settles_too(self):
        """A product decision: credit as soon as the chain confirms, not after the
        processor settles internally.
        """
        self.post(self.body(status='confirmed'))

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.balance(), 500)

    def test_confirmed_then_finished_credits_only_once(self):
        """The most important test here.

        Both statuses settle, so a real payment sends two IPNs. Without
        idempotency the wallet is credited twice with real money.
        """
        self.post(self.body(status='confirmed'))
        self.post(self.body(status='finished'))

        self.assertEqual(self.balance(), 500)

    def test_many_repeated_webhooks_credit_only_once(self):
        """Webhooks retry; the processor keeps going until it succeeds."""
        for _ in range(6):
            self.post(self.body())

        self.assertEqual(self.balance(), 500)

    def test_a_pending_status_does_not_credit(self):
        for status in ('waiting', 'confirming', 'sending', 'partially_paid'):
            with self.subTest(status=status):
                self.post(self.body(status=status))
                self.assertEqual(self.balance(), 0)

    def test_a_failure_status_does_not_credit(self):
        for status in ('failed', 'refunded', 'expired'):
            with self.subTest(status=status):
                self.post(self.body(status=status))
                self.assertEqual(self.balance(), 0)

    def test_a_failure_after_settlement_does_not_take_money_back(self):
        """The money is credited. Reversing that automatically is dangerous and
        should stay a manual decision.
        """
        self.post(self.body(status='finished'))
        self.post(self.body(status='refunded'))

        self.assertEqual(self.balance(), 500)

    # --- Malformed bodies ---

    def test_a_non_json_body_is_refused(self):
        response = self.client.post(
            reverse('payment_ipn'), data='<html>not json</html>',
            content_type='application/json',
            HTTP_X_NOWPAYMENTS_SIG='a' * 128,
        )
        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(self.balance(), 0)

    def test_an_unknown_order_is_refused(self):
        payload = self.body(order_id='999999')
        response = self.post(payload)

        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(self.balance(), 0)


class SecretPresenceTests(TestCase):
    """We deliberately do not assert that a key is set.

    Whether a key is configured is an environment decision, not a code
    invariant: empty locally so a live key cannot mint real invoices, filled
    on the server. A test asserting it would go red whenever the environment
    changed.

    What is invariant, and what is tested: whatever key exists must not be in
    the repository.
    """

    def test_the_secret_is_not_in_the_repository(self):
        from pathlib import Path

        source = (Path(settings.BASE_DIR) / 'DUploaderProject' / 'settings.py').read_text(
            encoding='utf-8'
        )
        secret = settings.PAYMENT_GATEWAYS['crypto']['ipn_secret']
        if secret:
            self.assertNotIn(secret, source)
