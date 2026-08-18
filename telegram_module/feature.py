"""Whether the feature is offered at all.

There is no site-wide bot token to check, so availability is a plain switch
rather than a question about credentials. It lives in the database so it can be
withdrawn without a deploy, which matters for something that depends on a third
party staying reachable.

This is read on every request, by the context processor that decides whether to
show the navigation link, so it is cached and never writes. A switch read on a
GET must not be able to insert a row.
"""
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError

CACHE_KEY = 'telegram:enabled'

#: Long, because the value changes about once a year and the staff page clears
#: the key the moment it does.
CACHE_SECONDS = 3600


def is_enabled():
    """Is the Telegram storage feature on?"""
    if not getattr(settings, 'TELEGRAM_STORAGE_ENABLED', True):
        # A hard off switch for an operator who wants it gone regardless of
        # what is in the database.
        return False

    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    value = _read()
    cache.set(CACHE_KEY, value, CACHE_SECONDS)
    return value


def _read():
    """The stored switch, defaulting to on.

    Deliberately a plain read: get_or_create here would mean a request for the
    sitemap could write to the database.

    Tolerates an unmigrated database, because this is called on pages that have
    nothing to do with Telegram and an optional feature must not take the site
    down during a deploy where the migration has not run yet.
    """
    try:
        from .models import TelegramSetting

        stored = (
            TelegramSetting.objects
            .filter(pk=1)
            .values_list('is_enabled', flat=True)
            .first()
        )
    except DatabaseError:
        return False
    # No row yet means nobody has turned it off.
    return True if stored is None else stored


def forget():
    """Drop the cached value. Called whenever the switch is saved."""
    cache.delete(CACHE_KEY)
