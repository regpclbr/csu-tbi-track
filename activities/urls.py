from django.urls import path

from certificates import page_views as certificate_page_views
from documents import page_views as document_page_views

from . import page_views, views

app_name = "activities"

urlpatterns = [
    # Pages (phase F2a)
    path("", page_views.activity_list, name="list"),
    path("new/", page_views.activity_create, name="create"),
    path("cards/<str:preset>/", page_views.card_dialog, name="card_dialog"),  # a list card's dialog (HTMX)
    path("<int:activity_id>/", page_views.activity_detail, name="detail"),
    path("<int:activity_id>/edit/", page_views.activity_edit, name="edit"),
    path("<int:activity_id>/qr/<str:kind>.png", page_views.qr_code, name="qr_code"),  # the Share card's QR codes
    path("<int:activity_id>/status/", page_views.activity_change_status, name="change_status"),
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
    # Certificates are created by the System (ASSUMPTIONS 362): no Generate. Each action redirects to the tab.
    path("<int:activity_id>/certificates/<int:certificate_id>/approve/", certificate_page_views.approve_one,
         name="certificates_approve"),
    path("<int:activity_id>/certificates/approve-ready/", certificate_page_views.approve_ready,
         name="certificates_approve_ready"),
    path("<int:activity_id>/certificates/apply-design/", certificate_page_views.apply_design,
         name="certificates_apply_design"),
    path("<int:activity_id>/certificates/action/", certificate_page_views.row_action, name="certificates_action"),
    path("<int:activity_id>/certificates/signatures/<str:position>/", certificate_page_views.signature_upload,
         name="certificates_signature"),
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
