from django.urls import path

from telegram_module import staff_views as telegram_views

from . import views

urlpatterns = [
    path('', views.OverviewView.as_view(), name='staff_overview'),
    path('users/', views.UserListView.as_view(), name='staff_users'),
    path('files/', views.FileListView.as_view(), name='staff_files'),
    path('withdrawals/', views.WithdrawalQueueView.as_view(), name='staff_withdrawals'),
    path('payments/', views.PaymentListView.as_view(), name='staff_payments'),
    path('tickets/', views.TicketQueueView.as_view(), name='staff_tickets'),
    path('exchange-rate/', views.ExchangeRateView.as_view(), name='staff_exchange_rate'),
    path('security/', views.SecurityView.as_view(), name='staff_security'),
    # The view lives in telegram_module; the route belongs here so the whole
    # admin area sits behind one prefix.
    path('telegram/', telegram_views.TelegramSettingsView.as_view(),
         name='staff_telegram'),
]
