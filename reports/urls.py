"""Dashboard and reports (mounted at /reports/)."""

from django.urls import path

from . import generator_views, page_views, views

app_name = "reports"

urlpatterns = [
    path("", page_views.report_list, name="list"),  # the Reports page: form (POST) and history
    path("fill/", page_views.fill_form, name="fill"),
    # The report generator (ASSUMPTIONS 442) and paper sheets (443)
    path("generator/", generator_views.generator_list, name="generator"),
    path("generator/<int:draft_id>/", generator_views.draft_page, name="generator_draft"),
    path("generator/<int:draft_id>/ask/", generator_views.ask, name="generator_ask"),
    path("generator/<int:draft_id>/again/", generator_views.try_again, name="generator_again"),
    path("generator/<int:draft_id>/files/", generator_views.add_files, name="generator_add_files"),
    path("generator/files/<int:attachment_id>/", generator_views.download_file, name="generator_file"),
    path("generator/files/<int:attachment_id>/remove/", generator_views.remove_file, name="generator_remove_file"),
    path("generator/files/<int:attachment_id>/read-sheet/", generator_views.read_sheet_from_attachment,
         name="generator_read_sheet"),
    path("generator/versions/<int:version_id>/status/", generator_views.version_status, name="generator_version_status"),
    path("generator/versions/<int:version_id>/section/", generator_views.edit_section, name="generator_edit_section"),
    path("generator/versions/<int:version_id>/export/<str:file_format>/", generator_views.export, name="generator_export"),
    path("generator/versions/<int:version_id>/save/", generator_views.save_to_documents, name="generator_save"),
    path("generator/versions/<int:version_id>/submit/", generator_views.submit_to_director, name="generator_submit"),
    path("paper-sheets/<int:read_id>/", generator_views.paper_sheet, name="paper_sheet"),
    path("paper-sheets/<int:read_id>/status/", generator_views.paper_sheet_status, name="paper_sheet_status"),
    path("paper-sheets/from-document/<int:document_id>/", generator_views.read_sheet_from_document,
         name="paper_sheet_from_document"),  # "Fill in the form" from a typed request (ASSUMPTIONS 384)
    path("<int:report_id>/row/", page_views.report_row, name="row"),  # one history row (HTMX polling)
    # One activity's evaluation summary, made on request (ASSUMPTIONS 401)
    path("activities/<int:activity_id>/evaluation-summary/<str:file_format>/", page_views.evaluation_summary,
         name="evaluation_summary"),
    path("api/dashboard/", views.dashboard_api, name="api_dashboard"),
    path("api/options/", views.options_api, name="api_options"),
    path("api/activities/<int:activity_id>/evaluation/", views.activity_evaluation_api, name="api_activity_evaluation"),
    path("api/", views.reports_api, name="api_reports"),  # GET list, POST request a report
    path("api/<int:report_id>/", views.report_detail_api, name="api_detail"),
    path("<int:report_id>/download/", views.download_report, name="download"),
]
