"""Public API tests.

Mostly about isolation: one site's key must never reach another site's files.
"""
import json
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from upload_module.models import UploadFile

from .models import ApiKey

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()

FILES_URL = '/api/v1/files/'
ME_URL = '/api/v1/me/'


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ApiTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.user = User.objects.create_user(
            username='site1', email='site1@example.com', password='pw',
            email_active_code='s1' * 5,
        )
        self.key, self.raw_key = ApiKey.generate(self.user, 'site1.com')

    def auth(self, raw_key=None):
        return {'HTTP_AUTHORIZATION': f'Bearer {raw_key or self.raw_key}'}

    def upload(self, name='demo.bin', body=b'hello world', **extra):
        payload = {'file': SimpleUploadedFile(name, body)}
        payload.update(extra)
        return self.client.post(FILES_URL, payload, **self.auth())


class KeyGenerationTests(ApiTestCase):
    def test_raw_key_is_not_stored(self):
        """A database leak must not hand over usable keys."""
        self.assertNotIn(self.raw_key, self.key.key_hash)
        self.assertFalse(
            ApiKey.objects.filter(key_hash__contains=self.raw_key).exists()
        )

    def test_key_has_the_expected_shape(self):
        # maxsplit=2 because the random part can contain underscores, which is
        # exactly what makes some keys fail silently if parsing ignores it.
        parts = self.raw_key.split('_', 2)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], 'dup')
        self.assertEqual(parts[1], self.key.prefix)
        self.assertTrue(parts[2])

    def test_authenticate_accepts_the_real_key(self):
        self.assertEqual(ApiKey.authenticate(self.raw_key), self.key)

    def test_authenticate_rejects_a_tampered_key(self):
        tampered = self.raw_key[:-1] + ('x' if self.raw_key[-1] != 'x' else 'y')
        self.assertIsNone(ApiKey.authenticate(tampered))

    def test_authenticate_rejects_a_revoked_key(self):
        self.key.revoke()
        self.assertIsNone(ApiKey.authenticate(self.raw_key))

    def test_authenticate_rejects_an_inactive_user(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertIsNone(ApiKey.authenticate(self.raw_key))

    def test_two_keys_never_collide(self):
        keys = {ApiKey.generate(self.user, f'k{i}')[1] for i in range(20)}
        self.assertEqual(len(keys), 20)

    def test_a_secret_containing_underscores_still_authenticates(self):
        """The random part of a key can contain underscores.

        If key parsing ignores that, a fraction of keys fail at random: a
        breakage that hits only some clients and is painful to trace.
        """
        key = ApiKey.objects.create(
            user=self.user, name='underscored', prefix='abcd1234',
            key_hash='',
        )
        raw = 'dup_abcd1234_aa_bb_cc-dd'
        from api_module.models import _hash
        ApiKey.objects.filter(pk=key.pk).update(key_hash=_hash(raw))

        self.assertEqual(ApiKey.authenticate(raw), key)

    def test_every_generated_key_authenticates(self):
        """Fifty keys in a row, to cover the random cases."""
        for index in range(50):
            _key, raw = ApiKey.generate(self.user, f'gen{index}')
            self.assertIsNotNone(ApiKey.authenticate(raw), raw)


class AuthenticationTests(ApiTestCase):
    def test_request_without_a_key_is_rejected(self):
        response = self.client.get(FILES_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'missing_credentials')

    def test_request_with_a_bad_key_is_rejected(self):
        response = self.client.get(FILES_URL, **self.auth('dup_dead_beef'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'invalid_credentials')

    def test_revoked_key_stops_working_immediately(self):
        self.assertEqual(self.client.get(FILES_URL, **self.auth()).status_code, 200)
        self.key.revoke()
        self.assertEqual(self.client.get(FILES_URL, **self.auth()).status_code, 401)

    def test_x_api_key_header_also_works(self):
        response = self.client.get(FILES_URL, HTTP_X_API_KEY=self.raw_key)
        self.assertEqual(response.status_code, 200)

    def test_using_a_key_records_last_used(self):
        self.assertIsNone(self.key.last_used_at)
        self.client.get(FILES_URL, **self.auth())
        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)


class UploadTests(ApiTestCase):
    def test_upload_returns_the_download_page_url(self):
        response = self.upload(visibility='public')
        self.assertEqual(response.status_code, 201)

        body = response.json()
        upload_file = UploadFile.objects.get(slug=body['slug'])
        self.assertEqual(upload_file.author, self.user)
        # What the client site receives is the download page, not the raw file: the
        # ad and the revenue counting happen there.
        self.assertTrue(body['download_page_url'].endswith(f"/dl/{body['slug']}"))

    def test_upload_defaults_to_private(self):
        response = self.upload()
        self.assertEqual(response.json()['visibility'], 'private')

    def test_upload_without_a_file_is_rejected(self):
        response = self.client.post(FILES_URL, {'name': 'x'}, **self.auth())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'missing_file')

    def test_upload_accepts_a_public_window(self):
        response = self.upload(visibility='public', public_for_hours='5')
        self.assertIsNotNone(response.json()['public_until'])

    def test_upload_rejects_a_negative_price(self):
        response = self.upload(visibility='public', price='-5')
        self.assertEqual(response.status_code, 422)
        self.assertIn('price', response.json()['error']['fields'])

    def test_a_private_file_cannot_be_priced(self):
        response = self.upload(visibility='private', price='500')
        self.assertEqual(response.status_code, 422)
        self.assertIn('price', response.json()['error']['fields'])

    def test_invalid_visibility_is_rejected(self):
        response = self.upload(visibility='sometimes')
        self.assertEqual(response.status_code, 422)

    def test_slug_is_not_derived_from_the_filename(self):
        """The slug must be unguessable, or a private file is found by its name."""
        response = self.upload(name='secret-report.pdf')
        self.assertNotIn('secret', response.json()['slug'])


