"""Per-user storage quota.

Usage is summed from the user's actual files rather than a separate counter.
A counter drifts from reality sooner or later: a failed delete, a migration,
an error mid-write. That drift either hands out free space or restricts
someone unfairly.
"""
from django.conf import settings
from django.db.models import Sum

GIGABYTE = 1024 ** 3

#: Free allowance per user.
DEFAULT_FREE_BYTES = 1 * GIGABYTE


class QuotaExceeded(Exception):
    """The upload would exceed the quota. The message is shown to the user."""

    def __init__(self, message, used, limit, incoming):
        super().__init__(message)
        self.used = used
        self.limit = limit
        self.incoming = incoming


def free_bytes():
    return getattr(settings, 'FREE_STORAGE_BYTES', DEFAULT_FREE_BYTES)


def limit_for(user):
    """Total allowance: base plus active packages."""
    from .models import StoragePlan

    return free_bytes() + StoragePlan.active_bonus_for(user)


def used_by(user):
    """Space the user takes on our storage.

    Files bound for the user's own Telegram channel are excluded from the
    moment they are uploaded, not from the moment the worker finishes. Charging
    for space we do not ultimately provide would make the feature pointless,
    and a file that is only briefly on our disk is not what a quota is for.

    That brief window is bounded separately by telegram_module.quota, which
    caps how many bytes one user may have queued at once. Without that cap,
    marking an upload for Telegram would be a way to ignore this function.

    Guest files have no author and are bounded by their auto-delete deadline.
    """
    from upload_module.models import UploadFile

    return UploadFile.objects.filter(author=user).exclude(
        storage_backend=UploadFile.StorageBackend.TELEGRAM,
    ).aggregate(total=Sum('size_bytes_cached'))['total'] or 0


def stored_on_telegram(user):
    """Space the user keeps in their own Telegram channel."""
    from upload_module.models import UploadFile

    return UploadFile.objects.filter(
        author=user, storage_backend=UploadFile.StorageBackend.TELEGRAM,
    ).aggregate(total=Sum('size_bytes_cached'))['total'] or 0


def remaining_for(user):
    return max(0, limit_for(user) - used_by(user))


def check_upload(user, incoming_bytes):
    """Called before saving. Raises QuotaExceeded if the upload does not fit."""
    from django.utils.translation import gettext as _

    limit = limit_for(user)
    used = used_by(user)

    if used + incoming_bytes <= limit:
        return

    raise QuotaExceeded(
        _('فضای شما پر است. %(free)s آزاد مانده و این فایل %(size)s است. '
          'برای فضای بیشتر اشتراک بگیر.') % {
            'free': human_size(max(0, limit - used)),
            'size': human_size(incoming_bytes),
        },
        used=used, limit=limit, incoming=incoming_bytes,
    )


def human_size(value):
    """A byte count in words, in whatever language is active.

    Terabytes are reachable: the largest package on sale is 2 TB, and stopping
    at gigabytes would print a quota of 2048.0 GB.

    The units are translated rather than fixed, or an English reader is told
    their quota in Persian words on an otherwise English page.
    """
    from django.utils.translation import gettext as _

    units = [
        (_('بایت'), True),
        (_('کیلوبایت'), False),
        (_('مگابایت'), False),
        (_('گیگابایت'), False),
        (_('ترابایت'), False),
    ]

    value = float(value or 0)
    for index, (unit, whole) in enumerate(units):
        last = index == len(units) - 1
        if value < 1024 or last:
            if whole:
                return f'{int(value)} {unit}'
            return f'{value:.1f} {unit}'
        value /= 1024


def percent_used(user):
    limit = limit_for(user)
    if not limit:
        return 0
    return min(100, round(used_by(user) * 100 / limit))
