"""One error shape for the whole API.

Every failure comes back as:

    {"error": {"code": "...", "message": "...", "fields": {...}}}

The code is stable and meant to be branched on; the message is Persian and
meant to be shown. Without the code, clients end up matching on message text,
which breaks the first time the wording is improved.

DRF's own shape varies by error type: a string for some, a list for others, a
dict of fields for validation. The handler below flattens all of it into the
one shape above, so nothing in this API answers in DRF's default format.
"""
from django.http import Http404
from django.utils.translation import gettext as _
from rest_framework import status
from rest_framework.exceptions import APIException, NotAuthenticated
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import ParseError, Throttled, ValidationError
from rest_framework.response import Response


class ApiError(APIException):
    """A failure with a code the client can branch on."""

    def __init__(self, code, message, status=400, fields=None):
        self.code = code
        self.status_code = status
        self.message = str(message)
        self.fields = fields
        super().__init__(self.message)


def _payload(code, message, fields=None):
    body = {'error': {'code': code, 'message': str(message)}}
    if fields:
        body['error']['fields'] = fields
    return body


def _flatten(detail):
    """Turn DRF's per-field detail into plain lists of strings."""
    if isinstance(detail, dict):
        return {
            name: _flatten(value) if isinstance(value, dict) else [
                str(item) for item in (value if isinstance(value, list) else [value])
            ]
            for name, value in detail.items()
        }
    if isinstance(detail, list):
        return [str(item) for item in detail]
    return [str(detail)]


def api_exception_handler(exc, context):
    """Rewrite every DRF error into this API's shape."""
    if isinstance(exc, ApiError):
        return Response(
            _payload(exc.code, exc.message, exc.fields), status=exc.status_code,
        )

    if isinstance(exc, ParseError):
        # A body that is not JSON at all is a different problem from a body
        # that is JSON but wrong, and clients have always been able to tell
        # them apart by this code.
        return Response(
            _payload('invalid_json', _('بدنه‌ی درخواست JSON معتبر نیست.')),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, ValidationError):
        # DRF answers 400 for these. This API has always used 422 for a body
        # that parsed but did not validate, and clients branch on it.
        return Response(
            _payload('validation_failed', _('ورودی نامعتبر است.'),
                     _flatten(exc.detail)),
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if isinstance(exc, Throttled):
        response = Response(
            _payload('rate_limited',
                     _('تعداد درخواست بیش از حد مجاز است. کمی بعد دوباره تلاش کن.')),
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
        if exc.wait:
            response['Retry-After'] = str(int(exc.wait))
        return response

    if isinstance(exc, (NotAuthenticated, DRFPermissionDenied)):
        return Response(
            _payload('invalid_credentials', _('کلید API نامعتبر یا غیرفعال است.')),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if isinstance(exc, Http404):
        return Response(
            _payload('not_found', _('پیدا نشد.')),
            status=status.HTTP_404_NOT_FOUND,
        )

    # Imported here, not at module level: DRF resolves this handler while
    # rest_framework.views is still being imported, and a top-level import of
    # it is a circular import at startup.
    from rest_framework.views import exception_handler as drf_handler

    handled = drf_handler(exc, context)
    if handled is None:
        # Not an API exception: let Django turn it into a 500 so it reaches the
        # logs. Swallowing it here would hide real breakage behind a tidy JSON
        # body.
        return None

    detail = handled.data
    message = detail.get('detail') if isinstance(detail, dict) else detail
    handled.data = _payload('error', message or _('خطای نامشخص.'))
    return handled
