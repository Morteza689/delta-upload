import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from account_module import wallet
from account_module.models import Transaction
from ads_module.models import DownloadSession

from . import purchases
from .models import Purchase, UploadFile

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()


class MediaTempMixin:
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def make_user(self, name, balance=0):
        user = User.objects.create_user(
            username=name, email=f'{name}@example.com', password='pw',
            email_active_code=name * 3,
        )
        if balance:
            User.objects.filter(pk=user.pk).update(balance=balance)
            user.refresh_from_db()
        return user

    def make_file(self, author, **kwargs):
        defaults = dict(
            name='demo', author=author, slug=kwargs.pop('slug', 'demo1'),
            file=SimpleUploadedFile('demo.bin', b'hello world'),
            visibility=UploadFile.Visibility.PUBLIC,
        )
        defaults.update(kwargs)
        return UploadFile.objects.create(**defaults)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class VisibilityTests(MediaTempMixin, TestCase):
    """Private and expired files must not be reachable from outside."""

    def setUp(self):
        self.owner = self.make_user('owner')

    def test_public_file_is_reachable(self):
        f = self.make_file(self.owner)
        self.assertTrue(f.is_public_now())
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 200)

    def test_private_file_is_not_reachable(self):
        f = self.make_file(self.owner, visibility=UploadFile.Visibility.PRIVATE)
        self.assertFalse(f.is_public_now())
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 404)

    def test_private_file_is_not_reachable_even_by_its_owner_publicly(self):
        f = self.make_file(self.owner, visibility=UploadFile.Visibility.PRIVATE)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 404)
        # But it is still reachable from their own panel.
        panel = self.client.get(reverse('owner_download', args=[f.pk]))
        self.assertEqual(panel.status_code, 200)
        b''.join(panel.streaming_content)

    def test_public_window_expires(self):
        f = self.make_file(self.owner, public_until=timezone.now() + timedelta(hours=1))
        self.assertTrue(f.is_public_now())
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 200)

        UploadFile.objects.filter(pk=f.pk).update(
            public_until=timezone.now() - timedelta(seconds=1)
        )
        f.refresh_from_db()
        self.assertFalse(f.is_public_now())
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 404)

    def test_inactive_file_is_not_reachable(self):
        f = self.make_file(self.owner, is_active=False)
        self.assertEqual(self.client.get(f'/dl/{f.slug}').status_code, 404)

    def test_step_two_also_enforces_visibility(self):
        """If a file turns private between step 1 and step 2, step 2 must close too."""
        f = self.make_file(self.owner)
        self.client.get(f'/dl/{f.slug}')
        session = DownloadSession.objects.filter(file=f).latest('step1_at')

        UploadFile.objects.filter(pk=f.pk).update(
            visibility=UploadFile.Visibility.PRIVATE
        )
        response = self.client.get(reverse('download_page_step2', args=[session.token]))
        self.assertEqual(response.status_code, 404)

    def test_step_two_needs_a_session_from_step_one(self):
        """Jumping straight to step 2 would skip the first ad."""
        import uuid
        response = self.client.get(reverse('download_page_step2', args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)

    def test_step_one_creates_a_session(self):
        f = self.make_file(self.owner)
        self.client.get(f'/dl/{f.slug}')
        self.assertEqual(DownloadSession.objects.filter(file=f).count(), 1)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class RecordWithoutAFileTests(MediaTempMixin, TestCase):
    """A row with no file must not 500 the page.

    Reading file.size on an empty field raises ValueError. Such rows really do
    exist, from folders or old partial records, and the pages must cope.
    """

    def setUp(self):
        self.owner = self.make_user('owner')
        self.empty = UploadFile.objects.create(
            name='folder', author=self.owner, slug='folder1',
            visibility=UploadFile.Visibility.PUBLIC,
        )

    def test_size_bytes_is_none_instead_of_raising(self):
        self.assertIsNone(self.empty.size_bytes)

    def test_size_bytes_is_none_when_the_file_is_gone_from_storage(self):
        present = self.make_file(self.owner, slug='gone1')
        present.file.storage.delete(present.file.name)
        self.assertIsNone(present.size_bytes)

    def test_my_files_page_renders_with_a_fileless_record(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('my_files_page'))
        self.assertEqual(response.status_code, 200)

    def test_download_page_renders_with_a_fileless_record(self):
        response = self.client.get(f'/dl/{self.empty.slug}')
        self.assertEqual(response.status_code, 200)

    def test_downloading_a_fileless_record_is_404_not_500(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('owner_download', args=[self.empty.pk]))
        self.assertEqual(response.status_code, 404)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class WalletTests(MediaTempMixin, TestCase):
    """The wallet must never go negative and must leave a trace for every change."""

    def setUp(self):
        self.user = self.make_user('walletuser', balance=1000)

    def test_credit_increases_balance_and_records_a_transaction(self):
        wallet.credit(self.user, 500, Transaction.Kind.TOPUP)
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1500)

        entry = Transaction.objects.get(user=self.user)
        self.assertEqual(entry.amount, 500)
        self.assertEqual(entry.balance_after, 1500)

    def test_debit_decreases_balance(self):
        wallet.debit(self.user, 400, Transaction.Kind.WITHDRAWAL)
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 600)

    def test_debit_beyond_balance_is_refused_and_changes_nothing(self):
        with self.assertRaises(wallet.InsufficientBalance):
            wallet.debit(self.user, 1001, Transaction.Kind.PURCHASE)

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1000)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_non_positive_amounts_are_rejected(self):
        for amount in (0, -5):
            with self.assertRaises(wallet.WalletError):
                wallet.credit(self.user, amount, Transaction.Kind.TOPUP)
            with self.assertRaises(wallet.WalletError):
                wallet.debit(self.user, amount, Transaction.Kind.WITHDRAWAL)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, SALE_SELLER_SHARE_PERCENT=60)
