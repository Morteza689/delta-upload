"""Moving file bytes to and from a Telegram channel.

Upload happens in a background worker, never in a request. A 2 GB file takes
minutes to push through Telegram in 19 MB parts, and a web worker held that
long is a web worker not serving anyone.

Download happens inside the request, streamed part by part. Nothing is ever
assembled on disk first: a hundred parts fetched and concatenated before the
first byte reaches the visitor would mean a long silence and a temp file the
size of the original.
"""
import logging
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from upload_module.models import UploadFile

from . import api, cache, crypto
from .models import TelegramChunk, TelegramConnection, TelegramFile

logger = logging.getLogger(__name__)

#: Give up after this many worker passes and tell the user. Retrying forever
#: hides a real problem behind a row that never changes.
MAX_ATTEMPTS = 5


class NotStored(Exception):
    """The file is not retrievable from Telegram right now."""


def chunk_size():
    return getattr(settings, 'TELEGRAM_CHUNK_BYTES', api.CHUNK_BYTES)


def queue(upload_file, connection):
    """Mark an upload for transfer and flip it onto the Telegram backend.

    The backend flips now rather than when the transfer finishes, because the
    quota has to stop counting the file immediately. A user buying space to
    cover a file that is on its way out of our storage would be a bug.
    """
    telegram_file = TelegramFile.objects.create(
        upload_file=upload_file,
        connection=connection,
        total_bytes=upload_file.size_bytes_cached or 0,
        chunk_bytes=chunk_size(),
        # Captured now, because the transfer clears the file field and that is
        # the only place the real filename lives.
        original_name=(upload_file.original_filename
                       or os.path.basename(upload_file.file.name or ''))[:255],
    )
    UploadFile.objects.filter(pk=upload_file.pk).update(
        storage_backend=UploadFile.StorageBackend.TELEGRAM,
    )
    upload_file.storage_backend = UploadFile.StorageBackend.TELEGRAM
    return telegram_file


def pending_queryset():
    return TelegramFile.objects.filter(
        status__in=[TelegramFile.Status.PENDING, TelegramFile.Status.UPLOADING],
        attempts__lt=MAX_ATTEMPTS,
    ).select_related('upload_file', 'connection')


def _iter_parts(handle, size):
    """Read the source file in upload-sized pieces."""
    while True:
        part = handle.read(size)
        if not part:
            return
        yield part


def transfer(telegram_file):
    """Push one queued file into its channel.

    Returns True when the file is fully stored. Failures are recorded on the
    row and re-raised to nobody: the worker moves to the next file.
    """
    connection = telegram_file.connection
    upload_file = telegram_file.upload_file

    if not connection.is_usable():
        _fail(telegram_file, 'the channel is not connected')
        return False

    if not upload_file.file:
        _fail(telegram_file, 'the source file is gone from our storage')
        return False

    try:
        token = connection.token
    except crypto.DecryptionFailed as exc:
        _fail(telegram_file, str(exc))
        return False

    TelegramFile.objects.filter(pk=telegram_file.pk).update(
        status=TelegramFile.Status.UPLOADING,
        attempts=telegram_file.attempts + 1,
    )
    telegram_file.attempts += 1

    # A retry resumes rather than restarting. Re-sending parts that already
    # arrived would double the traffic and leave duplicates in the channel.
    done = set(
        telegram_file.chunks.values_list('index', flat=True)
    )
    part_size = telegram_file.chunk_bytes or chunk_size()
    name = upload_file.name or 'file'

    try:
        with upload_file.file.open('rb') as handle:
            for index, part in enumerate(_iter_parts(handle, part_size)):
                if index in done:
                    continue
                _send_part(token, connection, telegram_file, index, part, name)
    except api.TelegramError as exc:
        if _is_access_problem(exc):
            connection.mark_broken(str(exc))
        _fail(telegram_file, str(exc))
        return False
    except OSError as exc:
        _fail(telegram_file, f'could not read the source file: {exc}')
        return False

    _finish(telegram_file, upload_file)
    return True


def _send_part(token, connection, telegram_file, index, part, name):
    result = api.send_document(
        token, connection.chat_id,
        filename=f'{name}.part{index:04d}',
        stream=part,
        caption=f'{name} [{index + 1}]',
    )
    document = result.get('document') or {}
    TelegramChunk.objects.create(
        telegram_file=telegram_file,
        index=index,
        size_bytes=len(part),
        message_id=result['message_id'],
        file_id=document.get('file_id', ''),
        file_unique_id=document.get('file_unique_id', ''),
    )


def _finish(telegram_file, upload_file):
    """Record success and release the copy on our storage.

    The local file is deleted last. Until every part is confirmed in the
    channel, our copy is the only one that exists.
    """
    with transaction.atomic():
        TelegramFile.objects.filter(pk=telegram_file.pk).update(
            status=TelegramFile.Status.STORED,
            stored_at=timezone.now(),
            last_error='',
        )

    storage, stored_name = upload_file.file.storage, upload_file.file.name
    UploadFile.objects.filter(pk=upload_file.pk).update(file='')
    try:
        storage.delete(stored_name)
    except OSError as exc:
        # The row is already correct, so the file is simply orphaned bytes for
        # the cleanup command to find. Not worth failing a finished transfer.
        logger.warning('could not delete %s after transfer: %s', stored_name, exc)


