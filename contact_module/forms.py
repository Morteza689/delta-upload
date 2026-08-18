from django import forms
from django.utils.translation import gettext_lazy as _

from account_module.forms import INPUT_CLASS

from .models import ContactUs, Ticket, TicketMessage

#: Ticket attachments are small; a separate cap from the gigabyte-scale
#: file upload limit.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


class AttachmentMixin:
    def clean_attachment(self):
        upload = self.cleaned_data.get('attachment')
        if upload and upload.size > MAX_ATTACHMENT_BYTES:
            raise forms.ValidationError(
                _('حجم پیوست بیشتر از حد مجاز است (حداکثر %(mb)s مگابایت).')
                % {'mb': MAX_ATTACHMENT_BYTES // (1024 * 1024)}
            )
        return upload


class TicketForm(AttachmentMixin, forms.ModelForm):
    """A new ticket: its details plus the first message."""

    body = forms.CharField(
        label=_('شرح مشکل'),
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': '6',
                                     'placeholder': _('هرچه دقیق‌تر بنویسی، سریع‌تر حل می‌شود.')}),
    )
    attachment = forms.FileField(
        label=_('پیوست'), required=False,
        help_text=_('اختیاری — تصویر خطا یا فایل مرتبط'),
        widget=forms.ClearableFileInput(attrs={'class': INPUT_CLASS}),
    )

    class Meta:
        model = Ticket
        fields = ['subject', 'department', 'priority']
        widgets = {
            'subject': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'department': forms.Select(attrs={'class': INPUT_CLASS}),
            'priority': forms.Select(attrs={'class': INPUT_CLASS}),
        }


class TicketReplyForm(AttachmentMixin, forms.ModelForm):
    class Meta:
        model = TicketMessage
        fields = ['body', 'attachment']
        widgets = {
            'body': forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': '4',
                                          'placeholder': _('پاسخ شما…')}),
            'attachment': forms.ClearableFileInput(attrs={'class': INPUT_CLASS}),
        }
        labels = {'body': _('پاسخ'), 'attachment': _('پیوست')}


class ContactUsModelForm(forms.ModelForm):
    class Meta:
        model = ContactUs
        fields = ['title', 'email', 'full_name', 'message']
        widgets = {
            'title': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'email': forms.EmailInput(attrs={'class': INPUT_CLASS}),
            'full_name': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'message': forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': '5'}),
        }
        labels = {
            'title': _('عنوان'),
            'email': _('ایمیل'),
            'full_name': _('نام و نام خانوادگی'),
            'message': _('متن پیام'),
        }
