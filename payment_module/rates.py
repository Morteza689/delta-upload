"""USD rate lookup from Iranian providers.

Each provider is one small fetch-and-read function, so adding another does not
touch the rest. A failed lookup never blocks payments: the last good rate stays
in the database and remains authoritative.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10

#: A rate outside this range means something is wrong: a unit change (rial
#: instead of toman), a broken payload, or an error page instead of JSON.
#: Better to skip the update than to price every wallet with a bad number.
MIN_SANE_TOMAN = 10_000
MAX_SANE_TOMAN = 10_000_000


class RateError(Exception):
    """Rate lookup failed. The message is shown to the admin as-is."""


def _get_json(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    # Some providers reject requests without a User-Agent.
    request.add_header('User-Agent', 'DeltaUpload/1.0 (+https://d-up.ir)')
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raise RateError(f'سرویس کد {exc.code} برگرداند') from exc
    except urllib.error.URLError as exc:
        raise RateError(f'اتصال برقرار نشد: {exc.reason}') from exc
    except OSError as exc:
        raise RateError(f'خطای شبکه: {exc}') from exc

    try:
        return json.loads(body)
    except ValueError as exc:
        raise RateError('پاسخ سرویس JSON معتبر نبود') from exc


def _sane(raw, source, divide_by=1):
    """Convert a raw provider value into a sanity-checked toman rate.

    The numeric conversion lives here rather than in the provider functions:
    a float() there would raise ValueError past RateError, giving the admin a
    vague message instead of a clear one.

    divide_by is 10 for providers that report rials.
    """
    if isinstance(raw, str):
        # Some providers return numbers with thousands separators.
        raw = raw.replace(',', '').strip()

    try:
        value = int(round(float(raw) / divide_by))
    except (TypeError, ValueError) as exc:
        raise RateError(f'{source}: مقدار نرخ عدد نبود ({raw!r})') from exc

    if not MIN_SANE_TOMAN <= value <= MAX_SANE_TOMAN:
        raise RateError(
            f'{source}: نرخ {value:,} تومان خارج از بازه‌ی منطقی است و رد شد'
        )
    return value


def from_nobitex(api_key=''):
    """Nobitex USDT price. No API key required.

    -rls markets are quoted in rials, hence the divide by 10.
    """
    payload = _get_json(
        'https://api.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls'
    )
    stats = (payload or {}).get('stats') or {}
    market = stats.get('usdt-rls') or {}
    rial = market.get('latest') or market.get('bestSell')

    if rial is None:
        raise RateError('نوبیتکس: قیمت تتر در پاسخ نبود')

    return _sane(rial, 'نوبیتکس', divide_by=10)


def from_brsapi(api_key=''):
    """BrsApi open-market USD rate. Needs a free key. Quoted in rials."""
    if not api_key:
        raise RateError('BrsApi بدون کلید کار نمی‌کند؛ کلید را در همین صفحه وارد کن')

    payload = _get_json(
        'https://BrsApi.ir/Api/Market/Gold_Currency.php?key='
        + urllib.parse.quote(api_key)
    )

    for row in (payload or {}).get('currency') or []:
        if row.get('symbol') == 'USD':
            return _sane(row.get('price'), 'BrsApi', divide_by=10)

    raise RateError('BrsApi: دلار در پاسخ پیدا نشد')


def from_tgju(api_key=''):
    """TGJU open-market USD rate, no key. Returns a comma-grouped rial string."""
    payload = _get_json('https://call1.tgju.org/ajax.json')
    row = (payload or {}).get('current', {}).get('price_dollar_rl')

    if not row or 'p' not in row:
        raise RateError('TGJU: نرخ دلار در پاسخ نبود')

    return _sane(row['p'], 'TGJU', divide_by=10)


#: key -> (label, fetcher, needs an API key)
#: Order matches the select list in the admin panel.
PROVIDERS = {
    'tgju': (_('TGJU (دلار آزاد) — بدون کلید'), from_tgju, False),
    'nobitex': (_('نوبیتکس (تتر) — بدون کلید'), from_nobitex, False),
    'brsapi': (_('BrsApi (دلار آزاد) — با کلید'), from_brsapi, True),
}

PROVIDER_CHOICES = [(key, label) for key, (label, _f, _k) in PROVIDERS.items()]

#: TGJU is the default: no key needed, and it reports the open-market rate
#: users have in mind. Switching provider is one click in the panel.
DEFAULT_PROVIDER = 'tgju'


def fetch(provider=DEFAULT_PROVIDER, api_key=''):
    """Fetch from the given provider.

    Returns toman per dollar as an integer, or raises RateError.
    """
    entry = PROVIDERS.get(provider)
    if entry is None:
        raise RateError(f'ارائه‌دهنده‌ی ناشناخته: {provider}')

    _label, function, _needs_key = entry
    rate = function(api_key)
    logger.info('نرخ ارز از %s: %s تومان', provider, rate)
    return rate
