"""Rate limiting and captcha tests.

Rate limiting is off in the rest of the suite, or tests that hit a page
repeatedly would trip it. Here it is switched on explicitly and the cache is
cleared between tests so counters do not leak.
"""
from captcha.models import CaptchaStore
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from account_module import login_attempts

User = get_user_model()

#: Present only when the captcha field has actually rendered. The bare word
#: "captcha" also appears in the base script on every page.
CAPTCHA_FIELD_MARKER = 'name="captcha_1"'


def make_user(name='rl', password='StrongPass!123'):
    user = User.objects.create_user(
        username=name, email=f'{name}@example.com', password=password,
        email_active_code=name * 3, is_active=True,
    )
    return user


class RateLimitTestCase(TestCase):
    """Base class that keeps rate limiting on and the cache clean."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)


@override_settings(RATELIMIT_ENABLE=True)
class LoginRateLimitTests(RateLimitTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()

    def post_login(self, email='rl@example.com', password='wrong'):
        return self.client.post(reverse('login_page'),
                                {'email': email, 'password': password})

    def test_repeated_failures_from_one_ip_get_blocked(self):
        for _ in range(10):
            self.post_login()
        response = self.post_login()
        self.assertEqual(response.status_code, 429)

    def test_the_block_page_explains_itself(self):
        for _ in range(11):
            self.post_login()
        response = self.post_login()
        self.assertContains(response, '429', status_code=429)

    def test_a_single_account_is_protected_across_addresses(self):
        """Per-email limiting: rotating addresses must not open one account to attack."""
        for index in range(6):
            self.client.post(
                reverse('login_page'),
                {'email': 'rl@example.com', 'password': 'wrong'},
                REMOTE_ADDR=f'10.0.0.{index}',
            )
        response = self.client.post(
            reverse('login_page'),
            {'email': 'rl@example.com', 'password': 'wrong'},
            REMOTE_ADDR='10.0.0.99',
        )
        self.assertEqual(response.status_code, 429)

    def test_get_requests_are_not_limited(self):
        """Only submissions are limited, not opening the page."""
        for _ in range(30):
            response = self.client.get(reverse('login_page'))
        self.assertEqual(response.status_code, 200)


@override_settings(RATELIMIT_ENABLE=True)
class OtherEndpointRateLimitTests(RateLimitTestCase):
    def test_registration_is_limited(self):
        for index in range(6):
            self.client.post(reverse('register_page'), {
                'username': f'u{index}', 'email': f'u{index}@example.com',
                'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
            })
        response = self.client.post(reverse('register_page'), {})
        self.assertEqual(response.status_code, 429)

    def test_forgot_password_is_limited(self):
        for _ in range(6):
            self.client.post(reverse('forgot_password_page'), {'email': 'a@b.com'})
        response = self.client.post(reverse('forgot_password_page'), {'email': 'a@b.com'})
        self.assertEqual(response.status_code, 429)

    def test_download_page_has_a_generous_limit(self):
        """Users sharing an address behind CGNAT must not be blocked by normal use."""
        for _ in range(50):
            response = self.client.get('/dl/does-not-exist')
        # A 404 means we have not hit the cap yet.
        self.assertEqual(response.status_code, 404)


@override_settings(RATELIMIT_ENABLE=False)
class LoginCaptchaTests(TestCase):
    """The captcha should appear only after repeated failures."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()

    def fail_login(self, times=1):
        for _ in range(times):
            response = self.client.post(reverse('login_page'),
                                        {'email': 'rl@example.com', 'password': 'nope'})
        return response

    def test_first_visit_has_no_captcha(self):
        response = self.client.get(reverse('login_page'))
        self.assertNotContains(response, CAPTCHA_FIELD_MARKER)

    def test_captcha_appears_after_the_threshold(self):
        self.fail_login(times=3)
        response = self.client.get(reverse('login_page'))
        self.assertContains(response, CAPTCHA_FIELD_MARKER)

    def test_captcha_is_required_once_shown(self):
        self.fail_login(times=3)
        response = self.client.post(reverse('login_page'),
                                    {'email': 'rl@example.com', 'password': 'nope'})
        self.assertContains(response, CAPTCHA_FIELD_MARKER)

    def test_correct_password_without_captcha_is_refused_once_required(self):
        """Even the right password must not pass without the captcha."""
        self.fail_login(times=3)
        response = self.client.post(reverse('login_page'), {
            'email': 'rl@example.com', 'password': 'StrongPass!123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_succeeds_with_a_valid_captcha(self):
        self.fail_login(times=3)
        self.client.get(reverse('login_page'))

        store = CaptchaStore.objects.last()
        response = self.client.post(reverse('login_page'), {
            'email': 'rl@example.com', 'password': 'StrongPass!123',
            'captcha_0': store.hashkey, 'captcha_1': store.response,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_a_wrong_captcha_blocks_login(self):
        self.fail_login(times=3)
        self.client.get(reverse('login_page'))

        store = CaptchaStore.objects.last()
        response = self.client.post(reverse('login_page'), {
            'email': 'rl@example.com', 'password': 'StrongPass!123',
            'captcha_0': store.hashkey, 'captcha_1': 'WRONG',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_successful_login_clears_the_counter(self):
        self.fail_login(times=2)
        request = self.client.request().wsgi_request
        self.assertEqual(login_attempts.failure_count(request), 2)

        self.client.post(reverse('login_page'), {
            'email': 'rl@example.com', 'password': 'StrongPass!123',
        })
        self.assertEqual(login_attempts.failure_count(request), 0)

    def test_the_counter_is_per_ip(self):
        for _ in range(3):
            self.client.post(reverse('login_page'),
                             {'email': 'rl@example.com', 'password': 'nope'},
                             REMOTE_ADDR='10.1.1.1')

        # A different address is still clean.
        response = self.client.get(reverse('login_page'), REMOTE_ADDR='10.2.2.2')
        self.assertNotContains(response, CAPTCHA_FIELD_MARKER)


class AlwaysOnCaptchaTests(TestCase):
    """Registration and password reset have no failure signal, so the captcha is
    always on.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_register_page_shows_a_captcha(self):
        response = self.client.get(reverse('register_page'))
        self.assertContains(response, CAPTCHA_FIELD_MARKER)

    def test_register_without_captcha_is_refused(self):
        response = self.client.post(reverse('register_page'), {
            'username': 'bot', 'email': 'bot@example.com',
            'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='bot@example.com').exists())

    def test_register_succeeds_with_a_valid_captcha(self):
        self.client.get(reverse('register_page'))
        store = CaptchaStore.objects.last()

        self.client.post(reverse('register_page'), {
            'username': 'human', 'email': 'human@example.com',
            'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
            'captcha_0': store.hashkey, 'captcha_1': store.response,
        })
        self.assertTrue(User.objects.filter(email='human@example.com').exists())

    def test_forgot_password_page_shows_a_captcha(self):
        response = self.client.get(reverse('forgot_password_page'))
        self.assertContains(response, CAPTCHA_FIELD_MARKER)


class ProjectChecksTests(TestCase):
    """The system checks must catch dangerous production states.

    TESTING=False is needed throughout: the checks stay quiet during test runs
    because Django's runner turns DEBUG off by itself.
    """

    @override_settings(DEBUG=False, TESTING=False, CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    })
    def test_locmem_cache_is_flagged_in_production(self):
        from utils.checks import check_shared_cache
        problems = check_shared_cache(None)
        self.assertEqual([p.id for p in problems], ['dup.W001'])

    @override_settings(DEBUG=False, TESTING=False, RATELIMIT_ENABLE=False)
    def test_disabled_ratelimit_is_flagged_in_production(self):
        from utils.checks import check_ratelimit_enabled
        problems = check_ratelimit_enabled(None)
        self.assertEqual([p.id for p in problems], ['dup.W002'])

    @override_settings(
        DEBUG=False,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        RATELIMIT_ENABLE=False,
        PAYMENT_GATEWAYS={'crypto': {'api_key': 'k', 'ipn_secret': ''}},
        EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
    )
    def test_every_check_stays_quiet_while_running_tests(self):
        """Every check, not just one.

        One of them lacked the guard, and setting a real gateway key brought
        the whole suite down with a SystemCheckError. This covers them all so
        a new check without the guard is caught here.
        """
        from django.core.checks import registry

        problems = []
        for check in registry.registry.get_checks():
            if getattr(check, 'tags', None) and 'dup' in check.tags:
                problems.extend(check(None))

        self.assertEqual([p.id for p in problems], [])

    @override_settings(DEBUG=False, TESTING=False,
                       PAYMENT_GATEWAYS={'crypto': {'api_key': 'k', 'ipn_secret': ''}})
    def test_missing_ipn_secret_is_an_error(self):
        from utils.checks import check_payment_secrets
        problems = check_payment_secrets(None)
        self.assertEqual([p.id for p in problems], ['dup.E001'])

    def test_the_development_secret_key_is_an_error_in_production(self):
        from DUploaderProject.settings import DEV_SECRET_KEY
        from utils.checks import check_secret_key

        with override_settings(DEBUG=False, TESTING=False,
                               SECRET_KEY=DEV_SECRET_KEY):
            problems = check_secret_key(None)
        self.assertEqual([p.id for p in problems], ['dup.E002'])

    def test_a_real_secret_key_passes(self):
        from utils.checks import check_secret_key

        with override_settings(DEBUG=False, TESTING=False,
                               SECRET_KEY='a-real-random-value'):
            self.assertEqual(check_secret_key(None), [])

    def test_the_development_secret_key_is_fine_while_debugging(self):
        from DUploaderProject.settings import DEV_SECRET_KEY
        from utils.checks import check_secret_key

        with override_settings(DEBUG=True, TESTING=False,
                               SECRET_KEY=DEV_SECRET_KEY):
            self.assertEqual(check_secret_key(None), [])

    def test_console_email_is_flagged_in_production(self):
        from utils.checks import check_email_configured

        with override_settings(
            DEBUG=False, TESTING=False,
            EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend',
        ):
            problems = check_email_configured(None)
        self.assertEqual([p.id for p in problems], ['dup.W003'])

    def test_no_secret_is_hardcoded_in_settings(self):
        """Every secret-shaped setting must be read from the environment.

        This is written as a shape rule rather than a list of known strings.
        A test naming the passwords it forbids has to carry them, and the file
        it lives in is committed, which is the thing being prevented.

        DEV_SECRET_KEY is the one literal allowed: it is the fallback that lets
        a fresh checkout run, and check_secret_key already refuses to let it
        reach production.
        """
        import re
        from pathlib import Path

        from django.conf import settings as django_settings

        source = (
            Path(django_settings.BASE_DIR) / 'DUploaderProject' / 'settings.py'
        ).read_text(encoding='utf-8')

        assignment = re.compile(
            r"^([A-Z_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|_KEY)[A-Z_]*)"
            r"\s*=\s*(['\"])(.*?)\2",
            re.M,
        )
        literals = [
            name for name, _quote, value in assignment.findall(source)
            if value and name != 'DEV_SECRET_KEY'
        ]
        self.assertEqual(
            literals, [],
            'these hold a literal secret; read them from os.environ instead',
        )

    def test_the_env_file_is_ignored_by_git(self):
        """Otherwise the same secrets reach the repository by another route."""
        from pathlib import Path

        from django.conf import settings as django_settings

        ignored = (Path(django_settings.BASE_DIR) / '.gitignore').read_text(
            encoding='utf-8'
        ).split()
        self.assertIn('.env', ignored)


class DotenvTests(TestCase):
    """The .env contract: the file supplies defaults, the real environment wins."""

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path

        self.os = os
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / '.env'

        self.before = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(),
                                 os.environ.update(self.before)))

    def load(self, text):
        from DUploaderProject.settings import load_dotenv

        self.path.write_text(text, encoding='utf-8')
        load_dotenv(self.path)

    def test_values_are_read(self):
        self.load('DUP_TEST_ONE=hello\n')
        self.assertEqual(self.os.environ['DUP_TEST_ONE'], 'hello')

    def test_comments_and_blank_lines_are_skipped(self):
        self.load('# a comment\n\nDUP_TEST_TWO=v\n')
        self.assertEqual(self.os.environ['DUP_TEST_TWO'], 'v')

    def test_quotes_are_stripped(self):
        self.load('DUP_TEST_THREE="quoted"\n')
        self.assertEqual(self.os.environ['DUP_TEST_THREE'], 'quoted')

    def test_a_value_containing_equals_survives(self):
        """Passwords often contain =, and a naive split cuts them in half."""
        self.load('DUP_TEST_FOUR=a=b=c\n')
        self.assertEqual(self.os.environ['DUP_TEST_FOUR'], 'a=b=c')

    def test_the_real_environment_wins(self):
        """A server must be able to override a file value without editing the file."""
        self.os.environ['DUP_TEST_FIVE'] = 'from-environment'
        self.load('DUP_TEST_FIVE=from-file\n')
        self.assertEqual(self.os.environ['DUP_TEST_FIVE'], 'from-environment')

    def test_a_missing_file_is_not_an_error(self):
        from pathlib import Path

        from DUploaderProject.settings import load_dotenv

        load_dotenv(Path(self.dir.name) / 'nope.env')  # must not raise

    def test_a_malformed_line_is_skipped(self):
        self.load('this line has no equals sign\nDUP_TEST_SIX=ok\n')
        self.assertEqual(self.os.environ['DUP_TEST_SIX'], 'ok')


class AllowedHostsTests(TestCase):
    """Hosts and origins built from the environment.

    Both lists are read by Django on every request, so a duplicate is not a
    correctness problem. It is a legibility one: the deployed value is what an
    operator reads back when a CSRF failure has to be diagnosed, and a list
    that repeats itself makes them doubt the list rather than the request.
    """

    def build(self, hosts_env, origins_env=''):
        """Rebuild the two lists the way settings.py does."""
        allowed = ['www.d-up.ir', 'd-up.ir', 'localhost', '127.0.0.1']
        for host in hosts_env.split(','):
            host = host.strip()
            if host and host not in allowed:
                allowed.append(host)

        origins = [
            f'https://{host}' for host in allowed
            if host not in ('localhost', '127.0.0.1', '[::1]')
            and not host.startswith('.')
        ]
        for origin in origins_env.split(','):
            origin = origin.strip()
            if origin and origin not in origins:
                origins.append(origin)
        return allowed, origins

    def test_naming_a_host_already_present_adds_nothing(self):
        allowed, _ = self.build('d-up.ir,www.d-up.ir')
        self.assertEqual(allowed,
                         ['www.d-up.ir', 'd-up.ir', 'localhost', '127.0.0.1'])

    def test_a_new_host_is_appended(self):
        allowed, origins = self.build('panel.d-up.ir')
        self.assertIn('panel.d-up.ir', allowed)
        self.assertIn('https://panel.d-up.ir', origins)

    def test_no_origin_appears_twice(self):
        _, origins = self.build('d-up.ir', 'https://d-up.ir')
        self.assertEqual(len(origins), len(set(origins)))

    def test_loopback_never_becomes_a_trusted_origin(self):
        _, origins = self.build('')
        for host in ('localhost', '127.0.0.1'):
            self.assertNotIn(f'https://{host}', origins)

    def test_an_empty_environment_changes_nothing(self):
        allowed, origins = self.build('', '')
        self.assertEqual(allowed,
                         ['www.d-up.ir', 'd-up.ir', 'localhost', '127.0.0.1'])
        self.assertEqual(origins, ['https://www.d-up.ir', 'https://d-up.ir'])

    def test_the_live_settings_hold_no_duplicate(self):
        from django.conf import settings as live

        self.assertEqual(len(live.ALLOWED_HOSTS),
                         len(set(live.ALLOWED_HOSTS)))
