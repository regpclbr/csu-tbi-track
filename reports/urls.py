"""Dashboard and reports (mounted at /reports/)."""

from django.urls import path

from config.views import coming_soon

from . import views

app_name = "reports"

urlpatterns = [
    path("", coming_soon, {"feature": "Reports"}, name="list"),  # page of a later frontend phase (config.views.COMING_SOON_PAGES)
    path("api/dashboard/", views.dashboard_api, name="api_dashboard"),
    path("api/options/", views.options_api, name="api_options"),
    path("api/activities/<int:activity_id>/evaluation/", views.activity_evaluation_api, name="api_activity_evaluation"),
    path("api/", views.reports_api, name="api_reports"),  # GET list, POST request a report
    path("api/<int:report_id>/", views.report_detail_api, name="api_detail"),
    path("<int:report_id>/download/", views.download_report, name="download"),
]
