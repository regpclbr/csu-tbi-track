"""Root URL configuration for CSU-TBI Track."""

from django.contrib import admin
from django.urls import include, path

from accounts.views import admin_login_redirect
from registrations import checkin_views, public_views
from registrations import views as registration_views
from reports.views import dashboard_page
from startups import page_views as startup_page_views
from startups import views as startup_views

from . import search, views

urlpatterns = [
    path("", dashboard_page, name="home"),  # the staff dashboard
    path("search/", search.search_page, name="search"),  # the top bar's search (activities, documents, startups)
    path("healthz/", views.healthz, name="healthz"),  # Docker's health check (no login)
    path("robots.txt", views.robots_txt, name="robots_txt"),  # no crawler visits any page
    path("privacy/", public_views.privacy_notice, name="privacy_notice"),  # the privacy notice (public pages link here)
    # Must come before admin.site.urls: the admin uses the staff login (with lockout).
    path("admin/login/", admin_login_redirect),
    path("admin/", admin.site.urls),
    # Public pages (no login, phase F3). Emails link to these page URLs; the JSON endpoints are under /api/.
    path("register/", public_views.open_list, name="register_list"),
    path("register/<int:activity_id>/", public_views.register_page, name="register"),
    path("register/<int:activity_id>/registered/", public_views.register_done, name="register_done"),
    path("register/<int:activity_id>/calendar.ics", public_views.activity_calendar, name="register_calendar"),
    path("evaluate/<str:token>/", public_views.evaluate_page, name="evaluate"),
    path("evaluate/<str:token>/thanks/", public_views.evaluate_done, name="evaluate_done"),
    # The general evaluation link of an activity (one per activity, shared as a QR code): emails the personal link.
    path("evaluation/<int:activity_id>/", public_views.evaluation_request_page, name="evaluation_request"),
    # QR attendance check-in and walk-ins (ASSUMPTIONS 434); the confirmation email's "I can no longer attend" link
    path("checkin/<int:activity_id>/", checkin_views.checkin_page, name="checkin"),
    path("checkin/<int:activity_id>/done/", checkin_views.checkin_done, name="checkin_done"),
    path("registration/cancel/<str:token>/", checkin_views.cancel_page, name="cancel_registration"),
    path("evaluation/<int:activity_id>/sent/", public_views.evaluation_request_sent, name="evaluation_request_sent"),
    # "done" first: otherwise it would be read as a token.
    path("startups/set-password/done/", startup_page_views.set_password_done, name="startup_set_password_done"),
    path("startups/set-password/<str:token>/", startup_page_views.set_password_page, name="startup_set_password"),
    # Public JSON endpoints (no login): registration, personal evaluation links, set-password links.
    path("api/activities/open/", registration_views.public_open_activities, name="api_open_activities"),
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
    path("approvals/", include("approvals.urls")),  # ASSUMPTIONS 437
]

# Errors for /api/ URLs are JSON (403 with the reason, 404, 500); pages get templates/errors/*.html.
# (404 and 500 handlers are used only when DEBUG is off; with DEBUG on Django shows its debug pages.)
handler403 = "config.views.permission_denied"
handler404 = "config.views.page_not_found"
handler500 = "config.views.server_error"

# Uploaded files are deliberately NOT served from /media/ (not even in DEBUG).
# Documents are downloaded only through /documents/<id>/download/, which requires a staff login.