def _fail(telegram_file, message):
    fields = {'last_error': str(message)[:2000]}
    if telegram_file.attempts >= MAX_ATTEMPTS:
        fields['status'] = TelegramFile.Status.FAILED
    else:
        fields['status'] = TelegramFile.Status.PENDING
    TelegramFile.objects.filter(pk=telegram_file.pk).update(**fields)
    logger.warning('telegram transfer failed for %s: %s',
                   telegram_file.upload_file_id, message)


def _is_access_problem(exc):
    """Did this fail because the bot lost the channel, or just this once?

    A network hiccup should be retried. Losing admin rights should not be, and
    should be visible to the user instead of retried five times in silence.
    """
    text = str(exc).lower()
    return any(marker in text for marker in (
        'chat not found', 'bot was kicked', 'not enough rights',
        'have no rights', 'chat_write_forbidden', 'bot is not a member',
    ))


def stream(upload_file, start=0, end=None):
    """Yield the file's bytes, fetching each part from Telegram in turn.

    start and end are inclusive byte offsets into the whole file, so a range
    request only pulls the parts it actually covers.
    """
    telegram_file = (
        TelegramFile.objects
        .filter(upload_file=upload_file, status=TelegramFile.Status.STORED)
        .prefetch_related('chunks')
        .first()
    )
    if telegram_file is None:
        raise NotStored('this file is not stored in Telegram')

    try:
        token = telegram_file.connection.token
    except crypto.DecryptionFailed as exc:
        raise NotStored(str(exc))
    if not token:
        raise NotStored('the owner has no bot registered')

    chunks = list(telegram_file.chunks.all())
    if not chunks:
        raise NotStored('no parts were recorded for this file')

    if end is None:
        end = telegram_file.total_bytes - 1

    offset = 0
    for chunk in chunks:
        chunk_start, chunk_end = offset, offset + chunk.size_bytes - 1
        offset = chunk_end + 1

        if chunk_end < start:
            continue
        if chunk_start > end:
            break

        # Where inside this part the wanted range begins and ends.
        skip = max(0, start - chunk_start)
        take = min(chunk.size_bytes, end - chunk_start + 1)

        for piece in _read_chunk(token, chunk, skip, take):
            yield piece


def _read_chunk(token, chunk, skip, take):
    """Yield [skip, take) of one part, from the cache or from Telegram."""
    cached = cache.read(chunk.file_unique_id or str(chunk.pk), skip, take)
    if cached is not None:
        yield from cached
        return

    try:
        path = api.get_file_path(token, chunk.file_id)
        response = api.open_file(token, path)
    except api.TelegramError as exc:
        raise NotStored(str(exc))

    writer = cache.writer(chunk.file_unique_id or str(chunk.pk), chunk.size_bytes)
    position = 0
    try:
        for piece in response.iter_content(64 * 1024):
            if writer is not None:
                writer.write(piece)

            piece_end = position + len(piece)
            if piece_end > skip and position < take:
                yield piece[max(0, skip - position):max(0, take - position)]
            position = piece_end
    finally:
        response.close()
        if writer is not None:
            writer.close(complete=position >= chunk.size_bytes)


def delete_from_telegram(telegram_file):
    """Remove a file's messages from the channel. Best effort.

    Telegram refuses to delete messages older than 48 hours in some chat types,
    and the bot may already be gone. Neither is worth blocking a delete on our
    side, so failures are logged and the rows go anyway.
    """
    connection = telegram_file.connection
    if not connection.chat_id or not connection.has_bot():
        return

    try:
        token = connection.token
    except crypto.DecryptionFailed:
        return

    for chunk in telegram_file.chunks.all():
        try:
            api.delete_message(token, connection.chat_id, chunk.message_id)
        except api.TelegramError as exc:
            logger.info('could not delete message %s: %s', chunk.message_id, exc)
        cache.forget(chunk.file_unique_id or str(chunk.pk))


def repair_connection(connection):
    """Re-check a broken connection. Returns True when it works again."""
    if not connection.chat_id or not connection.has_bot():
        return False

    try:
        token = connection.token
    except crypto.DecryptionFailed as exc:
        connection.mark_broken(str(exc))
        return False

    try:
        api.get_chat(token, connection.chat_id)
        allowed, reason = _bot_can_post_now(token, connection)
    except api.TelegramError as exc:
        connection.mark_broken(str(exc))
        return False

    if not allowed:
        connection.mark_broken(reason)
        return False

    connection.status = TelegramConnection.Status.ACTIVE
    connection.save(update_fields=['status'])
    connection.clear_error()
    return True


def _bot_can_post_now(token, connection):
    from .linking import bot_can_post

    return bot_can_post(token, connection.bot_id, connection.chat_id)
