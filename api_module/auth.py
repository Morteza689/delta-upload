"""API key authentication.

The key travels in a standard Authorization header so clients can use ordinary
HTTP libraries:

    Authorization: Bearer dup_xxxx_yyyy
"""
import functools

from django.utils.translation import gettext as _

from .models import ApiKey
from .responses import error


def extract_key(request):
    header = request.headers.get('Authorization', '')
    if header.lower().startswith('bearer '):
        return header[7:].strip()
    return request.headers.get('X-API-Key', '').strip()


def api_key_required(view):
    """Put a view behind an API key.

    The key and its user are attached to the request so the view need not look
    them up again.
    """

    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        raw_key = extract_key(request)
        if not raw_key:
            return error(
                'missing_credentials',
                _('کلید API ارسال نشده است. هدر Authorization: Bearer <key> را بفرست.'),
                status=401,
            )

        api_key = ApiKey.authenticate(raw_key)
        if api_key is None:
            # The message does not distinguish an unknown key from a revoked one.
            return error('invalid_credentials', _('کلید API نامعتبر یا غیرفعال است.'),
                         status=401)

        api_key.touch()
        request.api_key = api_key
        request.api_user = api_key.user
        return view(request, *args, **kwargs)

    # API requests carry a key, not a session cookie, so CSRF does not apply.
    wrapper.csrf_exempt = True
    return wrapper
