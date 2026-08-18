"""Guest upload, without an account.

A guest file has nobody answerable for it, so it carries two limits a member's
file does not: a smaller size cap and automatic deletion.
"""
from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.forms import INPUT_CLASS, captcha_field

from .models import UploadFile

#: Guest file lifetimes in hours, from one hour to five days.
LIFETIME_CHOICES = [
    ('1', _('۱ ساعت')),
    ('6', _('۶ ساعت')),
    ('24', _('۱ روز')),
    ('72', _('۳ روز')),
    ('120', _('۵ روز')),
]
DEFAULT_LIFETIME_HOURS = 24


class GuestUploadForm(forms.Form):
    """Upload form for visitors without an account."""

    file = forms.FileField(
        label=_('فایل'),
        widget=forms.FileInput(attrs={'class': INPUT_CLASS}),
    )
    name = forms.CharField(
        required=False, max_length=100, label=_('نام نمایشی'),
        help_text=_('اختیاری — اگر خالی بگذاری نام خود فایل استفاده می‌شود'),
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )
    lifetime_hours = forms.ChoiceField(
        choices=LIFETIME_CHOICES, initial=str(DEFAULT_LIFETIME_HOURS),
        label=_('بعد از چه مدتی پاک شود؟'),
        widget=forms.Select(attrs={'class': INPUT_CLASS}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        #: Without a captcha this form becomes free anonymous storage.
        self.fields['captcha'] = captcha_field()

    def clean_file(self):
        upload = self.cleaned_data['file']
        limit = guest_size_limit()
        if upload.size > limit:
            raise forms.ValidationError(
                _('حجم فایل برای آپلود مهمان بیشتر از حد مجاز است (حداکثر %(mb)s مگابایت). '
                  'برای فایل بزرگ‌تر وارد حسابت شو.')
                % {'mb': limit // (1024 * 1024)}
            )
        return upload

    @property
    def auto_delete_at(self):
        hours = int(self.cleaned_data['lifetime_hours'])
        return timezone.now() + timedelta(hours=hours)


def guest_size_limit():
    """The guest upload cap.

    Much lower than a member's: an unowned file cannot be attributed to
    anyone, so its storage cost and abuse risk must stay bounded.
    """
    return getattr(settings, 'MAX_GUEST_UPLOAD_SIZE_BYTES', 200 * 1024 * 1024)


def purge_expired(now=None):
    """Delete guest files whose deadline has passed.

    Returns (file count, bytes freed).
    """
    now = now or timezone.now()
    expired = UploadFile.objects.filter(
        auto_delete_at__isnull=False, auto_delete_at__lte=now,
    )

    removed = 0
    freed = 0
    for upload in expired:
        freed += upload.size_bytes or 0
        # The model's delete also removes the file from storage; a queryset delete
        # does not, and would leave orphans on disk.
        upload.delete()
        removed += 1

    return removed, freed
