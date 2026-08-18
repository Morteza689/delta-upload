from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django_ratelimit.decorators import ratelimit

from account_module import wallet
from ads_module import services as ads
from ads_module.models import AdSlot, DownloadSession
from upload_module import purchases
from upload_module.models import DownloadLink, FileDownload, UploadFile


def create_download_link(file, expiration_minutes=10, kind=None):
    """Issue a short-lived download link.

    The kind (free or paid) is recorded here because this is the only point
    where the purchase context is still available.
    """
    expires_at = timezone.now() + timedelta(minutes=expiration_minutes)
    download_link = DownloadLink.objects.create(
        file=file, expires_at=expires_at,
        kind=kind or FileDownload.Kind.FREE,
    )
    return download_link.token


def get_public_file_or_404(slug):
    """A publicly reachable file, or 404.

    Private files answer 404 rather than 403, which would confirm they exist.
    """
    upload_file = get_object_or_404(UploadFile, slug=slug)
    if not upload_file.is_public_now():
        raise Http404('File is not publicly available')
    return upload_file


# The cap is generous because many users share one address behind CGNAT, and a
# tight limit would punish real people. The goal here is to stop a script from
# filling the DownloadSession table; ad fraud is handled separately in
# ads_module.
@method_decorator(ratelimit(key='ip', rate='200/h'), 'dispatch')
class DwonloadPageView(View):
    """Step 1: file details, ad, and the start of a download session."""

    def get(self, request, slug):
        upload_file = get_public_file_or_404(slug)
        session = ads.start_session(request, upload_file)

        context = {
            'file': upload_file,
            'session_token': session.token,
            'ad_slot': ads.pick_slot(AdSlot.Position.STEP1),
            'ad_loader': ads.loader_script(),
            'needs_purchase': (
                not upload_file.is_free()
                and not upload_file.is_purchased_by(request.user)
            ),
        }
        return render(request, 'download_module/download_page.html', context=context)


@method_decorator(ratelimit(key='ip', rate='200/h'), 'dispatch')
class DwonloadPage2View(View):
    """Step 2: ad, then the download link.

    Reachable only with a session token issued at step 1.
    """

    def get(self, request: HttpRequest, token):
        session = get_object_or_404(
            DownloadSession.objects.select_related('file'), token=token,
        )
        upload_file = session.file

        if not upload_file.is_public_now():
            raise Http404('File is not publicly available')

        # The session is bound to the visitor and to a time window.
        #
        # Without it the step 2 link was a permanent, shareable bypass of step
        # 1: post it anywhere and a thousand people take the file without one
        # ad being seen. Anti-fraud only blocked the revenue, not the access.
        #
        # Redirect rather than 404: someone who got the link from a friend sees
        # step 1, the ad renders, and they get their own token.
        if not ads.session_belongs_to(request, session):
            return redirect(reverse('download_page', args=[upload_file.slug]))

        kind = FileDownload.Kind.FREE
        if not upload_file.is_free():
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not upload_file.is_purchased_by(request.user):
                return redirect(reverse('purchase_page', args=[upload_file.slug]))
            # The owner passes through here too, but never as a paid download.
            if upload_file.author_id != request.user.id:
                kind = FileDownload.Kind.PAID

        # Count the impression and record revenue. An invalid pass returns
        # None and the download still proceeds; only the earning is skipped.
        ads.complete_session(request, session)

        download_token = create_download_link(
            upload_file, expiration_minutes=15, kind=kind,
        )
        download_url = request.build_absolute_uri(
            reverse('download_file', args=[download_token])
        )

        context = {
            'file': upload_file,
            'download_url': download_url,
            'ad_slot': ads.pick_slot(AdSlot.Position.STEP2),
            'ad_loader': ads.loader_script(),
        }
        return render(
            request,
            template_name='download_module/download_page2.html',
            context=context,
        )


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='20/h', method='POST'), 'dispatch')
class PurchaseView(View):
    """Confirm and complete a file purchase from the wallet."""

    template_name = 'download_module/purchase.html'

    def get(self, request, slug):
        upload_file = get_public_file_or_404(slug)
        if upload_file.is_free() or upload_file.is_purchased_by(request.user):
            return redirect(reverse('download_page', args=[upload_file.slug]))

        return render(request, self.template_name, {
            'file': upload_file,
            'balance': request.user.balance,
            'enough_balance': request.user.balance >= upload_file.price,
        })

    def post(self, request, slug):
        upload_file = get_public_file_or_404(slug)

        try:
            purchases.purchase_file(request.user, upload_file)
        except wallet.InsufficientBalance:
            return render(request, self.template_name, {
                'file': upload_file,
                'balance': request.user.balance,
                'enough_balance': False,
                'error': _('موجودی کیف پول کافی نیست'),
            })
        except purchases.PurchaseError as exc:
            return render(request, self.template_name, {
                'file': upload_file,
                'balance': request.user.balance,
                'enough_balance': True,
                'error': str(exc),
            })

        # Send the buyer back to the start of the funnel for a fresh session.
        return redirect(reverse('download_page', args=[upload_file.slug]))
