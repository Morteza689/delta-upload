"""Payment settlement and wallet crediting."""
import logging

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from account_module import wallet
from account_module.models import Transaction

from .models import Payment

logger = logging.getLogger(__name__)


@transaction.atomic
def settle(payment, tracking_code=''):
    """Mark a payment paid and credit the wallet.

    The status change is a compare-and-swap: only the request that flips it
    from unpaid does the crediting. Webhooks retry and the user can refresh
    the return page, so without this a single payment would credit twice.
    """
    claimed = (
        Payment.objects
        .filter(pk=payment.pk)
        .exclude(status=Payment.Status.PAID)
        .update(
            status=Payment.Status.PAID,
            paid_at=timezone.now(),
            tracking_code=tracking_code,
            error='',
        )
    )

    if not claimed:
        # Another request got here first and already credited it.
        payment.refresh_from_db()
        logger.info('payment %s already settled, skipping credit', payment.pk)
        return payment

    payment.refresh_from_db()

    # The amount always comes from our own record, never from what the gateway
    # returned in the URL.
    entry = wallet.credit(
        payment.user,
        payment.amount,
        Transaction.Kind.TOPUP,
        description=_('شارژ کیف پول از %(gateway)s') % {'gateway': payment.gateway},
    )
    payment.transaction = entry
    payment.save(update_fields=['transaction'])

    logger.info('payment %s settled: %s cents to user %s',
                payment.pk, payment.amount, payment.user_id)
    return payment


def mark_failed(payment, reason='', status=Payment.Status.FAILED):
    """Mark a payment failed. A settled payment is never reversed."""
    updated = (
        Payment.objects
        .filter(pk=payment.pk)
        .exclude(status=Payment.Status.PAID)
        .update(status=status, error=reason[:300])
    )
    payment.refresh_from_db()
    return updated > 0
