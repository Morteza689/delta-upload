"""Exchange rate updates from an external provider.

Separate from rates.py: that one knows how to read a number from a given
service, this one knows when to ask, where to store it, and what to do on
failure.
"""
import logging

from django.utils import timezone

from . import rates
from .models import ExchangeRate, RateSetting

logger = logging.getLogger(__name__)


def refresh(setting=None, force=False):
    """Fetch the rate from the API and record it.

    Returns (rate or None, error message).

    Never raises. Wherever this is called from, a failed lookup must pass
    quietly and leave the previous rate in place: a payment at yesterday's
    rate beats a payment that cannot happen.

    force means fetch even when automatic updates are off, for the panel's
    fetch-now button.
    """
    setting = setting or RateSetting.load()

    if not setting.auto_update and not force:
        return None, ''

    setting.last_checked_at = timezone.now()

    try:
        toman = rates.fetch(setting.provider, setting.api_key)
    except rates.RateError as exc:
        setting.last_error = str(exc)[:300]
        setting.save(update_fields=['last_checked_at', 'last_error'])
        logger.warning('نرخ ارز به‌روز نشد: %s', exc)
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        # A parsing bug must not take down the cron job or the admin page.
        setting.last_error = f'خطای غیرمنتظره: {exc}'[:300]
        setting.save(update_fields=['last_checked_at', 'last_error'])
        logger.exception('خطای غیرمنتظره در گرفتن نرخ ارز')
        return None, setting.last_error

    rate = ExchangeRate.set_rate(
        toman, source=ExchangeRate.Source.API, provider=setting.provider,
    )

    setting.last_success_at = setting.last_checked_at
    setting.last_error = ''
    setting.save(update_fields=['last_checked_at', 'last_success_at', 'last_error'])

    return rate, ''
