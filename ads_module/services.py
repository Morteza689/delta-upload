"""Ad impression counting and uploader revenue."""
import hashlib
import logging
import random
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from utils.http_service import get_client_ip

from .models import LinkView, AdEarning, AdSetting, AdSlot, DownloadSession, EarningCarry

logger = logging.getLogger(__name__)

#: A full pass through the funnel shows two ads: step 1 and step 2.
IMPRESSIONS_PER_FUNNEL = 2


def visitor_hash(request):
    """Stable visitor identifier that never stores the IP itself.

    Salted with SECRET_KEY: the IPv4 space is small enough that an unsalted
    hash is equivalent to storing the address.

    The address comes from get_client_ip, which only trusts X-Forwarded-For
    when explicitly configured. Reading the raw header here would let an
    attacker be a new visitor on every request.
    """
    ip = get_client_ip(request)
    agent = request.META.get('HTTP_USER_AGENT', '')

    raw = f'{settings.SECRET_KEY}|{ip}|{agent}'.encode()
    return hashlib.sha256(raw).hexdigest()


def pick_slot(position):
    """An active slot for this position, chosen by weight."""
    slots = list(AdSlot.objects.filter(position=position, is_active=True))
    if not slots:
        return None
    weights = [max(slot.weight, 1) for slot in slots]
    return random.choices(slots, weights=weights, k=1)[0]


def start_session(request, upload_file):
    """Start a session at step 1."""
    return DownloadSession.objects.create(
        file=upload_file,
        visitor_hash=visitor_hash(request),
    )


#: User agents that are never a person's browser. Deliberately short: it names
#: common scripting tools and does not guess about real browsers.
BOT_AGENT_MARKERS = (
    'curl/', 'wget/', 'python-requests', 'python-urllib', 'httpie/', 'go-http-client',
    'java/', 'okhttp', 'axios/', 'node-fetch', 'libwww-perl', 'scrapy',
    'headlesschrome', 'phantomjs', 'puppeteer', 'playwright',
    'bot', 'spider', 'crawler',
)


def _looks_like_a_bot(request):
    agent = (request.META.get('HTTP_USER_AGENT') or '').lower()
    if not agent:
        # A real browser always sends a User-Agent.
        return True
    return any(marker in agent for marker in BOT_AGENT_MARKERS)


def _rejection_reason(request, session, setting):
    """Return why this pass should not count, or None.

    Checks run cheapest first so obviously invalid requests never reach the
    database.
    """
    if setting is None or setting.cpm_cents <= 0:
        return 'no_setting'

    if session.counted or session.step2_at is not None:
        return 'already_counted'

    # The session must belong to this visitor, or one token could be shared
    # around and earn repeatedly.
    if session.visitor_hash != visitor_hash(request):
        return 'visitor_mismatch'

    if _looks_like_a_bot(request):
        return 'bot_agent'

    # A real browser returns the cookie it was given at step 1.
    if setting.require_cookies and not request.COOKIES:
        return 'no_cookies'

    dwell = (timezone.now() - session.step1_at).total_seconds()
    if dwell < setting.min_dwell_seconds:
        return 'too_fast'
    if dwell > setting.max_dwell_seconds:
        return 'too_slow'

    author_id = session.file.author_id
    if author_id is None:
        return 'no_author'

    # An uploader must not earn from viewing their own file.
    if request.user.is_authenticated and request.user.id == author_id:
        return 'self_view'

    now = timezone.now()

    # One visitor earns once per file within the dedup window.
    since = now - timedelta(hours=setting.dedup_hours)
    if DownloadSession.objects.filter(
        file_id=session.file_id, visitor_hash=session.visitor_hash,
        counted=True, step2_at__gte=since,
    ).exists():
        return 'duplicate'

    # And must not earn back to back by hopping between files.
    cooldown = now - timedelta(seconds=setting.visitor_cooldown_seconds)
    if DownloadSession.objects.filter(
        visitor_hash=session.visitor_hash, counted=True, step2_at__gte=cooldown,
    ).exists():
        return 'cooldown'

    # Daily caps: the last line of defence once the other rules are evaded.
    day_start = now - timedelta(days=1)
    if AdEarning.objects.filter(
        file_id=session.file_id, created_at__gte=day_start,
    ).count() >= setting.max_earnings_per_file_per_day:
        return 'file_daily_cap'

    if AdEarning.objects.filter(
        user_id=author_id, created_at__gte=day_start,
    ).count() >= setting.max_earnings_per_user_per_day:
        return 'user_daily_cap'

    return None


@transaction.atomic
def complete_session(request, session):
    """Record step 2 and create an earning if the pass is valid."""
    setting = AdSetting.current()

    # Lock the session so two concurrent requests cannot both earn from it.
    locked = DownloadSession.objects.select_for_update().get(pk=session.pk)
    reason = _rejection_reason(request, locked, setting)

    if reason is not None:
        DownloadSession.objects.filter(pk=locked.pk, step2_at__isnull=True).update(
            step2_at=timezone.now(), reject_reason=reason,
        )
        logger.debug('session %s not counted: %s', locked.pk, reason)
        return None

    micro = setting.micro_per_impression() * IMPRESSIONS_PER_FUNNEL
    if micro <= 0:
        DownloadSession.objects.filter(pk=locked.pk).update(
            step2_at=timezone.now(), reject_reason='zero_value',
        )
        return None

    DownloadSession.objects.filter(pk=locked.pk).update(
        step2_at=timezone.now(), counted=True, reject_reason='',
    )

    return AdEarning.objects.create(
        user_id=locked.file.author_id,
        file_id=locked.file_id,
        session=locked,
        micro_cents=micro,
        impressions=IMPRESSIONS_PER_FUNNEL,
        available_at=timezone.now() + timedelta(days=setting.confirm_after_days),
    )


