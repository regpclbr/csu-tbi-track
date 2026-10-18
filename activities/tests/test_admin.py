"""Activities cannot be deleted from the Django admin."""

import pytest
from django.contrib.admin.sites import site
from django.test import RequestFactory

from accounts.models import TBIMember
from activities.models import Activity


@pytest.mark.django_db
def test_activity_admin_has_no_delete(admin_role, activity) -> None:
    admin_user = TBIMember.objects.create_user("admin@example.com", "Admin", admin_role, "pass-12345")
    request = RequestFactory().get("/admin/activities/activity/")
    request.user = admin_user
    model_admin = site._registry[Activity]

    assert model_admin.has_delete_permission(request, activity) is False
    assert "delete_selected" not in model_admin.get_actions(request)
