"""API key authentication for Django REST Framework.

The key travels in a standard Authorization header so clients can use ordinary
HTTP libraries:

    Authorization: Bearer dup_xxxx_yyyy

An absent key and a wrong key both answer 401, but with different codes, so a
client can tell "you forgot to send it" from "the one you sent is dead" without
either message revealing whether a given key ever existed.
"""
from django.utils.translation import gettext as _
from rest_framework import authentication

from .exceptions import ApiError
from .models import ApiKey


def extract_key(request):
    header = request.headers.get('Authorization', '')
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return request.headers.get('X-API-Key', '').strip()


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate by API key, and refuse anonymous access outright.

    Returning None here would let the request through as anonymous and leave
    the refusal to a permission class, which answers with a different shape.
    Every endpoint behind this needs a key, so the refusal belongs here.
    """

    def authenticate(self, request):
        raw_key = extract_key(request)
        if not raw_key:
            raise ApiError(
                'missing_credentials',
                _('کلید API ارسال نشده است. هدر Authorization: Bearer <key> را بفرست.'),
                status=401,
            )

        api_key = ApiKey.authenticate(raw_key)
        if api_key is None:
            # The message does not distinguish an unknown key from a revoked
            # one: telling them apart would confirm which keys exist.
            raise ApiError(
                'invalid_credentials',
                _('کلید API نامعتبر یا غیرفعال است.'),
                status=401,
            )

        api_key.touch()
        return api_key.user, api_key

    def authenticate_header(self, request):
        # Without this DRF answers 403 instead of 401.
        return 'Bearer'
