from django.urls import path

from . import views

urlpatterns = [
    path('topup/', views.TopUpView.as_view(), name='topup_page'),
    path('return/<int:pk>/', views.payment_return, name='payment_return'),
]
