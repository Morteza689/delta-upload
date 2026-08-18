"""URL map.

Language comes from a cookie, not a URL prefix, so every page has a single
stable address served in either language. Shared links stay short and no part
of the code has to reason about two URL variants.
"""
from pathlib import Path

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from account_module.language import set_language

from home_module.sitemaps import SITEMAPS
from home_module.views import robots_txt
from upload_module.guest_views import GuestUploadView
from utils.currency_views import set_currency
from link_module.views import go, go_continue, link_preview
from payment_module.views import payment_ipn

urlpatterns = [
    path('admin/', admin.site.urls),
    # Sets the language cookie and returns the user where they were.
    # Our own set_language, which is Django's plus a note on the account so
    # email can be written in the language the reader picked.
    path('i18n/setlang/', set_language, name='set_language'),
    path('i18n/', include('django.conf.urls.i18n')),
    path('currency/', set_currency, name='set_currency'),
    # Payment webhook. The address given to the processor must stay fixed.
    path('payment/ipn/', payment_ipn, name='payment_ipn'),
    path('captcha/', include('captcha.urls')),
    # robots.txt must sit exactly at the root.
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': SITEMAPS},
         name='django.contrib.sitemaps.views.sitemap'),
    # Public API: the address other sites hard-code.
    path('api/', include('api_module.urls')),
    path('api/', include('link_module.api_urls')),

    path('', include('home_module.urls')),
    path('account/', include('account_module.urls')),
    path('account/files/', include('upload_module.urls')),
    path('support/', include('contact_module.urls')),
    path('dl/', include('download_module.urls')),
    path('user/', include('user_panel_module.urls')),
    path('payment/', include('payment_module.urls')),
    path('earnings/', include('ads_module.urls')),
    path('api-keys/', include('api_module.panel_urls')),
    path('staff/', include('staff_module.urls')),
    path('', include('telegram_module.urls')),
    path('shorten/', include('link_module.urls')),
    path('quick-upload/', GuestUploadView.as_view(), name='guest_upload_page'),
    # Short link redirect. Registered last so it swallows no real path.
    path('s/<str:code>', go, name='short_link_go'),
    path('s/<str:code>/preview/', link_preview, name='short_link_preview'),
    path('s/<str:code>/go/', go_continue, name='short_link_continue'),
]

# User uploads are deliberately not served from here. The only route to their
# contents is upload_module, which checks ownership or a valid token. Restoring
# public media serving would bypass privacy, purchases, expiry and ad pages.
# Only public site assets are served, and only in development.
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL + 'images/',
        document_root=Path(settings.MEDIA_ROOT) / 'images',
    )

# Custom error pages; otherwise Django shows its default, which matches
# nothing else on the site.
handler404 = 'home_module.views.error_404'
handler500 = 'home_module.views.error_500'
handler403 = 'home_module.views.error_403'
