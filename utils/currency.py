"""Display currency: dollars or toman.

Amounts are stored in cents everywhere. This module only affects presentation,
never storage or arithmetic.

The active currency lives in a ContextVar, mirroring how Django handles the
active language: the middleware sets it per request and the template filters
read it, so nothing has to be threaded through every call.
"""
from contextvars import ContextVar

USD = 'usd'
TOMAN = 'toman'

CHOICES = (
    (USD, 'دلار'),
    (TOMAN, 'تومان'),
)
VALID = {USD, TOMAN}

COOKIE_NAME = 'dup_currency'
COOKIE_AGE = 60 * 60 * 24 * 365

_active = ContextVar('dup_currency', default=USD)
_rate = ContextVar('dup_currency_rate', default=0)


def activate(currency, rate=0):
    """Set the active currency for this request."""
    _active.set(currency if currency in VALID else USD)
    _rate.set(rate or 0)


def get_currency():
    return _active.get()


def get_rate():
    """Toman per dollar. Zero means no rate has been recorded."""
    return _rate.get()


def normalize(value):
    return value if value in VALID else USD


def format_money(cents):
    """Format cents in the active currency."""
    try:
        amount = int(cents)
    except (TypeError, ValueError):
        return cents

    if get_currency() == TOMAN:
        rate = get_rate()
        if not rate:
            # No rate recorded; fall back to dollars rather than show a meaningless
            # number.
            return _format_usd(amount)
        toman = round(amount * rate / 100)
        return f'{toman:,} تومان'

    return _format_usd(amount)


def _format_usd(cents):
    sign = '-' if cents < 0 else ''
    cents = abs(cents)
    return f'{sign}${cents // 100:,}.{cents % 100:02d}'


def format_micro(micro_cents):
    """Format micro-cents.

    Ad revenue is a fraction of a cent, so this keeps more decimal places than
    format_money.
    """
    try:
        micro = int(micro_cents)
    except (TypeError, ValueError):
        return micro_cents

    if get_currency() == TOMAN:
        rate = get_rate()
        if rate:
            toman = round(micro * rate / 1_000_000)
            return f'{toman:,} تومان'

    return f'${micro // 1_000_000:,}.{micro % 1_000_000 // 100:04d}'
