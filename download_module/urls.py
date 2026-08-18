from django.urls import path

from . import views

urlpatterns = [
    path('s/<uuid:token>/', views.DwonloadPage2View.as_view(), name='download_page_step2'),
    path('buy/<slug:slug>/', views.PurchaseView.as_view(), name='purchase_page'),
    # The catch-all slug pattern stays last so it does not swallow the routes
    # above it.
    path('<slug:slug>', views.DwonloadPageView.as_view(), name='download_page'),
]
