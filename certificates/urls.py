"""Certificates (mounted at /certificates/). The startup portal's "My certificates" is in startups/urls.py."""

from django.urls import path

from config.views import coming_soon

from . import views

app_name = "certificates"

urlpatterns = [
    path("", coming_soon, {"feature": "Certificates"}, name="list"),  # page of a later frontend phase (config.views.COMING_SOON_PAGES)
    # Staff: reading and files
    path("api/", views.list_api, name="api_list"),
    path("api/<int:certificate_id>/", views.detail_api, name="api_detail"),
    path("<int:certificate_id>/download/", views.download_certificate, name="download"),
    path("<int:certificate_id>/preview/", views.preview_certificate, name="preview"),
    # Staff: actions (can_create_activity)
    path("api/activities/<int:activity_id>/generate/", views.generate_api, name="api_generate"),
    path("api/<str:action>/", views.action_api, name="api_action"),  # review, sign, approve, reject, hand-over
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
