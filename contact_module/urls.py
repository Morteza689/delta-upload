from django.urls import path

from . import views

urlpatterns = [
    path('contact-us/', views.ContactUsView.as_view(), name='contact_us_page'),
    path('tickets/', views.TicketListView.as_view(), name='ticket_list'),
    path('tickets/new/', views.TicketCreateView.as_view(), name='ticket_new'),
    path('tickets/<int:pk>/', views.TicketDetailView.as_view(), name='ticket_detail'),
    path('tickets/attachment/<int:pk>/', views.ticket_attachment,
         name='ticket_attachment'),
]
