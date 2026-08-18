"""Persian numerals belong in Persian pages only.

Vazirmatn's ss01 feature swaps 0-9 for Persian numerals. It was set on body,
so it reached every page in every language and an English visitor read prices
and counts in Persian digits.

It is now keyed off the document language, and switched off again for anything
meant to be copied: a URL, an IBAN, a wallet address, a bot token. Copying ۱۲۳
out of one of those gives a value nothing will accept.
"""
import pathlib
import re

from django.conf import settings
from django.test import TestCase
from django.urls import reverse


def built_css():
    path = pathlib.Path(settings.BASE_DIR) / 'static' / 'css' / 'tailwind.css'
    if not path.exists():
        return ''
    return path.read_text(encoding='utf-8')


class DigitFeatureTests(TestCase):
    def setUp(self):
        self.css = built_css()
        if not self.css:
            self.skipTest('tailwind.css has not been built')

    def test_body_alone_does_not_turn_on_persian_digits(self):
        # The old rule. Anything matching it applies to English too.
        naked = re.search(r'(?<![\]\s])\bbody\s*\{[^}]*ss01', self.css)
        self.assertIsNone(naked, 'ss01 is applied without a language condition')

    def test_persian_digits_are_scoped_to_the_persian_document(self):
        self.assertIn('html[lang=fa] body', self.css)
        self.assertRegex(self.css, r'html\[lang=fa\] body\{[^}]*ss01')

    def test_copyable_values_keep_latin_digits(self):
        # A rule that switches ss01 back off for these.
        block = re.search(r'html\[lang=fa\][^{]*\[dir=ltr\][^{]*\{([^}]*)\}',
                          self.css)
        self.assertIsNotNone(block, 'no rule exempts ltr content')
        self.assertNotIn('ss01', block.group(1))

    def test_code_and_inputs_are_exempt_too(self):
        exempt = re.search(
            r'html\[lang=fa\][^{]*(code|input|textarea)[^{]*\{([^}]*)\}', self.css,
        )
        self.assertIsNotNone(exempt)
        self.assertNotIn('ss01', exempt.group(2))


class DocumentLanguageTests(TestCase):
    """The rule needs the html tag to carry the right language."""

    def test_the_persian_page_says_so(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'fa'
        html = self.client.get(reverse('home_page')).content.decode()
        self.assertIn('<html lang="fa"', html)
        self.assertIn('dir="rtl"', html)

    def test_the_english_page_says_so(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        html = self.client.get(reverse('home_page')).content.decode()
        self.assertIn('<html lang="en"', html)
        self.assertIn('dir="ltr"', html)


class TranslatedNumeralTests(TestCase):
    """Numerals written into Persian strings must not survive translation."""

    def test_no_english_translation_carries_a_persian_numeral(self):
        import polib

        path = (pathlib.Path(settings.BASE_DIR) / 'locale' / 'en' /
                'LC_MESSAGES' / 'django.po')
        if not path.exists():
            self.skipTest('no English catalogue')

        offenders = [
            entry.msgid[:60]
            for entry in polib.pofile(str(path))
            if entry.msgstr and re.search('[۰-۹]', entry.msgstr)
        ]
        self.assertEqual(offenders, [],
                         f'English translations with Persian digits: {offenders}')

    def test_the_english_landing_page_shows_no_persian_numeral(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        html = self.client.get(reverse('home_page')).content.decode()

        # Strip the language switcher, which names Persian in Persian on
        # purpose so a non-Persian speaker can find it.
        body = re.sub(r'<form[^>]*set_language.*?</form>', '', html, flags=re.S)
        found = re.findall('[۰-۹]+', body)
        self.assertEqual(found, [], f'Persian numerals in the English page: {found}')


class StepNumberTests(TestCase):
    """The big step numbers on the landing page must sit inside their card.

    They were pulled up by a negative offset while the card clipped its
    overflow, so every digit arrived with its top sliced off and read as a
    rendering fault rather than a decoration.
    """

    def markup(self):
        return self.client.get(reverse('home_page')).content.decode()

    def test_the_number_is_not_pulled_outside_the_card(self):
        html = self.markup()
        card = re.search(
            r'<div class="panel relative overflow-hidden">.*?</div>\s*</div>',
            html, re.S,
        )
        self.assertIsNotNone(card, 'the step card markup changed shape')
        # A negative offset inside a clipping box is what cut the digits.
        self.assertNotIn('-top-', card.group(0))

    def test_the_number_is_hidden_from_screen_readers(self):
        # The steps are already in order; announcing "one" before the heading
        # is noise.
        self.assertIn('aria-hidden="true"', self.markup())

    def test_the_title_is_kept_clear_of_the_number(self):
        html = self.markup()
        title = re.search(r'<h3 class="([^"]*)">', html)
        self.assertIsNotNone(title)
        # Logical padding, so it lands on the correct side in both directions.
        self.assertIn('pe-', title.group(1))

    def test_the_number_sits_on_the_trailing_edge_in_both_directions(self):
        # inset-inline-end rather than right, or the number crosses to the
        # wrong corner when the page flips to left-to-right.
        self.assertIn('inset-inline-end', self.markup())
