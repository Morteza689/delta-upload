from django.urls import path

from . import api_views

urlpatterns = [
    path('v1/links/', api_views.LinkCollectionView.as_view(), name='api_links'),
    path('v1/links/<str:code>/', api_views.LinkDetailView.as_view(),
         name='api_link_detail'),
]