class PurchaseTests(MediaTempMixin, TestCase):
    """Buying a paid file, the seller share, and access afterwards."""

    def setUp(self):
        self.seller = self.make_user('seller')
        self.buyer = self.make_user('buyer', balance=1000)
        self.paid = self.make_file(self.seller, price=500, slug='paid1')

    def test_purchase_moves_money_and_the_site_keeps_its_cut(self):
        purchases.purchase_file(self.buyer, self.paid)

        self.buyer.refresh_from_db()
        self.seller.refresh_from_db()
        self.assertEqual(self.buyer.balance, 500)
        self.assertEqual(self.seller.balance, 450)

        purchase = Purchase.objects.get(buyer=self.buyer, file=self.paid)
        self.assertEqual(purchase.amount, 500)
        self.assertEqual(purchase.seller_share, 450)
        self.assertEqual(purchase.site_commission, 50)

    def test_purchase_writes_both_sides_to_the_ledger(self):
        purchases.purchase_file(self.buyer, self.paid)

        buyer_entry = Transaction.objects.get(user=self.buyer)
        seller_entry = Transaction.objects.get(user=self.seller)
        self.assertEqual(buyer_entry.kind, Transaction.Kind.PURCHASE)
        self.assertEqual(buyer_entry.amount, -500)
        self.assertEqual(seller_entry.kind, Transaction.Kind.SALE)
        self.assertEqual(seller_entry.amount, 450)

    def test_purchase_without_enough_balance_changes_nothing(self):
        poor = self.make_user('poor', balance=100)

        with self.assertRaises(wallet.InsufficientBalance):
            purchases.purchase_file(poor, self.paid)

        poor.refresh_from_db()
        self.seller.refresh_from_db()
        self.assertEqual(poor.balance, 100)
        self.assertEqual(self.seller.balance, 0)
        self.assertFalse(Purchase.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    def test_buying_twice_does_not_charge_twice(self):
        purchases.purchase_file(self.buyer, self.paid)
        purchases.purchase_file(self.buyer, self.paid)

        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.balance, 500)
        self.assertEqual(Purchase.objects.count(), 1)

    def test_author_cannot_buy_own_file(self):
        with self.assertRaises(purchases.PurchaseError):
            purchases.purchase_file(self.seller, self.paid)

    def test_free_file_cannot_be_purchased(self):
        free = self.make_file(self.seller, price=0, slug='free1')
        with self.assertRaises(purchases.PurchaseError):
            purchases.purchase_file(self.buyer, free)

    # --- Web flow ---

    def step_two(self, upload_file):
        """Genuinely pass through step 1.

        This used to build the session by hand with a made-up visitor hash.
        Now that step 2 is bound to the visitor, that shortcut lands back on
        step 1 and never tested the real flow anyway.
        """
        self.client.get(f'/dl/{upload_file.slug}')
        session = DownloadSession.objects.filter(file=upload_file).latest('step1_at')
        return self.client.get(reverse('download_page_step2', args=[session.token]))

    def test_step_two_redirects_an_unpaid_visitor_to_the_purchase_page(self):
        self.client.force_login(self.buyer)
        response = self.step_two(self.paid)
        self.assertRedirects(
            response, reverse('purchase_page', args=[self.paid.slug]),
            fetch_redirect_response=False,
        )

    def test_step_two_requires_login_for_a_paid_file(self):
        response = self.step_two(self.paid)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/user/login/', response['Location'])

    def test_step_two_mints_no_token_for_an_unpaid_visitor(self):
        self.client.force_login(self.buyer)
        self.step_two(self.paid)
        self.assertFalse(self.paid.downloadlink_set.exists())

    def test_step_two_works_after_purchase(self):
        purchases.purchase_file(self.buyer, self.paid)
        self.client.force_login(self.buyer)
        response = self.step_two(self.paid)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.paid.downloadlink_set.exists())

    def test_author_reaches_step_two_without_paying(self):
        self.client.force_login(self.seller)
        response = self.step_two(self.paid)
        self.assertEqual(response.status_code, 200)

    def test_free_file_needs_no_purchase(self):
        free = self.make_file(self.seller, price=0, slug='free2')
        response = self.step_two(free)
        self.assertEqual(response.status_code, 200)

    def test_purchase_view_charges_the_buyer(self):
        self.client.force_login(self.buyer)
        response = self.client.post(reverse('purchase_page', args=[self.paid.slug]))

        self.assertEqual(response.status_code, 302)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.balance, 500)
        self.assertTrue(Purchase.objects.filter(buyer=self.buyer, file=self.paid).exists())

    def test_purchase_view_reports_insufficient_balance(self):
        poor = self.make_user('poor2', balance=10)
        self.client.force_login(poor)
        response = self.client.post(reverse('purchase_page', args=[self.paid.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'موجودی کیف پول کافی نیست')
        self.assertFalse(Purchase.objects.exists())
