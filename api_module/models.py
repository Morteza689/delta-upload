import hashlib
import hmac
import secrets

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from account_module.models import User

#: Full key format: dup_<prefix>_<secret>
KEY_NAMESPACE = 'dup'
PREFIX_LENGTH = 8
SECRET_LENGTH = 32


def _hash(raw_key: str) -> str:
    """Hash a key for storage.

    The key itself is never stored, so a database leak does not hand over
    working client keys.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


class ApiKey(models.Model):
    """An API key belonging to a client site.

    Each key belongs to a user; files uploaded with it belong to that user and
    so does their revenue.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='api_keys', verbose_name=_('کاربر'))
    name = models.CharField(max_length=100, verbose_name=_('نام'),
                            help_text=_('برای اینکه بعداً بدانی این کلید کجا استفاده می‌شود'))
    # The displayable part: used to find the row during authentication and to
    # let the owner tell their keys apart.
    prefix = models.CharField(max_length=PREFIX_LENGTH, unique=True, db_index=True,
                              verbose_name=_('پیشوند'))
    key_hash = models.CharField(max_length=64, verbose_name=_('هش کلید'))

    is_active = models.BooleanField(default=True, verbose_name=_('فعال'))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('تاریخ ساخت'))
    last_used_at = models.DateTimeField(null=True, blank=True,
                                        verbose_name=_('آخرین استفاده'))
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name=_('تاریخ ابطال'))

    class Meta:
        verbose_name = _('کلید API')
        verbose_name_plural = _('کلیدهای API')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.prefix}…)'

    @classmethod
    def generate(cls, user, name):
        """Create a key and return (record, raw key).

        The raw key is available this once only.
        """
        prefix = secrets.token_hex(PREFIX_LENGTH // 2)
        secret = secrets.token_urlsafe(SECRET_LENGTH)[:SECRET_LENGTH]
        raw_key = f'{KEY_NAMESPACE}_{prefix}_{secret}'

        api_key = cls.objects.create(
            user=user, name=name, prefix=prefix, key_hash=_hash(raw_key),
        )
        return api_key, raw_key

    @classmethod
    def authenticate(cls, raw_key):
        """The record for a raw key, or None.

        Only active keys match; revoking must take effect immediately.
        """
        if not raw_key:
            return None

        # maxsplit=2 matters: the secret comes from token_urlsafe and can
        # contain underscores. A plain split rejects those keys silently.
        parts = raw_key.split('_', 2)
        if len(parts) != 3 or parts[0] != KEY_NAMESPACE:
            return None

        api_key = cls.objects.filter(
            prefix=parts[1], is_active=True,
        ).select_related('user').first()
        if api_key is None:
            return None

        if not hmac.compare_digest(api_key.key_hash, _hash(raw_key)):
            return None

        if not api_key.user.is_active:
            return None

        return api_key

    def touch(self):
        """Stamp last use with update() rather than a full save()."""
        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now

    def revoke(self):
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=['is_active', 'revoked_at'])
