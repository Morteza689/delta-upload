"""Shortener interstitial revenue tests."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from ads_module.models import AdEarning, AdSetting, LinkView

from .models import ShortLink

User = get_user_model()
REAL_BROWSER = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'


def make_user(name='linker'):
    return User.objects.create_user(
        username=name, email=f'{name}@example.com', password='pw',
        email_active_code=name * 3, is_active=True,
    )


class InterstitialEarningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        self.owner = make_user('owner')
        # CPM of 100 cents, 60% share: 0.06 cents per impression, 600 micro-cents.
        self.setting = AdSetting.objects.create(
            cpm_cents=100, uploader_share_percent=60, confirm_after_days=7,
            dedup_hours=24, visitor_cooldown_seconds=60,
            max_earnings_per_file_per_day=500,
            max_earnings_per_user_per_day=2000,
        )
        self.link = ShortLink.create_for(
            'https://example.com/x', owner=self.owner,
            mode=ShortLink.Mode.INTERSTITIAL,
        )

    def visit(self, client=None, **extra):
        client = client or self.client
        extra.setdefault('HTTP_USER_AGENT', REAL_BROWSER)
        return client.get(self.link.get_absolute_url(), **extra)

    def test_a_valid_view_creates_earning(self):
        self.visit()

        earning = AdEarning.objects.get()
        self.assertEqual(earning.user, self.owner)
        self.assertEqual(earning.link, self.link)
        self.assertEqual(earning.micro_cents, 600)

    def test_it_counts_one_impression_not_two(self):
        """The download funnel has two pages; this has one."""
        self.visit()
        self.assertEqual(AdEarning.objects.get().impressions, 1)

    def test_the_earning_starts_pending(self):
        self.visit()
        self.assertEqual(AdEarning.objects.get().status,
                         AdEarning.Status.PENDING)

    def test_a_view_record_is_kept_either_way(self):
        self.visit()
        self.assertTrue(LinkView.objects.filter(counted=True).exists())

    def test_the_owner_earns_nothing_from_their_own_link(self):
        self.client.force_login(self.owner)
        self.visit()

        self.assertFalse(AdEarning.objects.exists())
        self.assertEqual(LinkView.objects.get().reject_reason, 'self_view')

    def test_a_bot_earns_nothing(self):
        self.visit(HTTP_USER_AGENT='python-requests/2.31')

        self.assertFalse(AdEarning.objects.exists())
        self.assertEqual(LinkView.objects.get().reject_reason, 'bot_agent')

    def test_the_same_visitor_earns_only_once(self):
        self.visit()
        self.visit()

        self.assertEqual(AdEarning.objects.count(), 1)
        self.assertEqual(
            LinkView.objects.filter(reject_reason='duplicate').count(), 1)

    def test_the_same_visitor_hopping_links_is_held_by_the_cooldown(self):
        """The cooldown is per visitor, not global.

        It stops someone working through link after link to earn, not a
        genuine second visitor.
        """
        self.visit()

        second_link = ShortLink.create_for(
            'https://example.com/y', owner=self.owner,
            mode=ShortLink.Mode.INTERSTITIAL,
        )
        self.client.get(second_link.get_absolute_url(),
                        HTTP_USER_AGENT=REAL_BROWSER)

        self.assertEqual(AdEarning.objects.count(), 1)
        self.assertTrue(LinkView.objects.filter(reject_reason='cooldown').exists())

    def test_a_genuinely_different_visitor_does_earn(self):
        """The cooldown must not reject a real second visitor."""
        AdSetting.objects.update(visitor_cooldown_seconds=0)
        self.visit()

        other = self.client_class()
        other.get(self.link.get_absolute_url(), HTTP_USER_AGENT=REAL_BROWSER,
                  REMOTE_ADDR='203.0.113.9')

        self.assertEqual(AdEarning.objects.count(), 2)

    def test_a_direct_link_earns_nothing(self):
        """Only the interstitial mode has a page, so only it earns."""
        direct = ShortLink.create_for('https://example.com/d', owner=self.owner,
                                      mode=ShortLink.Mode.DIRECT)
        self.client.get(direct.get_absolute_url(), HTTP_USER_AGENT=REAL_BROWSER)

        self.assertFalse(AdEarning.objects.exists())

    def test_a_link_without_an_owner_earns_nothing(self):
        orphan = ShortLink.create_for('https://example.com/o', owner=None,
                                      mode=ShortLink.Mode.INTERSTITIAL)
        self.client.get(orphan.get_absolute_url(), HTTP_USER_AGENT=REAL_BROWSER)

        self.assertFalse(AdEarning.objects.exists())

    def test_zero_cpm_earns_nothing(self):
        AdSetting.objects.update(cpm_cents=0)
        self.visit()

        self.assertFalse(AdEarning.objects.exists())

    def test_the_daily_cap_per_link_holds(self):
        AdSetting.objects.update(max_earnings_per_file_per_day=1,
                                 visitor_cooldown_seconds=0, dedup_hours=0)
        self.visit()

        other = self.client_class()
        other.get(self.link.get_absolute_url(), HTTP_USER_AGENT=REAL_BROWSER,
                  REMOTE_ADDR='203.0.113.20')

        self.assertEqual(AdEarning.objects.count(), 1)

    def test_earnings_survive_the_source_constraint(self):
        """An AdEarning must carry exactly one source: a file or a link."""
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            AdEarning.objects.create(
                user=self.owner, micro_cents=100,
                available_at=timezone.now() + timedelta(days=1),
            )

    def test_the_page_still_renders_when_earning_is_rejected(self):
        """Refusing the revenue must not break the page."""
        response = self.visit(HTTP_USER_AGENT='curl/8.0')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'example.com/x')

    def test_confirming_moves_it_to_the_wallet(self):
        from ads_module import services

        self.visit()
        AdEarning.objects.update(available_at=timezone.now() - timedelta(days=1))

        count, cents = services.confirm_user_earnings(self.owner)

        self.assertEqual(count, 1)
        self.owner.refresh_from_db()
        # 600 micro-cents is under a cent, so it goes to the carry, not the wallet.
        self.assertEqual(cents, 0)
