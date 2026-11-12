"""Certificates (mounted at /certificates/). The startup portal's "My certificates" is in startups/urls.py."""

from django.urls import path

from . import page_views, views

app_name = "certificates"

urlpatterns = [
    # Pages (phase F4)
    path("", page_views.certificate_list, name="list"),
    path("<int:certificate_id>/preview/dialog/", page_views.preview_dialog, name="preview_dialog"),
    path("<int:certificate_id>/evidence/", page_views.evidence, name="evidence"),
    path("<int:certificate_id>/evidence/upload/", page_views.evidence_upload, name="evidence_upload"),
    # Staff: reading and files
    path("api/", views.list_api, name="api_list"),
    path("api/<int:certificate_id>/", views.detail_api, name="api_detail"),
    path("<int:certificate_id>/download/", views.download_certificate, name="download"),
    path("<int:certificate_id>/preview/", views.preview_certificate, name="preview"),
    # Staff: actions (approve: can_approve_certificates; the others: can_create_activity). No generate: the System
    # creates certificates when the activity is Conducted (ASSUMPTIONS 362).
    path("api/<str:action>/", views.action_api, name="api_action"),  # approve, regenerate, hand-over, resend
    # Evidence documents (DOCUMENT.related_certificate)
    path("api/<int:certificate_id>/evidence/", views.evidence_api, name="api_evidence"),
    path("api/evidence/<int:document_id>/<str:action>/", views.evidence_action_api, name="api_evidence_action"),
    path("evidence/<int:document_id>/download/", views.evidence_download, name="evidence_download"),
    # Admin Console (can_manage_users)
    path("api/admin/templates/", views.templates_api, name="api_templates"),
    path("api/admin/templates/<int:template_id>/<str:action>/", views.template_action_api, name="api_template_action"),
    path("api/admin/signatories/", views.signatories_api, name="api_signatories"),
    path("api/admin/signatories/<int:signatory_id>/deactivate/", views.signatory_deactivate_api, name="api_signatory_deactivate"),
    path("admin-files/templates/<int:template_id>/", views.template_image, name="template_image"),
    path("admin-files/signatories/<int:signatory_id>/", views.signature_image, name="signature_image"),
]
