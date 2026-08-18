"""Client address resolution.

X-Forwarded-For is only trusted as far as TRUSTED_PROXY_COUNT says it should
be. The header is a client-supplied string: anyone can send one, so reading it
blindly means an attacker becomes a new visitor on every request and both rate
limiting and ad anti-fraud stop meaning anything.

Zero, the default, ignores the header entirely.
"""
from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """The client address, accounting for trusted proxies."""
    remote_addr = request.META.get('REMOTE_ADDR', '') or ''
    proxy_count = getattr(settings, 'TRUSTED_PROXY_COUNT', 0)

    if proxy_count <= 0:
        return remote_addr

    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not forwarded:
        return remote_addr

    parts = [part.strip() for part in forwarded.split(',') if part.strip()]
    if not parts:
        return remote_addr

    # The client can forge the left-hand entries. Only the ones our own proxies
    # appended are trustworthy, and those are counted from the right.
    index = len(parts) - proxy_count
    if index < 0:
        # The chain is shorter than expected: either the configuration is wrong or
        # the header was tampered with. Fall back to the reliable value.
        return remote_addr

    return parts[index]


def ratelimit_key(request: HttpRequest) -> str:
    """The address django-ratelimit counts against.

    Pointed at by RATELIMIT_IP_META_KEY. Left to itself the library reads
    REMOTE_ADDR directly and raises ImproperlyConfigured when it is empty,
    which is exactly the case behind a unix socket: there is no peer address to
    report, because the peer is a file. Every POST on the site answered 500 —
    login, upload, all of it — while every GET was fine.

    It also has to be this function rather than the header name, so the same
    trusted-proxy rules apply here as everywhere else. Handing the library
    HTTP_X_FORWARDED_FOR directly would let anyone become a new visitor per
    request by sending their own.

    An address we cannot determine shares one bucket instead of raising. That
    direction is the safe one: a shared limit can only refuse more than it
    should, never less.
    """
    return get_client_ip(request) or 'unknown'
