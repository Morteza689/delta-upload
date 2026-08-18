"""Wallet top-up tests.

Focused on what moves real money if it breaks: double crediting, forged
payments, an amount edited in the URL, and unsigned webhooks.
"""
import hashlib
import hmac
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from account_module.models import Transaction

from . import services
from .gateways import CryptoGateway, ZarinPalGateway, get_gateway
from .gateways.base import GatewayError, PaymentDeclined
from .models import ExchangeRate, Payment

User = get_user_model()

GATEWAY_CONFIG = {
    'zarinpal': {'merchant_id': 'test-merchant', 'sandbox': True},
    'crypto': {'api_key': 'test-key', 'ipn_secret': 'test-secret'},
}


def make_user(name='payer', balance=0):
    user = User.objects.create_user(
        username=name, email=f'{name}@example.com', password='pw',
        email_active_code=name * 3,
    )
    if balance:
        User.objects.filter(pk=user.pk).update(balance=balance)
        user.refresh_from_db()
    return user


@override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
class SettlementTests(TestCase):
    """Crediting must happen exactly once, however often it is called."""

    def setUp(self):
        self.user = make_user(balance=500)
        self.payment = Payment.objects.create(
            user=self.user, gateway='zarinpal', amount=1000,
        )

    def test_settle_credits_the_wallet_once(self):
        services.settle(self.payment, tracking_code='REF1')

        self.user.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.user.balance, 1500)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.tracking_code, 'REF1')
        self.assertEqual(Transaction.objects.count(), 1)

    def test_settling_twice_does_not_credit_twice(self):
        """Refreshing the return page or a repeated webhook must not add money."""
        services.settle(self.payment, tracking_code='REF1')
        services.settle(self.payment, tracking_code='REF1')
        services.settle(self.payment, tracking_code='REF1')

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1500)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_settled_payment_cannot_be_marked_failed(self):
        services.settle(self.payment)
        changed = services.mark_failed(self.payment, 'late failure')

        self.payment.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(self.payment.status, Payment.Status.PAID)

    def test_credit_amount_comes_from_the_record(self):
        """The credited amount comes from our record, not from external input."""
        services.settle(self.payment)
        entry = Transaction.objects.get()
        self.assertEqual(entry.amount, self.payment.amount)
        self.assertEqual(entry.kind, Transaction.Kind.TOPUP)


@override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
class ZarinPalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        ExchangeRate.objects.create(usd_to_toman=90000)
        self.gateway = get_gateway('zarinpal')
        self.payment = Payment.objects.create(
            user=self.user, gateway='zarinpal', amount=1000,  # $10
        )

    def test_start_converts_cents_to_toman_and_stores_the_rate(self):
        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {'code': 100, 'authority': 'A123'}}
            url = self.gateway.start(self.payment, 'https://d-up.ir/return/')

        sent = post.call_args[0][1]
        # $10 at a rate of 90,000 toman is 900,000 toman.
        self.assertEqual(sent['amount'], 900000)
        self.assertEqual(sent['currency'], 'IRT')

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.gateway_ref, 'A123')
        self.assertEqual(self.payment.rate_snapshot, 90000)
        self.assertIn('A123', url)

    def test_start_without_an_exchange_rate_fails_clearly(self):
        ExchangeRate.objects.all().delete()
        with self.assertRaises(GatewayError):
            self.gateway.start(self.payment, 'https://d-up.ir/return/')

    def test_verify_rejects_a_mismatched_authority(self):
        """Nobody should be able to attach another payment's authority to their
        own record.
        """
        self.payment.gateway_ref = 'A123'
        self.payment.rate_snapshot = 90000
        self.payment.save()

        with self.assertRaises(GatewayError):
            self.gateway.verify(self.payment, {'Status': 'OK', 'Authority': 'OTHER'})

    def test_verify_rejects_a_canceled_payment(self):
        self.payment.gateway_ref = 'A123'
        self.payment.save()
        with self.assertRaises(PaymentDeclined):
            self.gateway.verify(self.payment, {'Status': 'NOK', 'Authority': 'A123'})

    def test_verify_accepts_code_101_already_verified(self):
        """ZarinPal answers a repeated verify with 101, which is also success."""
        self.payment.gateway_ref = 'A123'
        self.payment.rate_snapshot = 90000
        self.payment.save()

        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {'code': 101, 'ref_id': 55512}}
            ref = self.gateway.verify(self.payment, {'Status': 'OK', 'Authority': 'A123'})

        self.assertEqual(ref, '55512')

    def test_verify_sends_our_amount_not_a_client_supplied_one(self):
        self.payment.gateway_ref = 'A123'
        self.payment.rate_snapshot = 90000
        self.payment.save()

        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {'code': 100, 'ref_id': 1}}
            self.gateway.verify(self.payment, {
                'Status': 'OK', 'Authority': 'A123', 'amount': '1',
            })

        self.assertEqual(post.call_args[0][1]['amount'], 900000)


