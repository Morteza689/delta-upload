import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from . import services
from .forms import TopUpForm
from .gateways import (CryptoGateway, GatewayError, PaymentDeclined,
                       available_gateways, get_gateway)
from .models import ExchangeRate, Payment

logger = logging.getLogger(__name__)


# Each submission creates a Payment row and calls the gateway API; both
# carry limited quotas.
@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='10/h', method='POST'), 'dispatch')
class TopUpView(View):
    """Choose an amount and a method, then hand off to the gateway."""

    template_name = 'payment_module/topup.html'

    def _context(self, form):
        return {
            'form': form,
            'gateways': available_gateways(),
            'rate': ExchangeRate.current(),
            'payments': Payment.objects.filter(user=self.request.user)[:10],
        }

    def get(self, request):
        return render(request, self.template_name, self._context(TopUpForm()))

    def post(self, request):
        form = TopUpForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._context(form))

        gateway = get_gateway(form.cleaned_data['gateway'])
        if gateway is None or not gateway.is_configured():
            form.add_error('gateway', _('این روش پرداخت در دسترس نیست'))
            return render(request, self.template_name, self._context(form))

        payment = Payment.objects.create(
            user=request.user,
            gateway=gateway.slug,
            amount=form.cleaned_data['amount'],
        )

        return_url = request.build_absolute_uri(
            reverse('payment_return', args=[payment.pk])
        )
        ipn_url = request.build_absolute_uri(reverse('payment_ipn'))

        try:
            redirect_url = gateway.start(payment, return_url, ipn_url=ipn_url)
        except GatewayError as exc:
            services.mark_failed(payment, str(exc))
            form.add_error(None, str(exc))
            return render(request, self.template_name, self._context(form))

        return HttpResponseRedirect(redirect_url)


@login_required
def payment_return(request, pk):
    """The user coming back from the gateway.

    Only bank gateways settle here. Crypto waits for the webhook, because a
    chain confirmation can take minutes and the browser will not.
    """
    payment = get_object_or_404(Payment, pk=pk, user=request.user)
    gateway = get_gateway(payment.gateway)
    if gateway is None:
        raise Http404('Unknown gateway')

    if payment.is_settled:
        return render(request, 'payment_module/result.html', {'payment': payment})

    if not gateway.settles_on_return:
        # Crypto: wait for network confirmation.
        services.mark_failed(
            payment, '', status=Payment.Status.AWAITING_CONFIRMATION,
        )
        return render(request, 'payment_module/result.html', {'payment': payment})

    try:
        tracking_code = gateway.verify(payment, request.GET)
    except PaymentDeclined as exc:
        services.mark_failed(payment, str(exc), status=Payment.Status.CANCELED)
        return render(request, 'payment_module/result.html', {'payment': payment})
    except GatewayError as exc:
        logger.warning('verify failed for payment %s: %s', payment.pk, exc)
        services.mark_failed(payment, str(exc))
        return render(request, 'payment_module/result.html', {'payment': payment})

    payment = services.settle(payment, tracking_code=tracking_code)
    return render(request, 'payment_module/result.html', {'payment': payment})


@csrf_exempt
@require_POST
def payment_ipn(request):
    """Crypto payment webhook.

    Signed by the processor and verified before anything is credited. Without
    that check anyone could top up their own wallet with a plain POST.

    Answers 200 even on a rejected payload: a non-200 makes the processor
    retry, and a forged request should not be retried at all.
    """
    gateway = get_gateway(CryptoGateway.slug)
    if gateway is None or not gateway.is_configured():
        return HttpResponse(status=503)

    signature = request.headers.get('x-nowpayments-sig', '')
    if not gateway.check_signature(request.body, signature):
        logger.warning('IPN with invalid signature from %s',
                       request.META.get('REMOTE_ADDR'))
        return HttpResponse('invalid signature', status=400)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponse('invalid payload', status=400)

    order_id, settled, failed, tracking_code = gateway.read_ipn(payload)

    payment = Payment.objects.filter(pk=order_id).first()
    if payment is None:
        logger.warning('IPN for unknown payment %r', order_id)
        # Answer 200 so the processor does not keep retrying.
        return HttpResponse('unknown payment', status=200)

    if settled:
        services.settle(payment, tracking_code=tracking_code)
    elif failed:
        services.mark_failed(payment, payload.get('payment_status', ''))

    return HttpResponse('ok')
