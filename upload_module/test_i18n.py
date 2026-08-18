"""Bilingual behaviour tests.

Language comes from a cookie, not a URL prefix, so each page has one address.
These lock down what breaks quietly: an uncompiled catalogue, a page direction
stuck on rtl, or a string somebody forgot to wrap.
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

LANG_COOKIE = settings.LANGUAGE_COOKIE_NAME


class SingleUrlPerPageTests(TestCase):
    """URLs must carry no language prefix."""

    def test_urls_have_no_language_prefix_in_either_language(self):
        with translation.override('fa'):
            self.assertEqual(reverse('login_page'), '/user/login/')
        with translation.override('en'):
            self.assertEqual(reverse('login_page'), '/user/login/')

    def test_the_language_prefixed_path_does_not_exist(self):
        """The /en/ prefix is deliberately absent; there is no second routing tree."""
        self.assertEqual(self.client.get('/en/user/login/').status_code, 404)

    def test_the_same_url_serves_both_languages(self):
        fa = self.client.get('/user/login/')
        self.assertContains(fa, 'ورود به حساب کاربری')

        self.client.cookies[LANG_COOKIE] = 'en'
        en = self.client.get('/user/login/')
        self.assertContains(en, 'Log in to your account')


class DirectionTests(TestCase):
    def test_persian_page_is_rtl(self):
        html = self.client.get('/user/login/').content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn('lang="fa"', html)

    def test_english_page_is_ltr(self):
        self.client.cookies[LANG_COOKIE] = 'en'
        html = self.client.get('/user/login/').content.decode()
        self.assertIn('dir="ltr"', html)
        self.assertIn('lang="en"', html)


class CatalogTests(TestCase):
    """These fail when the .mo files are not compiled, which is exactly what we
    want to hear about.
    """

    def test_english_catalog_is_loaded(self):
        with translation.override('en'):
            self.assertEqual(translation.gettext('کلمه عبور'), 'Password')
            self.assertEqual(translation.gettext('موجودی'), 'Balance')

    def test_persian_falls_back_to_the_source_string(self):
        with translation.override('fa'):
            self.assertEqual(translation.gettext('کلمه عبور'), 'کلمه عبور')

    def test_english_page_has_no_untranslated_persian_leak(self):
        self.client.cookies[LANG_COOKIE] = 'en'
        html = self.client.get('/user/login/').content.decode()
        self.assertIn('Log in to your account', html)
        self.assertNotIn('ورود به حساب کاربری', html)


class LanguageSwitcherTests(TestCase):
    def test_switching_sets_the_cookie_and_changes_the_language(self):
        response = self.client.post(
            reverse('set_language'),
            {'language': 'en', 'next': '/user/login/'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.cookies[LANG_COOKIE].value, 'en')

        html = self.client.get('/user/login/').content.decode()
        self.assertIn('Log in to your account', html)

    def test_the_choice_survives_later_requests(self):
        """Nobody should have to pick their language on every visit."""
        self.client.post(reverse('set_language'),
                         {'language': 'en', 'next': '/'})

        for path in ('/user/login/', '/user/register/', '/'):
            html = self.client.get(path).content.decode()
            self.assertIn('lang="en"', html, path)

    def test_switching_back_to_persian_works(self):
        self.client.post(reverse('set_language'), {'language': 'en', 'next': '/'})
        self.client.post(reverse('set_language'), {'language': 'fa', 'next': '/'})

        html = self.client.get('/user/login/').content.decode()
        self.assertIn('dir="rtl"', html)
        self.assertIn('ورود به حساب کاربری', html)

    def test_the_cookie_is_long_lived(self):
        response = self.client.post(reverse('set_language'),
                                    {'language': 'en', 'next': '/'})
        self.assertGreater(response.cookies[LANG_COOKIE]['max-age'], 60 * 60 * 24 * 30)

    def test_get_does_not_change_the_language(self):
        """Django only switches language on POST.

        So a plain link or a browser prefetch cannot change someone's language
        underneath them, which is why the switcher is a form and not an
        anchor.
        """
        self.client.get(reverse('set_language'), {'language': 'en', 'next': '/user/login/'})

        html = self.client.get('/user/login/').content.decode()
        self.assertIn('ورود به حساب کاربری', html)
        self.assertNotIn('Log in to your account', html)


class AcceptLanguageTests(TestCase):
    """With no cookie, the browser language decides."""

    def test_an_english_browser_gets_english(self):
        html = self.client.get('/user/login/',
                               HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9').content.decode()
        self.assertIn('lang="en"', html)

    def test_the_cookie_wins_over_the_browser_header(self):
        self.client.cookies[LANG_COOKIE] = 'fa'
        html = self.client.get('/user/login/',
                               HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9').content.decode()
        self.assertIn('lang="fa"', html)
