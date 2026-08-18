"""Account URLs.

The misspelled forgot-password path is kept as a permanent redirect so links
saved in bookmarks or old emails still work.

/account/ redirects to the dashboard: its old view showed only a name and an
email, which the dashboard covers in full.
"""
from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='user_dashbord', permanent=True),
         name='account_page'),

    path('active-account/<active_code>', views.ActiveAccountView.as_view(),
         name='activate_account_page'),

    path('forgot-password/', views.ForgatPasswordView.as_view(),
         name='forgot_password_page'),
    path('forgat-password/',
         RedirectView.as_view(pattern_name='forgot_password_page', permanent=True)),

    path('reset-password/<active_code>', views.RestPasswordView.as_view(),
         name='reset_password_page'),
]
