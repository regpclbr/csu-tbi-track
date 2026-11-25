from django.urls import path

from . import page_views, views

app_name = "documents"

urlpatterns = [
    # Pages (phase F4)
    path("", page_views.document_list, name="list"),
    path("review/", page_views.review_list, name="review"),
    path("<int:document_id>/", page_views.document_detail, name="detail"),
    path("<int:document_id>/approve/", page_views.approve, name="approve"),
    path("<int:document_id>/return/", page_views.return_document, name="return"),
    path("<int:document_id>/replace/", page_views.replace, name="replace"),
    path("<int:document_id>/delete/", page_views.delete, name="delete"),
    path("<int:document_id>/unapprove/", page_views.unapprove, name="unapprove"),  # ASSUMPTIONS 437
    path("<int:document_id>/rename/", page_views.rename, name="rename"),  # ASSUMPTIONS 437
    path("<int:document_id>/ai-scan/", page_views.ai_scan, name="ai_scan"),  # ASSUMPTIONS 438
    path("<int:document_id>/<str:action>/form/", page_views.action_form, name="action_form"),
    # JSON API
    path("api/", views.repository_api, name="api_repository"),
    path("api/review-queue/", views.review_queue_api, name="api_review_queue"),
    path("api/activities/<int:activity_id>/upload/", views.upload_api, name="api_upload"),
    path("api/activities/<int:activity_id>/checklist/", views.checklist_api, name="api_checklist"),
    path("api/<int:document_id>/", views.detail_api, name="api_detail"),
    path("api/<int:document_id>/approve/", views.approve_api, name="api_approve"),
    path("api/<int:document_id>/return/", views.return_api, name="api_return"),
    path("api/<int:document_id>/replace/", views.replace_api, name="api_replace"),
    path("api/<int:document_id>/delete/", views.delete_api, name="api_delete"),
    path("<int:document_id>/download/", views.download_document, name="download"),
    path("<int:document_id>/view/", views.view_document, name="view"),
    path("<int:document_id>/preview/", page_views.preview, name="preview"),
]
