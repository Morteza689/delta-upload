"""Display currency tests."""
from django.test import TestCase
from django.urls import reverse

from payment_module.models import ExchangeRate
from utils import currency


class FormatTests(TestCase):
    def tearDown(self):
        currency.activate(currency.USD, 0)

    def test_dollars_by_default(self):
        currency.activate(currency.USD)
        self.assertEqual(currency.format_money(1234), '$12.34')

    def test_zero(self):
        currency.activate(currency.USD)
        self.assertEqual(currency.format_money(0), '$0.00')

    def test_thousands_separator(self):
        currency.activate(currency.USD)
        self.assertEqual(currency.format_money(123456789), '$1,234,567.89')

    def test_negative_amounts_keep_their_sign(self):
        currency.activate(currency.USD)
        self.assertEqual(currency.format_money(-500), '-$5.00')

    def test_toman_conversion(self):
        currency.activate(currency.TOMAN, rate=95000)
        # $12.34 at 95,000 is 1,172,300 toman.
        self.assertEqual(currency.format_money(1234), '1,172,300 تومان')

    def test_toman_without_a_rate_falls_back_to_dollars(self):
        """A toman figure without a rate is a meaningless number."""
        currency.activate(currency.TOMAN, rate=0)
        self.assertEqual(currency.format_money(1234), '$12.34')

    def test_micro_money_in_dollars_keeps_four_decimals(self):
        currency.activate(currency.USD)
        # 1200 micro-cents is 0.12 of a cent.
        self.assertEqual(currency.format_micro(1200), '$0.0012')

    def test_micro_money_in_toman(self):
        currency.activate(currency.TOMAN, rate=95000)
        self.assertEqual(currency.format_micro(1_000_000), '95,000 تومان')

    def test_an_invalid_currency_falls_back_to_dollars(self):
        currency.activate('bitcoin')
        self.assertEqual(currency.get_currency(), currency.USD)


class SwitcherTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.create(usd_to_toman=95000)

    def test_default_is_dollars(self):
        html = self.client.get('/user/login/').content.decode()
        self.assertNotIn('تومان</button>', html.replace(' ', ''))
        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.display_currency, currency.USD)

    def test_switching_to_toman_sets_a_cookie(self):
        response = self.client.post(reverse('set_currency'),
                                    {'currency': 'toman', 'next': '/'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[currency.COOKIE_NAME].value, 'toman')

    def test_the_choice_persists(self):
        self.client.post(reverse('set_currency'), {'currency': 'toman', 'next': '/'})
        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.display_currency, currency.TOMAN)

    def test_amounts_render_in_the_chosen_currency(self):
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.create_user(
            username='c', email='c@example.com', password='pw',
            email_active_code='c' * 10, is_active=True,
        )
        User.objects.filter(pk=user.pk).update(balance=1234)
        self.client.force_login(user)

        html = self.client.get(reverse('user_dashbord')).content.decode()
        self.assertIn('$12.34', html)

        self.client.post(reverse('set_currency'), {'currency': 'toman', 'next': '/'})
        html = self.client.get(reverse('user_dashbord')).content.decode()
        self.assertIn('1,172,300', html)
        self.assertNotIn('$12.34', html)

    def test_toman_without_a_rate_stays_in_dollars(self):
        ExchangeRate.objects.all().delete()
        self.client.post(reverse('set_currency'), {'currency': 'toman', 'next': '/'})

        response = self.client.get('/')
        self.assertEqual(response.wsgi_request.display_currency, currency.USD)

    def test_an_external_next_url_is_ignored(self):
        """Otherwise this form becomes an open redirect for phishing."""
        response = self.client.post(reverse('set_currency'), {
            'currency': 'usd', 'next': 'https://evil.example/steal',
        })
        self.assertNotIn('evil.example', response['Location'])

    def test_get_does_not_change_the_currency(self):
        response = self.client.get(reverse('set_currency'), {'currency': 'toman'})
        self.assertEqual(response.status_code, 405)

    def test_the_currency_does_not_leak_between_requests(self):
        """The ContextVar must be cleared after every request."""
        self.client.post(reverse('set_currency'), {'currency': 'toman', 'next': '/'})
        self.client.get('/')

        fresh = self.client_class()
        response = fresh.get('/')
        self.assertEqual(response.wsgi_request.display_currency, currency.USD)
