"""Link shortener tests.

Mostly about abuse: an open shortener left unguarded gets the domain
blacklisted.
"""
import json

from captcha.models import CaptchaStore
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from api_module.models import ApiKey

from .models import RESERVED, ShortLink, generate_code

User = get_user_model()


def make_user(name='linker'):
    return User.objects.create_user(
        username=name, email=f'{name}@example.com', password='pw',
        email_active_code=name * 3, is_active=True,
    )


class CodeTests(TestCase):
    def test_codes_are_unique_across_many_links(self):
        codes = {generate_code() for _ in range(500)}
        self.assertGreater(len(codes), 490)

    def test_codes_avoid_ambiguous_characters(self):
        """Codes get read aloud and typed, so 0/O and 1/l must not be confusable."""
        sample = ''.join(generate_code() for _ in range(200))
        for char in '0O1lI':
            self.assertNotIn(char, sample)

    def test_reserved_words_are_never_used(self):
        link = ShortLink.create_for('https://example.com')
        self.assertNotIn(link.code.lower(), RESERVED)


class ShortenPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def solve_captcha(self):
        self.client.get(reverse('shorten_page'))
        store = CaptchaStore.objects.last()
        return {'captcha_0': store.hashkey, 'captcha_1': store.response}

    def test_the_page_is_public(self):
        """Shortening a link must not require an account."""
        response = self.client.get(reverse('shorten_page'))
        self.assertEqual(response.status_code, 200)

    def test_a_guest_can_create_a_link(self):
        data = {'target': 'https://example.com/very/long'}
        data.update(self.solve_captcha())
        response = self.client.post(reverse('shorten_page'), data)

        self.assertEqual(response.status_code, 200)
        link = ShortLink.objects.get()
        self.assertEqual(link.target, 'https://example.com/very/long')
        self.assertIsNone(link.owner)
        self.assertContains(response, link.code)

    def test_a_guest_without_captcha_is_refused(self):
        """Without a captcha this becomes a bulk anonymous link generator."""
        self.client.post(reverse('shorten_page'), {'target': 'https://example.com'})
        self.assertFalse(ShortLink.objects.exists())

    def test_a_logged_in_user_needs_no_captcha(self):
        self.client.force_login(make_user())
        self.client.post(reverse('shorten_page'), {'target': 'https://example.com'})

        link = ShortLink.objects.get()
        self.assertIsNotNone(link.owner)

    def test_javascript_urls_are_rejected(self):
        """Otherwise our domain becomes an XSS vector."""
        self.client.force_login(make_user())
        self.client.post(reverse('shorten_page'), {'target': 'javascript:alert(1)'})
        self.assertFalse(ShortLink.objects.exists())

    def test_data_urls_are_rejected(self):
        self.client.force_login(make_user())
        self.client.post(reverse('shorten_page'),
                         {'target': 'data:text/html,<script>alert(1)</script>'})
        self.assertFalse(ShortLink.objects.exists())

    def test_shortening_our_own_domain_is_rejected(self):
        """Otherwise an endless redirect loop can be built."""
        self.client.force_login(make_user())
        self.client.post(reverse('shorten_page'),
                         {'target': 'http://testserver/dl/something'})
        self.assertFalse(ShortLink.objects.exists())


class RedirectTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.link = ShortLink.create_for('https://example.com/target')

    def test_the_short_link_redirects(self):
        response = self.client.get(self.link.get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/target')

    def test_the_redirect_is_temporary(self):
        """A 301 is cached, after which deactivating the link has no effect."""
        response = self.client.get(self.link.get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(getattr(response, 'permanent', False))

    def test_clicks_are_counted(self):
        for _ in range(3):
            self.client.get(self.link.get_absolute_url())

        self.link.refresh_from_db()
        self.assertEqual(self.link.clicks, 3)
        self.assertIsNotNone(self.link.last_click_at)

    def test_an_inactive_link_reports_gone(self):
        """410 rather than 404: it existed and no longer does, and the visitor
        should be told why.
        """
        ShortLink.objects.filter(pk=self.link.pk).update(is_active=False)
        response = self.client.get(self.link.get_absolute_url())
        self.assertEqual(response.status_code, 410)

    def test_an_expired_link_reports_gone(self):
        from datetime import timedelta

        from django.utils import timezone
        ShortLink.objects.filter(pk=self.link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        response = self.client.get(self.link.get_absolute_url())
        self.assertEqual(response.status_code, 410)

    def test_a_code_that_never_existed_is_404(self):
        """Nothing was ever here, so 404 is right."""
        self.assertEqual(self.client.get('/s/neverexisted').status_code, 404)

    def test_an_unknown_code_is_404(self):
        self.assertEqual(self.client.get('/s/doesnotexist').status_code, 404)

    def test_preview_shows_the_target_without_redirecting(self):
        response = self.client.get(
            reverse('short_link_preview', args=[self.link.code]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'example.com/target')

        self.link.refresh_from_db()
        self.assertEqual(self.link.clicks, 0)


class LinkApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('apiuser')
        self.key, self.raw = ApiKey.generate(self.user, 'site')

    def auth(self, raw=None):
        return {'HTTP_AUTHORIZATION': f'Bearer {raw or self.raw}'}

    def test_creating_a_link_through_the_api(self):
        response = self.client.post(
            '/api/v1/links/',
            data=json.dumps({'target': 'https://example.com/api'}),
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(response.status_code, 201)

        payload = response.json()
        self.assertIn('short_url', payload)
        self.assertTrue(payload['short_url'].endswith('/s/' + payload['code']))
        self.assertEqual(ShortLink.objects.get().owner, self.user)

    def test_form_encoded_also_works(self):
        response = self.client.post(
            '/api/v1/links/', {'target': 'https://example.com/form'}, **self.auth())
        self.assertEqual(response.status_code, 201)

    def test_the_api_requires_a_key(self):
        response = self.client.post(
            '/api/v1/links/', {'target': 'https://example.com'})
        self.assertEqual(response.status_code, 401)

    def test_a_bad_target_is_rejected(self):
        response = self.client.post(
            '/api/v1/links/', {'target': 'javascript:alert(1)'}, **self.auth())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['error']['code'], 'invalid_target')

    def test_a_missing_target_is_rejected(self):
        response = self.client.post('/api/v1/links/', {}, **self.auth())
        self.assertEqual(response.status_code, 400)

    def test_listing_only_shows_own_links(self):
        ShortLink.create_for('https://mine.example', owner=self.user)
        other = make_user('other')
        ShortLink.create_for('https://theirs.example', owner=other)

        body = self.client.get('/api/v1/links/', **self.auth()).json()
        targets = {item['target'] for item in body['results']}
        self.assertIn('https://mine.example', targets)
        self.assertNotIn('https://theirs.example', targets)

    def test_reading_another_users_link_is_404(self):
        other = make_user('other2')
        link = ShortLink.create_for('https://theirs.example', owner=other)

        response = self.client.get(f'/api/v1/links/{link.code}/', **self.auth())
        self.assertEqual(response.status_code, 404)

    def test_deleting_own_link(self):
        link = ShortLink.create_for('https://mine.example', owner=self.user)
        response = self.client.delete(f'/api/v1/links/{link.code}/', **self.auth())

        self.assertEqual(response.status_code, 204)
        self.assertFalse(ShortLink.objects.filter(pk=link.pk).exists())

    def test_deleting_another_users_link_is_404(self):
        other = make_user('other3')
        link = ShortLink.create_for('https://theirs.example', owner=other)

        response = self.client.delete(f'/api/v1/links/{link.code}/', **self.auth())
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ShortLink.objects.filter(pk=link.pk).exists())


@override_settings(RATELIMIT_ENABLE=True)
class ShortenerRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_guests_cannot_create_links_without_limit(self):
        self.client.force_login(make_user())
        for _ in range(20):
            self.client.post(reverse('shorten_page'),
                             {'target': 'https://example.com/x'})
        response = self.client.post(reverse('shorten_page'),
                                    {'target': 'https://example.com/x'})
        self.assertEqual(response.status_code, 429)
