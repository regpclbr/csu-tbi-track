"""Staff API for forms, registrations and attendance (mounted at /registrations/).
The public URLs (/register/... and /evaluate/...) are in config/urls.py."""

from django.urls import path

from . import views

app_name = "registrations"

urlpatterns = [
    path("api/activities/<int:activity_id>/forms/", views.create_form_api, name="api_create_form"),
    path("api/forms/<int:template_id>/", views.form_api, name="api_form"),
    path("api/forms/<int:template_id>/fields/", views.add_field_api, name="api_add_field"),
    path("api/forms/<int:template_id>/reorder/", views.reorder_fields_api, name="api_reorder_fields"),
    path("api/fields/<int:field_id>/", views.update_field_api, name="api_update_field"),
    path("api/fields/<int:field_id>/remove/", views.remove_field_api, name="api_remove_field"),
    path("api/activities/<int:activity_id>/registrations/", views.registrations_api, name="api_registrations"),
    path("api/activities/<int:activity_id>/attendance/", views.attendance_api, name="api_attendance"),
    path(
        "api/activities/<int:activity_id>/send-evaluation-links/",
        views.send_evaluation_links_api,
        name="api_send_evaluation_links",
    ),
]
