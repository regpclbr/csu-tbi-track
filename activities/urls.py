from django.urls import path

from certificates import page_views as certificate_page_views
from certificates import views as certificate_views
from documents import page_views as document_page_views
from documents import views as document_views

from . import page_views, views

app_name = "activities"

urlpatterns = [
    # Pages (phase F2a)
    path("", page_views.activity_list, name="list"),
    path("new/", page_views.activity_create, name="create"),
    path("budget/preview/", page_views.budget_preview, name="budget_preview"),  # HTMX totals (ASSUMPTIONS 433)
    path("cards/<str:preset>/", page_views.card_dialog, name="card_dialog"),  # a list card's dialog (HTMX)
    path("<int:activity_id>/", page_views.activity_detail, name="detail"),
    path("<int:activity_id>/edit/", page_views.activity_edit, name="edit"),
    path("<int:activity_id>/qr/<str:kind>.png", page_views.qr_code, name="qr_code"),  # the Share card's QR codes
    path("<int:activity_id>/status/", page_views.activity_change_status, name="change_status"),
    path("<int:activity_id>/registration/", page_views.registration_switch, name="registration_switch"),  # 427
    path("<int:activity_id>/delete/", page_views.activity_delete, name="delete"),  # ASSUMPTIONS 389
    path("<int:activity_id>/resource-persons/add/", page_views.resource_person_add, name="add_resource_person"),
    path(
        "<int:activity_id>/resource-persons/<int:resource_person_id>/remove/",
        page_views.resource_person_remove,
        name="remove_resource_person",
    ),
    path("resource-persons/options/", page_views.resource_person_options, name="resource_person_options"),
    # Actions of the Documents and Certificates tabs (phase F4). Under /activities/ so the page that comes back
    # (an upload error, a refused bulk action) keeps "Activities" active in the sidebar; the views live in their apps.
    path("<int:activity_id>/documents/upload/", document_page_views.upload, name="document_upload"),
    # "Download payment packet" (ASSUMPTIONS 425): a zip of the Approved payment documents, made on request.
    path("<int:activity_id>/documents/payment-packet/", document_views.download_payment_packet, name="payment_packet"),
    # Certificates are created by the System (ASSUMPTIONS 362): no Generate. Each action redirects to the tab.
    # One approval per activity (ASSUMPTIONS 369); the wording of its certificates (ASSUMPTIONS 370).
    path("<int:activity_id>/certificates/approve/", certificate_page_views.approve_activity,
         name="certificates_approve_activity"),
    path("<int:activity_id>/certificates/unapprove/", certificate_page_views.unapprove_activity,
         name="certificates_unapprove_activity"),  # ASSUMPTIONS 437
    path("<int:activity_id>/certificates/text/", certificate_page_views.text_save, name="certificates_text"),
    # Before Conducted (ASSUMPTIONS 420): "Prepare the certificates now" and the sample preview.
    path("<int:activity_id>/certificates/prepare/", certificate_page_views.prepare,
         name="certificates_prepare"),
    path("<int:activity_id>/certificates/sample-preview/", certificate_views.activity_sample_preview,
         name="certificates_sample_preview"),
    path("<int:activity_id>/certificates/text/preview/", certificate_page_views.text_preview,
         name="certificates_text_preview"),
    path("<int:activity_id>/certificates/apply-design/", certificate_page_views.apply_design,
         name="certificates_apply_design"),
    path("<int:activity_id>/certificates/action/", certificate_page_views.row_action, name="certificates_action"),
    path("<int:activity_id>/certificates/signatures/<str:slot>/", certificate_page_views.signature_upload,
         name="certificates_signature"),
    path("<int:activity_id>/certificates/signatories/<str:slot>/", certificate_page_views.signatory_choose,
         name="certificates_signatory"),
    path("<int:activity_id>/certificates/signatories/<str:slot>/current-version/",
         certificate_page_views.signatory_current_version, name="certificates_signatory_current"),
    path("<int:activity_id>/certificates/<int:certificate_id>/reissue/", certificate_page_views.reissue_one,
         name="certificates_reissue"),
    path("<int:activity_id>/certificates/reissue-outdated/", certificate_page_views.reissue_all,
         name="certificates_reissue_all"),
    # JSON API
    path("api/", views.activities_api, name="api_list"),  # GET list, POST create
    path("api/stats/", views.stats_api, name="api_stats"),
    path("api/calendar/", views.calendar_api, name="api_calendar"),
    path("api/resource-persons/", views.resource_persons_api, name="api_resource_persons"),
    path("api/<int:activity_id>/", views.detail_api, name="api_detail"),
    path("api/<int:activity_id>/update/", views.update_api, name="api_update"),
    path("api/<int:activity_id>/status/", views.status_api, name="api_status"),
    path("api/<int:activity_id>/audit-log/", views.audit_log_api, name="api_audit_log"),
    path(
        "api/<int:activity_id>/resource-persons/",
        views.add_resource_person_api,
        name="api_add_resource_person",
    ),
    path(
        "api/<int:activity_id>/resource-persons/<int:resource_person_id>/remove/",
        views.remove_resource_person_api,
        name="api_remove_resource_person",
    ),
]
