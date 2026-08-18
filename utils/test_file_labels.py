"""A file is never shown to a person as a slug.

A slug is twelve random characters. It is correct and unique and tells the
owner nothing about which of their files it is, which is what the earnings
table was doing before these tests.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ads_module.models import AdEarning
from utils.templatetags.dup_filters import file_label
from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


def make_user(email='owner@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class FileLabelTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def make_file(self, title='', filename='holiday-photos.zip'):
        return UploadFile.objects.create(
            name=title, author=self.user, slug='abcdefgh1234',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile(filename, b'x' * 50),
            original_filename=filename,
        )

    def test_the_title_wins_when_there_is_one(self):
        upload = self.make_file(title='عکس‌های سفر')
        self.assertEqual(file_label(upload), 'عکس‌های سفر')

    def test_without_a_title_it_uses_the_filename(self):
        upload = self.make_file()
        # Leaving the title blank is the usual case, and the slug is useless
        # to the person reading it.
        self.assertEqual(file_label(upload), 'holiday-photos.zip')

    def test_it_falls_back_to_the_slug_only_as_a_last_resort(self):
        upload = UploadFile.objects.create(
            name='', author=self.user, slug='lastresort12',
            type_file=UploadFile.ChoiceType.File,
        )
        self.assertEqual(file_label(upload), 'lastresort12')

    def test_a_telegram_file_keeps_its_name(self):
        upload = self.make_file(filename='installer.exe')
        UploadFile.objects.filter(pk=upload.pk).update(
            storage_backend=UploadFile.StorageBackend.TELEGRAM, file='',
        )
        upload.refresh_from_db()
        # The file field is empty once the transfer finishes; reading the name
        # from it would show nothing at all.
        self.assertEqual(file_label(upload), 'installer.exe')

    def test_none_does_not_raise(self):
        self.assertEqual(file_label(None), '')


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=10 ** 9)
class EarningsTableTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.upload = UploadFile.objects.create(
            name='', author=self.user, slug='fhfgEc6oJNqm',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('game-save.zip', b'x' * 50),
            original_filename='game-save.zip',
        )
        self.earning = AdEarning.objects.create(
            user=self.user, file=self.upload, micro_cents=1200, impressions=2,
            available_at=timezone.now() + timezone.timedelta(days=3),
        )

    def test_the_row_names_the_file_not_the_slug(self):
        self.assertEqual(self.earning.source_label, 'game-save.zip')

    def test_the_row_links_to_the_files_report(self):
        self.assertEqual(self.earning.source_url,
                         reverse('file_report', args=[self.upload.pk]))

    def test_the_page_shows_the_name_and_the_link(self):
        response = self.client.get(reverse('earnings_page'))
        self.assertContains(response, 'game-save.zip')
        self.assertContains(response, reverse('file_report',
                                              args=[self.upload.pk]))

    def test_the_page_no_longer_shows_the_slug(self):
        response = self.client.get(reverse('earnings_page'))
        self.assertNotContains(response, 'fhfgEc6oJNqm')

    def test_a_deleted_file_is_named_but_not_linked(self):
        self.upload.soft_delete()
        self.earning.refresh_from_db()

        # The earning is still owed, so the row stays; its report page is gone.
        self.assertEqual(self.earning.source_label, 'game-save.zip')
        self.assertEqual(self.earning.source_url, '')

    def test_shortener_earnings_are_named_too(self):
        from link_module.models import ShortLink

        link = ShortLink.create_for(target='https://example.com/x',
                                    owner=self.user)
        earning = AdEarning.objects.create(
            user=self.user, link=link, micro_cents=800, impressions=1,
            available_at=timezone.now() + timezone.timedelta(days=3),
        )
        # Before, a shortener earning rendered an empty cell: the template only
        # looked at the file.
        self.assertEqual(earning.source_label, link.code)
        self.assertEqual(earning.source_kind, 'link')
