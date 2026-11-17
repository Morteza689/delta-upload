"""Display filters used across the templates.

Formatting only: sizes, money, and the name a file is shown under. Nothing
here touches the database or decides anything.
"""
from django import template
from django.utils import translation

from utils import currency

register = template.Library()

PERSIAN_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


@register.filter(name='local_digits')
def local_digits(value):
    """Persian numerals in Persian, Latin ones otherwise.

    The pages get theirs from Vazirmatn's ss01 feature, which an email cannot
    use: there is no web font in a mail client, so the digits have to be the
    characters themselves.
    """
    text = str(value)
    if (translation.get_language() or '').startswith('fa'):
        return text.translate(PERSIAN_DIGITS)
    return text


@register.filter(name='split')
def split_name_file(value):
    return str(value).split("/")[-1]


@register.filter(name='convertor_byte')
def convertor_byte(value):
    """Format a byte count for people."""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ''

    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:,.0f} {unit}' if unit == 'B' else f'{size:,.1f} {unit}'
        size /= 1024


@register.filter(name='three_digits_currency2')
def three_digits_currency2(value):
    try:
        return '{:,}'.format(int(value))
    except (TypeError, ValueError):
        return value


@register.filter(name='money')
def money(cents):
    """Render an amount in the visitor's chosen currency.

    Amounts are integer cents everywhere so floating point stays out of the books
    and a change in the exchange rate cannot rewrite history.
    """
    return currency.format_money(cents)


@register.filter(name='micro_money')
def micro_money(micro_cents):
    """Ad revenue in the visitor's chosen currency.

    The value is a fraction of a cent, so dollars need four decimal places or
    everything reads as $0.00.
    """
    return currency.format_micro(micro_cents)


@register.filter(name='toman')
def toman(cents, rate):
    """The toman equivalent of an amount in cents, at the given rate."""
    try:
        return '{:,}'.format(round(int(cents) * int(rate) / 100))
    except (TypeError, ValueError, ZeroDivisionError):
        return ''


@register.filter(name='file_label')
def file_label(upload_file):
    """A file's name for display, never a slug.

    The title if the owner set one, then the uploaded filename, and only then
    the slug. A slug is twelve random characters: correct, unique, and useless
    to the person reading it.
    """
    if upload_file is None:
        return ''
    if getattr(upload_file, 'name', ''):
        return upload_file.name

    from upload_module.downloads import download_filename

    label = download_filename(upload_file)
    if label and label != 'download':
        return label
    return getattr(upload_file, 'slug', '') or ''
