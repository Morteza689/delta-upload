"""The user-facing setup page.

There is no webhook route. Each user's bot token is theirs, so updates are
fetched with it on demand rather than pushed to a URL of ours, which removes a
publicly reachable endpoint and a shared secret from the feature.

The staff page is registered in staff_module.urls instead, beside the rest of
the admin pages: the /staff/ prefix is matched by that include first, so a
route added here under it would never be reached.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('user/telegram/', views.ConnectView.as_view(),
         name='telegram_connect_page'),
]
