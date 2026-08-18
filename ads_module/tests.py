"""Ad counting and revenue tests.

Users are paid for views of their own files, so cheating is the obvious move.
Most of these close a specific way of doing it.
"""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from account_module.models import Transaction
from upload_module.models import UploadFile

from . import services
from .models import (MICRO_PER_CENT, AdEarning, AdSetting, AdSlot,
                     DownloadSession, EarningCarry)

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()

#: The anti-fraud rules treat a request with no User-Agent as a bot, so
#: tests that model real behaviour must look like a real browser.
REAL_BROWSER = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'
)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AdBaseTest(TestCase):
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
            file=SimpleUploadedFile('demo.bin', b'hello'),
        )
        # CPM of 100 cents per thousand impressions, uploader share 60%.
        self.setting = AdSetting.objects.create(
            cpm_cents=100, uploader_share_percent=60, confirm_after_days=7,
            min_dwell_seconds=3, max_dwell_seconds=600, dedup_hours=24,
        )

    def visit_step_one(self):
        """Hit step 1 and return the session it created."""
        self.client.get(f'/dl/{self.file.slug}', HTTP_USER_AGENT=REAL_BROWSER)
        return DownloadSession.objects.latest('step1_at')

    def age_session(self, session, seconds):
        """Backdate the session past the minimum dwell time."""
        DownloadSession.objects.filter(pk=session.pk).update(
            step1_at=timezone.now() - timedelta(seconds=seconds),
        )
        session.refresh_from_db()
        return session

    def visit_step_two(self, session):
        return self.client.get(reverse('download_page_step2', args=[session.token]),
                               HTTP_USER_AGENT=REAL_BROWSER)


class EarningMathTests(AdBaseTest):
    def test_share_per_impression_is_computed_in_micro_cents(self):
        # $1 CPM gives 0.1 cents per impression; 60% of that is 600 micro-cents.
        self.assertEqual(self.setting.micro_per_impression(), 600)

    def test_a_valid_funnel_earns_two_impressions(self):
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)

        earning = AdEarning.objects.get()
        self.assertEqual(earning.user, self.author)
        self.assertEqual(earning.impressions, 2)
        self.assertEqual(earning.micro_cents, 1200)
        self.assertEqual(earning.status, AdEarning.Status.PENDING)

    def test_earning_is_not_credited_to_the_wallet_immediately(self):
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)

        self.author.refresh_from_db()
        self.assertEqual(self.author.balance, 0)
        self.assertFalse(Transaction.objects.exists())

    def test_zero_cpm_records_nothing(self):
        AdSetting.objects.update(cpm_cents=0)
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)
        self.assertFalse(AdEarning.objects.exists())

    def test_download_still_works_when_the_visit_is_not_counted(self):
        """Refusing the revenue must not break the download."""
        session = self.visit_step_one()   # no pause, so too_fast
        response = self.visit_step_two(session)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(AdEarning.objects.exists())
        self.assertTrue(self.file.downloadlink_set.exists())


