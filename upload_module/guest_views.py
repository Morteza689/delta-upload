"""Guest upload views."""
import logging

from django.shortcuts import render
from django.utils.crypto import get_random_string
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from .guest import DEFAULT_LIFETIME_HOURS, GuestUploadForm, guest_size_limit
from .models import UploadFile

logger = logging.getLogger(__name__)


@method_decorator(ratelimit(key='ip', rate='10/h', method='POST'), 'dispatch')
class GuestUploadView(View):
    """Upload without an account.

    A guest file has no owner, so it earns nothing and appears in nobody's panel.
    It is a temporary download link and nothing more.
    """

    template_name = 'upload_module/guest_upload.html'

    def context(self, form, upload=None):
        return {
            'form': form,
            'upload': upload,
            'size_limit_mb': guest_size_limit() // (1024 * 1024),
            'default_hours': DEFAULT_LIFETIME_HOURS,
        }

    def get(self, request):
        return render(request, self.template_name, self.context(GuestUploadForm()))

    def post(self, request):
        form = GuestUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, self.context(form))

        upload = UploadFile(
            author=None,
            name=(form.cleaned_data.get('name') or form.cleaned_data['file'].name)[:100],
            visibility=UploadFile.Visibility.PUBLIC,
            type_file=UploadFile.ChoiceType.File,
            slug=get_random_string(12),
            auto_delete_at=form.auto_delete_at,
        )
        upload.file = form.cleaned_data['file']
        upload.original_filename = form.cleaned_data['file'].name[:255]
        upload.save()

        logger.info('guest upload %s expires %s', upload.slug, upload.auto_delete_at)
        return render(request, self.template_name,
                      self.context(GuestUploadForm(), upload=upload))
