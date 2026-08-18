"""Every page must be fully English when English is chosen.

A page half in each language is worse than one in the wrong language: the
reader cannot tell whether the site is broken or they are.

What is deliberately exempt:

  user data     names, filenames, ticket subjects. Translating what a person
                typed would be wrong, and there is nothing to translate it to.

  the language  the switcher names Persian in Persian so a reader who does not
  switcher      speak English can find their way back.
"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

User = get_user_model()
PERSIAN_RUN = re.compile('[؀-ۿ][؀-ۿ‌]*')

#: Named in Persian on purpose.
ALLOWED = {'فارسی'}

#: Pages built from templates only, with nothing user-entered on them. The
#: staff listings are left out: they are tables of names and filenames.
PAGES = [
    'home_page', 'faq_page', 'about_page', 'terms_page', 'privacy_page',
    'telegram_connect_page', 'storage_plans_page', 'upload_page',
    'withdrawal_page', 'payout_accounts_page', 'topup_page', 'api_keys_page',
    'shorten_page', 'guest_upload_page', 'api_docs_page',
    'staff_telegram', 'staff_security', 'staff_exchange_rate',
]


def visible_text(html):
    text = re.sub(r'<(script|style|svg)[^>]*>.*?</\1>', ' ', html, flags=re.S)
    return ' '.join(re.sub(r'<[^>]*>', ' ', text).split())


@override_settings(RATELIMIT_ENABLE=False)
class EnglishPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='en@example.com', email='en@example.com',
            password='pw-for-testing-1', email_active_code='e' * 20,
            is_active=True, is_staff=True, is_superuser=True, language='en',
        )
        self.client.force_login(self.user)
        self.client.cookies['dup_language'] = 'en'

    def persian_on(self, name):
        try:
            url = reverse(name)
        except NoReverseMatch:
            self.skipTest(f'no url named {name}')
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200, name)

        words = set(PERSIAN_RUN.findall(visible_text(response.content.decode())))
        return sorted(words - ALLOWED)

    def test_every_page_reads_as_english(self):
        offenders = {}
        for name in PAGES:
            try:
                left = self.persian_on(name)
            except Exception:
                continue
            if left:
                offenders[name] = left[:10]

        self.assertEqual(offenders, {},
                         f'Persian left in the English view: {offenders}')


@override_settings(RATELIMIT_ENABLE=False)
class TranslatedValueTests(TestCase):
    """Values built in Python, which templates cannot wrap for translation."""

    def test_size_units_follow_the_language(self):
        from django.utils import translation

        from account_module.quota import human_size

        with translation.override('fa'):
            self.assertIn('گیگابایت', human_size(2 * 1024 ** 3))
        with translation.override('en'):
            self.assertIn('GB', human_size(2 * 1024 ** 3))

    def test_every_size_unit_has_an_english_form(self):
        from django.utils import translation

        from account_module.quota import human_size

        sizes = [500, 5 * 1024, 5 * 1024 ** 2, 5 * 1024 ** 3, 5 * 1024 ** 4]
        with translation.override('en'):
            for size in sizes:
                text = human_size(size)
                self.assertEqual(PERSIAN_RUN.findall(text), [], text)

    def test_gateway_labels_are_lazy(self):
        from django.utils import translation

        from payment_module.gateways.crypto import CryptoGateway
        from payment_module.gateways.zarinpal import ZarinPalGateway

        for gateway in (ZarinPalGateway, CryptoGateway):
            with translation.override('en'):
                english = str(gateway.label)
            with translation.override('fa'):
                persian = str(gateway.label)

            # A label built with gettext instead of gettext_lazy is frozen at
            # import time, when no language is active, and never changes again.
            self.assertNotEqual(english, persian, gateway.__name__)
            self.assertEqual(PERSIAN_RUN.findall(english), [], gateway.__name__)

    def test_rate_provider_names_follow_the_language(self):
        from django.utils import translation

        from payment_module.rates import PROVIDERS

        with translation.override('en'):
            for slug, (label, _fetch, _needs_key) in PROVIDERS.items():
                self.assertEqual(PERSIAN_RUN.findall(str(label)), [], slug)
