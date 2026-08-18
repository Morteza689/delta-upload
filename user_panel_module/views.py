import json

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, render, redirect
from django.http import HttpRequest, JsonResponse
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _
from django.views.generic import TemplateView, ListView, CreateView
from django.views import View
from django_ratelimit.decorators import ratelimit
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from account_module import forms, login_attempts, payout, wallet
from account_module.models import PayoutAccount, Transaction, Withdrawal, User
from ads_module.models import MICRO_PER_CENT, AdEarning
from upload_module.models import FileDownload, Purchase
from utils import charts
from upload_module.models import UploadFile
from upload_module.reports import file_report
from user_panel_module.forms import (PayoutAccountForm, UploadEditForm,
                                    UploadModelForm, WithdrawalModelForm)
from utils.email_service import send_email
from utils.http_service import get_client_ip

def send_activation_email(user):
    """Send the link that turns a new account on.

    Registration creates the user inactive, so without this nobody who signs
    up can ever log in. It was commented out, which is exactly what that
    looked like.
    """
    from django.utils.translation import gettext_lazy

    from account_module.language import language_for
    from utils.email_service import absolute_url

    url = absolute_url(
        reverse('activate_account_page', args=[user.email_active_code])
    )
    # Lazy, so the subject is translated inside the language override rather
    # than in whatever language happens to be active here.
    return send_email(
        gettext_lazy('فعال‌سازی حساب در دلتا آپلود'), user.email,
        {'user': user, 'activation_url': url},
        'emails/activate_account.html',
        language=language_for(user),
    )


# Registration: blocks bulk account creation. GET is unrestricted.
@method_decorator(ratelimit(key='ip', rate='5/h', method='POST'), 'dispatch')
class RegisterView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return redirect(reverse('user_dashbord'))
        register_form = forms.RegisterForm(remote_ip=get_client_ip(request))
        context = {
            'form': register_form
        }
        return render(request, 'user_panel_module/register.html', context)

    def post(self, request: HttpRequest):
        if request.user.is_authenticated:
            return redirect(reverse('user_dashbord'))
        register_form = forms.RegisterForm(request.POST,
                                           remote_ip=get_client_ip(request))
        if register_form.is_valid():
            user_email = register_form.cleaned_data.get('email')
            user_password = register_form.cleaned_data.get('password')
            user_name = register_form.cleaned_data.get('username')
            user_chack: bool = User.objects.filter(email__iexact=user_email).exists()
            if user_chack:
                register_form.add_error('email', _('ایمیل تکراری میباشد'))
            else:
                new_user = User(
                    username=user_name, email=user_email,
                    email_active_code=get_random_string(72), is_active=False,
                )
                new_user.set_password(user_password)
                new_user.save()

                send_activation_email(new_user)

                # The result page does not say whether the mail went out. A
                # send can fail for reasons the visitor cannot act on, and
                # telling them "we could not email you" at the moment they
                # registered reads as "your account is broken" when it is not.
                # The failure is in the log, and the address is on the page so
                # a typo is visible.
                return render(request, 'account_module/email_sended.html',
                              {'email': new_user.email})

        context = {
            'form': register_form
        }
        return render(request, 'user_panel_module/register.html', context)


