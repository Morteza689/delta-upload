"""Uniform API response shapes.

One structure everywhere so clients need no per-endpoint parsing: success
carries data, failure carries an error with a code and a message.
"""
from django.http import JsonResponse


def ok(data, status=200):
    return JsonResponse(data, status=status)


def error(code, message, status=400, fields=None):
    payload = {'error': {'code': code, 'message': str(message)}}
    if fields:
        # Per-field errors so the client knows exactly what to fix.
        payload['error']['fields'] = {
            name: [str(item) for item in messages]
            for name, messages in fields.items()
        }
    return JsonResponse(payload, status=status)


def file_payload(request, upload_file):
    """Standard file representation."""
    return {
        'slug': upload_file.slug,
        'name': upload_file.name,
        'description': upload_file.description,
        'size': upload_file.size_bytes,
        'visibility': upload_file.visibility,
        'price': upload_file.price,
        'is_free': upload_file.is_free(),
        'public_until': (
            upload_file.public_until.isoformat() if upload_file.public_until else None
        ),
        'is_public_now': upload_file.is_public_now(),
        'downloads': upload_file.number_of_download,
        'created_at': upload_file.created_deta.isoformat(),
        # What the client site shows its users: the download page, not the raw
        # file. The ad and the revenue counting happen on that page.
        'download_page_url': request.build_absolute_uri(
            upload_file.get_absolute_url()
        ) if upload_file.slug else None,
    }
