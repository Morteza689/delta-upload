"""Payout destination tests.

The strictest tests in the project, and they should be: money sent to a wrong
IBAN or address does not come back. Each one closes a way to lose real money.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import payout
from .models import PayoutAccount, Withdrawal

User = get_user_model()

# Real, checksum-valid IBANs.
SHEBA = 'IR062960000000100324200001'
SHEBA_2 = 'IR820540102680020817909002'
# Real addresses on each network.
TRC20 = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'
BEP20 = '0x2170Ed0880ac9A755fd29B2688956BD959F933F8'


def make_user(name='payee'):
    return User.objects.create_user(
        username=name, email=f'{name}@example.com', password='pw',
        email_active_code=name * 3, is_active=True,
    )


class ShebaTests(TestCase):
    def test_a_valid_sheba_passes(self):
        self.assertEqual(payout.validate_sheba(SHEBA), SHEBA)

    def test_spaces_and_dashes_are_accepted(self):
        """People paste the IBAN exactly as their banking app shows it."""
        spaced = 'IR06 2960 0000 0010 0324 2000 01'
        self.assertEqual(payout.validate_sheba(spaced), SHEBA)

    def test_the_ir_prefix_may_be_omitted(self):
        self.assertEqual(payout.validate_sheba(SHEBA[2:]), SHEBA)

    def test_lowercase_is_accepted(self):
        self.assertEqual(payout.validate_sheba(SHEBA.lower()), SHEBA)

    def test_persian_digits_are_accepted(self):
        """A Persian keyboard produces Persian digits and the user does not notice."""
        persian = SHEBA.translate({ord(str(d)): chr(ord('۰') + d) for d in range(10)})
        self.assertEqual(payout.validate_sheba(persian), SHEBA)

    def test_a_wrong_checksum_is_refused(self):
        """Two transposed digits is the most common IBAN typo."""
        broken = SHEBA[:8] + SHEBA[9] + SHEBA[8] + SHEBA[10:]
        if broken == SHEBA:  # those two digits were the same
            broken = SHEBA[:-1] + ('0' if SHEBA[-1] != '0' else '1')

        with self.assertRaises(ValidationError):
            payout.validate_sheba(broken)

    def test_too_few_digits_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_sheba('IR0629600000001003242')

    def test_too_many_digits_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_sheba(SHEBA + '7')

    def test_letters_inside_are_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_sheba('IR06296000000010032420000X')

    def test_empty_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_sheba('')

    def test_a_foreign_iban_is_refused(self):
        """A valid IBAN, but not one an Iranian bank will pay into."""
        with self.assertRaises(ValidationError):
            payout.validate_sheba('DE89370400440532013000')


class Trc20Tests(TestCase):
    def test_a_valid_address_passes(self):
        self.assertEqual(payout.validate_trc20(TRC20), TRC20)

    def test_a_single_wrong_character_is_caught(self):
        """Without the checksum this address would look valid and the money vanish."""
        swapped = 'q' if TRC20[10] != 'q' else 'r'
        broken = TRC20[:10] + swapped + TRC20[11:]

        with self.assertRaises(ValidationError):
            payout.validate_trc20(broken)

    def test_a_short_address_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_trc20(TRC20[:-1])

    def test_an_address_not_starting_with_t_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_trc20('A' + TRC20[1:])

    def test_an_ethereum_address_is_refused(self):
        """USDT sent to the wrong network is gone."""
        with self.assertRaises(ValidationError):
            payout.validate_trc20(BEP20)

    def test_characters_outside_base58_are_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_trc20('T0' + TRC20[2:])

    def test_surrounding_spaces_are_trimmed(self):
        self.assertEqual(payout.validate_trc20(f'  {TRC20}  '), TRC20)


class Bep20Tests(TestCase):
    def test_a_valid_address_passes(self):
        self.assertEqual(payout.validate_bep20(BEP20), BEP20)

    def test_lowercase_is_accepted(self):
        self.assertEqual(payout.validate_bep20(BEP20.lower()), BEP20.lower())

    def test_a_missing_prefix_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_bep20(BEP20[2:])

    def test_a_wrong_length_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_bep20(BEP20 + 'a')

    def test_a_tron_address_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_bep20(TRC20)

    def test_non_hex_characters_are_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_bep20('0x' + 'z' * 40)


class MethodTests(TestCase):
    def test_only_three_methods_exist(self):
        """The other methods were removed and must not come back."""
        self.assertEqual(
            set(PayoutAccount.Method.values),
            {'sheba', 'usdt-trc20', 'usdt-bep20'},
        )

    def test_the_old_methods_are_gone(self):
        values = set(PayoutAccount.Method.values)
        self.assertNotIn('pyeer-usd', values)
        self.assertNotIn('tron-trc20', values)

    def test_crypto_methods_are_marked(self):
        account = PayoutAccount(method=PayoutAccount.Method.USDT_TRC20)
        self.assertTrue(account.is_crypto)
        self.assertEqual(account.network, 'TRC20')

        bank = PayoutAccount(method=PayoutAccount.Method.SHEBA)
        self.assertFalse(bank.is_crypto)
        self.assertEqual(bank.network, '')

    def test_an_unknown_method_is_refused(self):
        with self.assertRaises(ValidationError):
            payout.validate_destination('paypal', 'someone@example.com')


class PayoutAccountPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse('payout_accounts_page')

    def add(self, **fields):
        data = {'method': PayoutAccount.Method.USDT_TRC20, 'destination': TRC20}
        data.update(fields)
        return self.client.post(self.url, data)

    def test_the_page_needs_login(self):
        self.assertNotEqual(self.client_class().get(self.url).status_code, 200)

    def test_adding_a_crypto_account(self):
        self.add()
        account = PayoutAccount.objects.get()
        self.assertEqual(account.user, self.user)
        self.assertEqual(account.destination, TRC20)

    def test_adding_a_bank_account(self):
        self.add(method=PayoutAccount.Method.SHEBA, destination=SHEBA,
                 holder_name='علی محمدی')

        account = PayoutAccount.objects.get()
        self.assertEqual(account.destination, SHEBA)
        self.assertEqual(account.holder_name, 'علی محمدی')

    def test_a_bank_account_without_a_holder_name_is_refused(self):
        """Banks reverse a transfer whose name does not match."""
        self.add(method=PayoutAccount.Method.SHEBA, destination=SHEBA)
        self.assertFalse(PayoutAccount.objects.exists())

    def test_a_crypto_account_needs_no_holder_name(self):
        self.add()
        self.assertTrue(PayoutAccount.objects.exists())

    def test_a_holder_name_is_dropped_for_crypto(self):
        """Personal data with no purpose should not be stored."""
        self.add(holder_name='یک نام')
        self.assertEqual(PayoutAccount.objects.get().holder_name, '')

    def test_an_invalid_address_is_refused(self):
        self.add(destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLjXX')
        self.assertFalse(PayoutAccount.objects.exists())

    def test_the_address_is_normalized_before_saving(self):
        self.add(method=PayoutAccount.Method.SHEBA,
                 destination='IR06 2960 0000 0010 0324 2000 01',
                 holder_name='نام')
        self.assertEqual(PayoutAccount.objects.get().destination, SHEBA)

    def test_a_duplicate_destination_is_refused(self):
        self.add()
        self.add()
        self.assertEqual(PayoutAccount.objects.count(), 1)

    def test_the_same_address_on_a_different_network_is_allowed(self):
        """One EVM address serves several networks."""
        self.add(method=PayoutAccount.Method.USDT_BEP20, destination=BEP20)
        self.add(method=PayoutAccount.Method.SHEBA, destination=SHEBA,
                 holder_name='نام')
        self.assertEqual(PayoutAccount.objects.count(), 2)

    def test_deleting_an_account(self):
        self.add()
        account = PayoutAccount.objects.get()

        self.client.post(self.url, {'action': 'delete', 'account_id': account.pk})
        self.assertFalse(PayoutAccount.objects.exists())

    def test_deleting_someone_elses_account_is_refused(self):
        other = make_user('thief')
        theirs = PayoutAccount.objects.create(
            user=other, method=PayoutAccount.Method.USDT_TRC20, destination=TRC20,
        )

        response = self.client.post(
            self.url, {'action': 'delete', 'account_id': theirs.pk})

        self.assertEqual(response.status_code, 404)
        self.assertTrue(PayoutAccount.objects.filter(pk=theirs.pk).exists())

    def test_an_account_with_a_pending_withdrawal_is_not_deleted(self):
        """The operator needs to know where to send the money."""
        self.add()
        account = PayoutAccount.objects.get()
        Withdrawal.objects.create(user=self.user, account=account,
                                  withdrawal_amount=100)

        self.client.post(self.url, {'action': 'delete', 'account_id': account.pk})
        self.assertTrue(PayoutAccount.objects.filter(pk=account.pk).exists())

    def test_an_account_with_paid_history_is_not_deleted(self):
        self.add()
        account = PayoutAccount.objects.get()
        Withdrawal.objects.create(user=self.user, account=account,
                                  withdrawal_amount=100, is_paid=True)

        self.client.post(self.url, {'action': 'delete', 'account_id': account.pk})
        self.assertTrue(PayoutAccount.objects.filter(pk=account.pk).exists())

    def test_only_my_accounts_are_listed(self):
        other = make_user('other')
        PayoutAccount.objects.create(
            user=other, method=PayoutAccount.Method.SHEBA,
            destination=SHEBA_2, holder_name='کس دیگر',
        )
        self.add()

        html = self.client.get(self.url).content.decode()
        self.assertIn(TRC20, html)
        self.assertNotIn(SHEBA_2, html)

    def test_it_is_linked_from_the_panel_menu(self):
        html = self.client.get(reverse('user_dashbord')).content.decode()
        self.assertIn(f'href="{self.url}"', html)


class WithdrawalPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('rich')
        User.objects.filter(pk=self.user.pk).update(balance=5000)
        self.user.refresh_from_db()
        self.client.force_login(self.user)

    def test_without_an_account_the_form_is_replaced_by_guidance(self):
        """An empty form that fails without explanation is the worst outcome."""
        response = self.client.get(reverse('withdrawal_page'))

        self.assertContains(response, 'اول مقصد برداشت را ثبت کن')
        self.assertContains(response, reverse('payout_accounts_page'))

    def test_with_an_account_the_form_appears(self):
        PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination=TRC20,
        )
        response = self.client.get(reverse('withdrawal_page'))
        self.assertContains(response, 'name="withdrawal_amount"')

    def test_a_withdrawal_debits_the_balance(self):
        account = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.SHEBA,
            destination=SHEBA, holder_name='نام',
        )
        self.client.post(reverse('withdrawal_page'), {
            'withdrawal_amount': '10.00', 'account': account.pk,
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 4000)
        self.assertEqual(Withdrawal.objects.get().account, account)

    def test_withdrawing_to_someone_elses_account_is_refused(self):
        other = make_user('victim')
        theirs = PayoutAccount.objects.create(
            user=other, method=PayoutAccount.Method.USDT_TRC20, destination=TRC20,
        )

        self.client.post(reverse('withdrawal_page'), {
            'withdrawal_amount': '10.00', 'account': theirs.pk,
        })

        self.assertFalse(Withdrawal.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 5000)
