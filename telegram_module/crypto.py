"""Encryption for the bot tokens users hand us.

Each user's token is a credential of theirs, not ours. Storing it in plain text
would mean a database dump, a backup on a laptop, or a careless SELECT hands
over control of every user's bot at once.

Fernet: AES-128-CBC with an HMAC, from cryptography. Not chosen for cleverness
but because it is the boring option that refuses to decrypt anything tampered
with, which is exactly what is wanted here.

This is encryption at rest, and only that. The server necessarily decrypts a
token to use it, so anyone who can run code on the server can read tokens. What
it stops is the far more common case: the data leaving without the key.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

logger = logging.getLogger(__name__)


class DecryptionFailed(Exception):
    """The stored value could not be read back with the current key."""


def current_key():
    """The encryption key, from its own setting or derived from SECRET_KEY.

    A dedicated TELEGRAM_TOKEN_KEY is better: it can be rotated on its own
    schedule, and rotating SECRET_KEY then does not silently invalidate every
    stored token. The derivation exists so the feature works on a fresh install
    without one more thing to set up first; a system check nags about it.

    Introducing an explicit key for the first time changes the key, so the
    telegram_rekey command has to run alongside it.
    """
    explicit = getattr(settings, 'TELEGRAM_TOKEN_KEY', '') or ''
    if explicit:
        return normalise_key(explicit)
    return secret_key_derived()


def secret_key_derived():
    """The fallback key, for rekeying away from it."""
    secret = getattr(settings, 'SECRET_KEY', '') or ''
    # Domain separated, so this key is not the same bytes as anything else
    # derived from SECRET_KEY elsewhere.
    digest = hashlib.sha256(f'telegram-bot-token:{secret}'.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def normalise_key(value):
    """Accept a Fernet key as given, or hash anything else into one."""
    raw = value.encode() if isinstance(value, str) else value
    try:
        # A real Fernet key is 32 url-safe base64 encoded bytes.
        if len(base64.urlsafe_b64decode(raw)) == 32:
            return raw
    except (ValueError, TypeError):
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def encrypt_with(plaintext, key):
    if not plaintext:
        return ''
    return Fernet(key).encrypt(plaintext.encode()).decode()


def decrypt_with(ciphertext, key):
    """Read a stored token back under a specific key.

    Raises rather than returning empty: an unreadable token means the key
    changed, and treating that as "no token" would quietly disconnect every
    user instead of showing an error somebody can act on.
    """
    if not ciphertext:
        return ''
    try:
        return Fernet(key).decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise DecryptionFailed(
            'the stored token could not be decrypted; the encryption key has '
            'probably changed'
        ) from exc


def encrypt(plaintext):
    return encrypt_with(plaintext, current_key())


def decrypt(ciphertext):
    return decrypt_with(ciphertext, current_key())


def key_is_explicit():
    """Is a dedicated key configured, rather than one derived from SECRET_KEY?"""
    return bool(getattr(settings, 'TELEGRAM_TOKEN_KEY', ''))


def generate_key():
    """A fresh key, for the deployment docs to point at."""
    return Fernet.generate_key().decode()
