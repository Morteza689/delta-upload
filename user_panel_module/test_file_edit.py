"""Editing a file's details from the panel."""
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


def make_user(email='owner@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class FileEditTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.file = UploadFile.objects.create(
            name='old name', description='old', author=self.user,
            slug='editme12345', type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('a.bin', b'x' * 100),
            visibility=UploadFile.Visibility.PRIVATE,
        )
        self.url = reverse('file_edit', args=[self.file.pk])

    def _post(self, **extra):
        data = {
            'name': 'new name',
            'description': 'new',
            'visibility': UploadFile.Visibility.PRIVATE,
        }
        data.update(extra)
        return self.client.post(self.url, data)

    def test_the_page_needs_a_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_the_owner_sees_the_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'old name')

    def test_someone_elses_file_is_not_editable(self):
        stranger = make_user('stranger@example.com')
        self.client.force_login(stranger)

        # 404 rather than 403: the error must not confirm the file exists.
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self._post().status_code, 404)

        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'old name')

    def test_saving_changes_the_details(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)

        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'new name')
        self.assertEqual(self.file.description, 'new')

    def test_the_file_itself_cannot_be_replaced(self):
        response = self.client.get(self.url)
        # Swapping the bytes under a slug already shared would hand a different
        # file to everyone holding the link.
        self.assertNotContains(response, 'type="file"')

        stored = self.file.file.name
        self.client.post(self.url, {
            'name': 'new name', 'description': 'new',
            'visibility': UploadFile.Visibility.PRIVATE,
            'file': SimpleUploadedFile('evil.bin', b'y' * 10),
        })
        self.file.refresh_from_db()
        self.assertEqual(self.file.file.name, stored)

    def test_the_slug_never_changes(self):
        self._post()
        self.file.refresh_from_db()
        # A shared link has to keep working.
        self.assertEqual(self.file.slug, 'editme12345')

    def test_publishing_can_be_turned_on(self):
        self._post(visibility=UploadFile.Visibility.PUBLIC)
        self.file.refresh_from_db()
        self.assertTrue(self.file.is_public_now())

    def test_a_private_file_cannot_carry_a_price(self):
        response = self._post(price='1.50')
        self.assertEqual(response.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.price, 0)

    def test_a_public_file_can_be_priced(self):
        self._post(visibility=UploadFile.Visibility.PUBLIC, price='2.50')
        self.file.refresh_from_db()
        self.assertEqual(self.file.price, 250)

    def test_the_current_window_is_prefilled(self):
        self.file.visibility = UploadFile.Visibility.PUBLIC
        self.file.public_until = timezone.now() + timedelta(hours=5)
        self.file.save()

        response = self.client.get(self.url)
        # Prefilled so saving without touching the field keeps roughly the end
        # time it had, rather than silently removing the limit.
        self.assertEqual(response.context['form']['public_for_hours'].value(), 5)

    def test_a_file_with_no_window_prefills_empty(self):
        response = self.client.get(self.url)
        self.assertIsNone(response.context['form']['public_for_hours'].value())

    def test_an_expired_window_prefills_empty(self):
        self.file.visibility = UploadFile.Visibility.PUBLIC
        self.file.public_until = timezone.now() - timedelta(hours=1)
        self.file.save()

        response = self.client.get(self.url)
        self.assertIsNone(response.context['form']['public_for_hours'].value())

    def test_the_window_can_be_set(self):
        self._post(visibility=UploadFile.Visibility.PUBLIC,
                   public_for_hours='3')
        self.file.refresh_from_db()
        self.assertIsNotNone(self.file.public_until)

    def test_a_private_file_cannot_have_a_window(self):
        response = self._post(public_for_hours='3')
        self.assertEqual(response.status_code, 200)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.public_until)

    def test_the_quota_is_not_re_checked_on_an_edit(self):
        # Nothing new is stored, so a full quota must not block renaming.
        with override_settings(FREE_STORAGE_BYTES=1):
            response = self._post()
        self.assertEqual(response.status_code, 302)

    def test_the_telegram_option_is_absent(self):
        response = self.client.get(self.url)
        # Where a file is stored is decided at upload; offering it here would
        # imply an edit can move the bytes, which it cannot.
        self.assertNotContains(response, 'store_on_telegram')

    def test_the_list_links_to_edit_and_download(self):
        response = self.client.get(reverse('my_files_page'))
        self.assertContains(response, reverse('file_edit', args=[self.file.pk]))
        self.assertContains(response,
                            reverse('owner_download', args=[self.file.pk]))
