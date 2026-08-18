from django.contrib.auth.decorators import login_required
from django.db.models import F
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django_ratelimit.decorators import ratelimit

from ads_module import services as ads_services

from . import downloads
from .models import DownloadLink, FileDownload, UploadFile


# The old UploadFileView was removed. It duplicated the one in
# user_panel_module but with a 5 character slug instead of 12, no rate limit
# and no size cap.

@method_decorator(login_required, 'dispatch')
class MyFileVeiw(ListView):
    template_name = 'upload_module/my_files.html'
    context_object_name = 'file'

    def get_queryset(self):
        # Own files only. This used to filter on a ?user_email= parameter,
        # which let anyone list anyone else's files.
        return UploadFile.objects.filter(
            author_id=self.request.user.id, deleted_at__isnull=True,
        )


@login_required
@ratelimit(key='user', rate='120/h')
def owner_download(request: HttpRequest, pk: int):
    """Owner download from the panel: no token, not counted."""
    upload_file = get_object_or_404(UploadFile, pk=pk, author_id=request.user.id)
    if not upload_file.has_content():
        raise Http404("File is missing")

    size = upload_file.size_bytes or 0

    def on_complete(complete=True, reached_end=True):
        # Logged for the report but raises no public counter: an owner fetching
        # their own file is neither a download statistic nor revenue.
        if not reached_end:
            return
        FileDownload.objects.create(
            file_id=upload_file.pk, kind=FileDownload.Kind.OWNER, bytes_sent=size,
        )

    return downloads.serve(upload_file, on_complete=on_complete, request=request)


# Bandwidth is expensive. The token is single use and short lived, but this
# cap stops someone minting tokens back to back for a bulk download.
@ratelimit(key='ip', rate='60/h')
def download_file(request: HttpRequest, token):
    """Download with the single-use token issued by the download pages."""
    download_link = (
        DownloadLink.objects
        .select_related('file')
        .filter(token=token, is_used=False, expires_at__gt=timezone.now())
        .first()
    )

    if download_link is None:
        raise Http404("Link is invalid or has expired")

    upload_file = download_link.file
    if not upload_file.has_content():
        raise Http404("File is missing")

    kind = download_link.kind or FileDownload.Kind.FREE
    visitor = ads_services.visitor_hash(request)
    size = upload_file.size_bytes or 0

    def on_complete(complete=True, reached_end=True):
        """Called once delivery finishes, with what was actually delivered.

        A download manager opens with a small probe range and then pulls the
        file in pieces. Burning the link on the first of those would break
        every request after it, which is most of the download.

        So the link is burned only by a delivery that covered the whole file.
        A partial one leaves it alive and relies on the fifteen minute expiry
        instead, which still bounds how long it can be reused.
        """
        if complete:
            DownloadLink.objects.filter(pk=download_link.pk).update(is_used=True)

        # Counted once, when the piece containing the last byte arrives. That
        # is the same event for a plain download and for a chunked one, and it
        # cannot fire twice for the same link.
        if not reached_end:
            return

        counters = {'number_of_download': F('number_of_download') + 1}
        if kind == FileDownload.Kind.PAID:
            counters['paid_downloads'] = F('paid_downloads') + 1
        else:
            counters['free_downloads'] = F('free_downloads') + 1
        UploadFile.objects.filter(pk=upload_file.pk).update(**counters)

        FileDownload.objects.create(
            file_id=upload_file.pk, kind=kind,
            visitor_hash=visitor, bytes_sent=size,
        )

    return downloads.serve(upload_file, on_complete=on_complete, request=request)
