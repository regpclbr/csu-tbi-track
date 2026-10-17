"""Root URL configuration for CSU-TBI Track."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("activities/", include("activities.urls")),
    path("registrations/", include("registrations.urls")),
    path("documents/", include("documents.urls")),
    path("certificates/", include("certificates.urls")),
    path("reports/", include("reports.urls")),
    path("startups/", include("startups.urls")),
    path("assistant/", include("assistant.urls")),
]

# Serve uploaded files during development only.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