# Two limits on login, by address and by the submitted email. The second
# matters more: without it an attacker rotates addresses to target one account.
@method_decorator(ratelimit(key='ip', rate='10/5m', method='POST'), 'dispatch')
@method_decorator(ratelimit(key='post:email', rate='5/5m', method='POST'), 'dispatch')
class loginView(View):
    def get(self, request: HttpRequest):
        if request.user.is_authenticated:
            return redirect(reverse('account_page'))
        login_form = forms.LoginForm(
            with_captcha=login_attempts.captcha_required(request)
        )
        return render(request, 'user_panel_module/login.html', {'form': login_form})

    def post(self, request: HttpRequest):
        if request.user.is_authenticated:
            return redirect(reverse('account_page'))

        # Decided before validation. Deciding after recording the failure
        # would validate a captcha the user was never shown.
        needs_captcha = login_attempts.captcha_required(request)
        login_form = forms.LoginForm(request.POST, with_captcha=needs_captcha)

        if login_form.is_valid():
            user_email = login_form.cleaned_data.get('email')
            user_pass = login_form.cleaned_data.get('password')
            user: User = User.objects.filter(email__iexact=user_email).first()
            if user is not None:
                if not user.is_active:
                    login_form.add_error('email', _('اکانت فعال نیست'))
                else:
                    is_password_correct = user.check_password(user_pass)
                    if is_password_correct:
                        login_attempts.reset(request)
                        login(request, user)
                        return redirect(reverse('user_dashbord'))
                    else:
                        login_form.add_error('email', _('کلمه عبور اشتباه'))
            else:
                # One message for both unknown user and wrong password, so the
                # form cannot be used to enumerate registered addresses.
                login_form.add_error('email', _('کلمه عبور اشتباه'))

        failures = login_attempts.record_failure(request)

        # If this failure crossed the threshold, re-render with a captcha.
        if not needs_captcha and login_attempts.captcha_required(request):
            login_form.fields['captcha'] = forms.captcha_field(get_client_ip(request))
            login_form.with_captcha = True

        return render(request, 'user_panel_module/login.html', {
            'form': login_form,
            'failed_attempts': failures,
        })


class LogoutView(View):
    def get(self, request):
        logout(request)
        return redirect(reverse('login_page'))


@method_decorator(login_required, 'dispatch')
class DashboardView(TemplateView):
    template_name = 'user_panel_module/user_dashboard_panel.html'

    #: Dashboard chart window.
    CHART_DAYS = 30

    def get_context_data(self, **kwargs):
        context = super().get_context_data()
        user = self.request.user
        since = timezone.now() - timedelta(days=self.CHART_DAYS)

        files = UploadFile.objects.filter(author_id=user.id, is_active=True)

        # Aggregate rather than looping: a user with a hundred files should not
        # load a hundred objects to produce one number.
        file_totals = files.aggregate(
            downloads=Sum('number_of_download'),
            free=Sum('free_downloads'),
            paid=Sum('paid_downloads'),
            count=Count('id'),
        )
        withdrawn = Withdrawal.objects.filter(user_id=user.id).aggregate(
            total=Sum('withdrawal_amount'),
        )['total'] or 0

        # Daily download trend.
        daily = list(
            FileDownload.objects
            .filter(file__author_id=user.id, created_at__gte=since)
            .exclude(kind=FileDownload.Kind.OWNER)
            .annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )

        # Revenue by source.
        sales_share = Purchase.objects.filter(file__author_id=user.id).aggregate(
            total=Sum('seller_share'),
        )['total'] or 0
        ad_micro = AdEarning.objects.filter(
            user_id=user.id, status=AdEarning.Status.CONFIRMED,
        ).aggregate(total=Sum('micro_cents'))['total'] or 0
        ad_pending_micro = AdEarning.objects.filter(
            user_id=user.id, status=AdEarning.Status.PENDING,
        ).aggregate(total=Sum('micro_cents'))['total'] or 0

        free_count = file_totals['free'] or 0
        paid_count = file_totals['paid'] or 0

        context.update({
            'user': user,
            'files': files,
            'file_count': file_totals['count'] or 0,
            'number_of_download': file_totals['downloads'] or 0,
            'free_downloads': free_count,
            'paid_downloads': paid_count,
            'all_amunt': withdrawn,

            'sales_share': sales_share,
            'ad_confirmed_micro': ad_micro,
            'ad_pending_micro': ad_pending_micro,
            'earned_total': sales_share + ad_micro // MICRO_PER_CENT,

            'chart': charts.timeseries(daily, self.CHART_DAYS),
            'chart_days': self.CHART_DAYS,
            'paid_ring': charts.ring(paid_count, free_count + paid_count),

            'top_files': files.order_by('-number_of_download')[:5],
            'list_last_withdrawal': (
                Withdrawal.objects
                .filter(user_id=user.id)
                .select_related('account')
                .order_by('-created_deta')[:5]
            ),
        })
        return context


