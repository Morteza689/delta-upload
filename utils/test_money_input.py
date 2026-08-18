"""Dollar amount input tests.

Amounts are entered in dollars and stored in cents. The conversion is where
money quietly goes wrong, so it is pinned down here.
"""
from decimal import Decimal

from django import forms
from django.test import TestCase, override_settings

from account_module.forms import DollarField


class DollarFieldTests(TestCase):
    def field(self, **kwargs):
        class Form(forms.Form):
            amount = DollarField(**kwargs)
        return Form

    def clean(self, value, **kwargs):
        form = self.field(**kwargs)({'amount': value})
        form.is_valid()
        return form

    def test_whole_dollars_become_cents(self):
        self.assertEqual(self.clean('5').cleaned_data['amount'], 500)

    def test_decimals_become_cents(self):
        self.assertEqual(self.clean('4.99').cleaned_data['amount'], 499)

    def test_half_a_dollar(self):
        self.assertEqual(self.clean('0.5').cleaned_data['amount'], 50)
        self.assertEqual(self.clean('0.50').cleaned_data['amount'], 50)

    def test_the_minimum_is_inclusive(self):
        """Someone withdrawing exactly the minimum must be able to."""
        form = self.clean('0.50', min_cents=50)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], 50)

    def test_just_below_the_minimum_is_refused(self):
        form = self.clean('0.49', min_cents=50)
        self.assertFalse(form.is_valid())

    def test_a_large_amount_is_capped(self):
        form = self.clean('9999', max_cents=100_000)
        self.assertFalse(form.is_valid())

    def test_more_than_two_decimals_is_refused(self):
        """There is no such amount as $0.505; rounding it silently is a wrong figure."""
        form = self.clean('0.505')
        self.assertFalse(form.is_valid())

    def test_a_negative_amount_is_refused(self):
        form = self.clean('-5', min_cents=0)
        self.assertFalse(form.is_valid())

    def test_stored_cents_are_shown_back_as_dollars(self):
        """When editing, a stored value must reappear as dollars, not cents."""
        field = DollarField()
        self.assertEqual(field.prepare_value(499), Decimal('4.99'))
        self.assertEqual(field.prepare_value(50), Decimal('0.5'))


@override_settings(PAYOUT_METHODS={'usdt-trc20': {'min_cents': 50, 'fee_cents': 0}})
class WithdrawalFormTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from account_module.models import PayoutAccount

        User = get_user_model()
        self.user = User.objects.create_user(
            username='w', email='w@example.com', password='pw',
            email_active_code='w' * 10, is_active=True,
        )
        self.account = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        )

    def form(self, amount):
        from user_panel_module.forms import WithdrawalModelForm
        return WithdrawalModelForm(
            {'withdrawal_amount': amount, 'account': self.account.pk},
            user=self.user,
        )

    def test_half_a_dollar_is_accepted(self):
        form = self.form('0.50')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['withdrawal_amount'], 50)

    def test_below_the_minimum_is_refused(self):
        self.assertFalse(self.form('0.25').is_valid())

    def test_a_normal_amount_converts_correctly(self):
        form = self.form('12.34')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['withdrawal_amount'], 1234)

    def test_only_own_accounts_are_selectable(self):
        """Otherwise a withdrawal could be sent to another user's account."""
        from django.contrib.auth import get_user_model
        from account_module.models import PayoutAccount
        from user_panel_module.forms import WithdrawalModelForm

        User = get_user_model()
        other = User.objects.create_user(
            username='o', email='o@example.com', password='pw',
            email_active_code='o' * 10,
        )
        their_account = PayoutAccount.objects.create(
            user=other, method=PayoutAccount.Method.USDT_TRC20,
            destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        )

        form = WithdrawalModelForm(
            {'withdrawal_amount': '1.00', 'account': their_account.pk},
            user=self.user,
        )
        self.assertFalse(form.is_valid())
