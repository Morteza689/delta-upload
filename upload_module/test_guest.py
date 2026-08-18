"""Guest upload tests."""
import shutil
import tempfile
from datetime import timedelta

from captcha.models import CaptchaStore
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .guest import purge_expired
from .models import UploadFile

MEDIA_ROOT = tempfile.mkdtemp()
User = get_user_model()


@override_settings(MEDIA_ROOT=MEDIA_ROOT, MAX_GUEST_UPLOAD_SIZE_BYTES=1024)
class GuestUploadTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def captcha(self):
        self.client.get(reverse('guest_upload_page'))
        store = CaptchaStore.objects.last()
        return {'captcha_0': store.hashkey, 'captcha_1': store.response}

    def upload(self, body=b'hello', hours='24', **extra):
        data = {
            'file': SimpleUploadedFile('guest.bin', body),
            'lifetime_hours': hours,
        }
        data.update(self.captcha())
        data.update(extra)
        return self.client.post(reverse('guest_upload_page'), data)

    def test_the_page_is_public(self):
        response = self.client.get(reverse('guest_upload_page'))
        self.assertEqual(response.status_code, 200)

    def test_a_guest_can_upload(self):
        response = self.upload()
        self.assertEqual(response.status_code, 200)

        upload = UploadFile.objects.get()
        self.assertIsNone(upload.author)
        self.assertEqual(upload.visibility, UploadFile.Visibility.PUBLIC)
        self.assertContains(response, upload.slug)

    def test_an_auto_delete_time_is_set(self):
        self.upload(hours='1')
        upload = UploadFile.objects.get()

        self.assertIsNotNone(upload.auto_delete_at)
        delta = (upload.auto_delete_at - timezone.now()).total_seconds()
        self.assertLess(abs(delta - 3600), 60)

    def test_the_longest_lifetime_is_five_days(self):
        self.upload(hours='120')
        upload = UploadFile.objects.get()
        delta = (upload.auto_delete_at - timezone.now()).total_seconds()
        self.assertLess(abs(delta - 5 * 86400), 120)

    def test_an_out_of_range_lifetime_is_refused(self):
        """Thirty days is not one of the choices; the form must not take arbitrary
        values.
        """
        self.upload(hours='720')
        self.assertFalse(UploadFile.objects.exists())

    def test_an_oversized_guest_upload_is_refused(self):
        response = self.upload(body=b'x' * 5000)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UploadFile.objects.exists())
        self.assertContains(response, 'وارد حسابت شو')

    def test_captcha_is_required(self):
        """Without a captcha this form is free anonymous storage."""
        self.client.post(reverse('guest_upload_page'), {
            'file': SimpleUploadedFile('x.bin', b'x'), 'lifetime_hours': '24',
        })
        self.assertFalse(UploadFile.objects.exists())

    def test_the_guest_file_is_downloadable_through_its_link(self):
        self.upload()
        upload = UploadFile.objects.get()
        response = self.client.get(f'/dl/{upload.slug}')
        self.assertEqual(response.status_code, 200)

    def test_a_guest_file_earns_nothing(self):
        """An unowned file has nobody to pay."""
        self.upload()
        upload = UploadFile.objects.get()
        self.assertIsNone(upload.author_id)


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class PurgeTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def make(self, auto_delete_at=None, slug='g1'):
        return UploadFile.objects.create(
            name='guest', author=None, slug=slug,
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile(f'{slug}.bin', b'data'),
            auto_delete_at=auto_delete_at,
        )

    def test_expired_files_are_removed(self):
        self.make(timezone.now() - timedelta(minutes=1), slug='old')
        removed, freed = purge_expired()

        self.assertEqual(removed, 1)
        self.assertGreater(freed, 0)
        self.assertFalse(UploadFile.objects.exists())

    def test_the_stored_file_is_removed_too(self):
        """A queryset delete leaves the file orphaned on disk."""
        upload = self.make(timezone.now() - timedelta(minutes=1), slug='disk')
        storage = upload.file.storage
        name = upload.file.name
        self.assertTrue(storage.exists(name))

        purge_expired()
        self.assertFalse(storage.exists(name))

    def test_files_that_are_not_due_survive(self):
        self.make(timezone.now() + timedelta(hours=1), slug='fresh')
        removed, _freed = purge_expired()

        self.assertEqual(removed, 0)
        self.assertTrue(UploadFile.objects.exists())

    def test_member_files_are_never_touched(self):
        """A member file has no auto_delete_at and must survive."""
        user = User.objects.create_user(
            username='m', email='m@example.com', password='pw',
            email_active_code='m' * 10,
        )
        UploadFile.objects.create(
            name='mine', author=user, slug='mine',
            file=SimpleUploadedFile('mine.bin', b'data'),
        )

        purge_expired()
        self.assertTrue(UploadFile.objects.filter(slug='mine').exists())

    def test_the_command_runs(self):
        self.make(timezone.now() - timedelta(minutes=1), slug='cmd')
        call_command('purge_expired_uploads')
        self.assertFalse(UploadFile.objects.exists())

    def test_the_command_dry_run_deletes_nothing(self):
        self.make(timezone.now() - timedelta(minutes=1), slug='dry')
        call_command('purge_expired_uploads', '--dry-run')
        self.assertTrue(UploadFile.objects.exists())


class GuestUploadIsReachableTests(TestCase):
    """Guest upload must be findable from the site.

    These look obvious but catch exactly what happened once: the page existed
    and was tested, yet nothing linked to it, so for a visitor it did not
    exist at all.
    """

    def setUp(self):
        self.url = reverse('guest_upload_page')

    def logged_in_client(self):
        user = User.objects.create_user(
            username='member', email='member@example.com', password='pw',
            email_active_code='member1234', is_active=True,
        )
        client = self.client_class()
        client.force_login(user)
        return client

    def test_the_landing_page_links_to_it(self):
        self.assertContains(self.client.get('/'), f'href="{self.url}"')

    def test_the_header_links_to_it_for_guests(self):
        """Every page a guest sees should offer the route."""
        for path in ('/', '/faq/', '/shorten/', '/user/login/'):
            with self.subTest(path=path):
                self.assertContains(self.client.get(path), f'href="{self.url}"')

    def test_the_registration_page_offers_it(self):
        """Someone with one file to send should not be stuck behind a signup form."""
        self.assertContains(self.client.get(reverse('register_page')),
                            f'href="{self.url}"')

    def test_the_footer_links_to_it(self):
        html = self.client.get('/terms/').content.decode()
        self.assertIn(f'href="{self.url}"', html)

    def test_the_landing_page_states_the_real_size_limit(self):
        """The number must come from settings, or the page promises what the form
        rejects.
        """
        with override_settings(MAX_GUEST_UPLOAD_SIZE_BYTES=50 * 1024 * 1024):
            self.assertContains(self.client.get('/'), '50')

    def test_a_logged_in_user_is_not_pushed_toward_it(self):
        """A member has a larger cap and earns revenue; steering them to guest
        upload costs them.

        The footer link stays: the footer is a site index, not a suggestion.
        What must go are the landing page buttons and the header item.
        """
        html = self.logged_in_client().get('/').content.decode()

        self.assertNotIn('آپلود بدون ثبت‌نام', html)
        self.assertNotIn('یا بدون حساب آپلود کن', html)
        # Once only, and that one is in the footer.
        self.assertEqual(html.count(f'href="{self.url}"'), 1)

    def test_a_guest_sees_it_in_both_the_header_and_the_body(self):
        html = self.client.get('/').content.decode()
        self.assertGreater(html.count(f'href="{self.url}"'), 1)
