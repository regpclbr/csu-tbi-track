"""Root URL configuration for CSU-TBI Track."""

from django.contrib import admin
from django.urls import include, path

from accounts.views import admin_login_redirect, home_view
from registrations import views as registration_views
from startups import views as startup_views

from . import views

urlpatterns = [
    path("", home_view, name="home"),
    # Must come before admin.site.urls: the admin uses the staff login (with lockout).
    path("admin/login/", admin_login_redirect),
    path("admin/", admin.site.urls),
    # Public pages (no login). Emails link to these URLs; they are reserved for the HTML pages of
    # the frontend phase and show a placeholder until then. The JSON endpoints are under /api/.
    path("register/<int:activity_id>/", views.page_placeholder, name="register"),
    path("evaluate/<str:token>/", views.page_placeholder, name="evaluate"),
    path("startups/set-password/<str:token>/", views.page_placeholder, name="startup_set_password"),
    # Public JSON endpoints (no login): registration, personal evaluation links, set-password links.
    path("api/register/<int:activity_id>/", registration_views.public_register, name="api_register"),
    path("api/evaluate/<str:token>/", registration_views.public_evaluate, name="api_evaluate"),
    path("api/startups/set-password/<str:token>/", startup_views.set_password, name="api_startup_set_password"),
    # Staff: id and name of the lookup rows (filters and form choices).
    path("api/lookups/", views.lookups_api, name="api_lookups"),
    path("accounts/", include("accounts.urls")),
    path("activities/", include("activities.urls")),
    path("registrations/", include("registrations.urls")),
    path("documents/", include("documents.urls")),
    path("certificates/", include("certificates.urls")),
    path("reports/", include("reports.urls")),
    path("startups/", include("startups.urls")),
    path("assistant/", include("assistant.urls")),
    path("notifications/", include("notifications.urls")),
]

# A clear 403 message (JSON for /api/ URLs) when a member lacks a permission.
handler403 = "config.views.permission_denied"

# Uploaded files are deliberately NOT served from /media/ (not even in DEBUG).
# Documents are downloaded only through /documents/<id>/download/, which requires a staff login.
