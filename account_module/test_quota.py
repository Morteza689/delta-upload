"""Storage quota and package tests."""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile

from . import quota
from .models import StoragePackage, StoragePlan, Transaction

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()

PLANS = [{'bytes': 2000, 'price_cents': 200, 'days': 30}]


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=1000,
                   STORAGE_PLANS=PLANS)
class QuotaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username='q', email='q@example.com', password='pw',
            email_active_code='q' * 12, is_active=True,
        )

    def add_file(self, size):
        return UploadFile.objects.create(
            name='f', slug=f'f{size}', author=self.user,
            file=SimpleUploadedFile(f'f{size}.bin', b'x' * size),
        )

    def test_a_new_user_has_the_free_quota(self):
        self.assertEqual(quota.limit_for(self.user), 1000)
        self.assertEqual(quota.used_by(self.user), 0)

    def test_usage_counts_uploaded_files(self):
        self.add_file(400)
        self.assertEqual(quota.used_by(self.user), 400)

    def test_size_is_recorded_on_save(self):
        upload = self.add_file(123)
        self.assertEqual(upload.size_bytes_cached, 123)

    def test_usage_ignores_other_users(self):
        other = User.objects.create_user(
            username='o', email='o@example.com', password='pw',
            email_active_code='o' * 12, is_active=True,
        )
        UploadFile.objects.create(
            name='theirs', slug='theirs', author=other,
            file=SimpleUploadedFile('t.bin', b'x' * 900),
        )
        self.assertEqual(quota.used_by(self.user), 0)

    def test_guest_files_do_not_count_against_anyone(self):
        UploadFile.objects.create(
            name='guest', slug='guest', author=None,
            file=SimpleUploadedFile('g.bin', b'x' * 900),
        )
        self.assertEqual(quota.used_by(self.user), 0)

    def test_an_upload_within_quota_passes(self):
        self.add_file(400)
        quota.check_upload(self.user, 500)  # must not raise

    def test_an_upload_over_quota_is_refused(self):
        self.add_file(800)
        with self.assertRaises(quota.QuotaExceeded):
            quota.check_upload(self.user, 300)

    def test_exactly_filling_the_quota_is_allowed(self):
        self.add_file(600)
        quota.check_upload(self.user, 400)

    def test_the_error_says_how_much_is_left(self):
        self.add_file(800)
        with self.assertRaises(quota.QuotaExceeded) as caught:
            quota.check_upload(self.user, 300)
        self.assertIn('200', str(caught.exception))

    def test_a_plan_raises_the_limit(self):
        StoragePlan.objects.create(
            user=self.user, bonus_bytes=2000, price_paid=200,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(quota.limit_for(self.user), 3000)

    def test_plans_stack(self):
        for _ in range(2):
            StoragePlan.objects.create(
                user=self.user, bonus_bytes=2000, price_paid=200,
                expires_at=timezone.now() + timedelta(days=30),
            )
        self.assertEqual(quota.limit_for(self.user), 5000)

    def test_an_expired_plan_does_not_count(self):
        StoragePlan.objects.create(
            user=self.user, bonus_bytes=2000, price_paid=200,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(quota.limit_for(self.user), 1000)

    def test_percent_used(self):
        self.add_file(500)
        self.assertEqual(quota.percent_used(self.user), 50)


@override_settings(MEDIA_ROOT=MEDIA_ROOT, FREE_STORAGE_BYTES=1000,
                   STORAGE_PLANS=PLANS, MAX_UPLOAD_SIZE_BYTES=10 ** 9)
class UploadEnforcementTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            username='up', email='up@example.com', password='pw',
            email_active_code='u' * 12, is_active=True,
        )
        self.client.force_login(self.user)

    def upload(self, size, slug='new'):
        return self.client.post(reverse('upload_page'), {
            'name': slug, 'description': 'x', 'visibility': 'private',
            'file': SimpleUploadedFile(f'{slug}.bin', b'x' * size),
        })

    def test_an_upload_within_quota_succeeds(self):
        self.upload(500)
        self.assertTrue(UploadFile.objects.filter(author=self.user).exists())

    def test_an_upload_over_quota_is_refused(self):
        self.upload(1500)
        self.assertFalse(UploadFile.objects.filter(author=self.user).exists())

    def test_the_refusal_explains_itself(self):
        response = self.upload(1500)
        self.assertContains(response, 'فضای شما پر است')

    def test_the_page_shows_current_usage(self):
        self.upload(500, slug='first')
        response = self.client.get(reverse('upload_page'))
        self.assertIn('quota_percent', response.context)

    def test_the_api_enforces_the_same_quota(self):
        """An API key must not be a way around the quota."""
        from api_module.models import ApiKey

        _key, raw = ApiKey.generate(self.user, 'site')
        boundary = '----dupquota'
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\nbig\r\n',
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="big.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
            + 'x' * 1500 + '\r\n',
            f'--{boundary}--\r\n',
        ]

        response = self.client.post(
            '/api/v1/files/', data=''.join(parts).encode(),
            content_type=f'multipart/form-data; boundary={boundary}',
            HTTP_AUTHORIZATION=f'Bearer {raw}',
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()['error']['code'], 'quota_exceeded')


