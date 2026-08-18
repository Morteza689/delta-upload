"""Setting up a user's own bot and channel.

There is no webhook view any more. Because each user's bot token belongs to
them, updates are fetched with it on demand when they press a button, so this
module has no public endpoint at all.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from . import feature, importing, linking, quota, storage
from .models import TelegramConnection, TelegramFile

logger = logging.getLogger(__name__)


@method_decorator(login_required, 'dispatch')
# Each action here spends a call to Telegram, and a token check does so before
# anything is stored. The cap is generous for real use and closes the door on
# using this page to probe tokens.
@method_decorator(ratelimit(key='user', rate='60/h', method='POST'), 'dispatch')
class ConnectView(View):
    """Register a bot, connect a channel, and import posts."""

    template_name = 'telegram_module/connect.html'

    def dispatch(self, request, *args, **kwargs):
        if not feature.is_enabled():
            raise Http404('Telegram storage is switched off')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        if request.GET.get('progress'):
            return self._progress(request)

        connection = linking.connection_for(request.user)
        return render(request, self.template_name,
                      self._context(request, connection))

    def post(self, request):
        action = request.POST.get('action')
        handler = {
            'register_bot': self._register_bot,
            'forget_bot': self._forget_bot,
            'find_channel': self._find_channel,
            'disconnect': self._disconnect,
            'recheck': self._recheck,
            'new_code': self._new_code,
            'import': self._import,
        }.get(action)

        if handler is None:
            return redirect(reverse('telegram_connect_page'))
        return handler(request)

    def _register_bot(self, request):
        try:
            connection = linking.register_bot(
                request.user, request.POST.get('bot_token', ''),
            )
        except linking.LinkError as exc:
            return self._error(request, str(exc))

        messages.success(request, _('ربات @%(name)s ثبت شد.') % {
            'name': connection.bot_username or '',
        })
        return redirect(reverse('telegram_connect_page'))

    def _forget_bot(self, request):
        linking.forget_bot(request.user)
        messages.success(request, _('ربات حذف شد.'))
        return redirect(reverse('telegram_connect_page'))

    def _find_channel(self, request):
        try:
            connection = linking.find_channel(request.user)
        except linking.LinkError as exc:
            return self._error(request, str(exc))

        messages.success(request, _('کانال «%(title)s» متصل شد.') % {
            'title': connection.chat_title or connection.chat_id,
        })
        return redirect(reverse('telegram_connect_page'))

    def _disconnect(self, request):
        linking.disconnect(request.user)
        messages.success(request, _('اتصال کانال قطع شد.'))
        return redirect(reverse('telegram_connect_page'))

    def _recheck(self, request):
        connection = linking.connection_for(request.user, create=False)
        if connection and storage.repair_connection(connection):
            messages.success(request, _('اتصال دوباره برقرار است.'))
        else:
            messages.error(request, _('هنوز وصل نیست. دسترسی ربات را بررسی کن.'))
        return redirect(reverse('telegram_connect_page'))

    def _new_code(self, request):
        connection = linking.connection_for(request.user)
        connection.rotate_code()
        messages.success(request, _('کد تازه ساخته شد.'))
        return redirect(reverse('telegram_connect_page'))

    def _import(self, request):
        """Register a post the user sent to their channel themselves."""
        connection = linking.connection_for(request.user, create=False)
        try:
            upload_file = importing.import_post(
                request.user, connection,
                link=request.POST.get('post_link', ''),
                description=request.POST.get('description', ''),
            )
        except importing.ImportError_ as exc:
            return self._error(request, '', import_error=str(exc),
                               post_link=request.POST.get('post_link', ''))

        messages.success(request, _('«%(name)s» اضافه شد.') % {
            'name': upload_file.name,
        })
        return redirect(reverse('my_files_page'))

    def _progress(self, request):
        """Transfer progress as JSON, for the bar on the page.

        A small endpoint rather than a full page reload: the table can be long,
        and reloading it every few seconds to move one number would throw away
        the user's scroll position each time.
        """
        connection = linking.connection_for(request.user, create=False)
        rows = []
        if connection is not None:
            queryset = (
                TelegramFile.objects
                .filter(connection=connection)
                .prefetch_related('chunks')[:50]
            )
            rows = [{
                'id': row.pk,
                'status': row.status,
                'label': str(row.get_status_display()),
                'percent': row.progress_percent,
                'sent': row.sent_bytes,
                'total': row.total_bytes,
                'in_flight': row.in_flight(),
            } for row in queryset]

        return JsonResponse({
            'files': rows,
            # The script stops polling once nothing is moving, so an idle page
            # costs one request rather than one every few seconds forever.
            'busy': any(row['in_flight'] for row in rows),
        })

    def _error(self, request, error, **extra):
        """Re-render rather than redirect, so what was typed survives.

        A token or a post link is something the user had to go and copy. Losing
        it on a failed submit means a second trip for no reason.
        """
        connection = linking.connection_for(request.user)
        context = self._context(request, connection)
        context['error'] = error
        context.update(extra)
        return render(request, self.template_name, context)

    def _context(self, request, connection):
        from account_module.quota import human_size

        files = TelegramFile.objects.filter(
            connection=connection,
        ).select_related('upload_file')

        return {
            'connection': connection,
            'has_bot': connection.has_bot(),
            'is_linked': connection.status == TelegramConnection.Status.ACTIVE,
            'files': files[:50],
            'pending_count': files.filter(
                status__in=[TelegramFile.Status.PENDING,
                            TelegramFile.Status.UPLOADING],
            ).count(),
            'failed_count': files.filter(status=TelegramFile.Status.FAILED).count(),
            'stored_bytes': human_size(
                sum(row.total_bytes for row in files
                    if row.status == TelegramFile.Status.STORED)
            ),
            'staging_used': human_size(quota.staging_used(request.user)),
            'staging_limit': human_size(quota.staging_limit()),
            'import_limit': human_size(importing.MAX_IMPORT_BYTES),
        }
