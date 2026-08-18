"""Anti-fraud rules and download kind separation."""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import DownloadLink, FileDownload, UploadFile
from upload_module.reports import file_report

from .models import AdEarning, AdSetting, DownloadSession

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()

REAL_BROWSER = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class HardeningTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.author = User.objects.create_user(
            username='author', email='author@example.com', password='pw',
            email_active_code='a' * 10,
        )
        self.file = UploadFile.objects.create(
            name='demo', author=self.author, slug='demo1',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('demo.bin', b'hello world'),
        )
        self.setting = AdSetting.objects.create(
            cpm_cents=100, uploader_share_percent=60, confirm_after_days=7,
            min_dwell_seconds=3, max_dwell_seconds=600, dedup_hours=24,
            visitor_cooldown_seconds=60,
            max_earnings_per_file_per_day=500,
            max_earnings_per_user_per_day=2000,
            require_cookies=True,
        )

    def funnel(self, agent=REAL_BROWSER, dwell=10, **extra):
        """A full pass through the funnel, like a real browser."""
        self.client.get(f'/dl/{self.file.slug}', HTTP_USER_AGENT=agent, **extra)
        session = DownloadSession.objects.latest('step1_at')
        DownloadSession.objects.filter(pk=session.pk).update(
            step1_at=timezone.now() - timedelta(seconds=dwell),
        )
        self.client.get(reverse('download_page_step2', args=[session.token]),
                        HTTP_USER_AGENT=agent, **extra)
        session.refresh_from_db()
        return session


class BotDetectionTests(HardeningTestCase):
    def test_a_real_browser_is_counted(self):
        session = self.funnel()
        self.assertTrue(session.counted)
        self.assertEqual(AdEarning.objects.count(), 1)

    def test_curl_is_not_counted(self):
        session = self.funnel(agent='curl/8.4.0')
        self.assertEqual(session.reject_reason, 'bot_agent')
        self.assertFalse(AdEarning.objects.exists())

    def test_python_requests_is_not_counted(self):
        session = self.funnel(agent='python-requests/2.31.0')
        self.assertEqual(session.reject_reason, 'bot_agent')

    def test_headless_chrome_is_not_counted(self):
        session = self.funnel(agent='Mozilla/5.0 HeadlessChrome/120.0')
        self.assertEqual(session.reject_reason, 'bot_agent')

    def test_a_missing_user_agent_is_not_counted(self):
        session = self.funnel(agent='')
        self.assertEqual(session.reject_reason, 'bot_agent')

    def test_a_generic_bot_string_is_not_counted(self):
        session = self.funnel(agent='SomeBot/1.0 (+http://example.com/bot)')
        self.assertEqual(session.reject_reason, 'bot_agent')

    def test_the_download_still_works_for_a_bot(self):
        """Anti-fraud cuts the revenue, not the service."""
        self.funnel(agent='curl/8.4.0')
        self.assertTrue(DownloadLink.objects.exists())


class CooldownTests(HardeningTestCase):
    def setUp(self):
        super().setUp()
        self.second_file = UploadFile.objects.create(
            name='other', author=self.author, slug='demo2',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('other.bin', b'x'),
        )

    def test_hopping_between_files_is_blocked_by_the_cooldown(self):
        """Nobody should earn back to back by moving between files."""
        self.funnel()
        self.assertEqual(AdEarning.objects.count(), 1)

        self.file = self.second_file
        session = self.funnel()

        self.assertEqual(session.reject_reason, 'cooldown')
        self.assertEqual(AdEarning.objects.count(), 1)

    def test_after_the_cooldown_the_next_file_counts(self):
        self.funnel()
        DownloadSession.objects.filter(counted=True).update(
            step2_at=timezone.now() - timedelta(minutes=10),
        )

        self.file = self.second_file
        session = self.funnel()

        self.assertTrue(session.counted)
        self.assertEqual(AdEarning.objects.count(), 2)


class DailyCapTests(HardeningTestCase):
    def test_a_file_stops_earning_at_its_daily_cap(self):
        AdSetting.objects.update(max_earnings_per_file_per_day=2,
                                 visitor_cooldown_seconds=0)
        for _ in range(3):
            self.funnel()
            # A new visitor each time, so dedup does not interfere.
            DownloadSession.objects.filter(counted=True).update(
                visitor_hash=f'v{timezone.now().timestamp()}'.ljust(64, 'x'),
            )

        self.assertEqual(AdEarning.objects.count(), 2)
        self.assertTrue(
            DownloadSession.objects.filter(reject_reason='file_daily_cap').exists()
        )

    def test_a_user_stops_earning_at_their_daily_cap(self):
        AdSetting.objects.update(max_earnings_per_user_per_day=1,
                                 visitor_cooldown_seconds=0)
        for _ in range(2):
            self.funnel()
            DownloadSession.objects.filter(counted=True).update(
                visitor_hash=f'u{timezone.now().timestamp()}'.ljust(64, 'x'),
            )

        self.assertEqual(AdEarning.objects.count(), 1)
        self.assertTrue(
            DownloadSession.objects.filter(reject_reason='user_daily_cap').exists()
        )


