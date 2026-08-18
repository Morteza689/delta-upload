"""Adblock rule guard.

Filter lists hide any element whose class or id contains ad, ads, advert,
banner or sponsor, and block URL paths like /ads/ as well.

Yektanet's publisher rules call that a violation and fine for it. Even without
that it is a real problem: a hidden slot earns nothing, and the user's own
earnings page under /ads/ would simply come up blank.

This failure mode is silent. Nothing goes red; the revenue just comes in low.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()

#: Words the filter lists block as a class or an id.
BLOCKED_WORDS = ['ad', 'ads', 'advert', 'advertise', 'advertiser',
                 'ad-image', 'ad-images', 'banner', 'sponsor']

#: Templates we wrote. Legacy layouts and third-party assets are skipped;
#: they are not used by any current page.
OUR_TEMPLATE_DIRS = [
    'templates',
    'download_module/templates',
    'link_module/templates',
    'upload_module/templates',
    'user_panel_module/templates',
    'staff_module/templates',
    'home_module/templates',
    'contact_module/templates',
    'payment_module/templates',
    'api_module/templates',
    'ads_module/templates',
    'account_module/templates',
]


def our_templates():
    base = Path(settings.BASE_DIR)
    for directory in OUR_TEMPLATE_DIRS:
        for path in (base / directory).rglob('*.html'):
            yield path


class ClassAndIdTests(TestCase):
    def test_no_template_uses_a_blocked_class_or_id(self):
        """A blocked class or id means that section vanishes for adblock users."""
        pattern = re.compile(
            r'(?:class|id)\s*=\s*"([^"]*)"', re.IGNORECASE,
        )
        offenders = []

        for path in our_templates():
            text = path.read_text(encoding='utf-8')
            for match in pattern.finditer(text):
                for token in match.group(1).split():
                    #: Split on separators so ad-slot and my_ads are both seen.
                    parts = re.split(r'[-_]', token.lower())
                    if any(part in BLOCKED_WORDS for part in parts):
                        offenders.append(f'{path.name}: {token}')

        self.assertEqual(offenders, [], f'نام‌های مسدودشونده: {offenders}')

    def test_the_css_defines_no_blocked_class(self):
        source = (Path(settings.BASE_DIR) / 'static' / 'src' / 'input.css').read_text(
            encoding='utf-8'
        )
        # Class definitions only, not comment text.
        selectors = re.findall(r'^\s*\.([a-zA-Z0-9_-]+)\s*\{', source, re.MULTILINE)

        offenders = [
            name for name in selectors
            if any(part in BLOCKED_WORDS for part in re.split(r'[-_]', name.lower()))
        ]
        self.assertEqual(offenders, [], f'کلاس‌های مسدودشونده: {offenders}')

    def test_the_slot_wrapper_has_a_neutral_name(self):
        """The most explicit case, and the one that was actually built wrong once."""
        path = (Path(settings.BASE_DIR) / 'download_module' / 'templates' /
                'download_module' / 'component' / 'ad_slot.html')
        markup = path.read_text(encoding='utf-8')

        self.assertNotIn('class="ad-slot"', markup)
        self.assertNotIn('data-ad-slot', markup)
        self.assertIn('embed-frame', markup)


class UrlPathTests(TestCase):
    def test_the_earnings_page_is_not_under_an_ads_path(self):
        """/ads/ in the URL turns the page blank for adblock users."""
        url = reverse('earnings_page')
        for segment in url.strip('/').split('/'):
            self.assertNotIn(segment.lower(), BLOCKED_WORDS)

    def test_the_earnings_page_still_opens(self):
        user = User.objects.create_user(
            username='earner', email='earner@example.com', password='pw',
            email_active_code='earner1234', is_active=True,
        )
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('earnings_page')).status_code, 200)

    def test_no_url_prefix_anywhere_is_blockable(self):
        """Any new path must follow the same rule."""
        from DUploaderProject import urls

        offenders = []
        for entry in urls.urlpatterns:
            prefix = str(getattr(entry, 'pattern', '')).strip('/').split('/')[0]
            parts = re.split(r'[-_]', prefix.lower())
            if any(part in BLOCKED_WORDS for part in parts):
                offenders.append(prefix)

        self.assertEqual(offenders, [])


class RenderedPageTests(TestCase):
    """What actually reaches the browser, not just the template source."""

    def test_the_download_page_markup_is_clean(self):
        from upload_module.models import UploadFile

        author = User.objects.create_user(
            username='pub', email='pub@example.com', password='pw',
            email_active_code='pub1234567', is_active=True,
        )
        UploadFile.objects.create(
            name='sample', slug='sample-file', author=author,
            visibility=UploadFile.Visibility.PUBLIC, is_active=True,
        )

        html = self.client.get('/dl/sample-file').content.decode()

        for word in ('class="ad-slot"', 'id="ads"', 'class="banner"',
                     'class="ads"', 'data-ad-slot'):
            self.assertNotIn(word, html)


class YektanetPublisherRuleTests(TestCase):
    """Publisher rules that carry a fine.

    Kept in code because none of them produce a visible error. One day a
    violation notice simply arrives.
    """

    def setUp(self):
        from ads_module.models import AdSetting, AdSlot
        from upload_module.models import UploadFile

        AdSetting.objects.create(
            is_active=True, cpm_cents=100,
            loader_script='<script>window.NETWORK_LOADER=1;</script>',
        )

        AdSlot.objects.update_or_create(
            position=AdSlot.Position.STEP1,
            defaults={'network': 'test', 'script': '<div id="anchor-one"></div>'},
        )

        author = User.objects.create_user(
            username='ruler', email='ruler@example.com', password='pw',
            email_active_code='ruler12345', is_active=True,
        )
        UploadFile.objects.create(
            name='rules', slug='rules-file', author=author,
            visibility=UploadFile.Visibility.PUBLIC, is_active=True,
        )

    def page(self):
        return self.client.get('/dl/rules-file').content.decode()

    def test_the_loader_appears_exactly_once(self):
        """The loader is shared; a page with two slots must not load it twice."""
        self.assertEqual(self.page().count('window.NETWORK_LOADER=1'), 1)

    def test_the_loader_is_absent_from_pages_without_ads(self):
        """The network's analytics script must not run on the panel or login pages."""
        for path in ('/', '/user/login/', '/faq/', '/shorten/'):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertNotIn('window.NETWORK_LOADER=1', html)

    def test_the_anchor_is_rendered_untouched(self):
        self.assertIn('<div id="anchor-one"></div>', self.page())

    def test_the_wrapper_does_not_impose_flex_on_the_slot(self):
        """A flex wrapper makes its child a flex item and changes the banner width."""
        source = (Path(settings.BASE_DIR) / 'static' / 'src' / 'input.css').read_text(
            encoding='utf-8'
        )
        block = source.split('.embed-frame {')[1].split('}')[0]

        self.assertNotIn('flex', block)
        self.assertNotIn('max-w', block)
        self.assertNotIn('max-h', block)

    def test_the_slot_is_not_inside_a_modal_or_sticky_container(self):
        """Putting a banner in a modal or floating it is a violation.

        Checked against the rendered output rather than the template source:
        the template comments discuss this rule and a text search would match
        them.
        """
        html = self.page()
        slot_start = html.index('data-embed-slot=')
        # The container immediately around the slot; 400 characters is enough.
        around = html[max(0, slot_start - 400):slot_start + 400]

        for forbidden in ('position:fixed', 'position: fixed', 'sticky',
                          'modal', 'role="dialog"'):
            self.assertNotIn(forbidden, around)

    def test_no_page_shows_more_than_four_slots(self):
        """Yektanet's cap: at most four banner slots per viewport."""
        import re

        for path in ('/dl/rules-file',):
            html = self.client.get(path).content.decode()
            count = len(re.findall(r'data-embed-slot=', html))
            self.assertLessEqual(count, 4)
