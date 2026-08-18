"""Page rendering and responsiveness tests.

Every page is opened in both languages, because the failures worth catching
here are the quiet ones: a page that 500s only in English, a template comment
rendered as text, or a layout that breaks below a breakpoint.
"""
import re
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ads_module.models import AdSetting
from api_module.models import ApiKey
from payment_module.models import ExchangeRate
from upload_module.models import UploadFile

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()
LANG_COOKIE = settings.LANGUAGE_COOKIE_NAME

GATEWAYS = {
    'zarinpal': {'merchant_id': 'm', 'sandbox': True},
    'crypto': {'api_key': 'k', 'ipn_secret': 's', 'sandbox': True},
}


@override_settings(MEDIA_ROOT=MEDIA_ROOT, PAYMENT_GATEWAYS=GATEWAYS)
class AllPagesTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='pages', email='pages@example.com', password='pw',
            email_active_code='p' * 10, is_active=True,
        )
        self.file = UploadFile.objects.create(
            name='demo', author=self.user, slug='pagedemo',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('demo.bin', b'hello'),
        )
        ExchangeRate.objects.create(usd_to_toman=95000)
        AdSetting.objects.create(cpm_cents=100)
        ApiKey.generate(self.user, 'site')
        self.client.force_login(self.user)

    #: Pages that only make sense for a guest. A signed-in user is redirected
    #: away from them, so they are not tested with an open session.
    ANONYMOUS_PATHS = [
        ('/user/login/', 'login'),
        ('/user/register/', 'register'),
    ]

    def all_paths(self):
        return [
            ('/', 'landing'),
            (reverse('about_page'), 'about'),
            (reverse('terms_page'), 'terms'),
            (reverse('privacy_page'), 'privacy'),
            (reverse('faq_page'), 'faq'),
            (reverse('shorten_page'), 'shortener'),
            (reverse('ticket_list'), 'tickets'),
            (reverse('ticket_new'), 'new ticket'),
            (reverse('forgot_password_page'), 'forgot password'),
            (reverse('contact_us_page'), 'contact'),
            (reverse('user_dashbord'), 'dashboard'),
            (reverse('my_files_page'), 'my files'),
            (reverse('file_report', args=[self.file.pk]), 'file report'),
            (reverse('upload_page'), 'upload'),
            (reverse('storage_plans_page'), 'storage'),
            (reverse('topup_page'), 'top up'),
            (reverse('earnings_page'), 'earnings'),
            (reverse('withdrawal_page'), 'withdrawal'),
            (reverse('withdrawal_list_page'), 'withdrawal list'),
            (reverse('api_keys_page'), 'api keys'),
            (reverse('api_docs_page'), 'api docs'),
            (f'/dl/{self.file.slug}', 'download step 1'),
            (reverse('myfile_page'), 'legacy files'),
        ]

    def test_every_page_renders_in_persian(self):
        for path, label in self.all_paths():
            with self.subTest(page=label):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, f'{label} → {path}')

    def test_every_page_renders_in_english(self):
        self.client.cookies[LANG_COOKIE] = 'en'
        for path, label in self.all_paths():
            with self.subTest(page=label):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, f'{label} → {path}')

    def test_every_page_has_the_viewport_meta(self):
        """Without it a phone renders the page at desktop width and scales it down."""
        for path, label in self.all_paths():
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                self.assertIn('name="viewport"', html, label)
                self.assertIn('width=device-width', html, label)

    def test_every_page_sets_direction_from_the_language(self):
        for path, label in self.all_paths():
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                self.assertIn('dir="rtl"', html, label)

        self.client.cookies[LANG_COOKIE] = 'en'
        for path, label in self.all_paths():
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                self.assertIn('dir="ltr"', html, label)

    def test_no_template_comment_leaks_into_the_output(self):
        """A multi-line {# #} comment renders as text.

        It happened twice, leaking Persian prose into the head of every page,
        so this scans the real output rather than trusting the templates.
        """
        for path, label in self.all_paths():
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                self.assertNotIn('{#', html, label)
                self.assertNotIn('#}', html, label)

    def test_no_unrendered_template_tag_leaks(self):
        for path, label in self.all_paths():
            with self.subTest(page=label):
                html = self.client.get(path).content.decode()
                self.assertNotIn('{%', html, label)
                self.assertNotIn('{{', html, label)

    def test_guest_pages_render_in_both_languages(self):
        self.client.logout()
        for code in ('fa', 'en'):
            self.client.cookies[LANG_COOKIE] = code
            for path, label in self.ANONYMOUS_PATHS:
                with self.subTest(page=label, language=code):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    html = response.content.decode()
                    self.assertIn('width=device-width', html)
                    self.assertNotIn('{#', html)
                    self.assertNotIn('{%', html)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, PAYMENT_GATEWAYS=GATEWAYS)
