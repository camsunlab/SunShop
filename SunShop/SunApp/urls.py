from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path("pending/", views.pending, name='pending'),
    path("login/", views.login_user, name='login'),
    path("logout/", views.logout_user, name='logout'),
    path("register/", views.register_user, name='register'),
    path("purchase_request/", views.purchase_request, name='purchase_request'),
    path("request_record/<int:pk>/", views.request_record, name='request_record'),
    path("delete_request/<int:pk>/", views.delete_request, name='delete_request'),
    path("add_request/", views.add_request, name='add_request'),
    path("add_recieved/", views.add_recieved, name='add_recieved'),
    path("update_request/<int:pk>/", views.update_request, name='update_request'),
    path("order_request/<int:pk>/", views.order_request, name='order_request'),
    path("all_purchase/", views.all_purchase_, name='all_purchase'),

]

