"""Security regression tests.

Each one covers a finding from the review, so a fix cannot quietly come
undone.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from utils.http_service import get_client_ip

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()


class ForwardedForSpoofingTests(TestCase):
    """X-Forwarded-For is sent by the client and can be forged.

    Trusting it blindly lets an attacker be a new visitor on every request,
    which defeats both rate limiting and ad anti-fraud.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def request_with(self, forwarded=None, remote='203.0.113.9'):
        headers = {'REMOTE_ADDR': remote}
        if forwarded is not None:
            headers['HTTP_X_FORWARDED_FOR'] = forwarded
        return self.factory.get('/', **headers)

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_forwarded_header_is_ignored_by_default(self):
        request = self.request_with(forwarded='1.2.3.4')
        self.assertEqual(get_client_ip(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_a_spoofed_chain_cannot_change_the_result(self):
        request = self.request_with(forwarded='9.9.9.9, 8.8.8.8, 7.7.7.7')
        self.assertEqual(get_client_ip(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_behind_one_proxy_the_last_entry_is_used(self):
        # The left-hand values were forged by the client; the last one was added by
        # our own proxy.
        request = self.request_with(forwarded='1.1.1.1, 198.51.100.7')
        self.assertEqual(get_client_ip(request), '198.51.100.7')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_short_chain_falls_back_to_remote_addr(self):
        """A chain shorter than expected means the header was tampered with."""
        request = self.request_with(forwarded='')
        self.assertEqual(get_client_ip(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_a_client_cannot_shorten_the_chain_to_win(self):
        request = self.request_with(forwarded='1.1.1.1')
        self.assertEqual(get_client_ip(request), '203.0.113.9')


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class VisitorHashSpoofingTests(TestCase):
    """The visitor hash must not be changeable with a forged header."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_rotating_the_header_does_not_create_new_visitors(self):
        from ads_module.services import visitor_hash

        factory = RequestFactory()
        hashes = set()
        for index in range(20):
            request = factory.get(
                '/', REMOTE_ADDR='203.0.113.9',
                HTTP_X_FORWARDED_FOR=f'10.0.0.{index}',
                HTTP_USER_AGENT='Mozilla/5.0',
            )
            hashes.add(visitor_hash(request))

        # All the same person, so all the same hash.
        self.assertEqual(len(hashes), 1)

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_different_real_addresses_are_different_visitors(self):
        from ads_module.services import visitor_hash

        factory = RequestFactory()
        first = visitor_hash(factory.get('/', REMOTE_ADDR='1.1.1.1',
                                         HTTP_USER_AGENT='Mozilla/5.0'))
        second = visitor_hash(factory.get('/', REMOTE_ADDR='2.2.2.2',
                                          HTTP_USER_AGENT='Mozilla/5.0'))
        self.assertNotEqual(first, second)

    def test_the_hash_does_not_contain_the_raw_address(self):
        from ads_module.services import visitor_hash

        request = RequestFactory().get('/', REMOTE_ADDR='203.0.113.9')
        self.assertNotIn('203.0.113.9', visitor_hash(request))


@override_settings(MEDIA_ROOT=MEDIA_ROOT, MAX_UPLOAD_SIZE_BYTES=1024)
class UploadSizeLimitTests(TestCase):
    """Without a size cap one person can fill the storage."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='up', email='up@example.com', password='pw',
            email_active_code='u' * 10, is_active=True,
        )
        self.client.force_login(self.user)

    def test_an_oversized_upload_is_rejected_in_the_panel(self):
        from upload_module.models import UploadFile

        response = self.client.post(reverse('upload_page'), {
            'name': 'big', 'description': '', 'visibility': 'private',
            'price': 0,
            'file': SimpleUploadedFile('big.bin', b'x' * 5000),
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UploadFile.objects.exists())

    def test_an_upload_within_the_limit_succeeds(self):
        from upload_module.models import UploadFile

        self.client.post(reverse('upload_page'), {
            'name': 'small', 'description': '', 'visibility': 'private',
            'price': 0,
            'file': SimpleUploadedFile('small.bin', b'x' * 100),
        })
        self.assertTrue(UploadFile.objects.exists())

    def test_an_oversized_upload_is_rejected_by_the_api(self):
        from api_module.models import ApiKey

        _key, raw = ApiKey.generate(self.user, 'site')
        response = self.client.post(
            '/api/v1/files/',
            {'file': SimpleUploadedFile('big.bin', b'x' * 5000)},
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error']['code'], 'file_too_large')


class UserEnumerationTests(TestCase):
    """The password reset response must not reveal which addresses are registered."""

    def setUp(self):
        User.objects.create_user(
            username='known', email='known@example.com', password='pw',
            email_active_code='k' * 10, is_active=True,
        )

    def response_for(self, email):
        """Fill the form as a real user would, captcha included."""
        from captcha.models import CaptchaStore

        self.client.get(reverse('forgot_password_page'))
        store = CaptchaStore.objects.last()
        return self.client.post(reverse('forgot_password_page'), {
            'email': email,
            'captcha_0': store.hashkey,
            'captcha_1': store.response,
        })

    def test_known_and_unknown_emails_look_the_same(self):
        known = self.response_for('known@example.com')
        unknown = self.response_for('nobody@example.com')

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertNotContains(known, 'کاربر وجود ندارد')
        self.assertNotContains(unknown, 'کاربر وجود ندارد')

    def test_both_show_the_same_confirmation(self):
        for email in ('known@example.com', 'nobody@example.com'):
            response = self.response_for(email)
            self.assertContains(response, 'اگر این ایمیل در سایت ثبت شده باشد')


class AdminSafetyTests(TestCase):
    """The admin must not store a password unhashed."""

    def test_user_admin_handles_passwords_properly(self):
        from django.contrib import admin
        from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

        registered = admin.site._registry[User]
        # With a plain ModelAdmin the password field becomes text input and saving
        # a user from the panel writes it unhashed.
        self.assertIsInstance(registered, BaseUserAdmin)

    def test_balance_is_not_editable_from_admin(self):
        from django.contrib import admin

        registered = admin.site._registry[User]
        # The balance must only change through wallet, so the ledger agrees with it.
        self.assertIn('balance', registered.readonly_fields)


class ResilientCacheTests(TestCase):
    """A Redis outage must not take the site down.

    With the stock backend every protected page would 500 and the whole site
    would go with it.
    """

    def make_cache(self):
        from utils.cache_backend import ResilientRedisCache

        # A port nobody is listening on, for a genuine connection error.
        return ResilientRedisCache('redis://127.0.0.1:6399/0', {})

    def test_get_returns_default_when_redis_is_down(self):
        self.assertIsNone(self.make_cache().get('anything'))

    def test_set_does_not_raise_when_redis_is_down(self):
        self.make_cache().set('k', 'v')

    def test_incr_reports_a_first_hit_when_redis_is_down(self):
        # 1 means first request in this window, so the rate limit lets it through.
        self.assertEqual(self.make_cache().incr('counter'), 1)

    def test_add_reports_success_when_redis_is_down(self):
        self.assertTrue(self.make_cache().add('k', 0, 60))

    def test_delete_does_not_raise_when_redis_is_down(self):
        self.make_cache().delete('k')

    @override_settings(RATELIMIT_ENABLE=True, CACHES={
        'default': {
            'BACKEND': 'utils.cache_backend.ResilientRedisCache',
            'LOCATION': 'redis://127.0.0.1:6399/0',
        },
    })
    def test_pages_still_work_when_redis_is_down(self):
        response = self.client.get(reverse('login_page'))
        self.assertEqual(response.status_code, 200)

    @override_settings(RATELIMIT_ENABLE=True, CACHES={
        'default': {
            'BACKEND': 'utils.cache_backend.ResilientRedisCache',
            'LOCATION': 'redis://127.0.0.1:6399/0',
        },
    })
    def test_login_still_works_when_redis_is_down(self):
        User.objects.create_user(
            username='res', email='res@example.com', password='StrongPass!123',
            email_active_code='r' * 10, is_active=True,
        )
        response = self.client.post(reverse('login_page'), {
            'email': 'res@example.com', 'password': 'StrongPass!123',
        })
        self.assertEqual(response.status_code, 302)


class SecurityHeaderTests(TestCase):
    def test_nosniff_is_sent(self):
        response = self.client.get(reverse('login_page'))
        self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')

    def test_clickjacking_protection_is_deny(self):
        response = self.client.get(reverse('login_page'))
        self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')

    def test_referrer_policy_is_set(self):
        response = self.client.get(reverse('login_page'))
        self.assertIn('strict-origin', response.headers.get('Referrer-Policy', ''))