class ResponsiveLayoutTests(TestCase):
    """Properties whose return would quietly break mobile."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='resp', email='resp@example.com', password='pw',
            email_active_code='r' * 10, is_active=True,
        )
        UploadFile.objects.create(
            name='demo', author=self.user, slug='respdemo',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('demo.bin', b'x'),
        )
        AdSetting.objects.create(cpm_cents=100)
        self.client.force_login(self.user)

    def test_header_has_a_mobile_menu(self):
        html = self.client.get('/').content.decode()
        # The mobile menu uses details so it opens without JavaScript.
        self.assertIn('<details', html)
        self.assertIn('sm:hidden', html)

    def test_desktop_navs_are_hidden_on_mobile(self):
        """Each horizontal header bar must be hidden below its own breakpoint.

        Without it the header overflows and the page scrolls sideways.
        """
        html = self.client.get('/').content.decode()
        header = re.search(r'<header.*?</header>', html, re.S)
        self.assertIsNotNone(header)

        navs = re.findall(r'<nav class="([^"]*)"', header.group(0))
        self.assertGreaterEqual(len(navs), 1)

        for classes in navs:
            with self.subTest(nav=classes[:40]):
                self.assertIn('hidden', classes)
                # Hidden up to a given breakpoint, then shown.
                self.assertRegex(classes, r'(sm|md|lg):flex')

    def test_panel_nav_scrolls_horizontally_on_mobile(self):
        html = self.client.get(reverse('user_dashbord')).content.decode()
        # A horizontal bar on mobile, a vertical column on desktop.
        self.assertIn('overflow-x-auto', html)
        self.assertIn('lg:flex-col', html)

    def test_tables_carry_labels_for_the_card_layout(self):
        """Below md each row becomes a card and every cell takes its title from
        data-label.
        """
        html = self.client.get(reverse('my_files_page')).content.decode()
        self.assertIn('class="rtable"', html)
        self.assertIn('data-label=', html)

    def test_every_table_cell_has_a_label(self):
        for path in (reverse('my_files_page'), reverse('withdrawal_list_page'),
                     reverse('earnings_page'), reverse('user_dashbord')):
            with self.subTest(page=path):
                html = self.client.get(path).content.decode()
                for table in re.findall(r'<table\b.*?</table>', html, re.S):
                    body = re.search(r'<tbody\b.*?</tbody>', table, re.S)
                    if not body:
                        continue
                    for cell in re.findall(r'<td\b([^>]*)>', body.group(0)):
                        # A full-width cell (an empty row) needs no label.
                        if 'colspan' in cell:
                            continue
                        self.assertIn('data-label', cell, f'{path}: <td{cell}>')

    def test_the_hero_scene_scales_down_on_small_screens(self):
        html = self.client.get('/').content.decode()
        # Card heights and widths are breakpointed, or they overlap on a phone.
        self.assertIn('h-[300px]', html)
        self.assertIn('sm:w-52', html)

    def built_css(self):
        """The built CSS, whitespace stripped: the build output is minified."""
        css = (settings.BASE_DIR / 'static' / 'css' / 'tailwind.css').read_text(
            encoding='utf-8'
        )
        return re.sub(r'\s+', '', css)

    def test_buttons_meet_the_touch_target_size(self):
        """A button on a phone needs at least 44 pixels of height."""
        self.assertIn('min-height:2.75rem', self.built_css())

    def test_the_responsive_table_css_is_built(self):
        css = self.built_css()
        self.assertIn('.rtable', css)
        self.assertIn('attr(data-label)', css)
        self.assertIn('max-width:767px', css)
