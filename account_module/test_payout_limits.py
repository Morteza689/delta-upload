"""Per-method minimum and fee tests.

The rule they all turn on: the fee comes out of the requested amount. Ask for
$10, $10 leaves the balance, $9 arrives.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from . import payout
from .models import PayoutAccount, Withdrawal
from .test_payout import SHEBA, TRC20, make_user

User = get_user_model()

RULES = {
    'sheba': {'min_cents': 200, 'fee_cents': 0},
    'usdt-trc20': {'min_cents': 1000, 'fee_cents': 100},
    'usdt-bep20': {'min_cents': 1000, 'fee_cents': 30},
}


@override_settings(PAYOUT_METHODS=RULES)
class MethodRuleTests(TestCase):
    def test_each_method_has_its_own_minimum(self):
        self.assertEqual(payout.min_cents('sheba'), 200)
        self.assertEqual(payout.min_cents('usdt-trc20'), 1000)
        self.assertEqual(payout.min_cents('usdt-bep20'), 1000)

    def test_each_method_has_its_own_fee(self):
        self.assertEqual(payout.fee_cents('sheba'), 0)
        self.assertEqual(payout.fee_cents('usdt-trc20'), 100)
        self.assertEqual(payout.fee_cents('usdt-bep20'), 30)

    def test_the_fee_comes_out_of_the_requested_amount(self):
        self.assertEqual(payout.net_cents('usdt-trc20', 1000), 900)

    def test_a_free_method_pays_the_full_amount(self):
        self.assertEqual(payout.net_cents('sheba', 500), 500)

    def test_the_net_amount_never_goes_negative(self):
        self.assertEqual(payout.net_cents('usdt-trc20', 50), 0)

    def test_the_rules_table_covers_every_method(self):
        methods = {row['method'] for row in payout.rules_table()}
        self.assertEqual(methods, set(PayoutAccount.Method.values))

    def test_an_unknown_method_has_no_minimum_or_fee(self):
        self.assertEqual(payout.min_cents('paypal'), 0)
        self.assertEqual(payout.fee_cents('paypal'), 0)


class ShippedDefaultsTests(TestCase):
    """The values actually in settings, not a test fixture."""

    def test_every_minimum_exceeds_its_fee(self):
        """Otherwise a withdrawal can be recorded that is entirely fee."""
        for row in payout.rules_table():
            with self.subTest(method=row['method']):
                self.assertGreater(row['min_cents'], row['fee_cents'])

    def test_the_crypto_minimums_cover_the_network_fee_comfortably(self):
        """A minimum barely above the fee is no minimum at all."""
        for method in ('usdt-trc20', 'usdt-bep20'):
            with self.subTest(method=method):
                self.assertGreaterEqual(
                    payout.min_cents(method), payout.fee_cents(method) * 3,
                )

    def test_every_method_has_a_rule(self):
        for method in PayoutAccount.Method.values:
            with self.subTest(method=method):
                self.assertGreater(payout.min_cents(method), 0)


@override_settings(PAYOUT_METHODS=RULES)
class WithdrawalLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('limits')
        User.objects.filter(pk=self.user.pk).update(balance=100000)
        self.user.refresh_from_db()
        self.client.force_login(self.user)

        self.bank = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.SHEBA,
            destination=SHEBA, holder_name='نام',
        )
        self.tron = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination=TRC20,
        )

    def request_withdrawal(self, account, dollars):
        return self.client.post(reverse('withdrawal_page'), {
            'withdrawal_amount': dollars, 'account': account.pk,
        })

    def test_the_bank_minimum_is_enforced(self):
        self.request_withdrawal(self.bank, '1.00')
        self.assertFalse(Withdrawal.objects.exists())

    def test_the_bank_minimum_is_accepted_exactly(self):
        self.request_withdrawal(self.bank, '2.00')
        self.assertTrue(Withdrawal.objects.exists())

    def test_the_crypto_minimum_is_higher(self):
        """Two dollars passes for a bank transfer but not for USDT."""
        self.request_withdrawal(self.tron, '2.00')
        self.assertFalse(Withdrawal.objects.exists())

    def test_the_crypto_minimum_is_accepted_exactly(self):
        self.request_withdrawal(self.tron, '10.00')
        self.assertTrue(Withdrawal.objects.exists())

    def test_the_error_names_the_actual_minimum(self):
        """The message must say how much, not merely that it is too little."""
        response = self.request_withdrawal(self.tron, '2.00')
        self.assertContains(response, '10.00')

    def test_the_fee_is_recorded_on_the_withdrawal(self):
        self.request_withdrawal(self.tron, '10.00')
        self.assertEqual(Withdrawal.objects.get().fee, 100)

    def test_the_full_amount_leaves_the_balance(self):
        """The fee comes out of the withdrawal, not out of what remains."""
        self.request_withdrawal(self.tron, '10.00')

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 100000 - 1000)

    def test_the_net_amount_is_what_gets_sent(self):
        self.request_withdrawal(self.tron, '10.00')
        self.assertEqual(Withdrawal.objects.get().net_amount, 900)

    def test_a_free_method_records_no_fee(self):
        self.request_withdrawal(self.bank, '5.00')

        withdrawal = Withdrawal.objects.get()
        self.assertEqual(withdrawal.fee, 0)
        self.assertEqual(withdrawal.net_amount, 500)

    def test_a_later_fee_change_does_not_alter_past_requests(self):
        """The user agreed to that day's fee, not today's."""
        self.request_withdrawal(self.tron, '10.00')

        with override_settings(PAYOUT_METHODS={
            'usdt-trc20': {'min_cents': 1000, 'fee_cents': 500},
        }):
            self.assertEqual(Withdrawal.objects.get().net_amount, 900)

    def test_the_page_says_the_fee_comes_out_of_the_amount(self):
        html = self.client.get(reverse('withdrawal_page')).content.decode()
        self.assertIn('کارمزد انتقال از موجودی برداشت شما برداشته می', html)

    def test_the_page_lists_every_minimum_and_fee(self):
        html = self.client.get(reverse('withdrawal_page')).content.decode()

        self.assertIn('$2.00', html)   # bank minimum
        self.assertIn('$10.00', html)  # crypto minimum
        self.assertIn('$1.00', html)   # TRC20 fee

    def test_the_select_carries_each_method_for_the_live_preview(self):
        html = self.client.get(reverse('withdrawal_page')).content.decode()
        self.assertIn('data-method="usdt-trc20"', html)
        self.assertIn('data-method="sheba"', html)

    def test_an_amount_at_or_below_the_fee_is_refused(self):
        """In case someone sets a minimum below the fee."""
        with override_settings(PAYOUT_METHODS={
            'usdt-trc20': {'min_cents': 50, 'fee_cents': 100},
        }):
            self.request_withdrawal(self.tron, '0.75')

        self.assertFalse(Withdrawal.objects.exists())


class OperatorViewTests(TestCase):
    """The operator must see the figure they actually send."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('payee2')
        self.staff = User.objects.create_user(
            username='op', email='op@example.com', password='pw',
            email_active_code='op12345678', is_active=True, is_staff=True,
        )
        account = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination=TRC20,
        )
        Withdrawal.objects.create(user=self.user, account=account,
                                  withdrawal_amount=1000, fee=100)
        self.client.force_login(self.staff)

    def test_the_queue_shows_the_net_amount(self):
        """Paying the gross loses the fee on every single payment."""
        html = self.client.get(reverse('staff_withdrawals')).content.decode()
        self.assertIn('$9.00', html)

    def test_the_queue_also_shows_the_gross_and_the_fee(self):
        html = self.client.get(reverse('staff_withdrawals')).content.decode()
        self.assertIn('$10.00', html)
        self.assertIn('$1.00', html)

    def test_the_user_sees_the_net_amount_in_their_history(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse('withdrawal_list_page')).content.decode()
        self.assertIn('$9.00', html)
