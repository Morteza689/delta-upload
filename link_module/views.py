"""Public link shortener. No account required."""
import logging

from django.db.models import F
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from ads_module import services as ads_services
from ads_module.models import AdSlot
from ads_module.services import visitor_hash

from .forms import ShortLinkForm
from .models import ShortLink

logger = logging.getLogger(__name__)

#: Interstitial wait: short enough not to lose the visitor, long enough for
#: the ad to load.
INTERSTITIAL_WAIT_SECONDS = 5


@method_decorator(ratelimit(key='ip', rate='20/h', method='POST'), 'dispatch')
class ShortenView(View):
    """Public shortener page."""

    template_name = 'link_module/shorten.html'

    def context(self, request, form, link=None):
        recent = None
        if request.user.is_authenticated:
            recent = ShortLink.objects.filter(owner=request.user)[:10]
        return {'form': form, 'link': link, 'recent': recent}

    def build_form(self, request, data=None):
        # Signed-in users skip the captcha: they are identified, and the
        # per-user rate limit covers abuse.
        return ShortLinkForm(
            data, request=request,
            is_authenticated=request.user.is_authenticated,
        )

    def get(self, request):
        return render(request, self.template_name,
                      self.context(request, self.build_form(request)))

    def post(self, request):
        form = self.build_form(request, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self.context(request, form))

        link = ShortLink.create_for(
            target=form.cleaned_data['target'],
            owner=request.user if request.user.is_authenticated else None,
            creator_hash=visitor_hash(request),
            mode=form.mode,
            expires_at=form.expires_at,
            max_clicks=form.cleaned_data.get('max_clicks'),
        )
        logger.info('short link %s created (%s)', link.code, link.mode)

        # A fresh form so a page refresh does not create a duplicate link.
        return render(request, self.template_name,
                      self.context(request, self.build_form(request), link=link))


def _consume(link):
    """Spend one use of the link.

    Uses F() so concurrent clicks do not overwrite each other, and applies the
    limit as a filter so only one of two racing requests can take the last use.
    """
    query = ShortLink.objects.filter(pk=link.pk)
    if link.max_clicks is not None:
        query = query.filter(clicks__lt=link.max_clicks)

    return query.update(clicks=F('clicks') + 1, last_click_at=timezone.now()) > 0


@ratelimit(key='ip', rate='600/h')
def go(request, code):
    """Redirect to the target.

    Direct links redirect straight away; interstitial links show a page first.

    302 rather than 301: browsers cache a permanent redirect, after which
    deactivating or expiring the link has no effect for anyone who opened it.
    """
    link = get_object_or_404(ShortLink, code=code)

    if not link.is_available():
        return render(request, 'link_module/unavailable.html', {
            'link': link, 'reason': link.unavailable_reason(),
        }, status=410)

    if link.mode == ShortLink.Mode.INTERSTITIAL:
        # The click is counted when the visitor continues, not when the page
        # opens; otherwise every view would spend a use.
        #
        # Its own slot, not the download page's. Networks report per slot, and
        # publisher rules require a slot to sit where it was defined.
        slot = (ads_services.pick_slot(AdSlot.Position.LINK_INTERSTITIAL)
                or ads_services.pick_slot(AdSlot.Position.STEP1))

        # Revenue is recorded here rather than in go_continue: the ad renders
        # on this page, so a visitor who leaves has still seen it.
        ads_services.record_link_view(request, link)
        return render(request, 'link_module/interstitial.html', {
            'link': link,
            'ad_slot': slot,
            'ad_loader': ads_services.loader_script(),
            'wait_seconds': INTERSTITIAL_WAIT_SECONDS,
        })

    if not _consume(link):
        return render(request, 'link_module/unavailable.html', {
            'link': link, 'reason': 'used_up',
        }, status=410)

    return HttpResponseRedirect(link.target)


@ratelimit(key='ip', rate='600/h')
def go_continue(request, code):
    """Continue from the interstitial to the target."""
    link = get_object_or_404(ShortLink, code=code)

    if not link.is_available() or not _consume(link):
        return render(request, 'link_module/unavailable.html', {
            'link': link, 'reason': link.unavailable_reason() or 'used_up',
        }, status=410)

    return HttpResponseRedirect(link.target)


def link_preview(request, code):
    """Show the target without redirecting."""
    link = get_object_or_404(ShortLink, code=code)
    return render(request, 'link_module/preview.html', {
        'link': link,
        'available': link.is_available(),
    })
