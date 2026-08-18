"""Email goes out in the reader's language, not the sender's.

The site takes its language from a cookie, which belongs to whichever browser
is in front of you. Mail has neither browser nor cookie: without a preference
on the account, every message would go out in the default language whatever
the reader picked, and it is often sent from a command where no language is
active at all.
"""
import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from account_module.language import language_for

User = get_user_model()
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_user(email='reader@example.com', language='fa'):
    return User.objects.create_user(
        username=email, email=email, password='pw-for-testing-1',
        email_active_code='c' * 20, is_active=True, language=language,
    )


class LanguagePreferenceTests(TestCase):
    def test_it_defaults_to_the_site_language(self):
        self.assertEqual(make_user().language, 'fa')

    def test_switching_records_it_on_the_account(self):
        user = make_user()
        self.client.force_login(user)

        self.client.post(reverse('set_language'), {'language': 'en', 'next': '/'})

        user.refresh_from_db()
        self.assertEqual(user.language, 'en')

    def test_switching_still_sets_the_cookie(self):
        user = make_user()
        self.client.force_login(user)

        self.client.post(reverse('set_language'), {'language': 'en', 'next': '/'})
        # Wrapping Django's view must not cost the behaviour it already had.
        self.assertEqual(self.client.cookies['dup_language'].value, 'en')

    def test_an_anonymous_switch_changes_no_account(self):
        user = make_user()
        self.client.post(reverse('set_language'), {'language': 'en', 'next': '/'})

        user.refresh_from_db()
        self.assertEqual(user.language, 'fa')

    def test_an_unsupported_language_is_ignored(self):
        user = make_user()
        self.client.force_login(user)

        self.client.post(reverse('set_language'), {'language': 'de', 'next': '/'})

        user.refresh_from_db()
        self.assertEqual(user.language, 'fa')

    def test_a_blank_preference_falls_back_to_the_default(self):
        user = make_user()
        user.language = ''
        self.assertEqual(language_for(user), 'fa')


@override_settings(EMAIL_BACKEND=LOCMEM, RATELIMIT_ENABLE=False,
                   SITE_BASE_URL='https://d-up.ir')
class EmailLanguageTests(TestCase):
    def send_activation(self, language):
        from user_panel_module.views import send_activation_email

        mail.outbox = []
        send_activation_email(make_user(f'{language}@example.com', language))
        return mail.outbox[0]

    def test_a_persian_reader_gets_persian(self):
        message = self.send_activation('fa')
        self.assertIn('حساب تو در', message.body)
        self.assertIn('فعال', message.subject)

    def test_an_english_reader_gets_english(self):
        message = self.send_activation('en')
        self.assertIn('account has been created', message.body)
        self.assertIn('Activate', message.subject)

    def test_the_english_body_has_no_persian_left_in_it(self):
        message = self.send_activation('en')
        persian = re.findall(r'[؀-ۿ]+', message.body)
        # A half-translated email reads worse than one in the wrong language.
        self.assertEqual(persian, [], f'Persian left in the English mail: {persian}')

    def test_the_persian_html_is_right_to_left(self):
        html = self.send_activation('fa').alternatives[0][0]
        self.assertIn('dir="rtl"', html)
        self.assertIn('align="right"', html)
        self.assertNotIn('align="left"', html)

    def test_the_english_html_is_left_to_right(self):
        html = self.send_activation('en').alternatives[0][0]
        self.assertIn('dir="ltr"', html)
        self.assertIn('align="left"', html)
        self.assertNotIn('align="right"', html)

    def test_the_language_tag_matches(self):
        for language in ('fa', 'en'):
            html = self.send_activation(language).alternatives[0][0]
            self.assertIn(f'lang="{language}"', html)

    def test_the_font_stack_suits_the_script(self):
        # Tahoma covers Persian; a Latin-only stack would fall back to whatever
        # the client happens to have and often renders Persian badly.
        self.assertIn('Tahoma', self.send_activation('fa').alternatives[0][0])
        self.assertIn('Arial,Helvetica',
                      self.send_activation('en').alternatives[0][0])

    def test_the_brand_name_follows_the_language(self):
        self.assertIn('دلتا آپلود', self.send_activation('fa').body)
        self.assertIn('Delta Upload', self.send_activation('en').body)

    def test_the_reset_email_follows_the_same_rule(self):
        from account_module.views import send_reset_email

        mail.outbox = []
        send_reset_email(make_user('enreset@example.com', 'en'))
        message = mail.outbox[0]

        self.assertIn('Reset', message.subject)
        self.assertEqual(re.findall(r'[؀-ۿ]+', message.body), [])

    def test_the_browsing_language_does_not_decide_the_email(self):
        from django.utils import translation

        from user_panel_module.views import send_activation_email

        mail.outbox = []
        # An operator browsing in English triggering mail to a Persian reader.
        with translation.override('en'):
            send_activation_email(make_user('mixed@example.com', 'fa'))

        self.assertIn('حساب تو در', mail.outbox[0].body)

    def test_the_active_language_is_restored_afterwards(self):
        from django.utils import translation

        from user_panel_module.views import send_activation_email

        with translation.override('en'):
            send_activation_email(make_user('restore@example.com', 'fa'))
            # The override must not leak into whatever the request was doing.
            self.assertEqual(translation.get_language(), 'en')
