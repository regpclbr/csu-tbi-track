from django.urls import path

from accounts.permissions import Permission, permission_required
from config.views import coming_soon

from . import views

app_name = "activities"

urlpatterns = [
    path("", coming_soon, {"feature": "Activities"}, name="list"),  # page of a later frontend phase (config.views.COMING_SOON_PAGES)
    path("new/", permission_required(Permission.CREATE_ACTIVITY)(coming_soon), {"feature": "New activity"}, name="create"),
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
