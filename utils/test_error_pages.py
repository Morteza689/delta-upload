"""Custom error page tests.

These pages only appear with DEBUG off; in development Django shows its own.
Without a test, a broken one stays hidden until the first production error.
"""
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'])
class ErrorPageTests(TestCase):
    def test_404_uses_our_template(self):
        response = self.client.get('/no-such-page-anywhere')

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'این صفحه وجود ندارد', status_code=404)

    def test_404_keeps_the_site_chrome(self):
        """Nobody should be stranded on an unrelated page with no way back."""
        response = self.client.get('/no-such-page-anywhere')
        html = response.content.decode()

        self.assertIn('دلتا آپلود', html)
        self.assertIn('href="/"', html)

    def test_404_is_translated(self):
        from django.conf import settings

        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = 'en'
        response = self.client.get('/no-such-page-anywhere')
        self.assertContains(response, 'This page does not exist', status_code=404)

    def test_the_500_template_renders_standalone(self):
        """The 500 page must not depend on a context processor.

        When it is shown something is already broken, and that something may
        be the context processor. If the template needs one, the error page
        errors and the visitor gets a blank screen.
        """
        from django.template.loader import render_to_string

        html = render_to_string('500.html')
        self.assertIn('500', html)
        self.assertIn('مشکلی پیش آمد', html)

    def test_the_500_template_uses_no_context_processors(self):
        from pathlib import Path

        from django.conf import settings

        source = (Path(settings.BASE_DIR) / 'templates' / '500.html').read_text(
            encoding='utf-8'
        )
        self.assertNotIn("{% extends", source)
        # request and user come from context processors and may be unavailable.
        self.assertNotIn('{{ user', source)
        self.assertNotIn('{{ request', source)

    def test_403_uses_our_template(self):
        from django.template.loader import render_to_string

        html = render_to_string('403.html')
        self.assertIn('403', html)


@override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'],
                   SECURE_SSL_REDIRECT=False)
class CsrfFailurePageTests(TestCase):
    """A stale form gets the site's own page, not Django's bare one.

    handler403 does not cover this: a CSRF failure never raises
    PermissionDenied, it goes through CSRF_FAILURE_VIEW instead. Before that
    setting existed, this was the one error page a real user was most likely
    to meet and the only one that looked broken.
    """

    def test_a_missing_token_answers_403(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login_page'), {'email': 'a@b.c'})
        self.assertEqual(response.status_code, 403)

    def test_it_uses_the_site_template(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login_page'), {'email': 'a@b.c'})
        self.assertTemplateUsed(response, '403.html')

    def test_it_explains_the_fix_rather_than_refusing_access(self):
        client = Client(enforce_csrf_checks=True)
        body = client.post(reverse('login_page'), {'email': 'a@b.c'}).content.decode()

        # Telling someone they have no access, when their form simply went
        # stale, sends them to support over something a reload fixes.
        self.assertIn('منقضی', body)
        self.assertNotIn('به این بخش دسترسی نداری', body)

    def test_the_reason_is_not_printed_on_the_page(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login_page'), {'email': 'a@b.c'})
        # It names internals; useful in a log, not to the visitor.
        self.assertNotContains(response, 'CSRF cookie', status_code=403)

    def test_a_permission_denied_still_gets_the_access_wording(self):
        from home_module.views import error_403

        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        body = error_403(request).content.decode()
        self.assertIn('به این بخش دسترسی نداری', body)
