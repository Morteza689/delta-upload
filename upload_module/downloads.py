"""Controlled serving of uploaded files.

No user file is reachable without an access check. The S3 bucket is private and
local media is not served by MEDIA_URL either; the views in this module are the
only route to a file's contents.

Delivery takes one of four paths depending on where the file lives:

1. Telegram, when the owner stores the file in their own channel. We fetch the
   parts and stream them out, so the download page and its ad are unchanged.
2. An S3 signed URL, the production path. The file never passes through our
   server, range requests work, and the URL dies after a few seconds.
3. X-Accel-Redirect, when files are local and nginx sits in front.
4. Streaming from Django, for local development only.
"""
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.http import (
    Http404, HttpResponse, HttpResponseRedirect, StreamingHttpResponse,
)

CHUNK_SIZE = 64 * 1024


def attachment_filename(file_field):
    """The filename for the download header, without any path."""
    return os.path.basename(file_field.name or '') or 'download'


def _attachment_value(filename):
    # The filename comes from the user, so it is sanitised before going into a
    # header; a name with a newline or a quote could otherwise rewrite the
    # response headers.
    ascii_name = re.sub(r'[^\x20-\x7e]', '', filename).replace('"', '') or 'download'
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def _presigned_response(upload_file, filename):
    """Redirect to a short-lived signed S3 URL."""
    if not getattr(settings, 'USE_S3_PRESIGNED_DOWNLOADS', False):
        return None

    storage = upload_file.file.storage
    name = upload_file.file.name
    expire = getattr(settings, 'S3_DOWNLOAD_URL_EXPIRE', 60)

    try:
        url = storage.url(
            name,
            parameters={'ResponseContentDisposition': _attachment_value(filename)},
            expire=expire,
        )
    except TypeError:
        # A backend that does not accept signing parameters.
        url = storage.url(name)

    return HttpResponseRedirect(url)


def _accel_response(upload_file, filename):
    """Hand local file serving to nginx, if configured."""
    prefix = getattr(settings, 'PROTECTED_MEDIA_ACCEL_PREFIX', None)
    if not prefix:
        return None

    response = HttpResponse(content_type='application/octet-stream')
    response['X-Accel-Redirect'] = f"{prefix.rstrip('/')}/{upload_file.file.name}"
    response['Content-Disposition'] = _attachment_value(filename)
    return response


def _stream(file_field, on_complete=None):
    with file_field.open('rb') as handle:
        while chunk := handle.read(CHUNK_SIZE):
            yield chunk
    # Only once the file is fully delivered. If the visitor disconnects the
    # generator closes and this never runs, so a partial download neither counts
    # nor burns the link.
    if on_complete is not None:
        on_complete(complete=True, reached_end=True)


def _parse_range(header, total):
    """One byte range, or None.

    Only the single-range form is honoured. Multipart ranges need a MIME
    envelope no download manager here actually asks for, and answering the
    whole file is a valid response to a range we do not support.
    """
    if not header or not total:
        return None

    match = re.match(r'^bytes=(\d*)-(\d*)$', header.strip())
    if not match:
        return None

    first, last = match.group(1), match.group(2)
    if first:
        start = int(first)
        end = int(last) if last else total - 1
    elif last:
        # A suffix range: the final N bytes.
        start, end = max(0, total - int(last)), total - 1
    else:
        return None

    end = min(end, total - 1)
    if start > end or start >= total:
        return None
    return start, end


def download_filename(upload_file):
    """What the download should arrive called.

    The name the browser sent, first. Storage sanitises names and, when one is
    long, truncates it and splices in a random suffix so the path stays unique
    and short enough. That is right for a file on disk and wrong for what the
    visitor sees: they uploaded "Save Manager 1.8.0.zip" and should not get
    back "Save_Mana_x7Kd2p.8.0.zip".

    Then the name recorded when the file moved to Telegram, for rows that
    predate the field above. Then the storage name, which is all older rows
    have. Then the display title, which often has no extension at all.
    """
    original = getattr(upload_file, 'original_filename', '')
    if original:
        return original

    telegram_file = getattr(upload_file, 'telegram_copy', None)
    stored = getattr(telegram_file, 'original_name', '') if telegram_file else ''
    if stored:
        return stored

    if not upload_file.on_telegram() and getattr(upload_file, 'file', None):
        return attachment_filename(upload_file.file)

    return upload_file.name or 'download'


def _telegram_response(upload_file, filename, request=None, on_complete=None):
    """Stream a file back out of the owner's Telegram channel."""
    from telegram_module import storage as telegram_storage

    total = upload_file.size_bytes_cached or 0
    wanted = _parse_range(
        request.headers.get('Range') if request is not None else None, total,
    )
    start, end = wanted if wanted else (0, max(0, total - 1))

    # A range counts as the whole file only when it really covers all of it.
    # This is what stops a download manager's opening probe from burning the
    # single-use link and breaking every request after it.
    complete = not wanted or (start == 0 and end >= total - 1)

    def body():
        yield from telegram_storage.stream(upload_file, start, end)
        if on_complete is not None:
            on_complete(complete=complete, reached_end=end >= total - 1)

    try:
        stream = body()
        # Pull the first piece here so a file whose parts have gone missing
        # answers 404 instead of a 200 that dies a byte later.
        head = next(stream, b'')
    except telegram_storage.NotStored:
        raise Http404('File is not retrievable from Telegram')

    def chained():
        if head:
            yield head
        yield from stream

    response = StreamingHttpResponse(
        chained(), content_type='application/octet-stream',
        status=206 if wanted else 200,
    )
    response['Content-Length'] = end - start + 1 if total else 0
    response['Accept-Ranges'] = 'bytes'
    if wanted:
        response['Content-Range'] = f'bytes {start}-{end}/{total}'
    response['Content-Disposition'] = _attachment_value(filename)
    return response


def serve(upload_file, on_complete=None, request=None):
    """Build a download response. Access control is the caller's job."""
    # Callers other than uploads pass a plain holder with just a file on it,
    # so Telegram routing is something a caller opts into rather than
    # something every caller has to know about.
    if getattr(upload_file, 'on_telegram', None) and upload_file.on_telegram():
        return _telegram_response(
            upload_file, download_filename(upload_file), request, on_complete,
        )

    # Plain holders from other modules carry only a file, so they fall back to
    # the storage name the way they always did.
    filename = (download_filename(upload_file)
                if hasattr(upload_file, 'original_filename')
                else attachment_filename(upload_file.file))

    for build in (_presigned_response, _accel_response):
        response = build(upload_file, filename)
        if response is not None:
            # In these two cases delivery leaves our control and we never see the end of
            # the download, so the accounting happens here, when the link is issued.
            if on_complete is not None:
                on_complete(complete=True, reached_end=True)
            return response

    response = StreamingHttpResponse(
        _stream(upload_file.file, on_complete),
        content_type='application/octet-stream',
    )
    try:
        response['Content-Length'] = upload_file.file.size
    except (OSError, ValueError, NotImplementedError):
        pass
    response['Content-Disposition'] = _attachment_value(filename)
    return response
