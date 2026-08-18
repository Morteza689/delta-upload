"""Exchange rate tests.

Nothing here touches the network: each provider's response is replaced with a
realistic sample. A test that depends on the internet goes red one day with no
code change, and on that day nobody believes it.
"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import rate_service, rates
from .models import ExchangeRate, RateSetting

User = get_user_model()

# Sample Nobitex response. -rls markets are quoted in rials.
NOBITEX_OK = {
    'status': 'ok',
    'stats': {
        'usdt-rls': {
            'isClosed': False,
            'bestSell': '1052000',
            'bestBuy': '1051000',
            'latest': '1051500',
        },
    },
}

TGJU_OK = {'current': {'price_dollar_rl': {'p': '1,058,000', 'h': '1,060,000'}}}

BRSAPI_OK = {
    'gold': [{'symbol': 'IR_GOLD_18K', 'price': 70000000}],
    'currency': [
        {'symbol': 'EUR', 'name': 'یورو', 'price': 1150000},
        {'symbol': 'USD', 'name': 'دلار', 'price': 1058000},
    ],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def responding(payload):
    return patch('urllib.request.urlopen', return_value=FakeResponse(payload))


def setting_for(provider, auto_update=False, **extra):
    """Build a setting with an explicit provider.

    No test should lean on DEFAULT_PROVIDER: changing the default is a product
    decision and must not turn half the suite red.
    """
    setting = RateSetting.load()
    setting.provider = provider
    setting.auto_update = auto_update
    for key, value in extra.items():
        setattr(setting, key, value)
    setting.save()
    return setting


class ProviderTests(TestCase):
    def test_nobitex_converts_rial_to_toman(self):
        with responding(NOBITEX_OK):
            self.assertEqual(rates.from_nobitex(), 105150)

    def test_tgju_strips_commas_and_converts(self):
        with responding(TGJU_OK):
            self.assertEqual(rates.from_tgju(), 105800)

    def test_brsapi_picks_the_dollar_row(self):
        with responding(BRSAPI_OK):
            self.assertEqual(rates.from_brsapi('a-key'), 105800)

    def test_brsapi_without_a_key_says_so(self):
        """The message must say what to do, not only that it failed."""
        with self.assertRaises(rates.RateError) as caught:
            rates.from_brsapi('')
        self.assertIn('کلید', str(caught.exception))

    def test_an_unknown_provider_is_refused(self):
        with self.assertRaises(rates.RateError):
            rates.fetch('some-service-we-never-added')


class BadResponseTests(TestCase):
    """A broken response must not become a broken rate."""

    def test_missing_market_data(self):
        with responding({'status': 'ok', 'stats': {}}):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()

    def test_invalid_json(self):
        class NotJson(FakeResponse):
            def read(self):
                return '<html>خطای سرور</html>'.encode()

        with patch('urllib.request.urlopen', return_value=NotJson(None)):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()

    def test_a_rate_that_is_far_too_low_is_refused(self):
        """If a provider ever returns toman instead of rials, the divide by 10
        wrecks the number.
        """
        payload = {'stats': {'usdt-rls': {'latest': '5000'}}}
        with responding(payload):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()

    def test_a_rate_that_is_far_too_high_is_refused(self):
        payload = {'stats': {'usdt-rls': {'latest': '999999999999'}}}
        with responding(payload):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()

    def test_a_non_numeric_price_is_refused(self):
        payload = {'stats': {'usdt-rls': {'latest': 'نامشخص'}}}
        with responding(payload):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()

    def test_a_network_failure_becomes_a_rate_error(self):
        import urllib.error

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('قطع')):
            with self.assertRaises(rates.RateError):
                rates.from_nobitex()


class RefreshTests(TestCase):
    def test_it_does_nothing_while_auto_update_is_off(self):
        setting = RateSetting.load()
        rate, error = rate_service.refresh(setting)

        self.assertIsNone(rate)
        self.assertEqual(error, '')
        self.assertFalse(ExchangeRate.objects.exists())

    def test_force_works_even_when_auto_update_is_off(self):
        """The fetch-now button must work without switching automatic mode on."""
        with responding(NOBITEX_OK):
            rate, error = rate_service.refresh(setting_for('nobitex'), force=True)

        self.assertEqual(error, '')
        self.assertEqual(rate.usd_to_toman, 105150)

    def test_it_records_the_source(self):
        setting = setting_for('nobitex', auto_update=True)

        with responding(NOBITEX_OK):
            rate, _error = rate_service.refresh(setting)

        self.assertEqual(rate.source, ExchangeRate.Source.API)
        self.assertEqual(rate.provider, 'nobitex')

    def test_a_failure_keeps_the_previous_rate(self):
        """The behaviour that matters most here: a provider outage must not stop
        payments.
        """
        import urllib.error

        ExchangeRate.set_rate(90000)
        setting = setting_for('nobitex', auto_update=True)

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('قطع')):
            rate, error = rate_service.refresh(setting)

        self.assertIsNone(rate)
        self.assertNotEqual(error, '')
        self.assertEqual(ExchangeRate.current().usd_to_toman, 90000)

    def test_a_failure_is_recorded_for_the_admin_to_see(self):
        import urllib.error

        setting = setting_for('nobitex', auto_update=True)

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('قطع')):
            rate_service.refresh(setting)

        setting.refresh_from_db()
        self.assertNotEqual(setting.last_error, '')
        self.assertIsNotNone(setting.last_checked_at)
        self.assertIsNone(setting.last_success_at)

    def test_a_success_clears_the_previous_error(self):
        setting = setting_for('nobitex', auto_update=True, last_error='خطای قبلی')

        with responding(NOBITEX_OK):
            rate_service.refresh(setting)

        setting.refresh_from_db()
        self.assertEqual(setting.last_error, '')
        self.assertIsNotNone(setting.last_success_at)

    def test_an_unexpected_error_does_not_escape(self):
        """A parsing bug must not take down the cron job."""
        setting = RateSetting.load()

        with patch('payment_module.rates.fetch', side_effect=KeyError('boom')):
            rate, error = rate_service.refresh(setting, force=True)

        self.assertIsNone(rate)
        self.assertIn('غیرمنتظره', error)


class SetRateTests(TestCase):
    def test_the_new_rate_becomes_active_and_the_old_one_does_not(self):
        first = ExchangeRate.set_rate(90000)
        second = ExchangeRate.set_rate(95000)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(ExchangeRate.current(), second)

    def test_an_unchanged_rate_creates_no_new_row(self):
        """An hourly cron must not fill the history with duplicates."""
        ExchangeRate.set_rate(95000)
        ExchangeRate.set_rate(95000)
        ExchangeRate.set_rate(95000)

        self.assertEqual(ExchangeRate.objects.count(), 1)

    def test_the_history_is_kept(self):
        ExchangeRate.set_rate(90000)
        ExchangeRate.set_rate(95000)
        self.assertEqual(ExchangeRate.objects.count(), 2)


class CommandTests(TestCase):
    def test_it_skips_while_auto_update_is_off(self):
        call_command('update_exchange_rate')
        self.assertFalse(ExchangeRate.objects.exists())

    def test_force_runs_anyway(self):
        setting_for('nobitex')
        with responding(NOBITEX_OK):
            call_command('update_exchange_rate', '--force')
        self.assertEqual(ExchangeRate.current().usd_to_toman, 105150)

    def test_it_exits_non_zero_on_failure(self):
        """Cron needs to be able to tell that nothing happened."""
        import urllib.error

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('قطع')):
            with self.assertRaises(SystemExit):
                call_command('update_exchange_rate', '--force')


class StaffPageTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='boss', email='boss@example.com', password='pw',
            email_active_code='boss123456', is_active=True, is_staff=True,
        )
        self.client.force_login(self.staff)
        self.url = reverse('staff_exchange_rate')

    def test_the_page_opens(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_normal_user_cannot_open_it(self):
        other = User.objects.create_user(
            username='normal', email='normal@example.com', password='pw',
            email_active_code='normal1234', is_active=True,
        )
        client = self.client_class()
        client.force_login(other)
        self.assertNotEqual(client.get(self.url).status_code, 200)

    def test_a_guest_cannot_open_it(self):
        self.assertNotEqual(self.client_class().get(self.url).status_code, 200)

    def test_saving_a_manual_rate(self):
        self.client.post(self.url, {
            'usd_to_toman': '97500', 'provider': 'nobitex',
        })

        rate = ExchangeRate.current()
        self.assertEqual(rate.usd_to_toman, 97500)
        self.assertEqual(rate.source, ExchangeRate.Source.MANUAL)

    def test_turning_on_auto_update(self):
        self.client.post(self.url, {
            'usd_to_toman': '97500', 'auto_update': 'on', 'provider': 'tgju',
        })

        setting = RateSetting.load()
        self.assertTrue(setting.auto_update)
        self.assertEqual(setting.provider, 'tgju')

    def test_auto_update_with_a_key_less_service_needs_no_key(self):
        response = self.client.post(self.url, {
            'usd_to_toman': '97500', 'auto_update': 'on', 'provider': 'nobitex',
        })
        self.assertEqual(response.status_code, 302)

    def test_auto_update_without_a_required_key_is_refused(self):
        """Otherwise the box stays ticked while the rate never updates."""
        response = self.client.post(self.url, {
            'usd_to_toman': '97500', 'auto_update': 'on', 'provider': 'brsapi',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RateSetting.load().auto_update)

    def test_fetch_now_does_not_need_the_form_to_be_valid(self):
        """The button is for testing a provider, not for saving settings."""
        setting_for('nobitex')
        with responding(NOBITEX_OK):
            response = self.client.post(self.url, {'action': 'fetch_now'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExchangeRate.current().usd_to_toman, 105150)

    def test_fetch_now_does_not_turn_on_auto_update(self):
        setting_for('nobitex')
        with responding(NOBITEX_OK):
            self.client.post(self.url, {'action': 'fetch_now'})
        self.assertFalse(RateSetting.load().auto_update)

    def test_a_failed_fetch_shows_the_reason(self):
        import urllib.error

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('قطع')):
            response = self.client.post(self.url, {'action': 'fetch_now'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'اتصال برقرار نشد')

    def test_the_page_shows_the_current_rate(self):
        ExchangeRate.set_rate(96500)
        self.assertContains(self.client.get(self.url), '96500')

    def test_the_page_warns_when_no_rate_is_set(self):
        """Without a rate neither toman display nor the rial gateway works."""
        self.assertContains(self.client.get(self.url), 'هنوز ثبت نشده')

    def test_the_history_is_listed(self):
        ExchangeRate.set_rate(90000)
        ExchangeRate.set_rate(95000)

        html = self.client.get(self.url).content.decode()
        self.assertIn('90000', html)
        self.assertIn('95000', html)

    def test_the_api_key_is_kept_after_saving(self):
        self.client.post(self.url, {
            'usd_to_toman': '97500', 'provider': 'brsapi', 'api_key': 'secret-key',
        })
        self.assertEqual(RateSetting.load().api_key, 'secret-key')

    def test_it_is_linked_from_the_staff_menu(self):
        html = self.client.get(reverse('staff_overview')).content.decode()
        self.assertIn(f'href="{self.url}"', html)


class DefaultProviderTests(TestCase):
    """The default must work with no extra configuration."""

    def test_the_default_provider_exists(self):
        self.assertIn(rates.DEFAULT_PROVIDER, rates.PROVIDERS)

    def test_the_default_provider_needs_no_key(self):
        """Otherwise a fresh install with automatic mode on never fetches anything."""
        _label, _fn, needs_key = rates.PROVIDERS[rates.DEFAULT_PROVIDER]
        self.assertFalse(needs_key)

    def test_a_fresh_setting_uses_a_known_provider(self):
        self.assertIn(RateSetting.load().provider, rates.PROVIDERS)