@method_decorator(login_required, 'dispatch')
class WithdrawalListView(ListView):
    model = Withdrawal
    template_name = 'user_panel_module/withdrawal_list.html'

    def get_queryset(self):
        queryset = super(WithdrawalListView, self).get_queryset()
        queryset = queryset.filter(user_id=self.request.user.id).order_by('-created_deta')
        return queryset


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='10/h', method='POST'), 'dispatch')
class WithdrawalView(CreateView):
    form_class = WithdrawalModelForm
    model = Withdrawal
    template_name = 'user_panel_module/withdrawal.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Only the user's own destinations; otherwise editing the select value
        # would withdraw to someone else's account.
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['user'] = self.request.user
        # With no destination the select is empty and the form fails without
        # explanation, so the template shows guidance instead.
        context['accounts'] = self.request.user.payout_accounts.all()
        # Minimums and fees, from the same source the form validates against.
        rules = payout.rules_table()
        context['rules'] = rules
        # The same data for the in-browser calculation.
        context['rules_json'] = json.dumps({
            row['method']: {'min': row['min_cents'], 'fee': row['fee_cents']}
            for row in rules
        })
        return context
    def form_valid(self, form):
        self.object = form.save(False)
        user = self.request.user
        withdrawal_amount = form.cleaned_data.get('withdrawal_amount')

        if withdrawal_amount <= 0:
            form.add_error('withdrawal_amount', _('مبلغ برداشت باید مثبت باشد'))
            return self.form_invalid(form)

        try:
            # Recording the withdrawal and debiting the balance happen
            # together, or one can exist without the other.
            with transaction.atomic():
                self.object.withdrawal_amount = withdrawal_amount
                self.object.user = user
                # Fee frozen now so a later settings change cannot alter this
                # request.
                self.object.fee = form.cleaned_data.get('fee', 0)
                self.object.save()
                wallet.debit(
                    user, withdrawal_amount, Transaction.Kind.WITHDRAWAL,
                    description=_('درخواست برداشت'),
                )
        except wallet.InsufficientBalance:
            form.add_error('withdrawal_amount', _('مقدار وارد شده بیشتر از موجودی است'))
            return self.form_invalid(form)

        return redirect(reverse('withdrawal_list_page'))


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='30/h', method='POST'), 'dispatch')
class UploadFileView(CreateView):
    form_class = UploadModelForm
    model = UploadFile
    template_name = 'user_panel_module/upload_file.html'
    success_url = '/user/'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # The form needs the user for the quota check.
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        from account_module import quota

        context = super().get_context_data(**kwargs)
        context['quota_used'] = quota.human_size(quota.used_by(self.request.user))
        context['quota_limit'] = quota.human_size(quota.limit_for(self.request.user))
        context['quota_percent'] = quota.percent_used(self.request.user)
        return context
    def is_ajax(self):
        return self.request.headers.get('x-requested-with') == 'XMLHttpRequest'

    def form_valid(self, form):
        self.object = form.save(commit=False)
        self.object.author_id = self.request.user.id
        self.object.type_file = UploadFile.ChoiceType.File
        # The slug must be unguessable: it is the only thing standing between
        # a stranger and the file's public page.
        self.object.slug = get_random_string(12)
        self.object.save()

        queued = False
        if form.wants_telegram():
            from telegram_module import storage as telegram_storage

            # Queued, not sent. Pushing a file to Telegram takes minutes and a
            # web worker held that long is a web worker serving nobody.
            telegram_storage.queue(self.object, form.telegram_connection)
            queued = True

        if self.is_ajax():
            return JsonResponse({
                'status': 'success',
                'slug': self.object.slug,
                'queued_for_telegram': queued,
                'redirect': reverse('my_files_page'),
            })
        return redirect(reverse('my_files_page'))

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self.is_ajax():
            # 422 tells the upload script this is a validation error, not a
            # server fault, so it can render the returned form HTML.
            response.status_code = 422
        return response

