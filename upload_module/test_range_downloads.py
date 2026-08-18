"""Range requests against the single-use download link.

A download manager opens with a small probe and then pulls the file in pieces.
Before these tests, the probe burned the link and every request after it got a
404, which broke the tool most of our visitors use.
"""
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import DownloadLink, FileDownload, UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()

#: 10240 bytes where every position is checkable.
BODY = bytes(range(256)) * 40
PART = 4096


def make_user(email='owner@example.com'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
    )


class FakeResponse:
    """The slice of requests.Response that the Telegram reader uses."""

    def __init__(self, body):
        self.body = body

    def iter_content(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start:start + size]

    def close(self):
        pass


def serving():
    """Patch the Telegram fetch so each part comes from BODY."""
    def open_file(token, path, timeout=None):
        index = int(path.split('-')[-1])
        start = index * PART
        return FakeResponse(BODY[start:start + PART])

    return (
        patch('telegram_module.storage.api.get_file_path',
              side_effect=lambda token, fid: 'path-' + fid[1:]),
        patch('telegram_module.storage.api.open_file', side_effect=open_file),
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, RATELIMIT_ENABLE=False,
                   TELEGRAM_CACHE_BYTES=0)
class TelegramRangeTests(TestCase):
    """The Telegram path, which is the one that parses Range itself."""

    def setUp(self):
        from telegram_module.models import TelegramChunk, TelegramFile
        from telegram_module.tests import make_connection

        self.user = make_user()
        self.connection = make_connection(self.user)
        self.upload = UploadFile.objects.create(
            name='big.bin', author=self.user, slug='ranged12345',
            type_file=UploadFile.ChoiceType.File,
            visibility=UploadFile.Visibility.PUBLIC,
            storage_backend=UploadFile.StorageBackend.TELEGRAM,
            size_bytes_cached=len(BODY),
            original_filename='big.bin',
        )
        telegram_file = TelegramFile.objects.create(
            upload_file=self.upload, connection=self.connection,
            total_bytes=len(BODY), chunk_bytes=PART,
            status=TelegramFile.Status.STORED, original_name='big.bin',
        )
        for index, start in enumerate(range(0, len(BODY), PART)):
            TelegramChunk.objects.create(
                telegram_file=telegram_file, index=index,
                size_bytes=len(BODY[start:start + PART]),
                message_id=100 + index, file_id='f' + str(index),
                file_unique_id='u' + str(index),
            )

    def link(self):
        record = DownloadLink.objects.create(
            file=self.upload,
            expires_at=timezone.now() + timedelta(minutes=15),
            kind=FileDownload.Kind.FREE,
        )
        return reverse('download_file', args=[record.token]), record

    def test_a_probe_does_not_burn_the_link(self):
        url, record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url, HTTP_RANGE='bytes=0-31')
            self.assertEqual(response.status_code, 206)
            b''.join(response.streaming_content)

        record.refresh_from_db()
        # Burning here is what broke every request the download manager made
        # after its opening probe.
        self.assertFalse(record.is_used)

    def test_several_ranges_work_on_one_link(self):
        url, _record = self.link()
        cases = [
            ('bytes=0-31', BODY[0:32]),
            ('bytes=4090-4100', BODY[4090:4101]),
            ('bytes=-16', BODY[-16:]),
        ]
        first, second = serving()
        with first, second:
            for header, expected in cases:
                response = self.client.get(url, HTTP_RANGE=header)
                self.assertEqual(response.status_code, 206, header)
                self.assertEqual(b''.join(response.streaming_content),
                                 expected, header)

    def test_a_range_reports_the_right_content_range(self):
        url, _record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url, HTTP_RANGE='bytes=100-199')
            b''.join(response.streaming_content)

        self.assertEqual(response['Content-Range'],
                         'bytes 100-199/' + str(len(BODY)))
        self.assertEqual(response['Content-Length'], '100')

    def test_a_full_download_still_burns_the_link(self):
        url, record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)

        record.refresh_from_db()
        self.assertTrue(record.is_used)

    def test_a_range_covering_everything_counts_as_full(self):
        url, record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(
                url, HTTP_RANGE='bytes=0-' + str(len(BODY) - 1),
            )
            b''.join(response.streaming_content)

        record.refresh_from_db()
        # It asked for the whole file, so it is a finished download.
        self.assertTrue(record.is_used)

    def test_a_probe_is_not_counted_as_a_download(self):
        url, _record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url, HTTP_RANGE='bytes=0-31')
            b''.join(response.streaming_content)

        self.upload.refresh_from_db()
        self.assertEqual(self.upload.number_of_download, 0)
        self.assertEqual(FileDownload.objects.count(), 0)

    def test_the_piece_with_the_last_byte_is_counted_once(self):
        url, _record = self.link()
        headers = ['bytes=0-4095', 'bytes=4096-8191',
                   'bytes=8192-' + str(len(BODY) - 1)]
        first, second = serving()
        with first, second:
            for header in headers:
                response = self.client.get(url, HTTP_RANGE=header)
                b''.join(response.streaming_content)

        self.upload.refresh_from_db()
        # Three requests, one download.
        self.assertEqual(self.upload.number_of_download, 1)
        self.assertEqual(FileDownload.objects.count(), 1)

    def test_an_unsatisfiable_range_serves_the_whole_file(self):
        url, _record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url, HTTP_RANGE='bytes=999999-')
            b''.join(response.streaming_content)

        # Answering with everything is a valid response to a range we cannot
        # satisfy, and better than an error the visitor cannot act on.
        self.assertEqual(response.status_code, 200)

    def test_a_malformed_range_header_is_ignored(self):
        url, _record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url, HTTP_RANGE='pages=1-2')
            b''.join(response.streaming_content)

        self.assertEqual(response.status_code, 200)

    def test_accept_ranges_is_advertised(self):
        url, _record = self.link()
        first, second = serving()
        with first, second:
            response = self.client.get(url)
            b''.join(response.streaming_content)

        # Without this a download manager will not even try to resume.
        self.assertEqual(response['Accept-Ranges'], 'bytes')


@override_settings(MEDIA_ROOT=MEDIA_ROOT, RATELIMIT_ENABLE=False)
class LocalDownloadTests(TestCase):
    def setUp(self):
        self.user = make_user('local@example.com')
        self.upload = UploadFile.objects.create(
            name='plain.bin', author=self.user, slug='plainfile12',
            type_file=UploadFile.ChoiceType.File,
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('plain.bin', BODY),
            original_filename='plain.bin',
        )

    def link(self):
        record = DownloadLink.objects.create(
            file=self.upload,
            expires_at=timezone.now() + timedelta(minutes=15),
            kind=FileDownload.Kind.FREE,
        )
        return reverse('download_file', args=[record.token]), record

    def test_a_local_file_downloads_whole(self):
        url, _record = self.link()
        response = self.client.get(url)
        self.assertEqual(b''.join(response.streaming_content), BODY)

    def test_it_burns_the_link_and_counts_once(self):
        url, record = self.link()
        response = self.client.get(url)
        b''.join(response.streaming_content)

        record.refresh_from_db()
        self.upload.refresh_from_db()
        self.assertTrue(record.is_used)
        self.assertEqual(self.upload.number_of_download, 1)