class AntiFraudTests(AdBaseTest):
    def test_going_straight_to_step_two_is_impossible(self):
        import uuid
        response = self.client.get(reverse('download_page_step2', args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)

    def test_a_visit_faster_than_the_minimum_is_not_counted(self):
        session = self.visit_step_one()
        self.visit_step_two(session)

        session.refresh_from_db()
        self.assertFalse(session.counted)
        self.assertEqual(session.reject_reason, 'too_fast')
        self.assertFalse(AdEarning.objects.exists())

    def test_a_stale_session_is_sent_back_to_step_one(self):
        """A stale session no longer reaches step 2 at all.

        It used to render and merely refuse the revenue, which meant the step 2
        link worked forever and was shareable.
        """
        session = self.age_session(self.visit_step_one(), 5000)
        response = self.visit_step_two(session)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AdEarning.objects.exists())

    def test_replaying_the_same_session_earns_only_once(self):
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)
        self.visit_step_two(session)
        self.visit_step_two(session)

        self.assertEqual(AdEarning.objects.count(), 1)

    def test_the_uploader_cannot_earn_from_their_own_file(self):
        self.client.force_login(self.author)
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)

        session.refresh_from_db()
        self.assertEqual(session.reject_reason, 'self_view')
        self.assertFalse(AdEarning.objects.exists())

    def test_the_same_visitor_is_counted_once_per_dedup_window(self):
        """Refreshing repeatedly must not earn."""
        first = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(first)

        # Cooldown out of the way so the dedup rule alone is under test.
        AdSetting.objects.update(visitor_cooldown_seconds=0)

        second = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(second)

        self.assertEqual(AdEarning.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.reject_reason, 'duplicate')

    def test_the_same_visitor_is_counted_again_after_the_window(self):
        first = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(first)
        DownloadSession.objects.filter(pk=first.pk).update(
            step2_at=timezone.now() - timedelta(hours=48),
        )

        second = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(second)

        self.assertEqual(AdEarning.objects.count(), 2)

    def test_a_session_token_shared_with_someone_else_is_not_counted(self):
        """A session token must not be shareable between visitors."""
        session = self.age_session(self.visit_step_one(), 10)
        DownloadSession.objects.filter(pk=session.pk).update(
            visitor_hash='someone-else' + 'z' * 50,
        )
        session.refresh_from_db()

        response = self.visit_step_two(session)

        # The page no longer renders at all: the visitor is sent back to step 1 for
        # their own token, and sees the ad.
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AdEarning.objects.exists())

    def test_a_file_without_an_author_earns_nothing(self):
        UploadFile.objects.filter(pk=self.file.pk).update(author=None)
        session = self.age_session(self.visit_step_one(), 10)
        self.visit_step_two(session)
        self.assertFalse(AdEarning.objects.exists())

    def test_visitor_hash_does_not_contain_the_raw_ip(self):
        session = self.visit_step_one()
        self.assertNotIn('127.0.0.1', session.visitor_hash)
        self.assertEqual(len(session.visitor_hash), 64)


