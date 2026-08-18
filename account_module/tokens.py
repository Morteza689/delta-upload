"""Single-use password reset tokens.

Kept separate from email_active_code. That code was previously accepted as a
reset token and was never rotated after activation, so the code sent in the
welcome email stayed a valid password-change link forever.

Tokens are stored hashed, expire, and are single use.
"""
import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Longer widens the attack window; shorter just makes users request again.
LIFETIME = timedelta(hours=1)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class PasswordResetToken(models.Model):
    user = models.ForeignKey('account_module.User', on_delete=models.CASCADE,
                             related_name='reset_tokens', verbose_name=_('کاربر'))
    #: The raw token is never stored, only its hash.
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('توکن بازیابی رمز')
        verbose_name_plural = _('توکن های بازیابی رمز')
        ordering = ['-created_at']

    def is_valid(self):
        return self.used_at is None and timezone.now() < self.expires_at

    @classmethod
    def issue(cls, user):
        """Issue a token and return the raw value.

        Earlier tokens for the same user are invalidated: several live reset
        links at once only widen the attack window.
        """
        cls.objects.filter(user=user, used_at__isnull=True).update(
            used_at=timezone.now(),
        )

        raw = secrets.token_urlsafe(48)
        cls.objects.create(
            user=user,
            token_hash=hash_token(raw),
            expires_at=timezone.now() + LIFETIME,
        )
        return raw

    @classmethod
    def consume(cls, raw):
        """Consume the token and return its user, or None.

        Compared on the hash and case-sensitively; a case-insensitive match
        shrinks the guess space.
        """
        token = cls.objects.filter(token_hash=hash_token(raw or '')).first()
        if token is None or not token.is_valid():
            return None

        # Conditional update: with two concurrent requests only one wins.
        updated = cls.objects.filter(pk=token.pk, used_at__isnull=True).update(
            used_at=timezone.now(),
        )
        return token.user if updated else None
