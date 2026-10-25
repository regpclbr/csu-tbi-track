from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("dropdown/", views.dropdown, name="dropdown"),  # HTML for the top bar's bell (HTMX)
    path("dropdown/read-all/", views.dropdown_read_all, name="dropdown_read_all"),
    path("api/", views.list_api, name="api_list"),
    path("api/unread-count/", views.unread_count_api, name="api_unread_count"),
    path("api/read-all/", views.mark_all_read_api, name="api_mark_all_read"),
    path("api/<int:notification_id>/read/", views.mark_read_api, name="api_mark_read"),
]
