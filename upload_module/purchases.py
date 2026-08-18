"""File purchase from the internal wallet."""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from account_module import wallet
from account_module.models import Transaction

from .models import Purchase, UploadFile


class PurchaseError(Exception):
    """The purchase cannot go ahead as things stand."""


def commission_percent():
    return getattr(settings, 'SALE_COMMISSION_PERCENT', 10)


def split(price):
    """Divide a sale price into the site's commission and the seller's share.

    The commission is rounded down and the seller takes the remainder, so the
    two always add back to exactly the price. Rounding each half separately
    loses or invents a cent on prices that do not divide evenly, and a books
    that do not balance are worse than a rounding rule nobody likes.
    """
    commission = price * commission_percent() // 100
    return commission, price - commission


def seller_share(price):
    """Kept for callers that only want the seller's half."""
    return split(price)[1]


@transaction.atomic
def purchase_file(buyer, upload_file: UploadFile) -> Purchase:
    """Buy the file for the buyer and credit the seller's share.

    Debit, credit and the purchase record happen in one transaction: any one
    of them alone leaves the books wrong.
    """
    if upload_file.is_free():
        raise PurchaseError(_('این فایل رایگان است'))
    if upload_file.author_id == buyer.id:
        raise PurchaseError(_('نمی‌توانی فایل خودت را بخری'))

    existing = Purchase.objects.filter(buyer=buyer, file=upload_file).first()
    if existing is not None:
        return existing

    # Price is read and recorded now; a later price change does not affect this
    # purchase.
    price = upload_file.price
    commission, share = split(price)

    wallet.debit(
        buyer, price, Transaction.Kind.PURCHASE, file=upload_file,
        description=f'خرید فایل {upload_file}',
    )

    if upload_file.author_id and share > 0:
        wallet.credit(
            upload_file.author, share, Transaction.Kind.SALE, file=upload_file,
            description=f'فروش فایل {upload_file}',
        )

    try:
        return Purchase.objects.create(
            buyer=buyer, file=upload_file, amount=price, seller_share=share,
            site_commission=commission,
        )
    except IntegrityError:
        # The same user buying the same file concurrently; the unique constraint
        # caught it.
        raise PurchaseError(_('این فایل قبلاً خریداری شده است'))
