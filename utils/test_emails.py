"""The two emails the site cannot work without.

Registration creates an inactive user, so with no activation mail nobody who
signs up can ever log in. Password reset told people a link had been sent when
nothing had. Both were dead code before these tests.
"""
import re
from email.utils import parseaddr

from captcha.models import CaptchaStore
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from account_module.tokens import PasswordResetToken, hash_token

User = get_user_model()

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def captcha_answer():
    """A solved captcha, so a form test is about the form and not the puzzle."""
    store = CaptchaStore.objects.last()
    return {'captcha_0': store.hashkey, 'captcha_1': store.response}


def links_in(body):
    return re.findall(r'https?://[^\s"<]+', body)


@override_settings(EMAIL_BACKEND=LOCMEM, RATELIMIT_ENABLE=False,
                   SITE_BASE_URL='https://d-up.ir')
class ActivationEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.client.get(reverse('register_page'))

    def register(self, email='newbie@example.com'):
        return self.client.post(reverse('register_page'), {
            'username': 'newbie', 'email': email,
            'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
            **captcha_answer(),
        })

    def test_registering_sends_one_email(self):
        response = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['newbie@example.com'])

    def test_the_new_account_starts_inactive(self):
        self.register()
        user = User.objects.get(email='newbie@example.com')
        # Which is why the mail matters: without it the account is a dead end.
        self.assertFalse(user.is_active)

    def test_the_link_activates_the_account(self):
        self.register()
        url = links_in(mail.outbox[0].body)[0]

        self.client.get(url)
        user = User.objects.get(email='newbie@example.com')
        self.assertTrue(user.is_active)

    def test_the_link_is_absolute(self):
        self.register()
        # Built from SITE_BASE_URL, because a relative link in an email goes
        # nowhere.
        self.assertTrue(links_in(mail.outbox[0].body)[0].startswith('https://d-up.ir/'))

    def test_it_carries_a_text_and_an_html_part(self):
        self.register()
        message = mail.outbox[0]

        self.assertTrue(message.body.strip())
        self.assertEqual(len(message.alternatives), 1)
        self.assertEqual(message.alternatives[0][1], 'text/html')

    def test_the_text_part_is_written_not_stripped(self):
        self.register()
        # A stripped table-based email arrives as a wall of run-together
        # words. This one reads as sentences and carries the link on its own
        # line.
        body = mail.outbox[0].body
        self.assertIn('\n', body.strip())
        self.assertNotIn('<', body)

    def test_the_html_part_carries_the_same_link(self):
        self.register()
        message = mail.outbox[0]
        text_link = links_in(message.body)[0]
        self.assertIn(text_link, message.alternatives[0][0])

    def test_the_page_repeats_the_address_back(self):
        response = self.register()
        # A typo in it is the usual reason the message never arrives, and the
        # one thing the visitor can see and fix.
        self.assertContains(response, 'newbie@example.com')

    def test_a_failing_mail_server_does_not_break_registration(self):
        with override_settings(
            EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
            EMAIL_HOST='127.0.0.1', EMAIL_PORT=1,
        ):
            response = self.register('nosmtp@example.com')

        # The account is made either way. Losing the registration because the
        # mail server hiccupped helps nobody.
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email='nosmtp@example.com').exists())


@override_settings(EMAIL_BACKEND=LOCMEM, RATELIMIT_ENABLE=False,
                   SITE_BASE_URL='https://d-up.ir')
