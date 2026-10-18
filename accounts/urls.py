from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.password_change_view, name="password_change"),
    # Admin Console (requires can_manage_users)
    path("api/roles/", views.roles_api, name="api_roles"),
    path("api/members/", views.members_api, name="api_members"),
    path("api/members/<int:member_id>/role/", views.member_role_api, name="api_member_role"),
    path("api/members/<int:member_id>/deactivate/", views.member_deactivate_api, name="api_member_deactivate"),
    path("api/members/<int:member_id>/reactivate/", views.member_reactivate_api, name="api_member_reactivate"),
]
