from django.urls import path
from .views import (
    LandingView, SignupView, LoginView, LogoutView, 
    DashboardView, ProfileView, ReprocessView, DeleteDocumentView, UploadView, ChatView,
    GlobalResourcesView, AcquireDocumentView
)

urlpatterns = [
    path('', LandingView.as_view(), name='landing'),
    path('signup/', SignupView.as_view(), name='signup'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('profile/', ProfileView.as_view(), name='user_profile'),
    path('reprocess/<int:doc_id>/', ReprocessView.as_view(), name='reprocess_document'),
    path('delete/<int:doc_id>/', DeleteDocumentView.as_view(), name='delete_document'),
    path('upload/', UploadView.as_view(), name='upload'),
    path('chat/', ChatView.as_view(), name='chat'),
    path('resources/', GlobalResourcesView.as_view(), name='global_resources'),
    path('acquire/<int:doc_id>/', AcquireDocumentView.as_view(), name='acquire_document'),
]
