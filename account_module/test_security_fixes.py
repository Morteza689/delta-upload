"""Tests for the security review findings.

Each class here closes a real way in.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .tokens import PasswordResetToken, hash_token

User = get_user_model()


def make_user(name='sec', active=False):
    return User.objects.create_user(
        username=name, email=f'{name}@example.com', password='StrongPass!123',
        # Trimmed to the column width. SQLite ignores it and Postgres does not,
        # so an over-long value here failed only on the real database.
        email_active_code=(name * 12)[:100], is_active=active,
    )


class ActivationCodeRotationTests(TestCase):
    """What an activation code may and may not do.

    It once doubled as a password reset token and was never retired, so anyone
    who saw the activation email could take the account over at any time. Reset
    has its own token now, and the code is single-purpose.

    It is no longer rotated on use, because rotating turned the second visit
    into a 404 and there is nearly always a second visit: mail scanners fetch
    every link in a message before the person sees it. activated_at is what
    keeps that safe, and the tests below are about that line.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()
        self.code = self.user.email_active_code

    def test_activation_works(self):
        self.client.get(reverse('activate_account_page', args=[self.code]))

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_the_use_is_recorded(self):
        self.client.get(reverse('activate_account_page', args=[self.code]))

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.activated_at)

    def test_a_second_visit_is_a_page_and_not_a_404(self):
        self.client.get(reverse('activate_account_page', args=[self.code]))
        response = self.client.get(
            reverse('activate_account_page', args=[self.code])
        )

        # A scanner fetching the link first must not break the visitor's click.
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['already_active'])

    def test_a_used_code_cannot_re_enable_a_disabled_account(self):
        self.client.get(reverse('activate_account_page', args=[self.code]))

        # An operator disables the account.
        self.user.refresh_from_db()
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])

        self.client.get(reverse('activate_account_page', args=[self.code]))

        self.user.refresh_from_db()
        # Otherwise a banned user turns themselves back on with an old email.
        self.assertFalse(self.user.is_active)

    def test_an_unknown_code_is_still_a_404(self):
        response = self.client.get(
            reverse('activate_account_page', args=['n' * 40])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_activation_code_is_not_a_password_reset_token(self):
        """The core of the finding: an activation code must not change a password."""
        response = self.client.post(
            reverse('reset_password_page', args=[self.code]),
            {'password': 'Hacked!12345', 'confirm_password': 'Hacked!12345'},
        )

        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('Hacked!12345'))
        self.assertEqual(response.status_code, 302)

    def test_matching_is_case_sensitive(self):
        """A case-insensitive match shrinks the guess space."""
        response = self.client.get(
            reverse('activate_account_page', args=[self.code.upper()]))
        # The code mixes upper and lower case, so an uppercased copy must not match.
        if self.code != self.code.upper():
            self.assertEqual(response.status_code, 404)


class PasswordResetTokenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('resetter', active=True)

    def test_a_fresh_token_works_once(self):
        raw = PasswordResetToken.issue(self.user)

        self.assertEqual(PasswordResetToken.consume(raw), self.user)
        self.assertIsNone(PasswordResetToken.consume(raw))

    def test_the_raw_token_is_never_stored(self):
        raw = PasswordResetToken.issue(self.user)
        stored = PasswordResetToken.objects.get().token_hash

        self.assertNotEqual(stored, raw)
        self.assertEqual(stored, hash_token(raw))

    def test_an_expired_token_is_refused(self):
        raw = PasswordResetToken.issue(self.user)
        PasswordResetToken.objects.update(
            expires_at=timezone.now() - timedelta(minutes=1))

        self.assertIsNone(PasswordResetToken.consume(raw))

    def test_issuing_a_new_token_kills_the_old_one(self):
        """Several live reset links at once only widen the attack window."""
        first = PasswordResetToken.issue(self.user)
        PasswordResetToken.issue(self.user)

        self.assertIsNone(PasswordResetToken.consume(first))

    def test_a_wrong_token_is_refused(self):
        PasswordResetToken.issue(self.user)
        self.assertIsNone(PasswordResetToken.consume('not-a-real-token'))

    def test_an_empty_token_is_refused(self):
        self.assertIsNone(PasswordResetToken.consume(''))

    def test_resetting_changes_the_password(self):
        raw = PasswordResetToken.issue(self.user)

        self.client.post(reverse('reset_password_page', args=[raw]), {
            'password': 'BrandNew!123', 'confirm_password': 'BrandNew!123',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNew!123'))

    def test_the_same_link_cannot_reset_twice(self):
        raw = PasswordResetToken.issue(self.user)

        self.client.post(reverse('reset_password_page', args=[raw]), {
            'password': 'FirstOne!123', 'confirm_password': 'FirstOne!123',
        })
        self.client.post(reverse('reset_password_page', args=[raw]), {
            'password': 'SecondOne!123', 'confirm_password': 'SecondOne!123',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('FirstOne!123'))

    def test_opening_the_link_does_not_burn_it(self):
        """Browser prefetch or an email scanner must not consume the link."""
        raw = PasswordResetToken.issue(self.user)

        self.client.get(reverse('reset_password_page', args=[raw]))
        self.client.post(reverse('reset_password_page', args=[raw]), {
            'password': 'StillWorks!123', 'confirm_password': 'StillWorks!123',
        })

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('StillWorks!123'))

    def test_resetting_logs_out_existing_sessions(self):
        """If an attacker knew the password and was logged in, this throws them out."""
        attacker = self.client_class()
        attacker.force_login(self.user)
        self.assertEqual(attacker.get(reverse('user_dashbord')).status_code, 200)

        raw = PasswordResetToken.issue(self.user)
        self.client_class().post(reverse('reset_password_page', args=[raw]), {
            'password': 'Recovered!123', 'confirm_password': 'Recovered!123',
        })

        self.assertNotEqual(attacker.get(reverse('user_dashbord')).status_code, 200)


class LegacyUploadViewTests(TestCase):
    def test_the_legacy_upload_route_is_gone(self):
        """A weaker copy: a 5 character slug, no rate limit, no size cap."""
        response = self.client.get('/account/files/upload-file')
        self.assertEqual(response.status_code, 404)

    def test_no_view_generates_a_short_slug(self):
        import re
        from pathlib import Path

        from django.conf import settings

        for path in Path(settings.BASE_DIR).rglob('*/views.py'):
            if '.venv' in str(path):
                continue
            source = path.read_text(encoding='utf-8')
            for match in re.finditer(r'slug\s*=\s*get_random_string\((\d+)\)', source):
                with self.subTest(file=path.name):
                    self.assertGreaterEqual(int(match.group(1)), 12)


class ShortenerInternalHostTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('shortener', active=True)
        self.client.force_login(self.user)

    def make(self, target):
        from link_module.models import ShortLink

        self.client.post(reverse('shorten_page'), {'target': target})
        return ShortLink.objects.filter(target=target).exists()

    def test_localhost_is_refused(self):
        self.assertFalse(self.make('http://localhost/admin'))

    def test_loopback_ip_is_refused(self):
        self.assertFalse(self.make('http://127.0.0.1:8000/admin/'))

    def test_private_ranges_are_refused(self):
        for target in ('http://192.168.1.1/', 'http://10.0.0.5/',
                       'http://172.16.0.1/'):
            with self.subTest(target=target):
                self.assertFalse(self.make(target))

    def test_link_local_metadata_address_is_refused(self):
        """169.254.169.254 is the cloud metadata address."""
        self.assertFalse(self.make('http://169.254.169.254/latest/meta-data/'))

    def test_a_normal_public_url_still_works(self):
        self.assertTrue(self.make('https://example.com/article'))


@override_settings(FREE_STORAGE_BYTES=1000)
class DownloadSessionSharingTests(TestCase):
    """The step 2 link must not be a shareable bypass of step 1."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        from django.core.files.uploadedfile import SimpleUploadedFile

        from upload_module.models import UploadFile

        self.author = make_user('owner', active=True)
        self.file = UploadFile.objects.create(
            name='shared', slug='shared-file', author=self.author,
            visibility=UploadFile.Visibility.PUBLIC, is_active=True,
            file=SimpleUploadedFile('shared.bin', b'data'),
        )

    def step_two_url(self):
        from ads_module.models import DownloadSession

        self.client.get('/dl/shared-file', HTTP_USER_AGENT='Mozilla/5.0 Chrome/120')
        session = DownloadSession.objects.latest('step1_at')
        return reverse('download_page_step2', args=[session.token]), session

    def test_the_original_visitor_can_continue(self):
        url, _session = self.step_two_url()
        response = self.client.get(url, HTTP_USER_AGENT='Mozilla/5.0 Chrome/120')
        self.assertEqual(response.status_code, 200)

    def test_a_different_visitor_is_sent_back_to_step_one(self):
        """A second visitor used to get the file with no ad shown at all."""
        url, _session = self.step_two_url()

        other = self.client_class()
        response = other.get(url, HTTP_USER_AGENT='Mozilla/5.0 Firefox/121',
                             REMOTE_ADDR='203.0.113.7')

        self.assertEqual(response.status_code, 302)
        self.assertIn('/dl/shared-file', response['Location'])

    def test_an_old_session_is_sent_back_to_step_one(self):
        from ads_module.models import DownloadSession

        url, session = self.step_two_url()
        DownloadSession.objects.filter(pk=session.pk).update(
            step1_at=timezone.now() - timedelta(hours=6))

        response = self.client.get(url, HTTP_USER_AGENT='Mozilla/5.0 Chrome/120')
        self.assertEqual(response.status_code, 302)

    def test_the_redirect_target_is_the_ad_bearing_page(self):
        """Not a 404: the visitor gets their file and we get our impression."""
        url, _session = self.step_two_url()

        other = self.client_class()
        response = other.get(url, HTTP_USER_AGENT='Mozilla/5.0 Firefox/121',
                             REMOTE_ADDR='203.0.113.8')
        followed = other.get(response['Location'],
                             HTTP_USER_AGENT='Mozilla/5.0 Firefox/121',
                             REMOTE_ADDR='203.0.113.8')

        self.assertEqual(followed.status_code, 200)
