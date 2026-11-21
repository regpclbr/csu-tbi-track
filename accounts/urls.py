from django.urls import path

from . import console_views, profile_views, views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.password_change_view, name="password_change"),
    path("profile/", profile_views.profile, name="profile"),  # My profile (ASSUMPTIONS 390)
    # Forgot password (self-service, by email)
    path("password/forgot/", views.ForgotPasswordView.as_view(), name="password_forgot"),
    path("password/forgot/sent/", views.ForgotPasswordDoneView.as_view(), name="password_forgot_done"),
    path("password/reset/<uidb64>/<token>/", views.ResetPasswordConfirmView.as_view(), name="password_reset_confirm"),
    path("password/reset/done/", views.ResetPasswordCompleteView.as_view(), name="password_reset_complete"),
    # Admin Console pages (phase F6, can_manage_users). Templates have no delete URL (never deleted); signatories are managed on /certificates/signatories/.
    path("console/", console_views.members, name="console"),
    path("console/members/invite/", console_views.invite, name="console_invite"),
    path("console/members/<int:member_id>/role/", console_views.change_role, name="console_member_role"),
    path("console/members/<int:member_id>/deactivate/", console_views.deactivate, name="console_member_deactivate"),
    path("console/members/<int:member_id>/reactivate/", console_views.reactivate, name="console_member_reactivate"),
    path("console/members/<int:member_id>/reset-password/", console_views.reset_password, name="console_member_reset"),
    path("console/roles/", console_views.roles, name="console_roles"),
    path("console/roles/<int:role_id>/activity-creation/", console_views.role_activity_creation,
         name="console_role_activity_creation"),
    path("console/roles/<int:role_id>/activity-deletion/", console_views.role_activity_deletion,
         name="console_role_activity_deletion"),
    path("console/roles/<int:role_id>/document-approval/", console_views.role_document_approval, name="console_role_approval"),
    path("console/roles/<int:role_id>/certificate-approval/", console_views.role_certificate_approval,
         name="console_role_certificate_approval"),
    path("console/roles/<int:role_id>/signatory-management/", console_views.role_signatory_management,
         name="console_role_signatory_management"),
    path("console/positions/", console_views.positions, name="console_positions"),  # ASSUMPTIONS 390
    path("console/positions/add/", console_views.position_add, name="console_position_add"),
    path("console/positions/<int:position_id>/rename/", console_views.position_rename, name="console_position_rename"),
    path("console/positions/<int:position_id>/active/", console_views.position_active, name="console_position_active"),
    path("console/deleted-activities/", console_views.deleted_activities, name="console_deleted_activities"),  # 389
    path("console/certificates/", console_views.certificate_design, name="console_certificates"),
    path("console/certificates/templates/", console_views.upload_template, name="console_template_upload"),
    path("console/certificates/templates/<int:template_id>/<str:action>/", console_views.template_action, name="console_template_action"),
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
