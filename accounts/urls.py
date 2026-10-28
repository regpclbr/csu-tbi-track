from django.urls import path

from . import console_views, views

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
    # Admin Console pages (phase F6, can_manage_users). Templates and signatories have no delete URL (never deleted).
    path("console/", console_views.members, name="console"),
    path("console/members/invite/", console_views.invite, name="console_invite"),
    path("console/members/<int:member_id>/role/", console_views.change_role, name="console_member_role"),
    path("console/members/<int:member_id>/deactivate/", console_views.deactivate, name="console_member_deactivate"),
    path("console/members/<int:member_id>/reactivate/", console_views.reactivate, name="console_member_reactivate"),
    path("console/members/<int:member_id>/reset-password/", console_views.reset_password, name="console_member_reset"),
    path("console/roles/", console_views.roles, name="console_roles"),
    path("console/certificates/", console_views.certificate_design, name="console_certificates"),
    path("console/certificates/templates/", console_views.upload_template, name="console_template_upload"),
    path("console/certificates/templates/<int:template_id>/<str:action>/", console_views.template_action, name="console_template_action"),
    path("console/certificates/signatories/", console_views.add_signatory, name="console_signatory_add"),
    path("console/certificates/signatories/<int:signatory_id>/deactivate/", console_views.deactivate_signatory, name="console_signatory_deactivate"),
    path("console/system/", console_views.system, name="console_system"),
    path("console/system/pdf/", console_views.system_pdf, name="console_system_pdf"),
    # Admin Console JSON API
    path("api/roles/", views.roles_api, name="api_roles"),
    path("api/members/", views.members_api, name="api_members"),
    path("api/members/<int:member_id>/role/", views.member_role_api, name="api_member_role"),
    path("api/members/<int:member_id>/deactivate/", views.member_deactivate_api, name="api_member_deactivate"),
    path("api/members/<int:member_id>/reactivate/", views.member_reactivate_api, name="api_member_reactivate"),
    path("api/members/<int:member_id>/reset-password/", views.member_reset_password_api, name="api_member_reset_password"),
]
