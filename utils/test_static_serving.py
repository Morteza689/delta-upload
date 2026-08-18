"""Static files have to arrive at the browser.

With DEBUG off Django serves nothing under STATIC_URL, on the assumption that a
web server in front is doing it. Where nothing is, every page renders correctly
and looks broken: no stylesheet, no scripts, no fonts. Nothing in the logs says
so, because as far as Django is concerned every request was answered.
"""
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.handlers.wsgi import WSGIHandler
from django.test import TestCase, override_settings

from utils.checks import check_static_files_served

MANIFEST_BACKEND = ('whitenoise.storage.'
                    'CompressedManifestStaticFilesStorage')


class ConfigurationTests(TestCase):
    def test_whitenoise_runs_before_anything_that_touches_state(self):
        order = settings.MIDDLEWARE
        static = next(i for i, m in enumerate(order) if 'whitenoise' in m)
        session = next(i for i, m in enumerate(order) if 'sessions' in m)

        # A request for a stylesheet has no business opening a session,
        # resolving a locale or looking up a user.
        self.assertEqual(static, 1)
        self.assertLess(static, session)

    def test_static_root_is_not_a_source_directory(self):
        # Collecting into a source directory makes this run's output the next
        # run's input, and every collect nests another copy.
        root = Path(settings.STATIC_ROOT).resolve()
        for source in settings.STATICFILES_DIRS:
            source = Path(source).resolve()
            self.assertNotEqual(root, source)
            self.assertFalse(str(root).startswith(str(source) + '\\'))
            self.assertFalse(str(root).startswith(str(source) + '/'))

    def test_tests_do_not_need_a_collected_manifest(self):
        # Tests run with DEBUG off. Under manifest storage every {% static %}
        # would raise until someone had run collectstatic, so a fresh clone
        # would fail for a reason unrelated to what it was testing.
        self.assertNotIn('Manifest',
                         settings.STORAGES['staticfiles']['BACKEND'])


class ChecksTests(TestCase):
    """The check is what turns a silent misconfiguration into a message."""

    def run_check(self, **overrides):
        base = dict(DEBUG=False, TESTING=False,
                    STATIC_ROOT=settings.STATIC_ROOT,
                    MIDDLEWARE=settings.MIDDLEWARE)
        base.update(overrides)
        with override_settings(**base):
            return [problem.id for problem in check_static_files_served(None)]

    def test_nothing_is_reported_in_development(self):
        self.assertEqual(self.run_check(DEBUG=True), [])

    def test_a_missing_static_root_is_an_error(self):
        self.assertEqual(self.run_check(STATIC_ROOT=''), ['dup.E003'])

    def test_an_empty_static_root_is_reported(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertIn('dup.W004', self.run_check(STATIC_ROOT=empty))

    def test_a_manifest_backend_without_its_manifest_is_an_error(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'something.css').write_text('a{}', encoding='utf-8')
            ids = self.run_check(
                STATIC_ROOT=root,
                STORAGES={**settings.STORAGES,
                          'staticfiles': {'BACKEND': MANIFEST_BACKEND}},
            )
        self.assertIn('dup.E004', ids)

    def test_removing_whitenoise_is_reported(self):
        without = [m for m in settings.MIDDLEWARE if 'whitenoise' not in m]
        self.assertIn('dup.W005', self.run_check(MIDDLEWARE=without))

    def test_a_complete_configuration_reports_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'something.css').write_text('a{}', encoding='utf-8')
            self.assertEqual(self.run_check(STATIC_ROOT=root), [])


class DeliveryTests(TestCase):
    """Every asset a page names must actually come back.

    The files are collected into a temporary directory rather than trusting
    whatever is in STATIC_ROOT. Otherwise this passes on a machine where
    somebody has run collectstatic and fails on a fresh clone, which makes it
    a test of the checkout instead of the code.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.core.management import call_command

        cls._root = tempfile.TemporaryDirectory()
        with override_settings(STATIC_ROOT=cls._root.name):
            call_command('collectstatic', '--noinput', verbosity=0)
        cls._override = override_settings(STATIC_ROOT=cls._root.name)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        cls._root.cleanup()
        super().tearDownClass()

    def assets_named_by(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode('utf-8', 'replace')
        return sorted(set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html)))

    def fetch_through_wsgi(self, path):
        """Ask the WSGI application directly, the way a browser would.

        The test client routes through URL resolution, which never sees a static
        path. WhiteNoise sits above that, so this is the only way to find out
        whether the file is served at all.
        """
        import wsgiref.util

        environ = {}
        wsgiref.util.setup_testing_defaults(environ)
        environ.update({'PATH_INFO': path, 'REQUEST_METHOD': 'GET',
                        'HTTP_HOST': 'd-up.ir', 'SERVER_NAME': 'd-up.ir',
                        'wsgi.url_scheme': 'https'})
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured['status'] = int(status[:3])
            captured['headers'] = dict(headers)

        body = b''.join(WSGIHandler()(environ, start_response))
        return captured['status'], body, captured['headers']

    def test_the_landing_page_names_a_stylesheet_and_scripts(self):
        assets = self.assets_named_by('/')

        self.assertTrue(any(a.endswith('.css') for a in assets), assets)
        self.assertTrue(any(a.endswith('.js') for a in assets), assets)

    def test_every_asset_the_landing_page_names_is_served(self):
        for path in self.assets_named_by('/'):
            status, body, _headers = self.fetch_through_wsgi(path)
            self.assertEqual(status, 200, path)
            self.assertGreater(len(body), 0, path)

    def test_a_missing_asset_is_a_404_not_a_page(self):
        # WhiteNoise must not fall through to the URL conf and hand back HTML
        # with a 200, which is how a broken asset becomes invisible.
        status, _body, _headers = self.fetch_through_wsgi(
            '/static/css/does-not-exist.css')

        self.assertEqual(status, 404)
