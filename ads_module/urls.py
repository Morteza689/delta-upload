"""Earnings report URLs.

The path prefix is deliberately not "ads": Adblock filter lists match /ads/ in
a URL, and this page is the user's own earnings report, not an advert. Blocked,
it would simply come up blank with no explanation.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.EarningListView.as_view(), name='earnings_page'),
]
