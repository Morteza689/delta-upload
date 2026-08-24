"""The share-link button in the file list.

The owner's own list is where they go to hand a file to someone. Reading the
address off the browser bar means opening the file first, and on a phone it
means selecting a long URL by hand.

The button is only offered for a file that is actually reachable. A private or
expired file has an address that answers 404 for everyone else, and the owner
would find that out from the person they sent it to.
"""
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class CopyLinkButtonTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner@example.com', email='owner@example.com',
            password='a-password-1', email_active_code='owner-code',
            is_active=True,
        )
        self.client.force_login(self.owner)

    def make_file(self, slug, **fields):
        return UploadFile.objects.create(
            name=f'{slug}.bin', author=self.owner, slug=slug,
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile(f'{slug}.bin', b'x' * 32),
            **fields,
        )

    def page(self):
        response = self.client.get(reverse('my_files_page'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode('utf-8')

    def test_a_public_file_offers_the_button(self):
        self.make_file('publicone', visibility=UploadFile.Visibility.PUBLIC)

        self.assertIn('js-copy', self.page())

    def test_the_address_is_absolute(self):
        published = self.make_file('publictwo',
                                   visibility=UploadFile.Visibility.PUBLIC)

        # Relative would copy something that pastes into a chat as text nobody
        # can click.
        self.assertIn(f'http://testserver{published.get_absolute_url()}',
                      self.page())

    def test_the_address_is_the_download_page(self):
        published = self.make_file('publicthree',
                                   visibility=UploadFile.Visibility.PUBLIC)

        # Not the direct file: the download page is where the ad impression is
        # counted, which is the whole reason the owner earns anything.
        self.assertEqual(published.get_absolute_url(),
                         reverse('download_page', args=[published.slug]))

    def test_a_private_file_does_not_offer_it(self):
        self.make_file('privateone', visibility=UploadFile.Visibility.PRIVATE)

        self.assertNotIn('js-copy', self.page())

    def test_an_expired_file_does_not_offer_it(self):
        self.make_file(
            'expiredone', visibility=UploadFile.Visibility.PUBLIC,
            public_until=timezone.now() - timezone.timedelta(days=1),
        )

        self.assertNotIn('js-copy', self.page())

    def test_an_inactive_file_does_not_offer_it(self):
        self.make_file('inactiveone', visibility=UploadFile.Visibility.PUBLIC,
                       is_active=False)

        self.assertNotIn('js-copy', self.page())

    def test_only_the_reachable_ones_get_a_button(self):
        self.make_file('mixedpublic', visibility=UploadFile.Visibility.PUBLIC)
        self.make_file('mixedprivate', visibility=UploadFile.Visibility.PRIVATE)

        html = self.page()
        self.assertEqual(html.count('js-copy'), 1)
        self.assertIn('mixedpublic', html)
        self.assertNotIn('/dl/mixedprivate', html)

    def test_another_persons_file_is_not_listed_at_all(self):
        stranger = User.objects.create_user(
            username='other@example.com', email='other@example.com',
            password='a-password-1', email_active_code='other-code',
            is_active=True,
        )
        UploadFile.objects.create(
            name='theirs.bin', author=stranger, slug='theirsone',
            type_file=UploadFile.ChoiceType.File,
            file=SimpleUploadedFile('theirs.bin', b'y' * 32),
            visibility=UploadFile.Visibility.PUBLIC,
        )

        self.assertNotIn('theirsone', self.page())


class CopyScriptTests(TestCase):
    """The script has to understand the attribute the template writes."""

    def script(self):
        from pathlib import Path

        from django.conf import settings

        return (Path(settings.BASE_DIR) / 'static' / 'src'
                / 'copy-button.js').read_text(encoding='utf-8')

    def test_it_reads_text_from_the_attribute(self):
        self.assertIn('copyText', self.script())

    def test_it_still_reads_text_from_another_element(self):
        # The API key page and the shortener point at an element; breaking that
        # to add the list case would trade one working button for another.
        self.assertIn('copyTarget', self.script())

    def test_it_is_loaded_on_every_page(self):
        response = self.client.get(reverse('home_page'))

        self.assertContains(response, 'copy-button')
