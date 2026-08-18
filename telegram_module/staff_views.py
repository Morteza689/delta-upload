"""Staff page: the feature switch, and how the feature is being used.

No credentials here. Every token belongs to a user and is encrypted on their
own row, so there is nothing site-wide for staff to hold, see, or leak.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View

from . import cache, crypto
from .models import TelegramConnection, TelegramFile, TelegramSetting


@method_decorator(login_required, 'dispatch')
class TelegramSettingsView(View):
    template_name = 'staff_module/telegram.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return redirect(reverse('login_page'))
        return super().dispatch(request, *args, **kwargs)

    def context(self, notice=''):
        connections = TelegramConnection.objects.aggregate(
            total=Count('id'),
            with_bot=Count('id', filter=~Q(bot_token_encrypted='')),
            active=Count('id', filter=Q(status=TelegramConnection.Status.ACTIVE)),
            broken=Count('id', filter=Q(status=TelegramConnection.Status.BROKEN)),
        )
        files = TelegramFile.objects.aggregate(
            total=Count('id'),
            stored=Count('id', filter=Q(status=TelegramFile.Status.STORED)),
            failed=Count('id', filter=Q(status=TelegramFile.Status.FAILED)),
            queued=Count('id', filter=Q(status__in=[
                TelegramFile.Status.PENDING, TelegramFile.Status.UPLOADING,
            ])),
            bytes=Sum('total_bytes', filter=Q(status=TelegramFile.Status.STORED)),
        )

        return {
            'setting': TelegramSetting.load(),
            'connections': connections,
            'files': files,
            'cache': cache.stats(),
            # A derived key works, but it ties every stored token to
            # SECRET_KEY: rotating that would lock every user out of their own
            # files with nothing on screen to explain why.
            'key_is_explicit': crypto.key_is_explicit(),
            'unreadable': self.unreadable_count(),
            'notice': notice,
        }

    def unreadable_count(self):
        """Tokens the current key cannot decrypt.

        Anything above zero means the encryption key changed and those users
        must register their bot again. Worth surfacing before they write in.
        """
        broken = 0
        for connection in TelegramConnection.objects.exclude(
            bot_token_encrypted='',
        ).only('bot_token_encrypted'):
            try:
                connection.token
            except crypto.DecryptionFailed:
                broken += 1
        return broken

    def get(self, request):
        return render(request, self.template_name, self.context())

    def post(self, request):
        if request.POST.get('action') == 'toggle':
            setting = TelegramSetting.load()
            setting.is_enabled = not setting.is_enabled
            setting.save()
            return render(request, self.template_name, self.context(
                notice=(_('قابلیت روشن شد.') if setting.is_enabled
                        else _('قابلیت خاموش شد. صفحه‌ی کاربران دیگر باز '
                               'نمی‌شود، ولی فایل‌های موجود سر جایشان هستند.')),
            ))
        return redirect(reverse('staff_telegram'))
