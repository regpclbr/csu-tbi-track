from django.urls import path

from . import page_views, views

app_name = "activities"

urlpatterns = [
    # Pages (phase F2a)
    path("", page_views.activity_list, name="list"),
    path("new/", page_views.activity_create, name="create"),
    path("<int:activity_id>/", page_views.activity_detail, name="detail"),
    path("<int:activity_id>/edit/", page_views.activity_edit, name="edit"),
    path("<int:activity_id>/status/", page_views.activity_change_status, name="change_status"),
    path("<int:activity_id>/resource-persons/add/", page_views.resource_person_add, name="add_resource_person"),
    path(
        "<int:activity_id>/resource-persons/<int:resource_person_id>/remove/",
        page_views.resource_person_remove,
        name="remove_resource_person",
    ),
    path("resource-persons/options/", page_views.resource_person_options, name="resource_person_options"),
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