class DownloadKindTests(HardeningTestCase):
    """Free, paid and owner downloads must count separately."""

    def setUp(self):
        super().setUp()
        self.buyer = User.objects.create_user(
            username='buyer', email='buyer@example.com', password='pw',
            email_active_code='b' * 10,
        )

    def consume(self, token):
        response = self.client.get(reverse('download_file', args=[token]))
        b''.join(response.streaming_content)
        return response

    def test_a_free_download_is_recorded_as_free(self):
        self.funnel()
        link = DownloadLink.objects.latest('id')
        self.assertEqual(link.kind, FileDownload.Kind.FREE)

        self.consume(link.token)

        self.file.refresh_from_db()
        self.assertEqual(self.file.free_downloads, 1)
        self.assertEqual(self.file.paid_downloads, 0)
        self.assertEqual(self.file.number_of_download, 1)
        self.assertEqual(
            FileDownload.objects.get().kind, FileDownload.Kind.FREE,
        )

    def test_a_paid_download_is_recorded_as_paid(self):
        from upload_module import purchases

        UploadFile.objects.filter(pk=self.file.pk).update(price=500)
        self.file.refresh_from_db()
        User.objects.filter(pk=self.buyer.pk).update(balance=1000)
        self.buyer.refresh_from_db()
        purchases.purchase_file(self.buyer, self.file)

        self.client.force_login(self.buyer)
        self.funnel()

        link = DownloadLink.objects.latest('id')
        self.assertEqual(link.kind, FileDownload.Kind.PAID)

        self.consume(link.token)

        self.file.refresh_from_db()
        self.assertEqual(self.file.paid_downloads, 1)
        self.assertEqual(self.file.free_downloads, 0)

    def test_an_owner_download_counts_for_nothing_public(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse('owner_download', args=[self.file.pk]))
        b''.join(response.streaming_content)

        self.file.refresh_from_db()
        self.assertEqual(self.file.number_of_download, 0)
        self.assertEqual(self.file.free_downloads, 0)
        self.assertEqual(self.file.paid_downloads, 0)
        # But it does appear in the report.
        self.assertEqual(
            FileDownload.objects.get().kind, FileDownload.Kind.OWNER,
        )

    def test_an_abandoned_download_is_not_recorded(self):
        """An unfinished download is neither counted nor reported."""
        self.funnel()
        link = DownloadLink.objects.latest('id')
        self.client.get(reverse('download_file', args=[link.token]))

        self.file.refresh_from_db()
        self.assertEqual(self.file.number_of_download, 0)
        self.assertFalse(FileDownload.objects.exists())


class ReportTests(HardeningTestCase):
    def test_report_separates_free_and_paid(self):
        FileDownload.objects.create(file=self.file, kind=FileDownload.Kind.FREE,
                                    visitor_hash='a' * 64, bytes_sent=100)
        FileDownload.objects.create(file=self.file, kind=FileDownload.Kind.FREE,
                                    visitor_hash='a' * 64, bytes_sent=100)
        FileDownload.objects.create(file=self.file, kind=FileDownload.Kind.PAID,
                                    visitor_hash='b' * 64, bytes_sent=100)
        FileDownload.objects.create(file=self.file, kind=FileDownload.Kind.OWNER,
                                    bytes_sent=100)

        report = file_report(self.file)

        self.assertEqual(report['free']['count'], 2)
        self.assertEqual(report['paid']['count'], 1)
        self.assertEqual(report['owner']['count'], 1)
        # An owner download is not part of the public figures.
        self.assertEqual(report['total_downloads'], 3)
        self.assertEqual(report['unique_visitors'], 2)

    def test_report_lists_rejection_reasons(self):
        for reason in ('too_fast', 'too_fast', 'duplicate'):
            DownloadSession.objects.create(
                file=self.file, visitor_hash='x' * 64,
                counted=False, reject_reason=reason,
            )

        report = file_report(self.file)
        counts = {row['reject_reason']: row['count'] for row in report['rejections']}
        self.assertEqual(counts['too_fast'], 2)
        self.assertEqual(counts['duplicate'], 1)

    def test_report_page_is_owner_only(self):
        stranger = User.objects.create_user(
            username='stranger', email='s@example.com', password='pw',
            email_active_code='s' * 10,
        )
        self.client.force_login(stranger)
        response = self.client.get(reverse('file_report', args=[self.file.pk]))
        self.assertEqual(response.status_code, 404)

    def test_report_page_renders_for_the_owner(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse('file_report', args=[self.file.pk]))
        self.assertEqual(response.status_code, 200)
