"""Requests arriving over a unix socket carry no peer address.

nginx talking to gunicorn over a socket leaves REMOTE_ADDR empty, because the
peer is a file and has no address. django-ratelimit reads REMOTE_ADDR directly
and raises ImproperlyConfigured when it finds nothing, so every rate-limited
view answered 500.

Only POSTs were affected, which is what made it look like a CSRF or database
problem: the landing page, the login form and every other GET rendered fine.

The test client always sets REMOTE_ADDR, which is why nothing caught this until
it was in production. These tests take it away.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.module_loading import import_string

from utils.http_service import get_client_ip, ratelimit_key

User = get_user_model()


class SocketAddressTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def socket_request(self, **meta):
        """A request as it arrives over a unix socket: no peer address."""
        request = self.factory.post('/user/login/')
        request.META['REMOTE_ADDR'] = ''
        request.META.update(meta)
        return request

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_the_client_address_comes_from_the_proxy_header(self):
        request = self.socket_request(HTTP_X_FORWARDED_FOR='203.0.113.9')

        self.assertEqual(ratelimit_key(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_forged_chain_does_not_win(self):
        # The client sent its own header; nginx appended the real address. Only
        # the entry our own proxy added can be trusted, and it is the last.
        request = self.socket_request(
            HTTP_X_FORWARDED_FOR='1.2.3.4, 203.0.113.9')

        self.assertEqual(ratelimit_key(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_an_unknowable_address_shares_one_bucket_rather_than_raising(self):
        # No peer address and no header. Sharing a limit can only refuse more
        # than it should; raising takes the whole site down.
        self.assertEqual(ratelimit_key(self.socket_request()), 'unknown')

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_without_trusted_proxies_the_header_is_ignored(self):
        request = self.socket_request(HTTP_X_FORWARDED_FOR='1.2.3.4')

        # Nothing reliable is left, so it must not fall back to a value the
        # client chose.
        self.assertEqual(ratelimit_key(request), 'unknown')

    def test_a_normal_request_is_unchanged(self):
        request = self.factory.post('/user/login/', REMOTE_ADDR='198.51.100.5')

        self.assertEqual(ratelimit_key(request), '198.51.100.5')
        self.assertEqual(get_client_ip(request), '198.51.100.5')


class SettingTests(TestCase):
    def test_the_setting_names_a_callable_that_exists(self):
        # A typo here is invisible until a rate-limited view is hit in
        # production, which is the same failure this whole file is about.
        key = settings.RATELIMIT_IP_META_KEY

        self.assertTrue(callable(import_string(key)), key)

    def test_the_setting_is_not_a_bare_header_name(self):
        # Naming HTTP_X_FORWARDED_FOR would work and would also let anyone
        # become a new visitor per request by sending their own.
        self.assertNotIn('HTTP_', settings.RATELIMIT_IP_META_KEY)


@override_settings(RATELIMIT_ENABLE=True, TRUSTED_PROXY_COUNT=1)
class PostsOverASocketTests(TestCase):
    """The failure as a user met it: every POST a 500."""

    def post_without_a_peer_address(self, url, data):
        return self.client.post(url, data, REMOTE_ADDR='',
                                HTTP_X_FORWARDED_FOR='203.0.113.40')

    def test_login_does_not_error(self):
        response = self.post_without_a_peer_address(
            reverse('login_page'),
            {'email': 'nobody@example.com', 'password': 'wrong-password'})

        self.assertLess(response.status_code, 500)

    def test_registration_does_not_error(self):
        response = self.post_without_a_peer_address(
            reverse('register_page'),
            {'email': 'someone@example.com', 'password': 'a-password-1',
             'confirm_password': 'a-password-1'})

        self.assertLess(response.status_code, 500)

    def test_the_guest_upload_form_does_not_error(self):
        response = self.post_without_a_peer_address(
            reverse('guest_upload_page'), {'name': 'x'})

        self.assertLess(response.status_code, 500)
