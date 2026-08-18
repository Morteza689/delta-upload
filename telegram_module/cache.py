"""A bounded on-disk cache for parts fetched back from Telegram.

Without it, every download of a popular file pulls every part across the
network again. A file downloaded a hundred times would be fetched from Telegram
a hundred times, which is slow for the visitor and rude to Telegram.

The cache is deliberately dumb: whole parts only, evicted oldest first, and
every operation degrades to a miss rather than an error. Nothing here is
allowed to break a download; a cache that can fail a request is worse than no
cache.
"""
import hashlib
import logging
import os
import tempfile

from django.conf import settings

logger = logging.getLogger(__name__)

READ_SIZE = 64 * 1024


def _enabled():
    return bool(_directory()) and _limit() > 0


def _directory():
    path = getattr(settings, 'TELEGRAM_CACHE_DIR', None)
    if path:
        return str(path)
    root = getattr(settings, 'MEDIA_ROOT', None)
    return os.path.join(str(root), 'telegram-cache') if root else None


def _limit():
    return int(getattr(settings, 'TELEGRAM_CACHE_BYTES', 2 * 1024 ** 3))


def _path(key):
    # The key comes from Telegram and is not guaranteed to be a safe filename.
    digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
    return os.path.join(_directory(), f'{digest}.part')


def read(key, skip, take):
    """Return an iterator over [skip, take) of a cached part, or None."""
    if not _enabled():
        return None

    path = _path(key)
    try:
        handle = open(path, 'rb')
    except OSError:
        return None

    # Reading counts as use, so a busy part is not evicted under a live stream.
    try:
        os.utime(path, None)
    except OSError:
        pass

    return _iter_range(handle, skip, take)


def _iter_range(handle, skip, take):
    try:
        handle.seek(skip)
        remaining = max(0, take - skip)
        while remaining > 0:
            block = handle.read(min(READ_SIZE, remaining))
            if not block:
                return
            remaining -= len(block)
            yield block
    finally:
        handle.close()


class _Writer:
    """Collects a part into a temp file, then puts it in place atomically.

    The rename is what makes concurrent downloads safe: a reader either sees no
    file or sees a complete one, never a half-written one.
    """

    def __init__(self, key, expected_bytes):
        self.key = key
        self.expected_bytes = expected_bytes
        directory = _directory()
        os.makedirs(directory, exist_ok=True)
        handle, self.temp_path = tempfile.mkstemp(dir=directory, suffix='.partial')
        self.handle = os.fdopen(handle, 'wb')
        self.written = 0

    def write(self, data):
        try:
            self.handle.write(data)
            self.written += len(data)
        except OSError as exc:
            logger.info('telegram cache write failed: %s', exc)
            self.abort()

    def close(self, complete):
        if self.handle is None:
            return
        try:
            self.handle.close()
        except OSError:
            pass
        self.handle = None

        # A truncated part is worse than no part: it would be served as if it
        # were the whole thing and silently corrupt the download.
        if not complete or self.written != self.expected_bytes:
            self._discard()
            return

        try:
            os.replace(self.temp_path, _path(self.key))
        except OSError as exc:
            logger.info('telegram cache rename failed: %s', exc)
            self._discard()
            return
        _evict()

    def abort(self):
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError:
                pass
            self.handle = None
        self._discard()

    def _discard(self):
        try:
            os.unlink(self.temp_path)
        except OSError:
            pass


class _NullWriter:
    def write(self, data):
        pass

    def close(self, complete):
        pass


def writer(key, expected_bytes):
    """A sink for a part being fetched. Never returns None; may be a no-op."""
    if not _enabled():
        return _NullWriter()
    try:
        return _Writer(key, expected_bytes)
    except OSError as exc:
        logger.info('telegram cache unavailable: %s', exc)
        return _NullWriter()


def forget(key):
    if not _enabled():
        return
    try:
        os.unlink(_path(key))
    except OSError:
        pass


def _entries():
    directory = _directory()
    try:
        names = os.listdir(directory)
    except OSError:
        return []

    found = []
    for name in names:
        if not name.endswith('.part'):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        found.append((stat.st_atime, stat.st_size, path))
    return found


def _evict():
    """Delete least recently used parts until the cache fits again."""
    entries = _entries()
    total = sum(size for _, size, _ in entries)
    limit = _limit()
    if total <= limit:
        return

    for _, size, path in sorted(entries):
        try:
            os.unlink(path)
        except OSError:
            continue
        total -= size
        if total <= limit:
            return


def stats():
    """Size and count, for the staff page and the cleanup command."""
    entries = _entries()
    return {
        'files': len(entries),
        'bytes': sum(size for _, size, _ in entries),
        'limit': _limit(),
        'enabled': _enabled(),
    }


def purge():
    """Empty the cache. Nothing here is authoritative, so this is always safe."""
    removed = 0
    for _, _, path in _entries():
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            continue
    return removed
