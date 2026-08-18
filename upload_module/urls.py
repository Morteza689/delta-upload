from django.urls import path

from . import views

urlpatterns = [
    path('myfile', views.MyFileVeiw.as_view(), name='myfile_page'),
    path('own/<int:pk>/', views.owner_download, name='owner_download'),
    path('download/<uuid:token>/', views.download_file, name='download_file'),
]
