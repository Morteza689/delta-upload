"""Shared payment gateway contract."""


class GatewayError(Exception):
    """The gateway could not complete the request."""


class GatewayNotConfigured(GatewayError):
    """This gateway has no keys set."""


class PaymentDeclined(GatewayError):
    """The payment failed or was cancelled. Not our error."""


class BaseGateway:
    slug = ''
    label = ''

    #: Is the user returning from the gateway proof of payment on its own?
    #:
    #: For bank gateways yes: we verify server side on return and the answer is
    #: final. For crypto no: the transaction must confirm on chain, which can
    #: take minutes, so crediting happens only via the webhook.
    settles_on_return = True

    def __init__(self, config):
        self.config = config or {}

    def is_configured(self):
        raise NotImplementedError

    def check_configured(self):
        if not self.is_configured():
            raise GatewayNotConfigured(f'درگاه {self.slug} پیکربندی نشده است')

    def start(self, payment, return_url, ipn_url=None):
        """Create the payment and return the URL to send the user to."""
        raise NotImplementedError

    def verify(self, payment, data):
        """Verify the payment and return a tracking reference.

        Raises PaymentDeclined when the gateway says it did not go through.
        """
        raise NotImplementedError
