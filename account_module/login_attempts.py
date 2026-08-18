"""Failed login counting, used to decide when to show a captcha.

The counter lives in the cache rather than the database: it is short-lived, and
writing every failed attempt to disk would itself be an abusable pressure
point.
"""
import logging

from django.conf import settings
from django.core.cache import cache

from utils.http_service import get_client_ip

logger = logging.getLogger(__name__)

KEY_TEMPLATE = 'login-failures:{ip}'


def _key(request):
    return KEY_TEMPLATE.format(ip=get_client_ip(request) or 'unknown')


def _window():
    return getattr(settings, 'LOGIN_FAILURE_WINDOW_SECONDS', 15 * 60)


def failure_count(request):
    try:
        return cache.get(_key(request), 0) or 0
    except Exception:
        # A cache failure must not break login. At worst the captcha is not
        # shown, and the rate limit still applies.
        logger.warning('login failure counter unavailable', exc_info=True)
        return 0


def record_failure(request):
    key = _key(request)
    try:
        # add only sets when the key is absent, so the window starts at the first
        # failure rather than being extended by each one.
        cache.add(key, 0, _window())
        return cache.incr(key)
    except ValueError:
        # The key expired between add and incr.
        cache.set(key, 1, _window())
        return 1
    except Exception:
        logger.warning('could not record login failure', exc_info=True)
        return 0


def reset(request):
    """Clear the counter after a successful login."""
    try:
        cache.delete(_key(request))
    except Exception:
        logger.warning('could not reset login failures', exc_info=True)


def captcha_required(request):
    threshold = getattr(settings, 'LOGIN_CAPTCHA_AFTER_FAILURES', 3)
    return failure_count(request) >= threshold