@override_settings(FREE_STORAGE_BYTES=1000)
class StoragePurchaseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # The catalogue comes from the database, not settings, so a price change
        # needs no deploy.
        StoragePackage.objects.all().delete()
        self.package = StoragePackage.objects.create(
            bonus_bytes=2000, price_cents=200, days=30,
        )
        self.user = User.objects.create_user(
            username='buyer', email='buyer@example.com', password='pw',
            email_active_code='b' * 12, is_active=True,
        )
        User.objects.filter(pk=self.user.pk).update(balance=1000)
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.url = reverse('storage_plans_page')

    def test_the_page_opens(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_a_guest_cannot_open_it(self):
        self.assertNotEqual(self.client_class().get(self.url).status_code, 200)

    def test_buying_a_plan_adds_space(self):
        self.client.post(self.url, {'plan': self.package.pk})
        self.assertEqual(quota.limit_for(self.user), 3000)

    def test_buying_debits_the_wallet(self):
        self.client.post(self.url, {'plan': self.package.pk})

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 800)

    def test_buying_writes_a_ledger_entry(self):
        self.client.post(self.url, {'plan': self.package.pk})
        self.assertTrue(Transaction.objects.filter(
            user=self.user, kind=Transaction.Kind.PURCHASE).exists())

    def test_buying_without_balance_is_refused(self):
        User.objects.filter(pk=self.user.pk).update(balance=10)

        response = self.client.post(self.url, {'plan': self.package.pk})

        self.assertFalse(StoragePlan.objects.exists())
        self.assertContains(response, 'کافی نیست')

    def test_no_money_is_taken_when_the_purchase_fails(self):
        User.objects.filter(pk=self.user.pk).update(balance=10)
        self.client.post(self.url, {'plan': self.package.pk})

        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 10)

    def test_an_invalid_plan_is_refused(self):
        self.client.post(self.url, {'plan': '999999'})

        self.assertFalse(StoragePlan.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1000)

    def test_an_inactive_package_cannot_be_bought(self):
        """A withdrawn package must not be purchasable with a stale id."""
        StoragePackage.objects.filter(pk=self.package.pk).update(is_active=False)

        self.client.post(self.url, {'plan': self.package.pk})

        self.assertFalse(StoragePlan.objects.exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.balance, 1000)

    def test_a_later_price_change_does_not_affect_a_bought_plan(self):
        """Size and price are frozen at purchase time."""
        self.client.post(self.url, {'plan': self.package.pk})
        StoragePackage.objects.filter(pk=self.package.pk).update(
            bonus_bytes=1, price_cents=9999)

        plan = StoragePlan.objects.get()
        self.assertEqual(plan.bonus_bytes, 2000)
        self.assertEqual(plan.price_paid, 200)

    def test_it_is_linked_from_the_panel_menu(self):
        html = self.client.get(reverse('user_dashbord')).content.decode()
        self.assertIn(f'href="{self.url}"', html)
