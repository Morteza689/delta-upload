"""Display currency switching."""
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from utils import currency


@require_POST
def set_currency(request):
    """Store the chosen currency in a cookie.

    POST only: a GET that changes state can be triggered by any image tag.
    """
    selected = currency.normalize(request.POST.get('currency'))

    # The return target must stay inside the site, or this form becomes an open
    # redirect for phishing.
    next_url = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse('home_page')

    response = HttpResponseRedirect(next_url)
    response.set_cookie(
        currency.COOKIE_NAME, selected,
        max_age=currency.COOKIE_AGE, samesite='Lax',
    )
    return response