class ResetEmailTests(TestCase):
    def setUp(self):
        mail.outbox = []
        self.user = User.objects.create_user(
            username='resetme', email='resetme@example.com',
            password='OldPass!12345', email_active_code='r' * 20,
            is_active=True,
        )
        self.client.get(reverse('forgot_password_page'))

    def ask(self, email='resetme@example.com'):
        return self.client.post(reverse('forgot_password_page'), {
            'email': email, **captcha_answer(),
        })

    def test_asking_sends_the_link(self):
        self.ask()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['resetme@example.com'])

    def test_a_token_is_issued(self):
        self.ask()
        self.assertEqual(
            PasswordResetToken.objects.filter(user=self.user).count(), 1,
        )

    def test_the_link_opens_the_reset_form(self):
        self.ask()
        url = links_in(mail.outbox[0].body)[0]
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_stored_token_is_only_a_hash(self):
        self.ask()
        raw = links_in(mail.outbox[0].body)[0].rstrip('/').rsplit('/', 1)[-1]
        record = PasswordResetToken.objects.get(user=self.user)

        self.assertEqual(record.token_hash, hash_token(raw))
        self.assertNotEqual(record.token_hash, raw)

    def test_asking_twice_retires_the_first_link(self):
        self.ask()
        first = links_in(mail.outbox[0].body)[0]

        self.client.get(reverse('forgot_password_page'))
        self.ask()

        # Several live reset links at once only widen the window. A retired
        # one sends the visitor to the login page rather than showing a form
        # that would refuse them at the last step.
        response = self.client.get(first)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_an_unknown_address_sends_nothing(self):
        self.ask('nobody@example.com')
        self.assertEqual(len(mail.outbox), 0)

    def test_an_unknown_address_looks_the_same_to_the_visitor(self):
        known = self.ask()
        self.client.get(reverse('forgot_password_page'))
        unknown = self.ask('nobody@example.com')

        # Reporting an unknown address would turn this form into a way to
        # discover which addresses have accounts.
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertContains(unknown, 'ارسال')

    def test_the_email_says_how_long_the_link_lasts(self):
        self.ask()
        self.assertIn('۱', mail.outbox[0].body.replace('1', '۱'))
        self.assertIn('ساعت', mail.outbox[0].body)

    def test_it_says_that_doing_nothing_is_safe(self):
        self.ask()
        # Somebody who did not ask for this needs telling that ignoring the
        # message is the right action.
        self.assertIn('نداده‌ای', mail.outbox[0].body)


@override_settings(EMAIL_BACKEND=LOCMEM, SITE_BASE_URL='https://d-up.ir')
class EmailRenderingTests(TestCase):
    """What the messages look like, rather than when they are sent."""

    def render(self, template, context):
        from django.template.loader import render_to_string

        from utils.email_service import site_context

        return render_to_string(template, {**site_context(), **context})

    def test_the_html_uses_the_site_palette(self):
        html = self.render('emails/activate_account.html',
                           {'activation_url': 'https://d-up.ir/a/1'})
        # The brand vermillion from static/src/input.css, not an approximation.
        self.assertIn('#E03E26', html)
        self.assertIn('#161618', html)

    def test_it_carries_no_remote_images(self):
        html = self.render('emails/activate_account.html',
                           {'activation_url': 'https://d-up.ir/a/1'})
        # Remote images are blocked by default in most clients, so a logo
        # loaded that way arrives as a broken box.
        self.assertNotIn('<img', html)

    def test_it_uses_no_stylesheet_or_web_font(self):
        html = self.render('emails/reset_password.html',
                           {'reset_url': 'https://d-up.ir/r/1', 'valid_hours': 1})
        self.assertNotIn('<link', html)
        self.assertNotIn('@import', html)
        self.assertNotIn('fonts.googleapis', html)

    def test_it_lays_out_with_tables(self):
        html = self.render('emails/reset_password.html',
                           {'reset_url': 'https://d-up.ir/r/1', 'valid_hours': 1})
        # Outlook renders with Word, which supports neither flex nor grid.
        self.assertIn('<table', html)
        self.assertNotIn('display:flex', html)
        self.assertNotIn('display:grid', html)

    def test_it_is_right_to_left(self):
        html = self.render('emails/activate_account.html',
                           {'activation_url': 'https://d-up.ir/a/1'})
        self.assertIn('dir="rtl"', html)

    def test_the_url_box_is_left_to_right(self):
        html = self.render('emails/activate_account.html',
                           {'activation_url': 'https://d-up.ir/a/1'})
        # A Latin address inside a right-to-left page otherwise renders broken
        # up and reversed.
        self.assertIn('dir="ltr"', html)

    @override_settings(DEFAULT_FROM_EMAIL='noreply@d-up.ir',
                       EMAIL_REPLY_TO='support@d-up.ir')
    def test_replies_go_to_support_not_the_sender(self):
        from utils.email_service import send_email

        mail.outbox = []
        self.assertTrue(send_email('x', 'a@b.c', {'activation_url': 'https://d-up.ir/a/1'},
                                   'emails/activate_account.html'))
        message = mail.outbox[0]
        # Nobody reads noreply@, so an answer sent there is lost.
        self.assertEqual(parseaddr(message.from_email)[1], 'noreply@d-up.ir')
        self.assertEqual(message.reply_to, ['support@d-up.ir'])

    @override_settings(DEFAULT_FROM_EMAIL='noreply@d-up.ir')
    def test_the_sender_carries_the_site_name_in_the_readers_language(self):
        from utils.email_service import send_email

        mail.outbox = []
        for language, name in (('fa', 'دلتا آپلود'), ('en', 'Delta Upload')):
            send_email('x', 'a@b.c', {'activation_url': 'https://d-up.ir/a/1'},
                       'emails/activate_account.html', language=language)
        # The inbox shows the name, not a bare noreply address.
        self.assertEqual([parseaddr(m.from_email) for m in mail.outbox],
                         [('دلتا آپلود', 'noreply@d-up.ir'),
                          ('Delta Upload', 'noreply@d-up.ir')])

    @override_settings(DEFAULT_FROM_EMAIL='Support Desk <noreply@d-up.ir>')
    def test_a_name_set_in_the_setting_is_kept(self):
        from utils.email_service import send_email

        mail.outbox = []
        send_email('x', 'a@b.c', {'activation_url': 'https://d-up.ir/a/1'},
                   'emails/activate_account.html')
        self.assertEqual(mail.outbox[0].from_email, 'Support Desk <noreply@d-up.ir>')

    @override_settings(DEFAULT_FROM_EMAIL='info@d-up.ir', EMAIL_REPLY_TO='')
    def test_without_a_reply_to_replies_go_to_the_sender(self):
        from utils.email_service import send_email

        mail.outbox = []
        send_email('x', 'a@b.c', {'activation_url': 'https://d-up.ir/a/1'},
                   'emails/activate_account.html')
        self.assertEqual(mail.outbox[0].reply_to, [])

    def test_a_missing_template_reports_rather_than_pretending(self):
        from utils.email_service import send_email

        sent = send_email('x', 'a@b.c', {}, 'emails/does_not_exist.html')
        # The old version swallowed everything, so a typo here looked exactly
        # like a delivered message.
        self.assertFalse(sent)


