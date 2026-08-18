"""API key management from the user panel."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from .models import ApiKey


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='20/h', method='POST'), 'dispatch')
class ApiKeyListView(View):
    template_name = 'api_module/keys.html'

    def context(self, **extra):
        context = {'keys': ApiKey.objects.filter(user=self.request.user)}
        context.update(extra)
        return context

    def get(self, request):
        return render(request, self.template_name, self.context())

    def post(self, request):
        action = request.POST.get('action')

        if action == 'revoke':
            key = get_object_or_404(
                ApiKey, pk=request.POST.get('key_id'), user=request.user,
            )
            key.revoke()
            return redirect(reverse('api_keys_page'))

        name = (request.POST.get('name') or '').strip()
        if not name:
            return render(request, self.template_name, self.context(
                error=_('برای کلید یک نام بگذار تا بعداً بدانی کجا استفاده می‌شود.'),
            ))

        _key, raw_key = ApiKey.generate(request.user, name[:100])

        # Shown once; only the hash is stored. Deliberately kept out of the
        # session and messages so a refresh does not reveal it again.
        return render(request, self.template_name, self.context(new_key=raw_key))


@method_decorator(login_required, 'dispatch')
class ApiDocsView(View):
    def get(self, request):
        return render(request, 'api_module/docs.html', {
            'base_url': request.build_absolute_uri('/').rstrip('/'),
        })
