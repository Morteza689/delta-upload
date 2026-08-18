"""How much an impression is worth.

Yektanet pays per click: native 50 to 1000 toman, banner 300 to 3000. We count
impressions, because a click happens on the network's side and is never
reported back to us for a particular view. So the impression rate is derived
from what a click pays and how often one happens.

Getting this wrong costs real money in one direction only. Every impression
accrues a promise to an uploader; if the rate is above what the network
actually pays, the difference comes out of the operator's pocket at withdrawal
time.
"""
from django.test import TestCase

from ads_module.models import MICRO_PER_CENT, AdSetting
from payment_module.models import ExchangeRate


class EffectiveRateTests(TestCase):
    def setUp(self):
        ExchangeRate.objects.all().delete()
        ExchangeRate.set_rate(100_000)

    def setting(self, **fields):
        AdSetting.objects.all().delete()
        return AdSetting.objects.create(
            uploader_share_percent=60, is_active=True, **fields,
        )

    def test_the_rate_comes_from_the_click_price(self):
        setting = self.setting(cpc_toman=400, clicks_per_mille=10)
        # 400 toman a click, ten clicks per thousand views, is 4000 toman per
        # thousand views, which at 100,000 toman a dollar is four cents.
        self.assertAlmostEqual(setting.effective_cpm_cents(), 4.0, places=3)

    def test_an_explicit_impression_rate_wins(self):
        setting = self.setting(cpc_toman=400, clicks_per_mille=10, cpm_cents=25)
        # For a network that really does pay per impression.
        self.assertEqual(setting.effective_cpm_cents(), 25.0)

    def test_no_rate_at_all_earns_nothing(self):
        setting = self.setting(cpc_toman=0, clicks_per_mille=0)
        self.assertEqual(setting.effective_cpm_cents(), 0.0)
        self.assertEqual(setting.micro_per_impression(), 0)

    def test_without_an_exchange_rate_nothing_accrues(self):
        ExchangeRate.objects.all().delete()
        setting = self.setting(cpc_toman=400, clicks_per_mille=10)

        # The network pays toman and balances are in cents. With no rate there
        # is no defensible conversion, and guessing one would promise money in
        # a currency we cannot price.
        self.assertEqual(setting.effective_cpm_cents(), 0.0)

    def test_a_fraction_of_a_cent_is_not_rounded_away(self):
        # 50 toman a click at half a percent is 250 toman per thousand views,
        # a quarter of a cent. An integer rate would make that zero and the
        # whole feature would silently stop earning.
        setting = self.setting(cpc_toman=50, clicks_per_mille=5)

        self.assertGreater(setting.effective_cpm_cents(), 0)
        self.assertLess(setting.effective_cpm_cents(), 1)
        self.assertGreater(setting.micro_per_impression(), 0)

    def test_the_share_is_applied(self):
        full = self.setting(cpc_toman=400, clicks_per_mille=10)
        full_micro = full.micro_per_impression()

        AdSetting.objects.all().delete()
        half = AdSetting.objects.create(
            cpc_toman=400, clicks_per_mille=10, uploader_share_percent=30,
            is_active=True,
        )
        self.assertAlmostEqual(half.micro_per_impression(), full_micro / 2,
                               delta=1)

    def test_a_realistic_rate_is_nothing_like_a_dollar_per_mille(self):
        """The number this replaced was fifty times too high.

        A hundred cents per thousand impressions promised $120 for a hundred
        thousand downloads. What Yektanet's published rates actually support is
        a couple of dollars.
        """
        setting = self.setting(cpc_toman=400, clicks_per_mille=10)
        per_download = setting.micro_per_impression() * 2 / MICRO_PER_CENT
        for_100k = per_download * 100_000 / 100

        self.assertLess(for_100k, 20)

    def test_the_rate_tracks_the_exchange_rate(self):
        setting = self.setting(cpc_toman=400, clicks_per_mille=10)
        before = setting.effective_cpm_cents()

        # Toman weakens: the same click is worth fewer cents.
        ExchangeRate.set_rate(200_000)
        after = AdSetting.current().effective_cpm_cents()

        self.assertAlmostEqual(after, before / 2, places=3)

    def test_the_published_bands_all_produce_a_workable_rate(self):
        for cpc in (50, 300, 1000, 3000):
            for per_mille in (5, 10, 20):
                setting = self.setting(cpc_toman=cpc,
                                       clicks_per_mille=per_mille)
                # Never zero, never absurd.
                self.assertGreater(setting.micro_per_impression(), 0,
                                   f'{cpc}/{per_mille}')
                self.assertLess(setting.effective_cpm_cents(), 100,
                                f'{cpc}/{per_mille}')
