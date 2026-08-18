"""The cap on how much a user may have waiting to move to Telegram.

Files bound for Telegram do not count against the storage quota, because they
end up costing us nothing. But between the upload finishing and the worker
running, they are on our disk like anything else.

Without a limit that gap is the quota's back door: tick the Telegram box on
every upload and the 1 GB allowance never applies. So the queue gets a cap of
its own, sized to let a few large files through at once and nothing more.
"""
from django.conf import settings
from django.db.models import Sum
from django.utils.translation import gettext as _

GIGABYTE = 1024 ** 3

#: Enough for a couple of large files in flight, not enough to park a library.
DEFAULT_STAGING_BYTES = 4 * GIGABYTE


class StagingFull(Exception):
    """The queue is full. The message is shown to the user."""


def staging_limit():
    return int(getattr(settings, 'TELEGRAM_STAGING_BYTES', DEFAULT_STAGING_BYTES))


def staging_used(user):
    """Bytes this user has queued but not yet moved."""
    from .models import TelegramFile

    return TelegramFile.objects.filter(
        connection__user=user,
        status__in=[TelegramFile.Status.PENDING, TelegramFile.Status.UPLOADING],
    ).aggregate(total=Sum('total_bytes'))['total'] or 0


def check_queue(user, incoming_bytes):
    """Called before accepting an upload bound for Telegram."""
    from account_module.quota import human_size

    limit = staging_limit()
    used = staging_used(user)
    if used + incoming_bytes <= limit:
        return

    raise StagingFull(
        _('در صف انتقال به تلگرام جا نیست. %(free)s آزاد است و این فایل '
          '%(size)s. صبر کن تا فایل‌های قبلی منتقل شوند.') % {
            'free': human_size(max(0, limit - used)),
            'size': human_size(incoming_bytes),
        }
    )
