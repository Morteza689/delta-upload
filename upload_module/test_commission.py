"""The site's cut of a file sale.

The money has to add up exactly: what the buyer paid equals what the seller
received plus what the site kept. A split that loses or invents a cent on
prices that do not divide evenly leaves books nobody can reconcile.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from account_module.models import Transaction
from upload_module import purchases
from upload_module.models import Purchase, UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


def make_user(name, balance=0):
    return User.objects.create_user(
        username=f'{name}@example.com', email=f'{name}@example.com',
        password='pw-for-testing-1', email_active_code=f'{name}-code-x',
        is_active=True, balance=balance,
    )


@override_settings(MEDIA_ROOT=MEDIA_ROOT, SALE_COMMISSION_PERCENT=10)
class CommissionTests(TestCase):
    def setUp(self):
        self.seller = make_user('seller')
        self.buyer = make_user('buyer', balance=100_00)
        self.file = UploadFile.objects.create(
            name='paid.bin', author=self.seller, slug='paidfile123',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('paid.bin', b'x' * 20),
            visibility=UploadFile.Visibility.PUBLIC, price=10_00,
        )

    def test_the_site_keeps_ten_percent(self):
        purchase = purchases.purchase_file(self.buyer, self.file)
        self.assertEqual(purchase.site_commission, 100)

    def test_the_seller_gets_the_rest(self):
        purchase = purchases.purchase_file(self.buyer, self.file)
        self.assertEqual(purchase.seller_share, 900)

    def test_the_two_halves_add_back_to_the_price(self):
        purchase = purchases.purchase_file(self.buyer, self.file)
        self.assertEqual(
            purchase.seller_share + purchase.site_commission, purchase.amount,
        )

    def test_they_add_up_at_every_price(self):
        for price in (1, 3, 7, 99, 101, 333, 1_00, 4_99, 19_99, 100_00):
            commission, share = purchases.split(price)
            self.assertEqual(commission + share, price, price)
            self.assertGreaterEqual(commission, 0, price)
            self.assertGreaterEqual(share, 0, price)

    def test_a_price_too_small_to_split_goes_to_the_seller(self):
        # A single cent cannot be divided; rounding the commission up would
        # take the whole sale and leave the seller nothing.
        commission, share = purchases.split(1)
        self.assertEqual(commission, 0)
        self.assertEqual(share, 1)

    def test_the_wallets_move_by_the_right_amounts(self):
        purchases.purchase_file(self.buyer, self.file)

        self.buyer.refresh_from_db()
        self.seller.refresh_from_db()
        self.assertEqual(self.buyer.balance, 100_00 - 10_00)
        self.assertEqual(self.seller.balance, 900)

    def test_the_commission_is_the_gap_between_the_two_transactions(self):
        purchases.purchase_file(self.buyer, self.file)

        paid = -Transaction.objects.get(
            user=self.buyer, kind=Transaction.Kind.PURCHASE).amount
        received = Transaction.objects.get(
            user=self.seller, kind=Transaction.Kind.SALE).amount

        # What the site kept is what left one wallet and did not arrive in the
        # other. The Purchase row is what makes that number explicit rather
        # than something to be inferred.
        purchase = Purchase.objects.get()
        self.assertEqual(paid - received, purchase.site_commission)

    @override_settings(SALE_COMMISSION_PERCENT=25)
    def test_the_rate_is_configurable(self):
        purchase = purchases.purchase_file(self.buyer, self.file)
        self.assertEqual(purchase.site_commission, 250)
        self.assertEqual(purchase.seller_share, 750)

    @override_settings(SALE_COMMISSION_PERCENT=0)
    def test_a_zero_commission_gives_the_seller_everything(self):
        purchase = purchases.purchase_file(self.buyer, self.file)
        self.assertEqual(purchase.site_commission, 0)
        self.assertEqual(purchase.seller_share, 10_00)

    def test_a_recorded_commission_is_not_recomputed_later(self):
        purchase = purchases.purchase_file(self.buyer, self.file)

        # The rate changes; last year's sale must not change with it.
        with override_settings(SALE_COMMISSION_PERCENT=50):
            purchase.refresh_from_db()
            self.assertEqual(purchase.site_commission, 100)

    def test_the_staff_page_reports_what_the_site_kept(self):
        purchases.purchase_file(self.buyer, self.file)

        staff = make_user('boss')
        staff.is_staff = True
        staff.is_superuser = True
        staff.save()
        self.client.force_login(staff)

        from django.urls import reverse
        response = self.client.get(reverse('staff_overview'))
        self.assertEqual(response.context['sales_commission'], 100)
