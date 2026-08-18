"""Sitemap.

URLs carry no language prefix, so each page appears once rather than twice.
"""
from django.contrib.sitemaps import Sitemap
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from upload_module.models import UploadFile


class StaticSitemap(Sitemap):
    """Pages that are always there."""

    protocol = 'https'
    changefreq = 'weekly'

    #: Login and registration are left out: nothing there to index.
    PAGES = {
        'home_page': 1.0,
        'shorten_page': 0.8,
        'guest_upload_page': 0.8,
        'faq_page': 0.6,
        'about_page': 0.5,
        'terms_page': 0.3,
        'privacy_page': 0.3,
    }

    def items(self):
        return list(self.PAGES)

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return self.PAGES[item]


class PublicFileSitemap(Sitemap):
    """Download pages for public files.

    The conditions deliberately mirror UploadFile.is_public_now: a page a
    crawler indexes but a visitor meets with a 404 is worse than no entry at
    all.

    Guest files are left out. They live at most five days, which is usually
    less than the time it takes to be crawled.
    """

    protocol = 'https'
    changefreq = 'daily'
    priority = 0.7
    limit = 5000

    def items(self):
        now = timezone.now()
        return (
            UploadFile.objects
            .filter(
                is_active=True,
                visibility=UploadFile.Visibility.PUBLIC,
                auto_delete_at__isnull=True,
            )
            .exclude(slug__isnull=True)
            .exclude(slug='')
            .filter(Q(public_until__isnull=True) | Q(public_until__gte=now))
            .order_by('-created_deta')
        )

    def lastmod(self, item):
        return item.created_deta


SITEMAPS = {
    'static': StaticSitemap,
    'files': PublicFileSitemap,
}
