"""A Redis cache that does not take the site down with it.

The rate limiter keeps its counters in the cache, so with the stock backend a
Redis outage turns every protected page into a 500 and the whole site goes with
it.

This subclass swallows connection errors and answers as if the key were
missing: rate limiting stops applying while Redis is down, but the site keeps
serving. The failure is logged so it does not pass unnoticed.
"""
import functools
import logging

from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)

# redis may not be installed, for example under LocMem in tests.
try:
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    RedisError = ()

# System level network errors count too, not only redis-py ones.
CONNECTION_ERRORS = (ConnectionError, TimeoutError, OSError) + (
    (RedisError,) if RedisError else ()
)


def _fail_soft(default=None):
    """Wrap a cache method so a connection error returns a neutral value."""

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except CONNECTION_ERRORS as exc:
                _log(method.__name__, exc)
                return default() if callable(default) else default

        return wrapper

    return decorator


def _log(name, exc):
    # No em dash: this line is read in a terminal, and a console on the
    # system codepage turns anything outside it into a question mark.
    logger.warning('cache unavailable (%s): %s. Falling back to no-cache.',
                   name, exc)


class ResilientRedisCache(RedisCache):
    def get(self, key, default=None, version=None):
        # Not _fail_soft: that decorator returns a value fixed at decoration
        # time, which would hand back None to a caller that asked for [] and
        # turn a Redis outage into a TypeError further up the stack. A missing
        # key and an unreachable Redis must look identical to the caller, so
        # the answer has to be the default this call passed in.
        try:
            return super().get(key, default, version)
        except CONNECTION_ERRORS as exc:
            _log('get', exc)
            return default

    @_fail_soft()
    def set(self, *args, **kwargs):
        return super().set(*args, **kwargs)

    @_fail_soft(default=True)
    def add(self, *args, **kwargs):
        # True means treat it as freshly created; the counter starts at zero.
        return super().add(*args, **kwargs)

    @_fail_soft(default=1)
    def incr(self, *args, **kwargs):
        # 1 means first request in this window, so let it through.
        return super().incr(*args, **kwargs)

    @_fail_soft()
    def delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)

    @_fail_soft(default=dict)
    def get_many(self, *args, **kwargs):
        return super().get_many(*args, **kwargs)

    @_fail_soft()
    def touch(self, *args, **kwargs):
        return super().touch(*args, **kwargs)

    @_fail_soft()
    def clear(self):
        return super().clear()
