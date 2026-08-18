import os
import shutil
import tempfile
from datetime import timedelta
from importlib import reload
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve, reverse
from django.utils import timezone

from .models import DownloadLink, UploadFile

MEDIA_ROOT = tempfile.mkdtemp()
FILE_BODY = b'payload-' + b'x' * 5000

User = get_user_model()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class ProtectedDownloadTests(TestCase):
    """Uploaded files must be reachable only through the controlled routes."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@example.com',
            password='pw', email_active_code='a' * 10,
        )
        self.stranger = User.objects.create_user(
            username='stranger', email='stranger@example.com',
            password='pw', email_active_code='b' * 10,
        )
        self.upload = UploadFile.objects.create(
            name='sample', author=self.owner, slug='abc12',
            file=SimpleUploadedFile('sample.bin', FILE_BODY),
        )

    def _token_for(self, minutes=15):
        return DownloadLink.objects.create(
            file=self.upload,
            expires_at=timezone.now() + timedelta(minutes=minutes),
        )

    # --- Direct media access ---

    def test_uploaded_files_are_not_reachable_through_media_url(self):
        """Even with DEBUG on there must be no route to dl/files."""
        import DUploaderProject.urls as project_urls

        with override_settings(DEBUG=True):
            reload(project_urls)
            clear_url_caches()
            try:
                with self.assertRaises(Resolver404):
                    resolve('/medias/dl/files/sample.bin')
                # Public site assets are still served in development.
                self.assertTrue(resolve('/medias/images/logo.png'))
            finally:
                reload(project_urls)
                clear_url_caches()

    # --- Owner download ---

    def test_owner_can_download_own_file(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('owner_download', args=[self.upload.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), FILE_BODY)

    def test_stranger_cannot_download_someone_elses_file(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse('owner_download', args=[self.upload.pk]))
        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse('owner_download', args=[self.upload.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/user/login/', response['Location'])

    def test_owner_download_does_not_count_as_a_download(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('owner_download', args=[self.upload.pk]))
        b''.join(response.streaming_content)
        self.upload.refresh_from_db()
        self.assertEqual(self.upload.number_of_download, 0)

    # --- Token download ---

    def test_valid_token_streams_the_file(self):
        link = self._token_for()
        response = self.client.get(reverse('download_file', args=[link.token]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), FILE_BODY)

    def test_token_is_burned_and_counted_after_a_complete_download(self):
        link = self._token_for()
        response = self.client.get(reverse('download_file', args=[link.token]))
        b''.join(response.streaming_content)

        link.refresh_from_db()
        self.upload.refresh_from_db()
        self.assertTrue(link.is_used)
        self.assertEqual(self.upload.number_of_download, 1)

    def test_token_survives_a_download_that_was_never_read(self):
        """The link must not be burned before the file is actually delivered."""
        link = self._token_for()
        self.client.get(reverse('download_file', args=[link.token]))

        link.refresh_from_db()
        self.upload.refresh_from_db()
        self.assertFalse(link.is_used)
        self.assertEqual(self.upload.number_of_download, 0)

    def test_used_token_is_rejected(self):
        link = self._token_for()
        DownloadLink.objects.filter(pk=link.pk).update(is_used=True)
        response = self.client.get(reverse('download_file', args=[link.token]))
        self.assertEqual(response.status_code, 404)

    def test_expired_token_is_rejected(self):
        link = self._token_for(minutes=-1)
        response = self.client.get(reverse('download_file', args=[link.token]))
        self.assertEqual(response.status_code, 404)

    # --- S3 path ---

    @override_settings(USE_S3_PRESIGNED_DOWNLOADS=True, S3_DOWNLOAD_URL_EXPIRE=60)
    def test_s3_mode_redirects_to_a_signed_url_instead_of_streaming(self):
        link = self._token_for()
        calls = {}

        class StubS3Storage:
            def size(self, name):
                return len(FILE_BODY)

            def url(self, name, parameters=None, expire=None):
                calls['name'] = name
                calls['parameters'] = parameters
                calls['expire'] = expire
                return 'https://bucket.example.com/signed?sig=abc'

        with mock.patch.object(
            UploadFile._meta.get_field('file'), 'storage', StubS3Storage()
        ):
            response = self.client.get(reverse('download_file', args=[link.token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://bucket.example.com/signed?sig=abc')
        self.assertEqual(calls['expire'], 60)
        stored_name = os.path.basename(self.upload.file.name)
        self.assertIn(stored_name, calls['parameters']['ResponseContentDisposition'])

        # Delivery leaves our control, so the accounting happens when the link is
        # issued.
        link.refresh_from_db()
        self.upload.refresh_from_db()
        self.assertTrue(link.is_used)
        self.assertEqual(self.upload.number_of_download, 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class FileListingTests(TestCase):
    """The file list must not show another user's files."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner2', email='owner2@example.com',
            password='pw', email_active_code='c' * 10,
        )
        self.stranger = User.objects.create_user(
            username='stranger2', email='stranger2@example.com',
            password='pw', email_active_code='d' * 10,
        )
        UploadFile.objects.create(
            name='secret-of-owner', author=self.owner, slug='sec01',
            file=SimpleUploadedFile('secret.bin', b'nope'),
        )

    def test_user_email_query_param_no_longer_leaks_other_users_files(self):
        self.client.force_login(self.stranger)
        response = self.client.get(
            reverse('myfile_page'), {'user_email': 'owner2@example.com'}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'secret-of-owner')

    def test_listing_requires_login(self):
        response = self.client.get(reverse('myfile_page'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/user/login/', response['Location'])
