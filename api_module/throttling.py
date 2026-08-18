"""Rate limits, counted per API key.

Per key rather than per address, because a client site may sit behind one fixed
address or call from several servers. Either way the quota belongs to the key,
and two customers behind one address must not eat each other's allowance.

Writing costs far more than reading, so uploads have their own much smaller
budget instead of sharing one number with list calls.
"""
from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class ApiKeyThrottle(SimpleRateThrottle):
    """Base class: identify the caller by key, and honour the global switch."""

    #: Only these methods are counted. None means all of them.
    methods = None

    def allow_request(self, request, view):
        # One switch for every rate limit in the project, so a test that turns
        # limiting off turns all of it off.
        if not getattr(settings, 'RATELIMIT_ENABLE', True):
            return True
        if self.methods and request.method not in self.methods:
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        api_key = getattr(request, 'auth', None)
        if api_key is not None:
            return f'throttle:{self.scope}:key:{api_key.pk}'
        # Before authentication there is no key yet; fall back to the address
        # so an unauthenticated flood is still bounded.
        return f'throttle:{self.scope}:ip:{self.get_ident(request)}'


class UploadThrottle(ApiKeyThrottle):
    scope = 'api-upload'
    methods = ('POST',)


class ReadThrottle(ApiKeyThrottle):
    scope = 'api-read'
    methods = ('GET', 'HEAD', 'OPTIONS')


class WriteThrottle(ApiKeyThrottle):
    scope = 'api-write'
    methods = ('PATCH', 'PUT', 'DELETE')
