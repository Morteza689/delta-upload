"""Staff dashboard.

Separate from Django admin, which is good for editing single rows but slow for
day-to-day work: site status, approving withdrawals, answering tickets.

Every view here is staff_member_required, not merely login_required.
"""
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import ListView

from account_module import wallet
from account_module.models import Transaction, Withdrawal
from ads_module.models import AdEarning, AdSetting, DownloadSession
from contact_module.models import Ticket
from payment_module.models import ExchangeRate, Payment, RateSetting
from upload_module.models import FileDownload, Purchase, UploadFile
from utils import charts

User = get_user_model()

staff_only = method_decorator(staff_member_required, 'dispatch')


@staff_only
class OverviewView(View):
    """Site overview."""

    CHART_DAYS = 30

    def get(self, request):
        now = timezone.now()
        since = now - timedelta(days=self.CHART_DAYS)
        day_ago = now - timedelta(days=1)

        downloads_daily = list(
            FileDownload.objects
            .filter(created_at__gte=since)
            .exclude(kind=FileDownload.Kind.OWNER)
            .annotate(day=TruncDate('created_at'))
            .values('day').annotate(count=Count('id')).order_by('day')
        )
        signups_daily = list(
            User.objects
            .filter(date_joined__gte=since)
            .annotate(day=TruncDate('date_joined'))
            .values('day').annotate(count=Count('id')).order_by('day')
        )

        paid_payments = Payment.objects.filter(status=Payment.Status.PAID)
        purchases = Purchase.objects.all()

        # Work waiting on an operator: the most important part of this page.
        pending = {
            'withdrawals': Withdrawal.objects.filter(is_paid=False).count(),
            'withdrawal_amount': Withdrawal.objects.filter(is_paid=False).aggregate(
                total=Sum('withdrawal_amount'))['total'] or 0,
            'tickets': Ticket.objects.filter(status=Ticket.Status.OPEN).count(),
            'payments': Payment.objects.filter(
                status=Payment.Status.AWAITING_CONFIRMATION).count(),
            'ad_earnings': AdEarning.objects.filter(
                status=AdEarning.Status.PENDING, available_at__lte=now).count(),
        }

        return render(request, 'staff_module/overview.html', {
            'pending': pending,

            'users_total': User.objects.count(),
            'users_new': User.objects.filter(date_joined__gte=day_ago).count(),
            'files_total': UploadFile.objects.count(),
            'files_public': UploadFile.objects.filter(
                visibility=UploadFile.Visibility.PUBLIC, is_active=True).count(),

            'downloads_total': FileDownload.objects.exclude(
                kind=FileDownload.Kind.OWNER).count(),
            'downloads_today': FileDownload.objects.filter(
                created_at__gte=day_ago).exclude(kind=FileDownload.Kind.OWNER).count(),
            'bandwidth': FileDownload.objects.aggregate(
                total=Sum('bytes_sent'))['total'] or 0,

            'revenue_topup': paid_payments.aggregate(t=Sum('amount'))['t'] or 0,
            'sales_gross': purchases.aggregate(t=Sum('amount'))['t'] or 0,
            'sales_share': purchases.aggregate(t=Sum('seller_share'))['t'] or 0,
            'sales_commission': purchases.aggregate(
                t=Sum('site_commission'))['t'] or 0,
            'ad_paid_micro': AdEarning.objects.filter(
                status=AdEarning.Status.CONFIRMED).aggregate(
                t=Sum('micro_cents'))['t'] or 0,
            'withdrawn': Withdrawal.objects.filter(is_paid=True).aggregate(
                t=Sum('withdrawal_amount'))['t'] or 0,

            'downloads_chart': charts.timeseries(downloads_daily, self.CHART_DAYS),
            'signups_chart': charts.timeseries(signups_daily, self.CHART_DAYS),
            'chart_days': self.CHART_DAYS,

            'fraud': list(
                DownloadSession.objects
                .filter(counted=False, step2_at__gte=since)
                .exclude(reject_reason='')
                .values('reject_reason')
                .annotate(count=Count('id')).order_by('-count')[:8]
            ),
            'ad_setting': AdSetting.current(),
        })