@transaction.atomic
def confirm_user_earnings(user, now=None):
    """Move a user's matured earnings into their wallet.

    Only whole cents are credited; the remainder is carried forward.
    Returns (record count, cents credited).
    """
    from account_module import wallet
    from account_module.models import Transaction
    from django.utils.translation import gettext as _

    from .models import MICRO_PER_CENT

    now = now or timezone.now()

    ready = list(
        AdEarning.objects
        .select_for_update()
        .filter(user=user, status=AdEarning.Status.PENDING, available_at__lte=now)
    )
    if not ready:
        return 0, 0

    carry, _created = EarningCarry.objects.select_for_update().get_or_create(user=user)

    total_micro = carry.micro_cents + sum(item.micro_cents for item in ready)
    cents = total_micro // MICRO_PER_CENT
    remainder = total_micro % MICRO_PER_CENT

    AdEarning.objects.filter(pk__in=[item.pk for item in ready]).update(
        status=AdEarning.Status.CONFIRMED,
    )
    EarningCarry.objects.filter(pk=carry.pk).update(micro_cents=remainder)

    if cents > 0:
        wallet.credit(
            user, cents, Transaction.Kind.AD_REVENUE,
            description=_('درآمد تبلیغات %(count)s بازدید') % {'count': len(ready)},
        )

    logger.info('confirmed %s earnings for user %s: %s cents (carry %s micro)',
                len(ready), user.pk, cents, remainder)
    return len(ready), cents


def loader_script():
    """The network loader script, or an empty string.

    Views that carry a slot put this in the context so base.html renders it
    once in the head.
    """
    setting = AdSetting.current()
    return setting.loader_script if setting else ''


def session_belongs_to(request, session):
    """Does this session belong to this visitor, and is it still fresh?

    Controls access to step 2, separately from the revenue decision. Rejecting
    only the revenue left the step 2 link a permanent, shareable bypass of
    step 1.

    Reuses max_dwell_seconds rather than introducing a second number to keep
    in sync.
    """
    if session.visitor_hash != visitor_hash(request):
        return False

    setting = AdSetting.current()
    window = setting.max_dwell_seconds if setting else 600

    age = (timezone.now() - session.step1_at).total_seconds()
    return age <= window


# --- Shortener interstitial revenue ---

def _link_rejection_reason(request, link, setting, visitor):
    """Return why this interstitial view should not earn, or None.

    Same rules as the download funnel, minus the dwell window: there is only
    one page here, not two.
    """
    if setting is None or setting.cpm_cents <= 0:
        return 'no_setting'

    if link.owner_id is None:
        return 'no_owner'

    if _looks_like_a_bot(request):
        return 'bot_agent'

    # No cookie check here, deliberately.
    #
    # It works in the download funnel because that has two pages: the first
    # sets a cookie, the second reads it back. The interstitial is a single
    # page and always the visitor's first contact, so no cookie exists yet.
    # The same rule here would reject every view.

    # A link owner must not earn from opening their own link.
    if request.user.is_authenticated and request.user.id == link.owner_id:
        return 'self_view'

    now = timezone.now()

    # One visitor earns once per link within the dedup window.
    since = now - timedelta(hours=setting.dedup_hours)
    if LinkView.objects.filter(
        link_id=link.pk, visitor_hash=visitor, counted=True,
        created_at__gte=since,
    ).exists():
        return 'duplicate'

    # Enforced gap between two earnings by one visitor, across links.
    cooldown = now - timedelta(seconds=setting.visitor_cooldown_seconds)
    if LinkView.objects.filter(
        visitor_hash=visitor, counted=True, created_at__gte=cooldown,
    ).exists():
        return 'cooldown'

    day_ago = now - timedelta(days=1)
    if LinkView.objects.filter(
        link_id=link.pk, counted=True, created_at__gte=day_ago,
    ).count() >= setting.max_earnings_per_file_per_day:
        return 'link_daily_cap'

    if AdEarning.objects.filter(
        user_id=link.owner_id, created_at__gte=day_ago,
    ).count() >= setting.max_earnings_per_user_per_day:
        return 'user_daily_cap'

    return None


@transaction.atomic
def record_link_view(request, link):
    """Record an interstitial view and create an earning if it is valid.

    One impression, not two: the download funnel has two pages, this has one.
    """
    setting = AdSetting.current()
    visitor = visitor_hash(request)

    reason = _link_rejection_reason(request, link, setting, visitor)

    view = LinkView.objects.create(
        link=link, visitor_hash=visitor,
        counted=reason is None, reject_reason=reason or '',
    )

    if reason is not None:
        logger.debug('link view %s not counted: %s', view.pk, reason)
        return None

    micro = setting.micro_per_impression()
    if micro <= 0:
        LinkView.objects.filter(pk=view.pk).update(
            counted=False, reject_reason='zero_value')
        return None

    return AdEarning.objects.create(
        user_id=link.owner_id,
        link=link,
        link_view=view,
        micro_cents=micro,
        impressions=1,
        available_at=timezone.now() + timedelta(days=setting.confirm_after_days),
    )
