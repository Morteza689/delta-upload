"""Captcha switch tests.

The security point behind all of these: this is a switch, not a fallback. If
the server accepted both a Google token and a local answer, an attacker would
always take the weaker path.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import captcha as captcha_service
from .models import CaptchaSetting

User = get_user_model()

KEYS = {'RECAPTCHA_SITE_KEY': 'site-key', 'RECAPTCHA_SECRET_KEY': 'secret-key'}


def google_says(success, error_codes=None):
    """Stand in for the siteverify response."""
    import io
    import json

    body = {'success': success}
    if error_codes:
        body['error-codes'] = error_codes

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return patch('urllib.request.urlopen',
                 return_value=FakeResponse(json.dumps(body).encode()))


class ProviderSelectionTests(TestCase):
    def test_local_is_the_default(self):
        self.assertEqual(captcha_service.active_provider(), captcha_service.LOCAL)

    @override_settings(**KEYS)
    def test_the_switch_changes_the_active_provider(self):
        setting = CaptchaSetting.load()
        setting.provider = CaptchaSetting.Provider.RECAPTCHA
        setting.save()

        self.assertEqual(captcha_service.active_provider(),
                         captcha_service.RECAPTCHA)

    def test_recaptcha_without_keys_degrades_to_local(self):
        """A configuration error must not end in no captcha at all.

        This is not an attacker-selectable weak path: no visitor controls it,
        and the result is a working captcha.
        """
        setting = CaptchaSetting.load()
        setting.provider = CaptchaSetting.Provider.RECAPTCHA
        setting.save()

        self.assertEqual(captcha_service.active_provider(), captcha_service.LOCAL)

    def test_an_unknown_provider_falls_back_to_local(self):
        CaptchaSetting.objects.update_or_create(pk=1, defaults={'provider': 'hcaptcha'})
        self.assertEqual(captcha_service.active_provider(), captcha_service.LOCAL)

    def test_there_is_no_way_to_turn_captcha_off(self):
        """An off switch would be one click from a bulk signup tool."""
        values = set(CaptchaSetting.Provider.values)
        self.assertEqual(values, {'local', 'recaptcha'})

    @override_settings(**KEYS)
    def test_the_field_type_follows_the_switch(self):
        from captcha.fields import CaptchaField

        self.assertIsInstance(captcha_service.captcha_field(), CaptchaField)

        setting = CaptchaSetting.load()
        setting.provider = CaptchaSetting.Provider.RECAPTCHA
        setting.save()

        self.assertIsInstance(captcha_service.captcha_field(),
                              captcha_service.ReCaptchaField)


@override_settings(**KEYS)
class ReCaptchaVerificationTests(TestCase):
    def field(self):
        return captcha_service.ReCaptchaField()

    def test_a_token_google_accepts_passes(self):
        with google_says(True):
            self.assertEqual(self.field().clean('token'), 'token')

    def test_a_token_google_rejects_fails(self):
        with google_says(False, ['invalid-input-response']):
            with self.assertRaises(ValidationError):
                self.field().clean('token')

    def test_an_empty_token_fails_without_calling_google(self):
        with patch('urllib.request.urlopen') as opened:
            with self.assertRaises(ValidationError):
                self.field().clean('')
        opened.assert_not_called()

    def test_an_unreachable_google_rejects_rather_than_allows(self):
        """The most important test here.

        Treating a network failure as success would let anyone bypass the
        captcha by cutting the server off from Google.
        """
        import urllib.error

        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('blocked')):
            with self.assertRaises(ValidationError) as caught:
                self.field().clean('token')

        self.assertEqual(caught.exception.code, 'unreachable')

    def test_a_timeout_also_rejects(self):
        with patch('urllib.request.urlopen', side_effect=OSError('timed out')):
            with self.assertRaises(ValidationError):
                self.field().clean('token')

    def test_a_malformed_google_response_rejects(self):
        import io

        class NotJson(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch('urllib.request.urlopen',
                   return_value=NotJson(b'<html>error</html>')):
            with self.assertRaises(ValidationError):
                self.field().clean('token')

    def test_the_secret_is_sent_not_the_site_key(self):
        """Sending the site key instead of the secret rejects every verification."""
        sent = {}

        def capture(request, **kwargs):
            sent['body'] = request.data.decode()
            import io
            import json

            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R(json.dumps({'success': True}).encode())

        with patch('urllib.request.urlopen', side_effect=capture):
            self.field().clean('token')

        self.assertIn('secret=secret-key', sent['body'])
        self.assertNotIn('site-key', sent['body'])

    def test_the_client_ip_is_forwarded_when_known(self):
        sent = {}

        def capture(request, **kwargs):
            sent['body'] = request.data.decode()
            import io
            import json

            class R(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R(json.dumps({'success': True}).encode())

        field = captcha_service.ReCaptchaField()
        field.remote_ip = '203.0.113.9'

        with patch('urllib.request.urlopen', side_effect=capture):
            field.clean('token')

        self.assertIn('remoteip=203.0.113.9', sent['body'])


@override_settings(**KEYS)
class FormIntegrationTests(TestCase):
    """The switch must reach real forms without a restart."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def use_recaptcha(self):
        setting = CaptchaSetting.load()
        setting.provider = CaptchaSetting.Provider.RECAPTCHA
        setting.save()

    def test_the_registration_page_shows_the_local_captcha_by_default(self):
        response = self.client.get(reverse('register_page'))
        self.assertContains(response, 'name="captcha_1"')
        self.assertNotContains(response, 'g-recaptcha')

    def test_switching_changes_the_form_without_a_restart(self):
        """The class-level field was built once at import and ignored the switch."""
        self.client.get(reverse('register_page'))  # builds the form once
        self.use_recaptcha()

        response = self.client.get(reverse('register_page'))
        self.assertContains(response, 'g-recaptcha')
        self.assertNotContains(response, 'name="captcha_1"')

    def test_the_site_key_is_rendered_but_not_the_secret(self):
        self.use_recaptcha()
        html = self.client.get(reverse('register_page')).content.decode()

        self.assertIn('site-key', html)
        self.assertNotIn('secret-key', html)

    def test_registration_fails_when_google_rejects(self):
        self.use_recaptcha()

        with google_says(False):
            self.client.post(reverse('register_page'), {
                'username': 'bot', 'email': 'bot@example.com',
                'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
                'g-recaptcha-response': 'token',
            })

        self.assertFalse(User.objects.filter(email='bot@example.com').exists())

    def test_registration_succeeds_when_google_accepts(self):
        self.use_recaptcha()

        with google_says(True):
            self.client.post(reverse('register_page'), {
                'username': 'human', 'email': 'human@example.com',
                'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
                'g-recaptcha-response': 'token',
            })

        self.assertTrue(User.objects.filter(email='human@example.com').exists())

    def test_a_local_captcha_answer_is_not_accepted_while_recaptcha_is_active(self):
        """The heart of it: an attacker must not be able to pick the weaker path."""
        from captcha.models import CaptchaStore

        self.client.get(reverse('register_page'))
        store = CaptchaStore.objects.create(
            challenge='ABCDE', response='abcde', hashkey='deadbeef' * 5,
        )
        self.use_recaptcha()

        import urllib.error
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('blocked')):
            self.client.post(reverse('register_page'), {
                'username': 'sneaky', 'email': 'sneaky@example.com',
                'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
                'captcha_0': store.hashkey, 'captcha_1': store.response,
            })

        self.assertFalse(User.objects.filter(email='sneaky@example.com').exists())

    def test_the_guest_upload_form_follows_the_switch_too(self):
        self.use_recaptcha()
        response = self.client.get(reverse('guest_upload_page'))
        self.assertContains(response, 'g-recaptcha')

    def test_the_shortener_form_follows_the_switch_too(self):
        self.use_recaptcha()
        response = self.client.get(reverse('shorten_page'))
        self.assertContains(response, 'g-recaptcha')


class StaffPageTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='boss', email='boss@example.com', password='pw',
            email_active_code='boss123456', is_active=True, is_staff=True,
        )
        self.client.force_login(self.staff)
        self.url = reverse('staff_security')

    def test_the_page_opens(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_normal_user_cannot_open_it(self):
        other = User.objects.create_user(
            username='normal', email='normal@example.com', password='pw',
            email_active_code='normal1234', is_active=True,
        )
        client = self.client_class()
        client.force_login(other)
        self.assertNotEqual(client.get(self.url).status_code, 200)

    def test_a_guest_cannot_open_it(self):
        self.assertNotEqual(self.client_class().get(self.url).status_code, 200)

    @override_settings(**KEYS)
    def test_switching_to_recaptcha_with_keys_works(self):
        self.client.post(self.url, {'provider': 'recaptcha'})
        self.assertEqual(CaptchaSetting.load().provider, 'recaptcha')

    def test_switching_to_recaptcha_without_keys_is_refused(self):
        """Otherwise the operator believes they changed something that did not change."""
        response = self.client.post(self.url, {'provider': 'recaptcha'})

        self.assertEqual(CaptchaSetting.load().provider, 'local')
        self.assertContains(response, 'ست نشده')

    def test_an_invalid_provider_is_refused(self):
        self.client.post(self.url, {'provider': 'none'})
        self.assertEqual(CaptchaSetting.load().provider, 'local')

    def test_switching_back_to_local_always_works(self):
        """The way out when Google becomes unreachable."""
        CaptchaSetting.objects.update_or_create(
            pk=1, defaults={'provider': 'recaptcha'})

        self.client.post(self.url, {'provider': 'local'})
        self.assertEqual(CaptchaSetting.load().provider, 'local')

    def test_it_is_linked_from_the_staff_menu(self):
        html = self.client.get(reverse('staff_overview')).content.decode()
        self.assertIn(f'href="{self.url}"', html)

    def test_the_secret_key_is_never_rendered(self):
        with override_settings(**KEYS):
            html = self.client.get(self.url).content.decode()
        self.assertNotIn('secret-key', html)
