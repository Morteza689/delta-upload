"""Deleting a file from the panel.

The point of these is that a delete removes the bytes and nothing else. A real
delete cascades into Purchase, FileDownload and AdEarning, which would wipe the
records of people who paid and the owner's own unconfirmed earnings.
"""
import os
from datetime import timedelta
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ads_module.models import AdEarning
from upload_module.models import FileDownload, Purchase, UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


def make_user(email='owner@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class FileDeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.file = UploadFile.objects.create(
            name='doomed.bin', author=self.user, slug='deleteme123',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('doomed.bin', b'x' * 500),
            visibility=UploadFile.Visibility.PUBLIC,
        )
        self.url = reverse('file_delete', args=[self.file.pk])

    def test_it_needs_a_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_a_get_only_asks(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        self.file.refresh_from_db()
        # A delete behind a plain link gets fired by prefetching and crawlers.
        self.assertIsNone(self.file.deleted_at)
        self.assertTrue(self.file.file)

    def test_someone_elses_file_cannot_be_deleted(self):
        self.client.force_login(make_user('stranger@example.com'))

        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 404)

        self.file.refresh_from_db()
        self.assertIsNone(self.file.deleted_at)

    def test_posting_removes_the_bytes(self):
        path = self.file.file.path
        self.assertTrue(os.path.exists(path))

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)

        self.file.refresh_from_db()
        self.assertIsNotNone(self.file.deleted_at)
        self.assertFalse(self.file.file)
        self.assertFalse(os.path.exists(path))

    def test_the_row_survives_so_records_keep_something_to_point_at(self):
        self.client.post(self.url)
        self.assertTrue(UploadFile.objects.filter(pk=self.file.pk).exists())

    def test_purchase_records_are_kept(self):
        buyer = make_user('buyer@example.com')
        Purchase.objects.create(buyer=buyer, file=self.file,
                                amount=500, seller_share=350)

        self.client.post(self.url)

        # The buyer paid. Erasing the record would leave no trace of that.
        self.assertEqual(Purchase.objects.filter(file=self.file).count(), 1)

    def test_download_history_is_kept(self):
        FileDownload.objects.create(file=self.file, bytes_sent=500)
        self.client.post(self.url)
        self.assertEqual(FileDownload.objects.filter(file=self.file).count(), 1)

    def test_unconfirmed_earnings_are_kept(self):
        AdEarning.objects.create(
            user=self.user, file=self.file, micro_cents=10_000,
            status=AdEarning.Status.PENDING,
            available_at=timezone.now() + timedelta(days=3),
        )
        self.client.post(self.url)

        # These are still owed to the owner and are moved by the confirm
        # command later; deleting a file must not throw them away.
        self.assertEqual(AdEarning.objects.filter(file=self.file).count(), 1)

    def test_it_releases_the_quota(self):
        from account_module import quota

        self.assertEqual(quota.used_by(self.user), 500)
        self.client.post(self.url)
        # Nothing is stored any more, so nothing should be charged for.
        self.assertEqual(quota.used_by(self.user), 0)

    def test_a_deleted_file_is_no_longer_public(self):
        self.assertEqual(self.client.get(f'/dl/{self.file.slug}').status_code, 200)
        self.client.post(self.url)
        self.assertEqual(self.client.get(f'/dl/{self.file.slug}').status_code, 404)

    def test_a_deleted_file_leaves_the_list(self):
        self.client.post(self.url)
        response = self.client.get(reverse('my_files_page'))
        self.assertNotContains(response, 'doomed.bin')

    def test_it_cannot_be_deleted_twice(self):
        self.client.post(self.url)
        # The second attempt has nothing to act on, and a page offering to
        # delete an already deleted file is just confusing.
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_the_owner_download_stops_working(self):
        self.client.post(self.url)
        response = self.client.get(
            reverse('owner_download', args=[self.file.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_confirmation_counts_what_is_at_stake(self):
        buyer = make_user('buyer@example.com')
        Purchase.objects.create(buyer=buyer, file=self.file,
                                amount=500, seller_share=350)

        response = self.client.get(self.url)
        self.assertEqual(response.context['purchase_count'], 1)
        # Told before, not discovered from a support ticket afterwards.
        self.assertContains(response, 'خریده')

    def test_the_list_offers_delete(self):
        response = self.client.get(reverse('my_files_page'))
        self.assertContains(response, self.url)