@override_settings(EMAIL_BACKEND=LOCMEM, RATELIMIT_ENABLE=False,
                   SITE_BASE_URL='https://d-up.ir')
class ActivationPageTests(TestCase):
    """The page the activation link lands on.

    It used to be one line of unstyled text, which left the visitor at the end
    of the flow they came for with nowhere to go but the address bar.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='lander@example.com', email='lander@example.com',
            password='pw-for-testing-1', email_active_code='k' * 40,
            is_active=False,
        )
        self.url = reverse('activate_account_page', args=[self.user.email_active_code])

    def test_it_uses_the_site_layout(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'account_module/account_activated.html')
        self.assertTemplateUsed(response, 'base.html')

    def test_it_activates_the_account(self):
        self.client.get(self.url)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)

    def test_it_offers_somewhere_to_go(self):
        response = self.client.get(self.url)
        self.assertContains(response, reverse('login_page'))

    def test_a_signed_in_visitor_is_pointed_at_the_panel(self):
        self.client.get(self.url)
        self.user.refresh_from_db()
        self.client.force_login(self.user)

        response = self.client.get(self.url)
        self.assertContains(response, reverse('upload_page'))

    def test_the_second_visit_says_so_plainly(self):
        self.client.get(self.url)
        response = self.client.get(self.url)

        self.assertTrue(response.context['already_active'])
        self.assertContains(response, 'از قبل فعال')

    def test_the_whole_flow_from_the_email(self):
        mail.outbox = []
        self.client.get(reverse('register_page'))
        self.client.post(reverse('register_page'), {
            'username': 'flow', 'email': 'flow@example.com',
            'password': 'StrongPass!123', 'confirm_password': 'StrongPass!123',
            **captcha_answer(),
        })
        link = links_in(mail.outbox[0].body)[0]

        # Opened twice, the way a scanner and then a person would.
        self.assertEqual(self.client.get(link).status_code, 200)
        self.assertEqual(self.client.get(link).status_code, 200)

        user = User.objects.get(email='flow@example.com')
        self.assertTrue(user.is_active)
