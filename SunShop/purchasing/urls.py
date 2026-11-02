from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("requests/", views.all_requests, name="all_requests"),
    path("requests/new/", views.request_new, name="request_new"),
    path("requests/<int:pk>/", views.request_detail, name="request_detail"),
    path("requests/<int:pk>/edit/", views.request_edit, name="request_edit"),
    path("requests/<int:pk>/<str:action>/", views.request_action, name="request_action"),
    path("approvals/", views.approvals, name="approvals"),
    path("purchasing/", views.purchasing_queue, name="purchasing_queue"),
    path("spending/", views.spending, name="spending"),
    path("attachments/<int:pk>/", views.attachment, name="attachment"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("register/", views.register_user, name="register"),
]
