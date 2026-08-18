import secrets

from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.models import User

#: Ambiguous characters are excluded: short codes get read aloud and typed.
ALPHABET = 'abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRSTUVWXYZ23456789'
CODE_LENGTH = 7

#: Codes that must never be generated, either because they collide with real
#: paths or because they read badly.
RESERVED = {
    'admin', 'staff', 'api', 'user', 'dl', 'support', 'account', 'payment',
    'ads', 'about', 'terms', 'privacy', 'faq', 'robots', 'static', 'media',
    's', 'go', 'new', 'login', 'signup', 'null', 'undefined',
}


def generate_code(length=CODE_LENGTH):
    return ''.join(secrets.choice(ALPHABET) for _ in range(length))


class ShortLink(models.Model):
    """A short link.

    Creating one needs no account. When the creator is signed in the link is
    attached to them so it shows up in their panel.
    """

    class Mode(models.TextChoices):
        DIRECT = 'direct', _('هدایت مستقیم')
        INTERSTITIAL = 'interstitial', _('نمایش تبلیغ قبل از هدایت')

    code = models.CharField(max_length=20, unique=True, db_index=True,
                            verbose_name=_('کد کوتاه'))
    target = models.URLField(max_length=2000, verbose_name=_('آدرس مقصد'))

    # Interstitial mode is for signed-in users only: the page earns money and
    # needs an owner.
    mode = models.CharField(max_length=20, choices=Mode.choices,
                            default=Mode.DIRECT, verbose_name=_('نوع لینک'))
    # Empty means unlimited.
    max_clicks = models.PositiveIntegerField(
        null=True, blank=True, verbose_name=_('حداکثر تعداد استفاده'),
    )

    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='short_links', verbose_name=_('سازنده'))
    # Salted hash of the guest creator, for rate limiting and abuse review.
    creator_hash = models.CharField(max_length=64, blank=True, db_index=True)

    clicks = models.PositiveIntegerField(default=0, verbose_name=_('تعداد کلیک'))
    last_click_at = models.DateTimeField(null=True, blank=True,
                                         verbose_name=_('آخرین کلیک'))

    is_active = models.BooleanField(default=True, verbose_name=_('فعال'))
    expires_at = models.DateTimeField(null=True, blank=True,
                                      verbose_name=_('انقضا'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ساخت'))

    class Meta:
        verbose_name = _('لینک کوتاه')
        verbose_name_plural = _('لینک های کوتاه')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.code} → {self.target[:50]}'

    @classmethod
    def create_for(cls, target, owner=None, creator_hash='', expires_at=None,
                   mode=None, max_clicks=None):
        """Create a link with a unique code.

        Retries because the code is random and a collision, though unlikely, is
        possible; relying on the unique index alone would 500 the user.
        """
        for _attempt in range(10):
            code = generate_code()
            if code.lower() in RESERVED or cls.objects.filter(code=code).exists():
                continue
            return cls.objects.create(
                code=code, target=target, owner=owner,
                creator_hash=creator_hash, expires_at=expires_at,
                mode=mode or cls.Mode.DIRECT, max_clicks=max_clicks,
            )
        raise RuntimeError('could not allocate a unique short code')

    @property
    def is_expired(self):
        return self.expires_at is not None and timezone.now() > self.expires_at

    @property
    def is_used_up(self):
        return self.max_clicks is not None and self.clicks >= self.max_clicks

    @property
    def remaining_clicks(self):
        if self.max_clicks is None:
            return None
        return max(self.max_clicks - self.clicks, 0)

    def is_available(self):
        """Active, not expired, and under its usage limit.

        Single source of truth: both the redirect view and the preview page use
        this so they cannot drift apart.
        """
        return self.is_active and not self.is_expired and not self.is_used_up

    def unavailable_reason(self):
        if not self.is_active:
            return 'disabled'
        if self.is_expired:
            return 'expired'
        if self.is_used_up:
            return 'used_up'
        return None

    def get_absolute_url(self):
        return reverse('short_link_go', args=[self.code])
