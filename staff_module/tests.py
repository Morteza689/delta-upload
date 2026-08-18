"""Staff dashboard tests.

Chiefly: none of these pages may open for an ordinary user.
"""
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from account_module.models import PayoutAccount, Transaction, Withdrawal
from contact_module.models import Ticket
from upload_module.models import UploadFile

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()

STAFF_URLS = [
    'staff_overview', 'staff_users', 'staff_files',
    'staff_withdrawals', 'staff_payments', 'staff_tickets',
]


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class StaffTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.staff = User.objects.create_user(
            username='agent', email='agent@example.com', password='pw',
            email_active_code='a' * 10, is_active=True, is_staff=True,
        )
        self.user = User.objects.create_user(
            username='normal', email='normal@example.com', password='pw',
            email_active_code='n' * 10, is_active=True,
        )


class AccessTests(StaffTestCase):
    def test_anonymous_is_sent_away_from_every_page(self):
        for name in STAFF_URLS:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)

    def test_a_normal_user_cannot_reach_any_page(self):
        """login_required is not enough; staff is required."""
        self.client.force_login(self.user)
        for name in STAFF_URLS:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertNotEqual(response.status_code, 200)

    def test_staff_can_reach_every_page(self):
        self.client.force_login(self.staff)
        for name in STAFF_URLS:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_the_management_link_only_shows_for_staff(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get('/'), reverse('staff_overview'))

        self.client.force_login(self.staff)
        self.assertContains(self.client.get('/'), reverse('staff_overview'))


class OverviewTests(StaffTestCase):
    def test_pending_work_is_counted(self):
        Ticket.objects.create(user=self.user, subject='کمک',
                              status=Ticket.Status.OPEN)
        account = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        )
        Withdrawal.objects.create(user=self.user, account=account,
                                  withdrawal_amount=500)

        self.client.force_login(self.staff)
        context = self.client.get(reverse('staff_overview')).context

        self.assertEqual(context['pending']['tickets'], 1)
        self.assertEqual(context['pending']['withdrawals'], 1)
        self.assertEqual(context['pending']['withdrawal_amount'], 500)

    def test_the_overview_renders_with_an_empty_database(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('staff_overview'))
        self.assertEqual(response.status_code, 200)


class WithdrawalQueueTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.user.pk).update(balance=1000)
        self.user.refresh_from_db()
        self.account = PayoutAccount.objects.create(
            user=self.user, method=PayoutAccount.Method.USDT_TRC20,
            destination='TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',
        )
        self.withdrawal = Withdrawal.objects.create(
            user=self.user, account=self.account, withdrawal_amount=300,
        )
        self.client.force_login(self.staff)

    def test_marking_a_withdrawal_paid(self):
        self.client.post(reverse('staff_withdrawals'), {
            'withdrawal_id': self.withdrawal.pk, 'action': 'mark_paid',
        })
        self.withdrawal.refresh_from_db()
        self.assertTrue(self.withdrawal.is_paid)
        self.assertIsNotNone(self.withdrawal.deta_is_paid)

    def test_rejecting_a_withdrawal_returns_the_money(self):
        """Without the refund the money has left the balance and is never paid."""
        self.client.post(reverse('staff_withdrawals'), {
            'withdrawal_id': self.withdrawal.pk, 'action': 'reject',
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1300)
        self.assertFalse(Withdrawal.objects.filter(pk=self.withdrawal.pk).exists())
        self.assertTrue(Transaction.objects.filter(user=self.user).exists())

    def test_an_already_paid_withdrawal_cannot_be_touched_again(self):
        Withdrawal.objects.filter(pk=self.withdrawal.pk).update(is_paid=True)
        response = self.client.post(reverse('staff_withdrawals'), {
            'withdrawal_id': self.withdrawal.pk, 'action': 'reject',
        })
        self.assertEqual(response.status_code, 404)

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1000)

    def test_a_normal_user_cannot_approve_a_withdrawal(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_withdrawals'), {
            'withdrawal_id': self.withdrawal.pk, 'action': 'mark_paid',
        })
        self.withdrawal.refresh_from_db()
        self.assertFalse(self.withdrawal.is_paid)


class FileModerationTests(StaffTestCase):
    def setUp(self):
        super().setUp()
        self.upload = UploadFile.objects.create(
            name='بد', author=self.user, slug='badfile',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('bad.bin', b'x'),
        )
        self.client.force_login(self.staff)

    def test_deactivating_a_file_closes_its_public_page(self):
        self.client.post(reverse('staff_files'), {'file_id': self.upload.pk})

        self.upload.refresh_from_db()
        self.assertFalse(self.upload.is_active)
        self.assertEqual(self.client.get(f'/dl/{self.upload.slug}').status_code, 404)

    def test_deactivating_keeps_the_record(self):
        """Disabling must not erase the history."""
        self.client.post(reverse('staff_files'), {'file_id': self.upload.pk})
        self.assertTrue(UploadFile.objects.filter(pk=self.upload.pk).exists())

    def test_reactivating_a_file(self):
        self.client.post(reverse('staff_files'), {'file_id': self.upload.pk})
        self.client.post(reverse('staff_files'), {'file_id': self.upload.pk})

        self.upload.refresh_from_db()
        self.assertTrue(self.upload.is_active)

    def test_a_normal_user_cannot_deactivate_a_file(self):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_files'), {'file_id': self.upload.pk})

        self.upload.refresh_from_db()
        self.assertTrue(self.upload.is_active)

    def test_search_filters_files(self):
        UploadFile.objects.create(
            name='خوب', author=self.user, slug='goodfile',
            file=SimpleUploadedFile('good.bin', b'x'),
        )
        response = self.client.get(reverse('staff_files'), {'q': 'goodfile'})
        self.assertContains(response, 'goodfile')
        self.assertNotContains(response, 'badfile')


class TicketQueueTests(StaffTestCase):
    def test_the_queue_defaults_to_unanswered(self):
        Ticket.objects.create(user=self.user, subject='باز',
                              status=Ticket.Status.OPEN)
        Ticket.objects.create(user=self.user, subject='بسته',
                              status=Ticket.Status.CLOSED)

        self.client.force_login(self.staff)
        response = self.client.get(reverse('staff_tickets'))

        self.assertContains(response, 'باز')
        self.assertNotContains(response, 'بسته<')

    def test_counts_per_status(self):
        Ticket.objects.create(user=self.user, subject='الف',
                              status=Ticket.Status.OPEN)
        self.client.force_login(self.staff)
        counts = self.client.get(reverse('staff_tickets')).context['counts']
        self.assertEqual(counts['open'], 1)
