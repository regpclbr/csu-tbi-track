from django.urls import path

from . import views

app_name = "startups"

urlpatterns = [
    # Staff (TBI members)
    path("api/", views.startups_api, name="api_list"),
    path("api/sync/", views.sync_api, name="api_sync"),
    path("api/<int:startup_id>/", views.startup_detail_api, name="api_detail"),
    path("api/<int:startup_id>/send-password-link/", views.send_password_link_api, name="api_send_password_link"),
    # The public set-password page (/startups/set-password/<token>/) and its JSON endpoint
    # (/api/startups/set-password/<token>/) are in config/urls.py.
    # Startup portal (startup login)
    path("portal/api/login/", views.portal_login, name="portal_login"),
    path("portal/api/logout/", views.portal_logout, name="portal_logout"),
    path("portal/api/me/", views.portal_me, name="portal_me"),
    path("portal/api/registrations/", views.portal_registrations, name="portal_registrations"),
    path("portal/api/activities/<int:activity_id>/register/", views.portal_register, name="portal_register"),
    path("portal/api/certificates/", views.portal_certificates, name="portal_certificates"),
    path("portal/certificates/<int:certificate_id>/download/", views.portal_certificate_download, name="portal_certificate_download"),
    path("portal/api/notifications/", views.portal_notifications, name="portal_notifications"),
    path("portal/api/notifications/unread-count/", views.portal_unread_count, name="portal_unread_count"),
    path("portal/api/notifications/read-all/", views.portal_mark_all_read, name="portal_mark_all_read"),
    path("portal/api/notifications/<int:notification_id>/read/", views.portal_mark_read, name="portal_mark_read"),
]
