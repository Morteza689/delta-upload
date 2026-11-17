"""Logging in to the mail server with an API key.

The server bans this machine's address for good after 100 failed logins in a
day, so what matters here is that the key goes out once, as XOAUTH2, and is
never offered again after a refusal.
"""
import base64
import json
import smtplib
from unittest import mock

from django.core.mail import get_connection
from django.test import SimpleTestCase, override_settings

from utils.mail_backend import (
    XOAuth2EmailBackend, XOAuth2SMTP, XOAuth2SMTP_SSL, xoauth2,
)

BACKEND = 'utils.mail_backend.XOAuth2EmailBackend'


def unconnected():
    # No host, so nothing is dialled; local_hostname skips the FQDN lookup.
    return XOAuth2SMTP(local_hostname='localhost')


class XOAuth2LoginTests(SimpleTestCase):
    def test_the_key_goes_out_as_xoauth2(self):
        smtp = unconnected()
        with mock.patch.object(smtp, 'ehlo_or_helo_if_needed'), \
                mock.patch.object(smtp, 'docmd', return_value=(235, b'ok')) as docmd:
            smtp.login('info@d-up.ir', 'API_test')

        verb, argument = docmd.call_args.args
        mechanism, payload = argument.split(' ')
        self.assertEqual(verb, 'AUTH')
        self.assertEqual(mechanism, 'XOAUTH2')
        self.assertEqual(base64.b64decode(payload),
                         b'user=info@d-up.ir\x01auth=Bearer API_test\x01\x01')

    def test_a_refusal_is_one_attempt_not_two(self):
        error = base64.b64encode(json.dumps({'status': '401'}).encode())
        smtp = unconnected()
        with mock.patch.object(smtp, 'ehlo_or_helo_if_needed'), \
                mock.patch.object(smtp, 'docmd', side_effect=[
                    (334, error), (535, b'5.7.8 Authentication credentials invalid.'),
                ]) as docmd:
            with self.assertRaises(smtplib.SMTPAuthenticationError):
                smtp.login('info@d-up.ir', 'API_wrong')

        # The error challenge is answered with an empty line, which ends the
        # exchange. Answering it with the key again would be a second login.
        self.assertEqual(docmd.call_count, 2)
        self.assertEqual(docmd.call_args.args, ('',))

    def test_the_answer_to_a_challenge_never_carries_the_key(self):
        answer = xoauth2('info@d-up.ir', 'API_test')
        self.assertIn('API_test', answer())
        self.assertEqual(answer(b'{"status":"401"}'), '')


@override_settings(EMAIL_API_KEY='API_test', EMAIL_HOST_PASSWORD='')
class XOAuth2EmailBackendTests(SimpleTestCase):
    def test_logs_in_with_the_key(self):
        connection = get_connection(BACKEND)
        self.assertIsInstance(connection, XOAuth2EmailBackend)
        self.assertEqual(connection.password, 'API_test')

    def test_an_explicit_password_still_wins(self):
        self.assertEqual(get_connection(BACKEND, password='other').password, 'other')

    def test_connects_through_the_xoauth2_classes(self):
        self.assertIs(get_connection(BACKEND, use_tls=True, use_ssl=False)
                      .connection_class, XOAuth2SMTP)
        self.assertIs(get_connection(BACKEND, use_tls=False, use_ssl=True)
                      .connection_class, XOAuth2SMTP_SSL)
