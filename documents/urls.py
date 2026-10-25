from django.urls import path

from config.views import coming_soon

from . import views

app_name = "documents"

urlpatterns = [
    path("", coming_soon, {"feature": "Documents"}, name="list"),  # page of a later frontend phase (config.views.COMING_SOON_PAGES)
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
]
