from django.urls import path

from config.views import coming_soon

from . import views
from .permissions import Permission, permission_required

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.password_change_view, name="password_change"),
    # Forgot password (self-service, by email)
    path("password/forgot/", views.ForgotPasswordView.as_view(), name="password_forgot"),
    path("password/forgot/sent/", views.ForgotPasswordDoneView.as_view(), name="password_forgot_done"),
    path("password/reset/<uidb64>/<token>/", views.ResetPasswordConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/done/", views.ResetPasswordCompleteView.as_view(), name="password_reset_complete"),
    # Admin Console (requires can_manage_users); the page comes in a later frontend phase
    path("console/", permission_required(Permission.MANAGE_USERS)(coming_soon), {"feature": "Admin Console"}, name="console"),
    path("api/roles/", views.roles_api, name="api_roles"),
    path("api/members/", views.members_api, name="api_members"),
    path("api/members/<int:member_id>/role/", views.member_role_api, name="api_member_role"),
    path("api/members/<int:member_id>/deactivate/", views.member_deactivate_api, name="api_member_deactivate"),
    path("api/members/<int:member_id>/reactivate/", views.member_reactivate_api, name="api_member_reactivate"),
    path("api/members/<int:member_id>/reset-password/", views.member_reset_password_api, name="api_member_reset_password"),
]
