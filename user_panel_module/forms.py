from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.forms import INPUT_CLASS, DollarField
from account_module import payout
from account_module.models import PayoutAccount, Withdrawal
from upload_module.models import UploadFile


class PayoutAccountForm(forms.ModelForm):
    """Add a payout destination: IBAN or USDT wallet.

    Validation is strict here because money sent to the wrong destination does
    not come back.
    """

    class Meta:
        model = PayoutAccount
        fields = ['method', 'destination', 'holder_name']
        widgets = {
            'method': forms.Select(attrs={'class': INPUT_CLASS,
                                          'data-payout-method': ''}),
            'destination': forms.TextInput(attrs={'class': INPUT_CLASS,
                                                  'dir': 'ltr',
                                                  'autocomplete': 'off'}),
            'holder_name': forms.TextInput(attrs={'class': INPUT_CLASS}),
        }
        labels = {
            'method': _('روش برداشت'),
            'destination': _('شبا یا آدرس کیف پول'),
            'holder_name': _('نام صاحب حساب'),
        }
        help_texts = {
            'destination': _('برای شبا: IR و ۲۴ رقم. برای تتر: آدرس کامل کیف پول همان شبکه.'),
            'holder_name': _('فقط برای واریز بانکی لازم است — باید دقیقاً با نام صاحب حساب یکی باشد.'),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['holder_name'].required = False

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get('method')
        destination = cleaned.get('destination')

        if not method or not destination:
            return cleaned

        try:
            cleaned['destination'] = payout.validate_destination(method, destination)
        except forms.ValidationError as error:
            self.add_error('destination', error)
            return cleaned

        # Banks reverse a transfer whose name does not match, and the money
        # then sits in transit for weeks. Cheaper to catch it here.
        if method == PayoutAccount.Method.SHEBA and not cleaned.get('holder_name'):
            self.add_error('holder_name',
                           _('برای واریز بانکی نام صاحب حساب لازم است.'))

        if method in PayoutAccount.CRYPTO_METHODS:
            # Meaningless for crypto, and storing it is personal data with no
            # purpose.
            cleaned['holder_name'] = ''

        if self.user and PayoutAccount.objects.filter(
            user=self.user, method=method, destination=cleaned['destination'],
        ).exists():
            self.add_error('destination', _('این مقصد قبلاً ثبت شده است.'))

        return cleaned


class PayoutAccountSelect(forms.Select):
    """Destination select that carries each option's method.

    The option value is a destination id, which tells the script nothing about
    fees. data-method closes that gap without shipping a separate id-to-method
    map to the page.
    """

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        account = getattr(value, 'instance', None)
        if account is not None:
            option['attrs']['data-method'] = account.method
        return option


class WithdrawalModelForm(forms.ModelForm):
    """Amount entered in dollars, stored in cents.

    The minimum is not fixed: each method carries its own network fee, so the
    field itself allows zero and the real check happens in clean, where the
    chosen destination is known.
    """

    withdrawal_amount = DollarField(
        label=_('مبلغ (دلار)'),
        min_cents=0,
    )

    class Meta:
        model = Withdrawal
        fields = ['withdrawal_amount', 'account']
        widgets = {'account': PayoutAccountSelect(attrs={'class': INPUT_CLASS})}
        labels = {'account': _('واریز به')}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            # Own destinations only; editing the select value would otherwise
            # withdraw to someone else's account.
            self.fields['account'].queryset = user.payout_accounts.all()

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get('withdrawal_amount')
        account = cleaned.get('account')

        if amount is None or account is None:
            return cleaned

        minimum = payout.min_cents(account.method)
        if amount < minimum:
            self.add_error('withdrawal_amount', _(
                'حداقل برداشت برای این روش %(min)s دلار است.'
            ) % {'min': f'{minimum / 100:.2f}'})
            return cleaned

        fee = payout.fee_cents(account.method)
        if amount <= fee:
            # Impossible with the current minimums, but a settings change must
            # not allow a withdrawal whose net amount is zero.
            self.add_error('withdrawal_amount', _(
                'مبلغ باید از کارمزد انتقال بیشتر باشد.'
            ))
            return cleaned

        cleaned['fee'] = fee
        return cleaned


class UploadModelForm(forms.ModelForm):
    # The user picks a duration; what gets stored is a moment in time.
    public_for_hours = forms.IntegerField(
        required=False, min_value=1,
        label=_('چند ساعت عمومی بماند؟'),
        help_text=_('خالی بگذار تا بدون محدودیت زمانی عمومی بماند'),
        widget=forms.NumberInput(attrs={'class': INPUT_CLASS}),
    )
    price = DollarField(
        required=False, label=_('قیمت (دلار)'), min_cents=0,
        help_text=_('خالی یا صفر یعنی رایگان'),
    )

    class Meta:
        model = UploadFile
        fields = ['name', 'description', 'file', 'visibility']
        widgets = {
            'name': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'description': forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': '4'}),
            'file': forms.FileInput(attrs={'class': INPUT_CLASS}),
            'visibility': forms.Select(attrs={'class': INPUT_CLASS}),
        }
        labels = {'visibility': _('وضعیت انتشار')}

    # Only offered when the user has actually connected a channel. A checkbox
    # that cannot work is worse than no checkbox.
    store_on_telegram = forms.BooleanField(
        required=False, label=_('ذخیره در کانال تلگرام من'),
        help_text=_('از سهمیه‌ی فضای شما کم نمی‌شود'),
        widget=forms.CheckboxInput(attrs={'class': 'accent-[var(--accent)]'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.telegram_connection = self._telegram_connection()
        if self.telegram_connection is None:
            del self.fields['store_on_telegram']

    def _telegram_connection(self):
        """The user's usable channel, or None."""
        if self.user is None or not self.user.is_authenticated:
            return None

        from telegram_module import feature
        from telegram_module.linking import connection_for

        if not feature.is_enabled():
            return None
        connection = connection_for(self.user, create=False)
        return connection if connection and connection.is_usable() else None

    def wants_telegram(self):
        return bool(self.cleaned_data.get('store_on_telegram')) and \
            self.telegram_connection is not None

    def clean_file(self):
        upload = self.cleaned_data.get('file')
        # Without a cap one person can fill the disk or bucket.
        limit = getattr(settings, 'MAX_UPLOAD_SIZE_BYTES', 0)
        if upload and limit and upload.size > limit:
            raise forms.ValidationError(
                _('حجم فایل بیشتر از حد مجاز است (حداکثر %(limit)s گیگابایت).')
                % {'limit': round(limit / (1024 ** 3), 1)}
            )
        return upload

    def clean(self):
        cleaned = super().clean()
        visibility = cleaned.get('visibility')
        hours = cleaned.get('public_for_hours')
        price = cleaned.get('price') or 0

        if visibility == UploadFile.Visibility.PRIVATE:
            if price:
                self.add_error('price', _('فایل خصوصی قابل فروش نیست'))
            if hours:
                self.add_error('public_for_hours', _('فایل خصوصی پنجره‌ی انتشار ندارد'))

        upload = cleaned.get('file')
        if not upload or self.user is None:
            return cleaned

        # Which limit applies depends on where the file is going, so this
        # cannot live in clean_file: the checkbox is not cleaned yet there.
        if self.wants_telegram():
            from telegram_module import quota as telegram_quota

            try:
                telegram_quota.check_queue(self.user, upload.size)
            except telegram_quota.StagingFull as exc:
                self.add_error('store_on_telegram', str(exc))
        else:
            from account_module import quota

            # Separate from the per-file cap: that stops one huge file, this
            # stops a thousand small ones.
            try:
                quota.check_upload(self.user, upload.size)
            except quota.QuotaExceeded as exc:
                self.add_error('file', str(exc))
        return cleaned

    def save(self, commit=True):
        upload = super().save(commit=False)
        upload.price = self.cleaned_data.get('price') or 0

        incoming = self.cleaned_data.get('file')
        if incoming is not None and getattr(incoming, 'name', ''):
            upload.original_filename = incoming.name[:255]

        hours = self.cleaned_data.get('public_for_hours')
        if upload.visibility == UploadFile.Visibility.PUBLIC and hours:
            upload.public_until = timezone.now() + timedelta(hours=hours)
        else:
            upload.public_until = None

        if commit:
            upload.save()
        return upload


class UploadEditForm(UploadModelForm):
    """Edit a file's details after it is uploaded.

    The file itself is not editable. Replacing the bytes under a slug that is
    already shared would hand a different file to everyone holding the link,
    and for a file already moved to Telegram it would leave the stored parts
    describing something that no longer exists. Deleting and uploading again is
    the honest way to change the contents.
    """

    class Meta(UploadModelForm.Meta):
        fields = ['name', 'description', 'visibility']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Storage is already decided for this file; the choice belongs on the
        # upload form only.
        self.fields.pop('store_on_telegram', None)

        instance = kwargs.get('instance') or self.instance
        self.fields['price'].initial = instance.price or None
        self.fields['public_for_hours'].initial = self._hours_left(instance)
        self.fields['public_for_hours'].help_text = _(
            'خالی یعنی بدون محدودیت زمانی. عددی که می‌بینی زمان باقی‌مانده‌ی '
            'فعلی است.'
        )

    @staticmethod
    def _hours_left(instance):
        """The current publishing window, as whole hours from now.

        Prefilled so saving without touching this field keeps roughly the end
        time it already had, instead of silently removing the limit. Rounded
        up, so an edit never shortens the window.
        """
        if not instance.public_until:
            return None
        remaining = instance.public_until - timezone.now()
        if remaining.total_seconds() <= 0:
            return None
        return -(-int(remaining.total_seconds()) // 3600) or 1

    def wants_telegram(self):
        return False