class ConfirmationTests(AdBaseTest):
    def earn(self, micro, ready=True):
        session = DownloadSession.objects.create(
            file=self.file, visitor_hash='h' * 64, counted=True,
            step2_at=timezone.now(),
        )
        offset = -1 if ready else 1
        return AdEarning.objects.create(
            user=self.author, file=self.file, session=session,
            micro_cents=micro,
            available_at=timezone.now() + timedelta(days=offset),
        )

    def test_confirming_moves_whole_cents_to_the_wallet(self):
        self.earn(25_000)   # 2.5 cents

        records, cents = services.confirm_user_earnings(self.author)

        self.author.refresh_from_db()
        self.assertEqual(records, 1)
        self.assertEqual(cents, 2)
        self.assertEqual(self.author.balance, 2)

    def test_the_fractional_remainder_is_carried_not_dropped(self):
        self.earn(25_000)
        services.confirm_user_earnings(self.author)

        carry = EarningCarry.objects.get(user=self.author)
        self.assertEqual(carry.micro_cents, 5_000)   # 0.5 cents carried

    def test_the_carry_is_added_to_the_next_round(self):
        self.earn(25_000)                       # 2.5 cents pays 2, carries 0.5
        services.confirm_user_earnings(self.author)
        self.earn(25_000)                       # 2.5 plus the 0.5 carried is 3 cents
        _records, cents = services.confirm_user_earnings(self.author)

        self.author.refresh_from_db()
        self.assertEqual(cents, 3)
        self.assertEqual(self.author.balance, 5)
        self.assertEqual(EarningCarry.objects.get(user=self.author).micro_cents, 0)

    def test_earnings_below_a_cent_credit_nothing_but_are_not_lost(self):
        self.earn(3_000)   # 0.3 cents
        _records, cents = services.confirm_user_earnings(self.author)

        self.author.refresh_from_db()
        self.assertEqual(cents, 0)
        self.assertEqual(self.author.balance, 0)
        self.assertEqual(EarningCarry.objects.get(user=self.author).micro_cents, 3_000)

    def test_earnings_that_are_not_due_yet_are_left_alone(self):
        self.earn(50_000, ready=False)
        records, cents = services.confirm_user_earnings(self.author)

        self.assertEqual((records, cents), (0, 0))
        self.assertEqual(AdEarning.objects.get().status, AdEarning.Status.PENDING)

    def test_confirming_twice_does_not_pay_twice(self):
        self.earn(MICRO_PER_CENT * 5)
        services.confirm_user_earnings(self.author)
        services.confirm_user_earnings(self.author)

        self.author.refresh_from_db()
        self.assertEqual(self.author.balance, 5)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_the_credit_lands_in_the_ledger_as_ad_revenue(self):
        self.earn(MICRO_PER_CENT * 4)
        services.confirm_user_earnings(self.author)

        entry = Transaction.objects.get()
        self.assertEqual(entry.kind, Transaction.Kind.AD_REVENUE)
        self.assertEqual(entry.amount, 4)

    def test_management_command_confirms_due_earnings(self):
        self.earn(MICRO_PER_CENT * 7)
        call_command('confirm_ad_earnings')

        self.author.refresh_from_db()
        self.assertEqual(self.author.balance, 7)

    def test_management_command_dry_run_changes_nothing(self):
        self.earn(MICRO_PER_CENT * 7)
        call_command('confirm_ad_earnings', '--dry-run')

        self.author.refresh_from_db()
        self.assertEqual(self.author.balance, 0)
        self.assertEqual(AdEarning.objects.get().status, AdEarning.Status.PENDING)


class AdSlotTests(AdBaseTest):
    def test_the_slot_script_is_rendered_on_step_one(self):
        AdSlot.objects.create(
            position=AdSlot.Position.STEP1, network='yektanet',
            script='<script id="step1-ad"></script>',
        )
        html = self.client.get(f'/dl/{self.file.slug}').content.decode()
        self.assertIn('step1-ad', html)

    def test_the_slot_script_is_rendered_on_step_two(self):
        AdSlot.objects.create(
            position=AdSlot.Position.STEP2, network='tapsell',
            script='<script id="step2-ad"></script>',
        )
        session = self.age_session(self.visit_step_one(), 10)
        html = self.visit_step_two(session).content.decode()
        self.assertIn('step2-ad', html)

    def test_an_inactive_slot_is_not_rendered(self):
        AdSlot.objects.create(
            position=AdSlot.Position.STEP1, network='yektanet',
            script='<script id="off-ad"></script>', is_active=False,
        )
        html = self.client.get(f'/dl/{self.file.slug}').content.decode()
        self.assertNotIn('off-ad', html)

    def test_two_networks_can_run_on_different_steps(self):
        AdSlot.objects.create(position=AdSlot.Position.STEP1, network='yektanet',
                              script='<b id="net-a"></b>')
        AdSlot.objects.create(position=AdSlot.Position.STEP2, network='tapsell',
                              script='<b id="net-b"></b>')

        # The same User-Agent in both steps: the visitor hash is built from the
        # address and the agent, and step 2 is bound to it.
        step1 = self.client.get(f'/dl/{self.file.slug}',
                                HTTP_USER_AGENT=REAL_BROWSER).content.decode()
        session = self.age_session(DownloadSession.objects.latest('step1_at'), 10)
        step2 = self.visit_step_two(session).content.decode()

        self.assertIn('net-a', step1)
        self.assertNotIn('net-b', step1)
        self.assertIn('net-b', step2)

    def test_pick_slot_returns_none_when_nothing_is_configured(self):
        self.assertIsNone(services.pick_slot(AdSlot.Position.STEP1))
