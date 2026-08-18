"""Download step 2 tests."""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ads_module.models import DownloadSession
from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()
REAL_BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DownloadLinkOnPageTests(TestCase):
    """The link must be visible and copyable, not only
    hidden behind a button.

    Practical reason: someone using a download manager, or sending the link to
    another device, needs the address itself. Right-clicking the button works
    on a desktop but not on a phone.
    """

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.author = User.objects.create_user(
            username='writer', email='writer@example.com', password='pw',
            email_active_code='writer1234', is_active=True,
        )
        self.file = UploadFile.objects.create(
            name='CONTRIBUTING.md', author=self.author, slug='contributing',
            visibility=UploadFile.Visibility.PUBLIC,
            file=SimpleUploadedFile('CONTRIBUTING.md', b'# hello'),
        )

    def step_two(self):
        """Pass step 1 and return the step 2 response."""
        self.client.get(f'/dl/{self.file.slug}', HTTP_USER_AGENT=REAL_BROWSER)
        session = DownloadSession.objects.latest('step1_at')

        # Satisfy the minimum dwell time, or the pass is rejected as too fast.
        DownloadSession.objects.filter(pk=session.pk).update(
            step1_at=timezone.now() - timedelta(seconds=10),
        )
        return self.client.get(reverse('download_page_step2', args=[session.token]),
                               HTTP_USER_AGENT=REAL_BROWSER)

    def test_the_page_opens(self):
        self.assertEqual(self.step_two().status_code, 200)

    def test_the_link_itself_is_shown(self):
        response = self.step_two()
        self.assertContains(response, response.context['download_url'])

    def test_the_link_is_copyable(self):
        html = self.step_two().content.decode()

        self.assertIn('id="download-link"', html)
        self.assertIn('data-copy-target="#download-link"', html)
        self.assertIn('js-copy', html)

    def test_the_copy_script_is_loaded(self):
        """Without it the copy button does nothing."""
        self.assertContains(self.step_two(), 'copy-button.js')

    def test_the_download_button_is_still_there(self):
        """The link was added, it did not replace the button."""
        html = self.step_two().content.decode()

        self.assertIn('دانلود فایل', html)
        self.assertIn('btn-primary', html)

    def test_the_link_is_rendered_left_to_right(self):
        """A latin URL in a right-to-left page breaks apart without dir=ltr."""
        html = self.step_two().content.decode()
        block = html[html.index('id="download-link"') - 200:
                     html.index('id="download-link"') + 200]
        self.assertIn('dir="ltr"', block)

    def test_the_shown_link_is_the_one_that_works(self):
        """If the displayed text differs from the href, copying it is useless."""
        response = self.step_two()
        html = response.content.decode()
        url = response.context['download_url']

        self.assertIn(f'>{url}</code>', html)
        self.assertIn(f'href="{url}"', html)

    def test_the_expiry_notice_is_kept(self):
        self.assertContains(self.step_two(), '۱۵ دقیقه')
