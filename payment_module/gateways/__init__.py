"""Payment gateway registry."""
from django.conf import settings

from .base import (BaseGateway, GatewayError, GatewayNotConfigured,
                   PaymentDeclined)
from .crypto import CryptoGateway
from .zarinpal import ZarinPalGateway

GATEWAY_CLASSES = {
    ZarinPalGateway.slug: ZarinPalGateway,
    CryptoGateway.slug: CryptoGateway,
}


def get_gateway(slug):
    """A gateway instance, or None if there is no such gateway."""
    gateway_class = GATEWAY_CLASSES.get(slug)
    if gateway_class is None:
        return None
    config = getattr(settings, 'PAYMENT_GATEWAYS', {}).get(slug, {})
    return gateway_class(config)


def available_gateways():
    """Only gateways whose keys are set.

    An unconfigured gateway is never offered: better than letting someone pick it
    and hit an error.
    """
    gateways = []
    for slug in GATEWAY_CLASSES:
        gateway = get_gateway(slug)
        if gateway is not None and gateway.is_configured():
            gateways.append(gateway)
    return gateways


__all__ = [
    'BaseGateway', 'GatewayError', 'GatewayNotConfigured', 'PaymentDeclined',
    'CryptoGateway', 'ZarinPalGateway',
    'get_gateway', 'available_gateways', 'GATEWAY_CLASSES',
]
