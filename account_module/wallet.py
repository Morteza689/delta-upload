"""Internal wallet operations.

Every balance change goes through here rather than assigning user.balance:
this takes a row lock and writes a ledger row. Without it two concurrent
requests can corrupt the balance and leave no trace.
"""
from django.db import transaction
from django.db.models import F
from django.utils.translation import gettext as _

from .models import Transaction, User


class WalletError(Exception):
    """Base wallet error."""


class InsufficientBalance(WalletError):
    """The balance does not cover this debit."""


@transaction.atomic
def _apply(user, amount, kind, file=None, description=''):
    # Lock the user row for the transaction so the read and the write of the
    # balance cannot be separated.
    locked = User.objects.select_for_update().get(pk=user.pk)

    new_balance = locked.balance + amount
    if new_balance < 0:
        raise InsufficientBalance(
            _('موجودی کافی نیست: موجودی %(balance)s، مبلغ درخواستی %(amount)s')
            % {'balance': locked.balance, 'amount': abs(amount)}
        )

    User.objects.filter(pk=user.pk).update(balance=F('balance') + amount)
    # Refresh the caller's instance so later code does not see a stale value.
    user.balance = new_balance

    return Transaction.objects.create(
        user=locked,
        kind=kind,
        amount=amount,
        balance_after=new_balance,
        file=file,
        description=description,
    )


def credit(user, amount, kind, file=None, description=''):
    """Credit the wallet."""
    if amount <= 0:
        raise WalletError(_('مبلغ واریز باید مثبت باشد'))
    return _apply(user, amount, kind, file=file, description=description)


def debit(user, amount, kind, file=None, description=''):
    """Debit the wallet, raising InsufficientBalance if it does not fit."""
    if amount <= 0:
        raise WalletError(_('مبلغ برداشت باید مثبت باشد'))
    return _apply(user, -amount, kind, file=file, description=description)
