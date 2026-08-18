from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.models import User


class ContactUs(models.Model):
    """A one-way contact message.

    Kept separate from tickets: this has no conversation and no status, it is
    just something a visitor sent.
    """

    title = models.CharField(max_length=300, verbose_name=_('عنوان'))
    email = models.EmailField(max_length=300, verbose_name=_('ایمیل'))
    full_name = models.CharField(max_length=300, verbose_name=_('نام و نام خانوادگی'))
    message = models.TextField(verbose_name=_('متن تماس با ما'))
    response = models.TextField(null=True, blank=True, verbose_name=_('متن پاسخ تماس با ما'))
    is_read_by_admin = models.BooleanField(verbose_name=_('خوانده شده'), default=False)
    created_deta = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))

    class Meta:
        verbose_name = _('تماس با ما')
        verbose_name_plural = _('لیست تماس با ما')

    def __str__(self):
        return self.title


class Ticket(models.Model):
    """A support conversation.

    Status says whose turn it is, so the queue can be ordered by what actually
    needs an answer.
    """

    class Department(models.TextChoices):
        TECHNICAL = 'technical', _('فنی')
        FINANCIAL = 'financial', _('مالی و پرداخت')
        ACCOUNT = 'account', _('حساب کاربری')
        ABUSE = 'abuse', _('گزارش تخلف')
        OTHER = 'other', _('سایر')

    class Priority(models.TextChoices):
        LOW = 'low', _('کم')
        NORMAL = 'normal', _('عادی')
        HIGH = 'high', _('زیاد')

    class Status(models.TextChoices):
        OPEN = 'open', _('در انتظار پاسخ')
        ANSWERED = 'answered', _('پاسخ داده شده')
        CLOSED = 'closed', _('بسته شده')

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='tickets', verbose_name=_('کاربر'))
    subject = models.CharField(max_length=200, verbose_name=_('موضوع'))
    department = models.CharField(max_length=20, choices=Department.choices,
                                  default=Department.TECHNICAL,
                                  verbose_name=_('بخش'))
    priority = models.CharField(max_length=20, choices=Priority.choices,
                                default=Priority.NORMAL, verbose_name=_('اولویت'))
    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.OPEN, db_index=True,
                              verbose_name=_('وضعیت'))

    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ایجاد'))
    # Last message time, for ordering the support queue. Not auto_now: a status
    # change must not push a ticket back to the top.
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True,
                                           verbose_name=_('آخرین پیام'))
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name=_('تاریخ بسته شدن'))

    class Meta:
        verbose_name = _('تیکت')
        verbose_name_plural = _('تیکت ها')
        ordering = ['-last_message_at']
        indexes = [models.Index(fields=['status', '-last_message_at'])]

    def __str__(self):
        return f'#{self.pk} {self.subject}'

    @property
    def is_open(self):
        return self.status != self.Status.CLOSED

    @property
    def waiting_for_staff(self):
        return self.status == self.Status.OPEN

    def touch(self, status):
        """Update the status and the last message time together."""
        self.status = status
        self.last_message_at = timezone.now()
        if status == self.Status.CLOSED:
            self.closed_at = timezone.now()
        self.save(update_fields=['status', 'last_message_at', 'closed_at'])


def ticket_attachment_path(instance, filename):
    # Attachments are kept apart from user uploads so the public download paths
    # can never reach them.
    return f'tickets/{instance.ticket_id}/{filename}'


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE,
                               related_name='messages', verbose_name=_('تیکت'))
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                               related_name='ticket_messages', verbose_name=_('نویسنده'))
    body = models.TextField(verbose_name=_('متن پیام'))
    attachment = models.FileField(upload_to=ticket_attachment_path, blank=True, null=True,
                                  verbose_name=_('پیوست'))
    # Taken from the author's role at send time, not their current is_staff: if
    # someone becomes staff later, their old messages must not turn into support
    # replies.
    is_staff_reply = models.BooleanField(default=False, verbose_name=_('پاسخ پشتیبانی'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ'))

    class Meta:
        verbose_name = _('پیام تیکت')
        verbose_name_plural = _('پیام های تیکت')
        ordering = ['created_at']

    def __str__(self):
        return f'{self.ticket_id} / {self.created_at:%Y-%m-%d %H:%M}'
