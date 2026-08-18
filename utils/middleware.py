"""Turn rate limit errors into 429 responses.

django-ratelimit raises Ratelimited, a PermissionDenied subclass, so Django
answers 403 by default. That tells the visitor they lack permission when the
truth is they should wait.
"""
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django_ratelimit.exceptions import Ratelimited

logger = logging.getLogger(__name__)


class RatelimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, Ratelimited):
            return None

        logger.warning(
            'rate limit hit: %s %s (user=%s)',
            request.method, request.path,
            request.user.pk if request.user.is_authenticated else 'anon',
        )

        message = _('درخواست‌های شما بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.')

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'detail': message}, status=429)

        return render(request, '429.html', {'message': message}, status=429)
