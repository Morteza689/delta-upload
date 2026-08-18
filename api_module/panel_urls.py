from django.urls import path

from . import panel_views

urlpatterns = [
    path('keys/', panel_views.ApiKeyListView.as_view(), name='api_keys_page'),
    path('docs/', panel_views.ApiDocsView.as_view(), name='api_docs_page'),
]
