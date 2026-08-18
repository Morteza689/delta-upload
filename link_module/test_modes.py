"""Short link modes: direct or interstitial, expiry, usage limit."""
from datetime import timedelta

from captcha.models import CaptchaStore
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ads_module.models import AdSlot

from .models import ShortLink

User = get_user_model()


def make_user(name='linker'):
    return User.objects.create_user(
        username=name, email=f'{name}@example.com', password='pw',
        email_active_code=name * 3, is_active=True,
    )


class ModeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()

    def test_a_direct_link_redirects_immediately(self):
        link = ShortLink.create_for('https://example.com/x',
                                    mode=ShortLink.Mode.DIRECT)
        response = self.client.get(link.get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], 'https://example.com/x')

    def test_an_interstitial_link_shows_a_page_first(self):
        link = ShortLink.create_for('https://example.com/x', owner=self.user,
                                    mode=ShortLink.Mode.INTERSTITIAL)
        response = self.client.get(link.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'example.com/x')
        # Asserted on the slot itself rather than the class name: the class name
        # deliberately says nothing about ads (utils/test_adblock.py), and tying the
        # test to it would make following that rule turn this red.
        self.assertContains(response, 'data-embed-slot="link_interstitial"')

    def test_the_interstitial_shows_the_ad_script(self):
        AdSlot.objects.create(position=AdSlot.Position.STEP1, network='yektanet',
                              script='<span id="LINK-AD"></span>')
        link = ShortLink.create_for('https://example.com/x', owner=self.user,
                                    mode=ShortLink.Mode.INTERSTITIAL)
        response = self.client.get(link.get_absolute_url())
        self.assertContains(response, 'LINK-AD')

    def test_opening_the_interstitial_does_not_use_a_click(self):
        """Otherwise every page view would spend a use."""
        link = ShortLink.create_for('https://example.com/x', owner=self.user,
                                    mode=ShortLink.Mode.INTERSTITIAL, max_clicks=1)
        self.client.get(link.get_absolute_url())

        link.refresh_from_db()
        self.assertEqual(link.clicks, 0)

    def test_continuing_from_the_interstitial_redirects_and_counts(self):
        link = ShortLink.create_for('https://example.com/x', owner=self.user,
                                    mode=ShortLink.Mode.INTERSTITIAL)
        response = self.client.get(
            reverse('short_link_continue', args=[link.code]))

        self.assertEqual(response.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.clicks, 1)

    def test_a_guest_cannot_choose_the_ad_mode(self):
        """The interstitial earns money and needs a known owner."""
        self.client.get(reverse('shorten_page'))
        store = CaptchaStore.objects.last()

        self.client.post(reverse('shorten_page'), {
            'target': 'https://example.com', 'with_ads': 'on',
            'captcha_0': store.hashkey, 'captcha_1': store.response,
        })

        link = ShortLink.objects.get()
        self.assertEqual(link.mode, ShortLink.Mode.DIRECT)

    def test_a_logged_in_user_can_choose_the_ad_mode(self):
        self.client.force_login(self.user)
        self.client.post(reverse('shorten_page'), {
            'target': 'https://example.com', 'with_ads': 'on',
        })

        link = ShortLink.objects.get()
        self.assertEqual(link.mode, ShortLink.Mode.INTERSTITIAL)

    def test_the_checkbox_is_hidden_from_guests(self):
        response = self.client.get(reverse('shorten_page'))
        self.assertNotContains(response, 'name="with_ads"')

    def test_the_checkbox_is_offered_to_members(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('shorten_page'))
        self.assertContains(response, 'name="with_ads"')


class ExpiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()
        self.client.force_login(self.user)

    def test_expiry_is_set_from_the_chosen_window(self):
        self.client.post(reverse('shorten_page'), {
            'target': 'https://example.com', 'expires_in': '24',
        })
        link = ShortLink.objects.get()

        self.assertIsNotNone(link.expires_at)
        self.assertLess(abs((link.expires_at - timezone.now()).total_seconds() - 86400), 60)

    def test_no_expiry_by_default(self):
        self.client.post(reverse('shorten_page'), {'target': 'https://example.com'})
        self.assertIsNone(ShortLink.objects.get().expires_at)

    def test_an_expired_link_is_gone(self):
        link = ShortLink.create_for('https://example.com', owner=self.user)
        ShortLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1))

        response = self.client.get(link.get_absolute_url())
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'مهلت این لینک تمام شده', status_code=410)


class ClickLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user()

    def test_the_limit_is_saved_from_the_form(self):
        self.client.force_login(self.user)
        self.client.post(reverse('shorten_page'), {
            'target': 'https://example.com', 'max_clicks': '10',
        })
        self.assertEqual(ShortLink.objects.get().max_clicks, 10)

    def test_a_link_stops_working_after_the_limit(self):
        link = ShortLink.create_for('https://example.com', max_clicks=3)

        for _ in range(3):
            self.assertEqual(self.client.get(link.get_absolute_url()).status_code, 302)

        response = self.client.get(link.get_absolute_url())
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, 'سقف تعداد استفاده', status_code=410)

    def test_remaining_clicks_are_reported(self):
        link = ShortLink.create_for('https://example.com', max_clicks=5)
        self.client.get(link.get_absolute_url())

        link.refresh_from_db()
        self.assertEqual(link.remaining_clicks, 4)

    def test_no_limit_means_unlimited(self):
        link = ShortLink.create_for('https://example.com')
        for _ in range(15):
            self.assertEqual(self.client.get(link.get_absolute_url()).status_code, 302)
        self.assertIsNone(link.remaining_clicks)

    def test_the_interstitial_also_respects_the_limit(self):
        link = ShortLink.create_for('https://example.com', owner=self.user,
                                    mode=ShortLink.Mode.INTERSTITIAL, max_clicks=1)
        self.client.get(reverse('short_link_continue', args=[link.code]))

        response = self.client.get(
            reverse('short_link_continue', args=[link.code]))
        self.assertEqual(response.status_code, 410)

    def test_availability_combines_every_rule(self):
        link = ShortLink.create_for('https://example.com', max_clicks=1)
        self.assertTrue(link.is_available())

        ShortLink.objects.filter(pk=link.pk).update(clicks=1)
        link.refresh_from_db()
        self.assertFalse(link.is_available())
        self.assertEqual(link.unavailable_reason(), 'used_up')


class InterstitialSlotTests(TestCase):
    """The interstitial needs its own slot, not the download page's."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user('slotter')
        self.link = ShortLink.create_for('https://example.com/x', owner=self.user,
                                         mode=ShortLink.Mode.INTERSTITIAL)

    def test_its_own_slot_is_used_when_it_exists(self):
        AdSlot.objects.create(position=AdSlot.Position.LINK_INTERSTITIAL,
                              network='yektanet', script='<span>OWN-SLOT</span>')
        AdSlot.objects.create(position=AdSlot.Position.STEP1,
                              network='yektanet', script='<span>DOWNLOAD-SLOT</span>')

        html = self.client.get(self.link.get_absolute_url()).content.decode()
        self.assertIn('OWN-SLOT', html)
        self.assertNotIn('DOWNLOAD-SLOT', html)

    def test_it_falls_back_to_the_download_slot(self):
        """Until its own slot exists, an empty box helps nobody."""
        AdSlot.objects.create(position=AdSlot.Position.STEP1,
                              network='yektanet', script='<span>DOWNLOAD-SLOT</span>')

        html = self.client.get(self.link.get_absolute_url()).content.decode()
        self.assertIn('DOWNLOAD-SLOT', html)

    def test_the_position_is_a_real_choice(self):
        """This position was missing from the model, so no slot could be created."""
        self.assertIn('link_interstitial', AdSlot.Position.values)