@method_decorator(login_required, 'dispatch')
class MyFileVeiw(ListView):
    model = UploadFile
    template_name = 'user_panel_module/my_files.html'
    context_object_name = 'files'
    def get_queryset(self):
        # Own files, and not the ones already deleted. Their rows survive so
        # purchases and earnings keep something to point at, but the owner
        # asked for them to be gone and should not keep seeing them.
        return (
            super().get_queryset()
            .filter(author_id=self.request.user.id, deleted_at__isnull=True)
        )


@method_decorator(login_required, 'dispatch')
class FileReportView(View):
    """Per-file report: downloads, revenue, visitors, rejected passes."""

    def get(self, request, pk):
        upload_file = get_object_or_404(
            UploadFile, pk=pk, author_id=request.user.id,
        )
        context = file_report(upload_file)
        return render(request, 'user_panel_module/file_report.html', context)


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='60/h', method='POST'), 'dispatch')
class FileEditView(View):
    """Change a file's name, description, publishing, and price."""

    template_name = 'user_panel_module/file_edit.html'

    def get_object(self, request, pk):
        # Filtered on author, and 404 rather than 403: a stranger must not be
        # able to learn which file ids exist by the error they get.
        return get_object_or_404(UploadFile, pk=pk, author_id=request.user.id)

    def get(self, request, pk):
        upload_file = self.get_object(request, pk)
        form = UploadEditForm(instance=upload_file, user=request.user)
        return render(request, self.template_name,
                      self.context(upload_file, form))

    def post(self, request, pk):
        upload_file = self.get_object(request, pk)
        form = UploadEditForm(request.POST, instance=upload_file,
                              user=request.user)
        if not form.is_valid():
            return render(request, self.template_name,
                          self.context(upload_file, form))

        form.save()
        messages.success(request, _('تغییرات ذخیره شد.'))
        return redirect(reverse('my_files_page'))

    def context(self, upload_file, form):
        return {'file': upload_file, 'form': form}


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='30/h', method='POST'), 'dispatch')
class FileDeleteView(View):
    """Delete a file's contents, after showing what that costs.

    POST only for the deletion itself. A delete behind a plain link gets fired
    by link prefetching, mail scanners and crawlers, none of which meant to.
    """

    template_name = 'user_panel_module/file_delete.html'

    def get_object(self, request, pk):
        return get_object_or_404(
            UploadFile, pk=pk, author_id=request.user.id, deleted_at__isnull=True,
        )

    def get(self, request, pk):
        upload_file = self.get_object(request, pk)
        return render(request, self.template_name, self.context(upload_file))

    def post(self, request, pk):
        upload_file = self.get_object(request, pk)
        name = upload_file.name or upload_file.slug

        upload_file.soft_delete()

        messages.success(request, _('«%(name)s» حذف شد.') % {'name': name})
        return redirect(reverse('my_files_page'))

    def context(self, upload_file):
        """What the owner is about to lose, counted rather than guessed."""
        return {
            'file': upload_file,
            'purchase_count': upload_file.purchases.count(),
            'download_count': upload_file.number_of_download,
            'pending_earnings': AdEarning.objects.filter(
                file=upload_file, status=AdEarning.Status.PENDING,
            ).count(),
        }


