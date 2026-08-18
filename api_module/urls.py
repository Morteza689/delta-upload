from django.urls import path

from . import views

# Registered outside any language prefix: the API address is a contract other
# sites hard-code and must not shift with the browser language.
urlpatterns = [
    path('v1/files/', views.FileCollectionView.as_view(), name='api_files'),
    path('v1/files/<slug:slug>/', views.FileDetailView.as_view(),
         name='api_file_detail'),
    path('v1/me/', views.AccountView.as_view(), name='api_me'),
]
