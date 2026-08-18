"""Payout destination validation.

Money sent to a wrong IBAN or wallet address does not come back, so we verify
checksums where they exist, not just the shape.
"""
import hashlib
import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

#: Bitcoin/Tron base58 alphabet. Ambiguous characters (0 O I l) are excluded.
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


# --- IBAN ---

def normalize_sheba(value):
    """Normalise to IR plus 24 digits.

    Users paste IBANs with spaces, without the IR prefix, or with Persian
    digits. Storing one canonical form keeps duplicate detection working.
    """
    if not value:
        return ''

    text = str(value).strip()

    # Persian and Arabic digits to latin
    digits = {ord('۰') + i: str(i) for i in range(10)}
    digits.update({ord('٠') + i: str(i) for i in range(10)})
    text = text.translate(digits)

    text = re.sub(r'[\s\-_.]', '', text).upper()

    if text.startswith('IR'):
        text = text[2:]

    return 'IR' + text


def sheba_checksum_ok(sheba):
    """Standard IBAN mod-97 checksum.

    Catches transposed digits, the most common IBAN typo.
    """
    rearranged = sheba[4:] + sheba[:4]

    numeric = ''
    for char in rearranged:
        if char.isdigit():
            numeric += char
        elif char.isalpha():
            numeric += str(ord(char) - 55)  # A=10 … Z=35
        else:
            return False

    return int(numeric) % 97 == 1


def validate_sheba(value):
    """Return the normalised IBAN or raise ValidationError."""
    sheba = normalize_sheba(value)

    if not re.fullmatch(r'IR\d{24}', sheba):
        raise ValidationError(
            _('شبا باید با IR شروع شود و ۲۴ رقم داشته باشد.')
        )

    if not sheba_checksum_ok(sheba):
        raise ValidationError(
            _('این شبا معتبر نیست. رقم‌ها را دوباره بررسی کن — احتمالاً یکی جابه‌جا شده.')
        )

    return sheba


# --- USDT ---

def base58_decode(text):
    """Decode base58 to bytes, or None on an invalid character."""
    number = 0
    for char in text:
        index = BASE58.find(char)
        if index < 0:
            return None
        number = number * 58 + index

    raw = number.to_bytes((number.bit_length() + 7) // 8, 'big')

    # Leading zeros are '1' in base58 and get lost in the integer conversion.
    padding = len(text) - len(text.lstrip('1'))
    return b'\x00' * padding + raw


def validate_trc20(value):
    """Tron address: base58check with a 0x41 prefix.

    The checksum is verified, not just length and alphabet. Without it an
    address with one wrong character still looks valid.
    """
    address = str(value or '').strip()

    if not address.startswith('T') or len(address) != 34:
        raise ValidationError(
            _('آدرس TRC20 باید با T شروع شود و ۳۴ کاراکتر باشد.')
        )

    decoded = base58_decode(address)
    if decoded is None or len(decoded) != 25:
        raise ValidationError(_('آدرس TRC20 معتبر نیست.'))

    payload, checksum = decoded[:21], decoded[21:]

    if payload[0] != 0x41:
        raise ValidationError(_('این آدرس متعلق به شبکه‌ی ترون نیست.'))

    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise ValidationError(
            _('آدرس TRC20 معتبر نیست. احتمالاً موقع کپی کردن ناقص شده.')
        )

    return address


def validate_bep20(value):
    """BEP20 (EVM) address: 0x plus 40 hex digits.

    Shape only. EIP-55 needs keccak256, which is not in the standard library
    (sha3_256 is not keccak), and one dependency for that alone was not worth
    it. Unchecksummed addresses are valid on-chain, so this rejects no good
    address; it just misses some typos.
    """
    address = str(value or '').strip()

    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
        raise ValidationError(
            _('آدرس BEP20 باید با 0x شروع شود و ۴۰ رقم هگزادسیمال داشته باشد.')
        )

    return address


#: method -> validator
VALIDATORS = {
    'sheba': validate_sheba,
    'usdt-trc20': validate_trc20,
    'usdt-bep20': validate_bep20,
}


def validate_destination(method, value):
    """Validate for the given method and return the normalised value."""
    validator = VALIDATORS.get(method)
    if validator is None:
        raise ValidationError(_('روش برداشت نامعتبر است.'))
    return validator(value)


def mask(method, destination):
    """Short display form. Users recognise an account by its first and last
    few characters, not the full string.
    """
    text = str(destination or '')
    if len(text) <= 12:
        return text
    return f'{text[:6]}…{text[-4:]}'


# --- Minimums and transfer fees ---
#
# Each method carries its own network fee, and its minimum must exceed that
# fee by enough that the user is not withdrawing mostly fee.
#
# The fee comes out of the requested amount, not out of the remaining balance.

#: Defaults track real network fees. Override with PAYOUT_METHODS in settings
#: so changing them needs no code change.
DEFAULT_METHOD_RULES = {
    # Domestic bank transfer: negligible fee, so the minimum can be low.
    'sheba': {'min_cents': 200, 'fee_cents': 0},
    # Tron: a USDT transfer costs roughly one dollar in network fees.
    'usdt-trc20': {'min_cents': 1000, 'fee_cents': 100},
    # BSC is cheaper but not free.
    'usdt-bep20': {'min_cents': 1000, 'fee_cents': 30},
}


def method_rules(method):
    """Minimum and fee for a method, both in cents."""
    from django.conf import settings

    configured = getattr(settings, 'PAYOUT_METHODS', None) or DEFAULT_METHOD_RULES
    return configured.get(method) or DEFAULT_METHOD_RULES.get(method) or {
        'min_cents': 0, 'fee_cents': 0,
    }


def min_cents(method):
    return method_rules(method)['min_cents']


def fee_cents(method):
    return method_rules(method)['fee_cents']


def net_cents(method, gross_cents):
    """What the user actually receives. Never negative."""
    return max(0, gross_cents - fee_cents(method))


def rules_table():
    """Rules for every method, for display on the withdrawal form.

    Reads the same source the validation uses, so the numbers shown can never
    disagree with the numbers accepted.
    """
    from .models import PayoutAccount

    rows = []
    for value, label in PayoutAccount.Method.choices:
        rules = method_rules(value)
        rows.append({
            'method': value,
            'label': label,
            'min_cents': rules['min_cents'],
            'fee_cents': rules['fee_cents'],
        })
    return rows