@override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
class CryptoSignatureTests(TestCase):
    def setUp(self):
        self.gateway = get_gateway('crypto')

    def sign(self, payload, secret='test-secret'):
        body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        return hmac.new(secret.encode(), body.encode(), hashlib.sha512).hexdigest()

    def test_valid_signature_is_accepted(self):
        payload = {'payment_status': 'finished', 'order_id': '7'}
        body = json.dumps(payload).encode()
        self.assertTrue(self.gateway.check_signature(body, self.sign(payload)))

    def test_wrong_secret_is_rejected(self):
        payload = {'payment_status': 'finished', 'order_id': '7'}
        body = json.dumps(payload).encode()
        self.assertFalse(
            self.gateway.check_signature(body, self.sign(payload, 'wrong-secret'))
        )

    def test_tampered_body_is_rejected(self):
        payload = {'payment_status': 'finished', 'order_id': '7'}
        signature = self.sign(payload)
        payload['order_id'] = '8'
        tampered = json.dumps(payload).encode()
        self.assertFalse(self.gateway.check_signature(tampered, signature))

    def test_missing_signature_is_rejected(self):
        self.assertFalse(self.gateway.check_signature(b'{}', ''))

    def test_crypto_does_not_settle_on_browser_return(self):
        self.assertFalse(CryptoGateway.settles_on_return)
        self.assertTrue(ZarinPalGateway.settles_on_return)


class SandboxTests(TestCase):
    """Sandbox mode must call the sandbox host, not the live one."""

    @override_settings(PAYMENT_GATEWAYS={
        'zarinpal': {'merchant_id': 'm', 'sandbox': True},
        'crypto': {'api_key': 'k', 'ipn_secret': 's', 'sandbox': True},
    })
    def test_sandbox_endpoints_are_used(self):
        self.assertIn('sandbox', get_gateway('zarinpal').api_base)
        self.assertIn('sandbox', get_gateway('zarinpal').start_base)
        self.assertIn('sandbox', get_gateway('crypto').API)

    @override_settings(PAYMENT_GATEWAYS={
        'zarinpal': {'merchant_id': 'm', 'sandbox': False},
        'crypto': {'api_key': 'k', 'ipn_secret': 's', 'sandbox': False},
    })
    def test_live_endpoints_are_used_by_default(self):
        self.assertNotIn('sandbox', get_gateway('zarinpal').api_base)
        self.assertNotIn('sandbox', get_gateway('crypto').API)