@staff_only
class UserListView(ListView):
    template_name = 'staff_module/users.html'
    context_object_name = 'users'
    paginate_by = 40

    def get_queryset(self):
        queryset = User.objects.annotate(
            file_count=Count('uploadfile', distinct=True),
        ).order_by('-date_joined')
        search = self.request.GET.get('q', '').strip()
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search) | Q(username__icontains=search)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('q', '')
        return context


@staff_only
class WithdrawalQueueView(View):
    """Withdrawal queue."""

    def get(self, request):
        pending = list(
            Withdrawal.objects.filter(is_paid=False)
            .select_related('user', 'account').order_by('created_deta')
        )

        # Bank transfers are paid in toman while amounts are stored in cents.
        # Leaving that conversion to the operator invites a wrong payment.
        rate = ExchangeRate.current()
        for item in pending:
            # The net amount is what to send: the fee already came out of the
            # withdrawal, so paying the gross loses the fee every time.
            item.toman = (
                item.net_amount * rate.usd_to_toman // 100
                if rate and not item.account.is_crypto else None
            )

        return render(request, 'staff_module/withdrawals.html', {
            'pending': pending,
            'rate': rate,
            'recent': Withdrawal.objects.filter(is_paid=True)
                      .select_related('user', 'account').order_by('-deta_is_paid')[:20],
        })

    def post(self, request):
        withdrawal = get_object_or_404(
            Withdrawal, pk=request.POST.get('withdrawal_id'), is_paid=False,
        )
        action = request.POST.get('action')

        if action == 'mark_paid':
            withdrawal.is_paid = True
            withdrawal.deta_is_paid = timezone.now()
            withdrawal.save(update_fields=['is_paid', 'deta_is_paid'])

        elif action == 'reject':
            # Rejecting must refund, or the money leaves the balance and is
            # never paid out.
            with transaction.atomic():
                wallet.credit(
                    withdrawal.user, withdrawal.withdrawal_amount,
                    Transaction.Kind.TOPUP,
                    description=_('بازگشت وجه برداشت رد شده #%(id)s') % {'id': withdrawal.pk},
                )
                withdrawal.delete()

        return redirect(reverse('staff_withdrawals'))


@staff_only
class TicketQueueView(ListView):
    template_name = 'staff_module/tickets.html'
    context_object_name = 'tickets'
    paginate_by = 30

    def get_queryset(self):
        queryset = Ticket.objects.select_related('user').annotate(
            message_count=Count('messages'),
        )
        status = self.request.GET.get('status', Ticket.Status.OPEN)
        if status in Ticket.Status.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['counts'] = {
            'open': Ticket.objects.filter(status=Ticket.Status.OPEN).count(),
            'answered': Ticket.objects.filter(status=Ticket.Status.ANSWERED).count(),
            'closed': Ticket.objects.filter(status=Ticket.Status.CLOSED).count(),
        }
        context['active_status'] = self.request.GET.get('status', Ticket.Status.OPEN)
        return context


@staff_only
class FileListView(ListView):
    template_name = 'staff_module/files.html'
    context_object_name = 'files'
    paginate_by = 40

    def get_queryset(self):
        queryset = UploadFile.objects.select_related('author').order_by('-created_deta')
        search = self.request.GET.get('q', '').strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(slug__icontains=search)
                | Q(author__email__icontains=search)
            )
        if self.request.GET.get('visibility') == 'public':
            queryset = queryset.filter(visibility=UploadFile.Visibility.PUBLIC)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('q', '')
        return context

    def post(self, request, *args, **kwargs):
        """Disable a file without deleting it, so the record survives."""
        upload = get_object_or_404(UploadFile, pk=request.POST.get('file_id'))
        upload.is_active = not upload.is_active
        upload.save(update_fields=['is_active'])
        return redirect(request.META.get('HTTP_REFERER', reverse('staff_files')))


@staff_only
class PaymentListView(ListView):
    template_name = 'staff_module/payments.html'
    context_object_name = 'payments'
    paginate_by = 40

    def get_queryset(self):
        queryset = Payment.objects.select_related('user').order_by('-created_at')
        status = self.request.GET.get('status')
        if status in Payment.Status.values:
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_status'] = self.request.GET.get('status', '')
        context['totals'] = {
            'paid': Payment.objects.filter(status=Payment.Status.PAID).aggregate(
                t=Sum('amount'))['t'] or 0,
            'count': Payment.objects.filter(status=Payment.Status.PAID).count(),
        }
        return context


