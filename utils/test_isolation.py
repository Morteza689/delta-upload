"""One account must never reach another account's things.

This is the failure that matters most here: files, earnings, payout
destinations and API keys all hang off a user, and a view that forgets the
filter hands them to whoever asks. Two of those have been found by hand
already, so this walks every one of them.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from account_module.models import PayoutAccount, Withdrawal
from api_module.models import ApiKey
from contact_module.models import Ticket
from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


def make_user(name):
    return User.objects.create_user(
        username=f'{name}@example.com', email=f'{name}@example.com',
        password='pw-for-testing-1', email_active_code=f'{name}-code-xx',
        is_active=True,
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, RATELIMIT_ENABLE=False)
class CrossAccountTests(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.owner = make_user('owner')
        self.stranger = make_user('stranger')
        self.client.force_login(self.stranger)

        self.file = UploadFile.objects.create(
            name='private.bin', author=self.owner, slug='ownerfile12',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('private.bin', b'x' * 30),
            visibility=UploadFile.Visibility.PRIVATE,
        )

    def assert_denied(self, url, method='get'):
        response = getattr(self.client, method)(url)
        self.assertIn(
            response.status_code, (302, 403, 404),
            f'{method.upper()} {url} returned {response.status_code}',
        )
        return response

    def test_a_stranger_cannot_read_the_file_report(self):
        self.assert_denied(reverse('file_report', args=[self.file.pk]))

    def test_a_stranger_cannot_open_the_edit_form(self):
        self.assert_denied(reverse('file_edit', args=[self.file.pk]))

    def test_a_stranger_cannot_save_an_edit(self):
        self.assert_denied(reverse('file_edit', args=[self.file.pk]), 'post')

        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'private.bin')

    def test_a_stranger_cannot_delete(self):
        self.assert_denied(reverse('file_delete', args=[self.file.pk]), 'post')

        self.file.refresh_from_db()
        self.assertIsNone(self.file.deleted_at)

    def test_a_stranger_cannot_download_from_the_panel(self):
        self.assert_denied(reverse('owner_download', args=[self.file.pk]))

    def test_a_private_file_has_no_public_page(self):
        response = self.client.get(f'/dl/{self.file.slug}')
        self.assertEqual(response.status_code, 404)

    def test_the_file_list_shows_only_your_own(self):
        response = self.client.get(reverse('my_files_page'))
        self.assertNotContains(response, 'private.bin')

    def test_a_stranger_cannot_see_a_payout_destination(self):
        PayoutAccount.objects.create(
            user=self.owner, method=PayoutAccount.Method.SHEBA,
            destination='IR820540102680020817909002', holder_name='Owner',
        )
        response = self.client.get(reverse('payout_accounts_page'))
        self.assertNotContains(response, '820540102680020817909002')

    def test_a_stranger_cannot_withdraw_to_your_destination(self):
        account = PayoutAccount.objects.create(
            user=self.owner, method=PayoutAccount.Method.SHEBA,
            destination='IR820540102680020817909002', holder_name='Owner',
        )
        self.stranger.balance = 100_00
        self.stranger.save(update_fields=['balance'])

        self.client.post(reverse('withdrawal_page'), {
            'withdrawal_amount': '5.00', 'account': account.pk,
        })
        # The form limits the choices to your own destinations; without that,
        # money leaves one account and arrives in another person's bank.
        self.assertFalse(
            Withdrawal.objects.filter(account=account,
                                      user=self.stranger).exists()
        )

    def test_a_stranger_cannot_see_an_api_key(self):
        _key, raw = ApiKey.generate(self.owner, 'owner site')
        response = self.client.get(reverse('api_keys_page'))
        self.assertNotContains(response, raw)

    def test_a_stranger_cannot_read_a_ticket(self):
        ticket = Ticket.objects.create(user=self.owner, subject='private matter')
        self.assert_denied(reverse('ticket_detail', args=[ticket.pk]))

    def test_a_stranger_cannot_reach_a_telegram_connection(self):
        from telegram_module.models import TelegramConnection

        connection = TelegramConnection.objects.create(user=self.owner)
        response = self.client.get(reverse('telegram_connect_page'))

        # The link code is what attaches a channel to an account.
        self.assertNotContains(response, connection.link_code)

    def test_the_earnings_page_shows_only_your_own(self):
        from ads_module.models import AdEarning

        AdEarning.objects.create(
            user=self.owner, file=self.file, micro_cents=5000, impressions=2,
            available_at=timezone.now() + timezone.timedelta(days=3),
        )
        response = self.client.get(reverse('earnings_page'))
        self.assertNotContains(response, 'private.bin')


@override_settings(MEDIA_ROOT=MEDIA_ROOT, RATELIMIT_ENABLE=False)
class StaffOnlyTests(TestCase):
    """Ordinary accounts must not reach the admin pages."""

    def setUp(self):
        self.user = make_user('plain')
        self.client.force_login(self.user)

    def test_every_staff_page_turns_an_ordinary_user_away(self):
        pages = [
            'staff_overview', 'staff_users', 'staff_files', 'staff_withdrawals',
            'staff_payments', 'staff_tickets', 'staff_exchange_rate',
            'staff_security', 'staff_telegram',
        ]
        for name in pages:
            response = self.client.get(reverse(name))
            self.assertIn(response.status_code, (302, 403, 404), name)
