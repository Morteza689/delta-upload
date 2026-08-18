"""The only network call in the payment module.

Every gateway goes through this function so tests can patch one place and run
without touching a live service.
"""
import json
import logging

import requests

from .base import GatewayError

logger = logging.getLogger(__name__)

TIMEOUT = 20


def post_json(url, payload, headers=None):
    """POST JSON and return the JSON response.

    Any network error or invalid response becomes a GatewayError, so the views
    never need to know about the HTTP library.
    """
    try:
        response = requests.post(
            url, json=payload, headers=headers or {}, timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning('payment gateway request failed: %s %s', url, exc)
        raise GatewayError(f'ارتباط با درگاه برقرار نشد: {exc}') from exc

    try:
        return response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning('payment gateway returned non-json: %s %s',
                       url, response.text[:200])
        raise GatewayError('پاسخ درگاه قابل خواندن نبود') from exc
