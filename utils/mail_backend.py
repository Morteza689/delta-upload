"""SMTP that logs in with the mailbox's API key instead of its password.

The mail server takes an API key (API_...) over SASL XOAUTH2 and not as a
password, so Django's SMTP backend cannot use one as it is: smtplib's own login
offers it through CRAM-MD5, PLAIN and LOGIN, and every one of those is a failed
login. That costs more than it sounds. After 100 failed logins in a day the
server bans the address for good, on every protocol, and the address is this
web server's.

A key rather than the password because the key can be revoked on its own,
without changing the password of a mailbox people also read by hand.
"""
import smtplib

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend


def xoauth2(user, key):
    """The SASL XOAUTH2 answer, as smtplib's auth() expects to call it."""

    def answer(challenge=None):
        # A refused login comes back as a challenge carrying the error, and the
        # server waits for an empty line before it says 535. Sending the key
        # again here would be a second login attempt.
        if challenge is not None:
            return ''
        return f'user={user}\x01auth=Bearer {key}\x01\x01'

    return answer


class XOAuth2Login:
    """One XOAUTH2 attempt in place of smtplib's try-each-mechanism login."""

    def login(self, user, password, *, initial_response_ok=True):
        self.ehlo_or_helo_if_needed()
        return self.auth('XOAUTH2', xoauth2(user, password))


class XOAuth2SMTP(XOAuth2Login, smtplib.SMTP):
    pass


class XOAuth2SMTP_SSL(XOAuth2Login, smtplib.SMTP_SSL):
    pass


class XOAuth2EmailBackend(EmailBackend):
    """Django's SMTP backend, logging in with EMAIL_API_KEY."""

    def __init__(self, *args, password=None, **kwargs):
        if password is None:
            password = settings.EMAIL_API_KEY
        super().__init__(*args, password=password, **kwargs)

    @property
    def connection_class(self):
        return XOAuth2SMTP_SSL if self.use_ssl else XOAuth2SMTP
