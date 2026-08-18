"""What a download arrives called.

Storage sanitises filenames and, when one is long, truncates it and splices in
a random suffix so the path stays unique. That is right for a file on disk and
wrong for the name the visitor sees.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from upload_module.downloads import download_filename
from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()

LONG_NAME = ('Elden Ring Save File Manager 1.8.0 - Windows 9271 1.8.0 '
             '2026-07-31T17-15Z RbQTOYubl.zip')


def make_user(email='owner@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class DownloadNameTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def _upload(self, filename, title=''):
        self.client.post(reverse('upload_page'), {
            'name': title, 'description': '',
            'visibility': UploadFile.Visibility.PRIVATE,
            'file': SimpleUploadedFile(filename, b'x' * 100),
        })
        return UploadFile.objects.order_by('-id').first()

    def test_a_long_name_with_spaces_comes_back_intact(self):
        upload = self._upload(LONG_NAME)
        self.assertEqual(download_filename(upload), LONG_NAME)

    def test_the_stored_path_may_differ_from_the_download_name(self):
        upload = self._upload(LONG_NAME)
        # Storage is free to rewrite the path; the header must not follow it.
        self.assertNotEqual(upload.file.name, download_filename(upload))

    def test_a_display_title_does_not_replace_the_filename(self):
        upload = self._upload('report.pdf', title='گزارش فصلی')
        # The title has no extension; using it would lose the file type.
        self.assertEqual(download_filename(upload), 'report.pdf')

    def test_the_content_disposition_carries_it(self):
        upload = self._upload(LONG_NAME)
        response = self.client.get(reverse('owner_download', args=[upload.pk]))

        disposition = response['Content-Disposition']
        self.assertIn('filename*=UTF-8', disposition)
        # The percent-encoded form is the one a browser actually uses.
        self.assertIn('Elden%20Ring', disposition)

    def test_a_persian_filename_survives(self):
        name = 'گزارش سالانه ۱۴۰۴.pdf'
        upload = self._upload(name)
        self.assertEqual(download_filename(upload), name)

    def test_a_row_from_before_the_field_falls_back_to_storage(self):
        upload = self._upload('legacy.bin')
        UploadFile.objects.filter(pk=upload.pk).update(original_filename='')
        upload.refresh_from_db()

        # Older rows have only the storage name, which is still better than
        # the title.
        self.assertTrue(download_filename(upload).startswith('legacy'))

    def test_a_row_with_nothing_left_still_gets_a_name(self):
        upload = UploadFile.objects.create(
            name='', author=self.user, slug='bare12345678',
            type_file=UploadFile.ChoiceType.File,
        )
        # Never an empty header: browsers then invent their own name.
        self.assertEqual(download_filename(upload), 'download')

    def test_a_quote_in_the_name_cannot_rewrite_the_header(self):
        from upload_module.downloads import _attachment_value

        value = _attachment_value('evil".bin')
        # A quote would close the filename parameter and let the rest of the
        # name inject header syntax.
        self.assertNotIn('evil"', value)
