"""Root URL configuration for CSU-TBI Track."""

from django.contrib import admin
from django.urls import include, path

from accounts.views import admin_login_redirect, home_view
from registrations import views as registration_views

urlpatterns = [
    path("", home_view, name="home"),
    # Must come before admin.site.urls: the admin uses the staff login (with lockout).
    path("admin/login/", admin_login_redirect),
    path("admin/", admin.site.urls),
    # Public (no login): registration and personal evaluation links.
    path("register/<int:activity_id>/", registration_views.public_register, name="register"),
    path("evaluate/<str:token>/", registration_views.public_evaluate, name="evaluate"),
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