class IsolationTests(ApiTestCase):
    """One site's key must not reach another site's files."""

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(
            username='site2', email='site2@example.com', password='pw',
            email_active_code='s2' * 5,
        )
        _key, self.other_raw = ApiKey.generate(self.other, 'site2.com')
        self.other_file = UploadFile.objects.create(
            name='theirs', author=self.other, slug='theirslug123',
            file=SimpleUploadedFile('theirs.bin', b'private'),
        )

    def test_listing_only_shows_own_files(self):
        self.upload()
        body = self.client.get(FILES_URL, **self.auth()).json()
        slugs = {item['slug'] for item in body['results']}
        self.assertNotIn(self.other_file.slug, slugs)

    def test_reading_another_sites_file_is_404(self):
        response = self.client.get(
            f'/api/v1/files/{self.other_file.slug}/', **self.auth(),
        )
        self.assertEqual(response.status_code, 404)

    def test_deleting_another_sites_file_is_404(self):
        response = self.client.delete(
            f'/api/v1/files/{self.other_file.slug}/', **self.auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(UploadFile.objects.filter(pk=self.other_file.pk).exists())

    def test_patching_another_sites_file_is_404(self):
        response = self.client.patch(
            f'/api/v1/files/{self.other_file.slug}/',
            data=json.dumps({'visibility': 'public'}),
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(response.status_code, 404)


class DetailTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.slug = self.upload(visibility='public').json()['slug']

    def test_detail_returns_the_file(self):
        response = self.client.get(f'/api/v1/files/{self.slug}/', **self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['slug'], self.slug)

    def test_patch_updates_price_and_visibility(self):
        response = self.client.patch(
            f'/api/v1/files/{self.slug}/',
            data=json.dumps({'price': 250, 'visibility': 'public'}),
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['price'], 250)
        self.assertFalse(response.json()['is_free'])

    def test_patch_can_make_a_file_private(self):
        self.client.patch(
            f'/api/v1/files/{self.slug}/',
            data=json.dumps({'visibility': 'private'}),
            content_type='application/json', **self.auth(),
        )
        upload_file = UploadFile.objects.get(slug=self.slug)
        self.assertFalse(upload_file.is_public_now())
        # And its download page closes immediately.
        self.assertEqual(self.client.get(f'/dl/{self.slug}').status_code, 404)

    def test_delete_removes_the_file(self):
        response = self.client.delete(f'/api/v1/files/{self.slug}/', **self.auth())
        self.assertEqual(response.status_code, 204)
        self.assertFalse(UploadFile.objects.filter(slug=self.slug).exists())

    def test_malformed_json_gives_a_clear_error(self):
        response = self.client.patch(
            f'/api/v1/files/{self.slug}/', data='{not json',
            content_type='application/json', **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'invalid_json')


class PaginationTests(ApiTestCase):
    def test_listing_is_paginated(self):
        for index in range(5):
            self.upload(name=f'f{index}.bin')

        body = self.client.get(f'{FILES_URL}?limit=2', **self.auth()).json()
        self.assertEqual(body['count'], 5)
        self.assertEqual(len(body['results']), 2)

    def test_limit_is_capped(self):
        body = self.client.get(f'{FILES_URL}?limit=9999', **self.auth()).json()
        self.assertLessEqual(body['limit'], 100)

    def test_bad_pagination_is_rejected(self):
        response = self.client.get(f'{FILES_URL}?limit=abc', **self.auth())
        self.assertEqual(response.status_code, 400)


class MeTests(ApiTestCase):
    def test_me_returns_account_info(self):
        body = self.client.get(ME_URL, **self.auth()).json()
        self.assertEqual(body['email'], self.user.email)
        self.assertEqual(body['api_key']['prefix'], self.key.prefix)
        # The raw key never appears in a response.
        self.assertNotIn(self.raw_key, json.dumps(body))


@override_settings(RATELIMIT_ENABLE=True, MEDIA_ROOT=MEDIA_ROOT)
class ApiRateLimitTests(ApiTestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()

    def test_uploads_are_capped_per_key(self):
        for index in range(60):
            self.upload(name=f'f{index}.bin')
        response = self.upload(name='over.bin')
        self.assertEqual(response.status_code, 429)

    def test_the_cap_is_per_key_not_per_ip(self):
        """Two sites behind one address must not eat each other's quota."""
        other = User.objects.create_user(
            username='site3', email='site3@example.com', password='pw',
            email_active_code='s3' * 5,
        )
        _key, other_raw = ApiKey.generate(other, 'site3.com')

        for index in range(60):
            self.upload(name=f'f{index}.bin')
        self.assertEqual(self.upload().status_code, 429)

        response = self.client.post(
            FILES_URL, {'file': SimpleUploadedFile('ok.bin', b'x')},
            HTTP_AUTHORIZATION=f'Bearer {other_raw}',
        )
        self.assertEqual(response.status_code, 201)


class PanelTests(ApiTestCase):
    def test_key_page_requires_login(self):
        response = self.client.get(reverse('api_keys_page'))
        self.assertEqual(response.status_code, 302)

    def test_creating_a_key_shows_it_once(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('api_keys_page'), {'name': 'newsite.com'})
        self.assertEqual(response.status_code, 200)

        key = ApiKey.objects.filter(name='newsite.com').first()
        self.assertIsNotNone(key)
        self.assertContains(response, key.prefix)

        # Refreshing the page no longer reveals the key.
        again = self.client.get(reverse('api_keys_page'))
        self.assertNotContains(again, 'dup_')

    def test_revoking_from_the_panel_works(self):
        self.client.force_login(self.user)
        self.client.post(reverse('api_keys_page'), {
            'action': 'revoke', 'key_id': self.key.pk,
        })
        self.key.refresh_from_db()
        self.assertFalse(self.key.is_active)

    def test_cannot_revoke_someone_elses_key(self):
        other = User.objects.create_user(
            username='site4', email='site4@example.com', password='pw',
            email_active_code='s4' * 5,
        )
        other_key, _raw = ApiKey.generate(other, 'other')

        self.client.force_login(self.user)
        response = self.client.post(reverse('api_keys_page'), {
            'action': 'revoke', 'key_id': other_key.pk,
        })
        self.assertEqual(response.status_code, 404)
        other_key.refresh_from_db()
        self.assertTrue(other_key.is_active)
