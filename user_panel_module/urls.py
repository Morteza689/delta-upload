from django.urls import path
from django.views.generic import RedirectView
from .views import *

urlpatterns = [
    path('', DashboardView.as_view(), name='user_dashbord'),
    path('withdrawal-list/', WithdrawalListView.as_view(), name='withdrawal_list_page'),
    path('withdrawal/', WithdrawalView.as_view(), name='withdrawal_page'),
    path('payout-accounts/', PayoutAccountView.as_view(), name='payout_accounts_page'),
    path('upload/', UploadFileView.as_view(), name='upload_page'),
    path('storage/', StoragePlanView.as_view(), name='storage_plans_page'),
    path('my-files/', MyFileVeiw.as_view(), name='my_files_page'),
    # The old path was singular; 301 so bookmarks keep working.
    path('my-file/', RedirectView.as_view(
        pattern_name='my_files_page', permanent=True)),
    path('my-files/<int:pk>/report/', FileReportView.as_view(),
         name='file_report'),
    path('my-files/<int:pk>/edit/', FileEditView.as_view(), name='file_edit'),
    path('my-files/<int:pk>/delete/', FileDeleteView.as_view(),
         name='file_delete'),
    path('my-file/<int:pk>/report/', RedirectView.as_view(
        pattern_name='file_report', permanent=True)),
    path('register/', RegisterView.as_view(), name='register_page'),
    path('login/', loginView.as_view(), name='login_page'),
    path('logout/', LogoutView.as_view(), name='logout_page'),
]