@staff_only
class ExchangeRateView(View):
    """USD to toman rate: current value, history, manual entry, auto fetch."""

    template_name = 'staff_module/exchange_rate.html'
    HISTORY = 20

    def context(self, form, setting, fetch_error=''):
        from payment_module.rates import PROVIDERS

        return {
            'form': form,
            'setting': setting,
            'current': ExchangeRate.current(),
            'history': ExchangeRate.objects.all()[:self.HISTORY],
            'fetch_error': fetch_error,
            'providers': PROVIDERS,
        }

    def get(self, request):
        from payment_module.forms import ExchangeRateForm

        setting = RateSetting.load()
        current = ExchangeRate.current()
        form = ExchangeRateForm(initial={
            'usd_to_toman': current.usd_to_toman if current else None,
            'auto_update': setting.auto_update,
            'provider': setting.provider,
            'api_key': setting.api_key,
        })
        return render(request, self.template_name, self.context(form, setting))

    def post(self, request):
        from payment_module import rate_service
        from payment_module.forms import ExchangeRateForm

        setting = RateSetting.load()

        # Fetch now does not save settings, it just fetches once. Validating
        # the whole form first would force the operator to fill everything in
        # before they could test a provider.
        if request.POST.get('action') == 'fetch_now':
            _rate, error = rate_service.refresh(setting, force=True)
            if not error:
                return redirect(reverse('staff_exchange_rate'))

            current = ExchangeRate.current()
            form = ExchangeRateForm(initial={
                'usd_to_toman': current.usd_to_toman if current else None,
                'auto_update': setting.auto_update,
                'provider': setting.provider,
                'api_key': setting.api_key,
            })
            return render(request, self.template_name,
                          self.context(form, setting, fetch_error=error))

        form = ExchangeRateForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name,
                          self.context(form, setting))

        setting.auto_update = form.cleaned_data['auto_update']
        setting.provider = form.cleaned_data['provider']
        setting.api_key = form.cleaned_data['api_key']
        setting.save(update_fields=['auto_update', 'provider', 'api_key'])

        ExchangeRate.set_rate(form.cleaned_data['usd_to_toman'])

        return redirect(reverse('staff_exchange_rate'))


@staff_only
class SecurityView(View):
    """Security settings; currently the captcha provider.

    The switch lives here rather than in a settings file so reverting it is one
    click. Secrets go the other way: environment only, never the database.
    """

    template_name = 'staff_module/security.html'

    def context(self, error=''):
        from account_module import captcha as captcha_service
        from account_module.models import CaptchaSetting

        return {
            'setting': CaptchaSetting.load(),
            'active': captcha_service.active_provider(),
            'choices': CaptchaSetting.Provider.choices,
            'recaptcha_ready': captcha_service.recaptcha_is_configured(),
            'error': error,
        }

    def get(self, request):
        return render(request, self.template_name, self.context())

    def post(self, request):
        from account_module import captcha as captcha_service
        from account_module.models import CaptchaSetting

        provider = request.POST.get('provider')
        if provider not in dict(CaptchaSetting.Provider.choices):
            return render(request, self.template_name,
                          self.context(error=_('سرویس انتخاب‌شده معتبر نیست.')))

        # Selecting reCAPTCHA without keys would leave the switch on while the
        # local captcha keeps running, so the operator thinks they changed
        # something that did not change.
        if (provider == CaptchaSetting.Provider.RECAPTCHA
                and not captcha_service.recaptcha_is_configured()):
            return render(request, self.template_name, self.context(
                error=_('کلیدهای reCAPTCHA در متغیرهای محیطی ست نشده‌اند؛ '
                        'تا آن‌ها را نگذاری این گزینه کار نمی‌کند.'),
            ))

        setting = CaptchaSetting.load()
        setting.provider = provider
        setting.save(update_fields=['provider', 'updated_at'])

        return redirect(reverse('staff_security'))
