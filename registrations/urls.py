"""Staff pages and API for forms, registrations and attendance (mounted at /registrations/).
The public URLs (/api/register/..., /api/evaluate/... and their HTML page URLs) are in config/urls.py."""

from django.urls import path

from . import page_views, views

app_name = "registrations"

urlpatterns = [
    # Pages and actions of the activity page's Forms and Registrations tabs (phase F2b)
    path("activities/<int:activity_id>/forms/<str:form_type>/create/", page_views.form_create, name="form_create"),
    path("activities/<int:activity_id>/forms/registration/ready/", page_views.form_ready, name="form_ready"),  # ASSUMPTIONS 409
    path("forms/<int:template_id>/card/", page_views.form_card, name="form_card"),
    path("forms/<int:template_id>/fields/new/", page_views.field_editor, name="field_new"),
    path("forms/<int:template_id>/fields/<int:field_id>/edit/", page_views.field_editor, name="field_edit"),
    path("forms/<int:template_id>/fields/add/", page_views.field_add, name="field_add"),
    path("fields/<int:field_id>/save/", page_views.field_update, name="field_update"),
    path("fields/<int:field_id>/remove/", page_views.field_remove, name="field_remove"),
    path("fields/<int:field_id>/move/", page_views.field_move, name="field_move"),
    path(
        "activities/<int:activity_id>/registrations/<int:registration_id>/",
        page_views.registration_detail,
        name="registration_detail",
    ),
    path(
        "activities/<int:activity_id>/registrations/<int:registration_id>/evaluation.pdf",
        page_views.evaluation_pdf,
        name="evaluation_pdf",
    ),
    path("activities/<int:activity_id>/attendance/", page_views.attendance, name="attendance"),
    path("activities/<int:activity_id>/attendance/<int:registration_id>/sign/", page_views.attendance_signature,
         name="attendance_signature"),
    path("activities/<int:activity_id>/registrations/<int:registration_id>/signature.png", page_views.signature_image,
         name="signature_image"),
    path("activities/<int:activity_id>/attendance-forms/<str:kind>/", page_views.generate_attendance_form,
         name="generate_attendance_form"),
    path("activities/<int:activity_id>/checkin-poster/", page_views.checkin_poster, name="checkin_poster"),
    path("activities/<int:activity_id>/evaluation-links/", page_views.send_links, name="send_links"),
    path("activities/<int:activity_id>/attendance-sheet/", page_views.attendance_sheet, name="attendance_sheet"),
    # JSON API
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
