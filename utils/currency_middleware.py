"""Per-request display currency activation."""
from utils import currency


class CurrencyMiddleware:
    """Read the display currency from the cookie and activate it.

    The exchange rate is only loaded when toman is selected, so the default
    path costs no query.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        selected = currency.normalize(request.COOKIES.get(currency.COOKIE_NAME))
        rate = 0

        if selected == currency.TOMAN:
            rate = self.current_rate()
            if not rate:
                # No rate recorded; a toman figure would be meaningless, so fall back to
                # dollars.
                selected = currency.USD

        currency.activate(selected, rate)
        request.display_currency = selected

        response = self.get_response(request)
        # Keep the ContextVar from leaking between requests on the same thread.
        currency.activate(currency.USD, 0)
        return response

    def current_rate(self):
        from payment_module.models import ExchangeRate

        rate = ExchangeRate.current()
        return rate.usd_to_toman if rate else 0