# The shareF view was removed: it referenced Fernet through a commented-out
# import and 500ed on every request, and it rendered files without checking
# visibility. Sharing now goes through the slug and the visibility rules.


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='20/h', method='POST'), 'dispatch')
class PayoutAccountView(View):
    """Add and remove payout destinations."""

    template_name = 'user_panel_module/payout_accounts.html'

    def context(self, request, form, delete_error=''):
        return {
            'form': form,
            'accounts': request.user.payout_accounts.all(),
            # Delete errors are kept off the add form: they are unrelated,
            # and add_error does not work on an unbound form anyway.
            'delete_error': delete_error,
        }

    def get(self, request):
        form = PayoutAccountForm(user=request.user)
        return render(request, self.template_name, self.context(request, form))

    def refuse_delete(self, request, message):
        return render(request, self.template_name,
                      self.context(request, PayoutAccountForm(user=request.user),
                                   delete_error=message))

    def post(self, request):
        if request.POST.get('action') == 'delete':
            account = get_object_or_404(
                PayoutAccount, pk=request.POST.get('account_id'), user=request.user,
            )
            # A destination with a pending withdrawal must survive: the
            # operator needs to know where to send the money.
            if account.withdrawal_set.filter(is_paid=False).exists():
                return self.refuse_delete(request, _(
                    'این مقصد یک برداشت در انتظار دارد و تا تعیین تکلیف آن حذف نمی‌شود.'))

            if account.withdrawal_set.exists():
                return self.refuse_delete(request, _(
                    'این مقصد سابقه‌ی برداشت دارد و برای حفظ سوابق قابل حذف نیست.'))

            account.delete()
            return redirect(reverse('payout_accounts_page'))

        form = PayoutAccountForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, self.context(request, form))

        account = form.save(commit=False)
        account.user = request.user
        account.save()
        return redirect(reverse('payout_accounts_page'))


@method_decorator(login_required, 'dispatch')
@method_decorator(ratelimit(key='user', rate='20/h', method='POST'), 'dispatch')
class StoragePlanView(View):
    """Buy extra storage from the wallet balance."""

    template_name = 'user_panel_module/storage_plans.html'

    def plans(self):
        """Active packages from the catalogue.

        Read from the database rather than settings: pricing is a business
        decision and should not need a deploy.
        """
        from account_module import quota
        from account_module.models import StoragePackage

        return [
            {
                'pk': package.pk,
                'bytes': package.bonus_bytes,
                'label': quota.human_size(package.bonus_bytes),
                'price_cents': package.price_cents,
                'days': package.days,
            }
            for package in StoragePackage.for_sale()
        ]

    def context(self, request, error=''):
        from account_module import quota
        from account_module.models import StoragePlan

        return {
            'plans': self.plans(),
            'error': error,
            'used': quota.human_size(quota.used_by(request.user)),
            'limit': quota.human_size(quota.limit_for(request.user)),
            'percent': quota.percent_used(request.user),
            'free_base': quota.human_size(quota.free_bytes()),
            'active': StoragePlan.objects.filter(
                user=request.user, expires_at__gt=timezone.now()),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        from account_module.models import StoragePlan

        from account_module.models import StoragePackage

        # The form posts a package id, not a list index: indexes shift when a
        # package is deactivated and a stale page would buy the wrong one. The
        # is_active filter stops an old id buying a withdrawn package.
        package = StoragePackage.objects.filter(
            pk=request.POST.get('plan'), is_active=True).first()
        if package is None:
            return render(request, self.template_name,
                          self.context(request, _('بسته‌ی انتخابی معتبر نیست.')))

        plan = {
            'bytes': package.bonus_bytes,
            'price_cents': package.price_cents,
            'days': package.days,
            'label': __import__('account_module.quota', fromlist=['quota'])
                     .human_size(package.bonus_bytes),
        }

        try:
            # Debit and plan creation happen together, or money leaves
            # without space arriving.
            with transaction.atomic():
                wallet.debit(
                    request.user, plan['price_cents'], Transaction.Kind.PURCHASE,
                    description=_('خرید %(size)s فضای اضافه') % {'size': plan['label']},
                )
                StoragePlan.objects.create(
                    user=request.user,
                    bonus_bytes=plan['bytes'],
                    price_paid=plan['price_cents'],
                    expires_at=timezone.now() + timedelta(days=plan['days']),
                )
        except wallet.InsufficientBalance:
            return render(request, self.template_name, self.context(
                request, _('موجودی کیف پولت کافی نیست. اول کیف پول را شارژ کن.')))

        return redirect(reverse('storage_plans_page'))
