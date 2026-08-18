from django.urls import path

from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home_page'),
    path('about/', views.AboutView.as_view(), name='about_page'),
    path('terms/', views.TermsView.as_view(), name='terms_page'),
    path('privacy/', views.PrivacyView.as_view(), name='privacy_page'),
    path('faq/', views.FaqView.as_view(), name='faq_page'),
]
