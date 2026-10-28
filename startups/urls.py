from django.urls import path

from . import portal_views, staff_views, views

app_name = "startups"

urlpatterns = [
    # Staff pages (phase F5)
    path("", staff_views.startup_list, name="list"),
    path("sync/", staff_views.sync_now, name="sync"),
    path("<int:startup_id>/", staff_views.startup_detail, name="detail"),
    path("<int:startup_id>/send-password-link/", staff_views.send_password_link, name="send_password_link"),
    # Staff (TBI members)
    path("api/", views.startups_api, name="api_list"),
    path("api/sync/", views.sync_api, name="api_sync"),
    path("api/<int:startup_id>/", views.startup_detail_api, name="api_detail"),
    path("api/<int:startup_id>/send-password-link/", views.send_password_link_api, name="api_send_password_link"),
    # The public set-password page (/startups/set-password/<token>/) and its JSON endpoint
    # (/api/startups/set-password/<token>/) are in config/urls.py.
    # Startup portal pages (phase F6; startup login, never staff)
    path("portal/", portal_views.home, name="portal_home"),
    path("portal/login/", portal_views.login_page, name="portal_login_page"),
    path("portal/logout/", portal_views.logout_page, name="portal_logout_page"),
    path("portal/activities/", portal_views.open_activities, name="portal_activities"),
    path("portal/activities/<int:activity_id>/register/", portal_views.register_page, name="portal_register_page"),
    path("portal/activities/<int:activity_id>/registered/", portal_views.registered, name="portal_registered"),
    path("portal/registrations/", portal_views.my_registrations, name="portal_registrations_page"),
    path("portal/certificates/", portal_views.my_certificates, name="portal_certificates_page"),
    path("portal/notifications/", portal_views.notifications, name="portal_notifications_page"),
    path("portal/notifications/read-all/", portal_views.mark_all_read, name="portal_read_all_page"),
    path("portal/notifications/<int:notification_id>/read/", portal_views.mark_read, name="portal_read_page"),
    path("portal/profile/", portal_views.profile, name="portal_profile"),
    # Startup portal JSON API (startup login)
    path("portal/api/login/", views.portal_login, name="portal_login"),
    path("portal/api/logout/", views.portal_logout, name="portal_logout"),
    path("portal/api/me/", views.portal_me, name="portal_me"),
    path("portal/api/registrations/", views.portal_registrations, name="portal_registrations"),
    path("portal/api/activities/", views.portal_open_activities, name="portal_open_activities"),
    path("portal/api/activities/<int:activity_id>/register/", views.portal_register, name="portal_register"),
    path("portal/api/certificates/", views.portal_certificates, name="portal_certificates"),
    path("portal/certificates/<int:certificate_id>/download/", views.portal_certificate_download, name="portal_certificate_download"),
    path("portal/api/notifications/", views.portal_notifications, name="portal_notifications"),
    path("portal/api/notifications/unread-count/", views.portal_unread_count, name="portal_unread_count"),
    path("portal/api/notifications/read-all/", views.portal_mark_all_read, name="portal_mark_all_read"),
    path("portal/api/notifications/<int:notification_id>/read/", views.portal_mark_read, name="portal_mark_read"),
]
