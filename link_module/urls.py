from django.urls import path

from . import views

# The shortener form.
urlpatterns = [
    path('', views.ShortenView.as_view(), name='shorten_page'),
]
