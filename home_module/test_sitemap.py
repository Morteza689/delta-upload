"""Sitemap and robots.txt tests.

The main point: whatever the sitemap lists must actually open. An indexed URL
that meets a visitor with a 404 is worse than no entry.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from upload_module.models import UploadFile

User = get_user_model()


class SitemapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username='mapper', email='mapper@example.com', password='pw',
            email_active_code='mapper1234', is_active=True,
        )

    def make_file(self, slug, **kwargs):
        options = {
            'name': slug,
            'slug': slug,
            'author': self.user,
            'visibility': UploadFile.Visibility.PUBLIC,
            'is_active': True,
        }
        options.update(kwargs)
        return UploadFile.objects.create(**options)

    def body(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_it_is_valid_xml(self):
        from xml.etree import ElementTree

        ElementTree.fromstring(self.body())

    def test_it_is_served_as_xml(self):
        response = self.client.get('/sitemap.xml')
        self.assertIn('xml', response['Content-Type'])

    def test_static_pages_are_listed(self):
        body = self.body()
        for path in ('/faq/', '/about/', '/terms/', '/privacy/', '/shorten/'):
            self.assertIn(path, body)

    def test_the_login_page_is_not_listed(self):
        """Nothing there to index."""
        self.assertNotIn('/user/login/', self.body())

    def test_every_listed_static_page_actually_opens(self):
        import re

        for location in re.findall(r'<loc>([^<]+)</loc>', self.body()):
            path = location.split('testserver', 1)[-1]
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_a_public_file_appears(self):
        self.make_file('public-one')
        self.assertIn('/dl/public-one', self.body())

    def test_a_private_file_does_not_appear(self):
        self.make_file('secret-one', visibility=UploadFile.Visibility.PRIVATE)
        self.assertNotIn('secret-one', self.body())

    def test_a_deactivated_file_does_not_appear(self):
        self.make_file('off-one', is_active=False)
        self.assertNotIn('off-one', self.body())

    def test_an_expired_file_does_not_appear(self):
        self.make_file('gone-one',
                       public_until=timezone.now() - timedelta(hours=1))
        self.assertNotIn('gone-one', self.body())

    def test_a_file_still_inside_its_window_appears(self):
        self.make_file('live-one',
                       public_until=timezone.now() + timedelta(days=2))
        self.assertIn('live-one', self.body())

    def test_a_guest_file_does_not_appear(self):
        """It lives five days at most, usually less than the time to be crawled."""
        self.make_file('guest-one',
                       auto_delete_at=timezone.now() + timedelta(days=1))
        self.assertNotIn('guest-one', self.body())

    def test_a_file_without_a_slug_does_not_appear(self):
        """Otherwise a broken <loc> ends up in the sitemap."""
        UploadFile.objects.create(
            name='no slug', slug='', author=self.user,
            visibility=UploadFile.Visibility.PUBLIC, is_active=True,
        )
        body = self.body()
        self.assertNotIn('<loc></loc>', body)
        self.assertNotIn('/dl/</loc>', body)

    def test_it_stays_cheap_as_files_grow(self):
        """The query count must not grow with the number of files."""
        def query_count():
            from django.test.utils import CaptureQueriesContext
            from django.db import connection

            with CaptureQueriesContext(connection) as captured:
                self.client.get('/sitemap.xml')
            return len(captured)

        self.make_file('only-one')
        # Warm anything cached per process first. The claim under test is that
        # the count does not grow with the number of files, not that a cold
        # first request costs the same as a warm one.
        query_count()
        with_one = query_count()

        for index in range(30):
            self.make_file(f'many-{index}')
        with_many = query_count()

        self.assertEqual(with_one, with_many)


class RobotsTests(TestCase):
    def test_robots_is_served(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')

    def test_it_points_at_a_sitemap_that_exists(self):
        body = self.client.get('/robots.txt').content.decode()
        line = [l for l in body.splitlines() if l.startswith('Sitemap:')]
        self.assertEqual(len(line), 1)

        path = line[0].split('testserver', 1)[-1]
        self.assertEqual(self.client.get(path).status_code, 200)

    def test_panel_paths_are_disallowed(self):
        body = self.client.get('/robots.txt').content.decode()
        for path in ('/user/', '/admin/', '/staff/', '/api/', '/payment/'):
            self.assertIn(f'Disallow: {path}', body)

    def test_short_links_are_disallowed(self):
        """An indexed shortener lets spammers borrow the domain's reputation."""
        body = self.client.get('/robots.txt').content.decode()
        self.assertIn('Disallow: /s/', body)