@override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
class IpnViewTests(TestCase):
    def setUp(self):
        self.user = make_user(balance=0)
        self.payment = Payment.objects.create(
            user=self.user, gateway='crypto', amount=2500,
        )

    def post_ipn(self, payload, secret='test-secret'):
        body = json.dumps(payload)
        normalized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        signature = hmac.new(
            secret.encode(), normalized.encode(), hashlib.sha512
        ).hexdigest()
        return self.client.post(
            reverse('payment_ipn'), data=body,
            content_type='application/json',
            HTTP_X_NOWPAYMENTS_SIG=signature,
        )

    def test_finished_payment_credits_the_wallet(self):
        response = self.post_ipn({
            'payment_status': 'finished',
            'order_id': str(self.payment.pk),
            'payment_id': 'NP-1',
        })

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.user.balance, 2500)
        self.assertEqual(self.payment.status, Payment.Status.PAID)
        self.assertEqual(self.payment.tracking_code, 'NP-1')

    def test_repeated_webhook_credits_only_once(self):
        """The processor sends the webhook several times on purpose."""
        body = {'payment_status': 'finished', 'order_id': str(self.payment.pk),
                'payment_id': 'NP-1'}
        self.post_ipn(body)
        self.post_ipn(body)
        self.post_ipn(body)

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 2500)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_forged_webhook_credits_nothing(self):
        """Without this check anyone could top up their own wallet."""
        response = self.client.post(
            reverse('payment_ipn'),
            data=json.dumps({'payment_status': 'finished',
                             'order_id': str(self.payment.pk)}),
            content_type='application/json',
            HTTP_X_NOWPAYMENTS_SIG='deadbeef',
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 0)
        self.assertFalse(Transaction.objects.exists())

    def test_webhook_without_a_signature_credits_nothing(self):
        response = self.client.post(
            reverse('payment_ipn'),
            data=json.dumps({'payment_status': 'finished',
                             'order_id': str(self.payment.pk)}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 0)

    def test_waiting_status_does_not_credit(self):
        self.post_ipn({'payment_status': 'waiting',
                       'order_id': str(self.payment.pk)})
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 0)

    def test_failed_status_marks_the_payment_failed(self):
        self.post_ipn({'payment_status': 'failed',
                       'order_id': str(self.payment.pk)})
        self.payment.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.Status.FAILED)
        self.assertEqual(self.user.balance, 0)

    def test_unknown_order_is_acknowledged_without_crediting(self):
        response = self.post_ipn({'payment_status': 'finished',
                                  'order_id': '999999'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transaction.objects.exists())

    def test_ipn_url_has_no_language_prefix(self):
        """The address given to the processor must stay fixed."""
        self.assertEqual(reverse('payment_ipn'), '/payment/ipn/')


@override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
class TopUpFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        ExchangeRate.objects.create(usd_to_toman=90000)
        self.client.force_login(self.user)

    def test_page_renders(self):
        response = self.client.get(reverse('topup_page'))
        self.assertEqual(response.status_code, 200)

    def test_page_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('topup_page'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/user/login/', response['Location'])

    def test_submitting_creates_a_pending_payment_and_redirects(self):
        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {'code': 100, 'authority': 'A9'}}
            response = self.client.post(reverse('topup_page'), {
                'amount': '12.50', 'gateway': 'zarinpal',
            })

        payment = Payment.objects.get()
        self.assertEqual(payment.amount, 1250)      # stored in cents
        self.assertEqual(payment.status, Payment.Status.PENDING)
        self.assertEqual(response.status_code, 302)
        self.assertIn('A9', response['Location'])

    def test_amount_below_minimum_is_rejected(self):
        response = self.client.post(reverse('topup_page'), {
            'amount': '0.50', 'gateway': 'zarinpal',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.exists())

    def test_gateway_failure_marks_the_payment_and_does_not_redirect(self):
        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {}, 'errors': {'message': 'merchant invalid'}}
            response = self.client.post(reverse('topup_page'), {
                'amount': '10', 'gateway': 'zarinpal',
            })

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.FAILED)
        self.assertIn('merchant invalid', payment.error)

    def test_return_view_settles_a_zarinpal_payment(self):
        payment = Payment.objects.create(
            user=self.user, gateway='zarinpal', amount=1000,
            gateway_ref='A9', rate_snapshot=90000,
        )

        with mock.patch('payment_module.gateways.zarinpal.post_json') as post:
            post.return_value = {'data': {'code': 100, 'ref_id': 777}}
            response = self.client.get(
                reverse('payment_return', args=[payment.pk]),
                {'Status': 'OK', 'Authority': 'A9'},
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1000)

    def test_return_view_of_another_user_is_not_reachable(self):
        stranger = make_user('stranger')
        payment = Payment.objects.create(
            user=stranger, gateway='zarinpal', amount=1000, gateway_ref='A9',
        )
        response = self.client.get(reverse('payment_return', args=[payment.pk]))
        self.assertEqual(response.status_code, 404)

    def test_crypto_return_only_shows_pending_and_credits_nothing(self):
        payment = Payment.objects.create(
            user=self.user, gateway='crypto', amount=1000, gateway_ref='INV1',
        )
        response = self.client.get(reverse('payment_return', args=[payment.pk]))

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.AWAITING_CONFIRMATION)
        self.assertEqual(self.user.balance, 0)


class GatewayAvailabilityTests(TestCase):
    @override_settings(PAYMENT_GATEWAYS={'zarinpal': {}, 'crypto': {}})
    def test_unconfigured_gateways_are_hidden(self):
        from .gateways import available_gateways
        self.assertEqual(available_gateways(), [])

    @override_settings(PAYMENT_GATEWAYS=GATEWAY_CONFIG)
    def test_configured_gateways_are_listed(self):
        from .gateways import available_gateways
        slugs = {gateway.slug for gateway in available_gateways()}
        self.assertEqual(slugs, {'zarinpal', 'crypto'})
