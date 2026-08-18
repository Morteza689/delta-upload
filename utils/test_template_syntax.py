"""No template syntax may survive into a rendered page.

Django's {# #} is single-line only. A multi-line one is not a comment at all:
it renders as literal text. That is how a note about how email clients behave
became the entire visible body of a message delivered to a real inbox.
"""
import pathlib
import re

from django.conf import settings
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.utils import translation

SKIP = ('.venv', 'node_modules', 'static/admin', 'static/static', 'assets',
        'select2', 'polls/static', 'user_panel_module/css')


def template_files():
    for path in pathlib.Path(settings.BASE_DIR).rglob('*.html'):
        text = str(path).replace('\\', '/')
        if any(part in text for part in SKIP):
            continue
        yield path


def opens_across_lines(body):
    """Is there a {# whose closing #} is not on the same line?"""
    for match in re.finditer(r'\{#', body):
        line_end = body.find('\n', match.start())
        close = body.find('#}', match.start())
        if close == -1 or (line_end != -1 and close > line_end):
            return True
    return False


class MultiLineCommentTests(TestCase):
    def test_no_template_uses_a_multi_line_hash_comment(self):
        offenders = [
            str(path.relative_to(settings.BASE_DIR))
            for path in template_files()
            if opens_across_lines(path.read_text(encoding='utf-8'))
        ]
        self.assertEqual(
            offenders, [],
            'these use {# #} across lines, which renders as visible text; '
            'use the comment tag instead',
        )


@override_settings(SITE_BASE_URL='https://d-up.ir')
class RenderedEmailTests(TestCase):
    """The emails specifically, since they are what the bug reached."""

    CASES = [
        ('emails/activate_account.html',
         {'activation_url': 'https://d-up.ir/a/X'}),
        ('emails/reset_password.html',
         {'reset_url': 'https://d-up.ir/r/X', 'valid_hours': 1}),
    ]

    def render(self, name, extra, language='fa'):
        """Render one email in a language we name.

        Named rather than inherited: an email is rendered from a queue, not
        from a request, so whatever language happens to be active is an
        accident. A test that relies on it passes or fails depending on which
        other tests ran first.
        """
        from utils.email_service import site_context

        with translation.override(language):
            return render_to_string(name, {**site_context(), **extra})

    def visible_text(self, html):
        stripped = re.sub(r'<(script|style|svg)[^>]*>.*?</\1>', ' ', html,
                          flags=re.S)
        return ' '.join(re.sub(r'<[^>]*>', ' ', stripped).split())

    def test_nothing_raw_survives_rendering(self):
        for name, extra in self.CASES:
            html = self.render(name, extra)
            for marker in ('{#', '#}', '{%', '%}', '{{', '}}'):
                self.assertNotIn(marker, html, f'{name} leaked {marker}')

    def test_the_body_starts_with_the_message(self):
        html = self.render(*self.CASES[0])
        visible = self.visible_text(html)

        # The first thing a reader sees must be the message, not a note left
        # for whoever maintains the template.
        self.assertNotIn('Email is not the web', visible)
        self.assertNotIn('Tables for layout', visible)
        self.assertIn('خوش آمدی', visible)

    def test_the_english_body_starts_with_the_message(self):
        visible = self.visible_text(self.render(*self.CASES[0], language='en'))

        self.assertNotIn('Email is not the web', visible)
        self.assertIn('Welcome', visible)

    def test_no_english_maintenance_note_reaches_the_reader(self):
        for name, extra in self.CASES:
            visible = self.visible_text(self.render(name, extra))
            # Every word a recipient sees is Persian or a URL. A run of plain
            # English sentences means a comment leaked.
            runs = re.findall(r'[A-Za-z]{4,}(?:\s+[A-Za-z]{4,}){3,}', visible)
            self.assertEqual(runs, [], f'{name} shows English prose: {runs}')
