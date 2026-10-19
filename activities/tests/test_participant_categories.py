"""Participant categories: the Admin can add them; seeded ones are protected; names are unique
ignoring case and spaces; and nothing assumes there are only three."""

import json
from io import StringIO

import pytest
from django.contrib.admin.sites import site
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from activities import services
from activities.models import Activity, ParticipantCategory

URL = "/admin/activities/participantcategory/"


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def category(name: str) -> ParticipantCategory:
    return ParticipantCategory.objects.get(name=name)


# --- Adding and editing -------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_adds_a_category_with_trimmed_name(client, admin_member) -> None:
    client.force_login(admin_member)
    response = client.post(f"{URL}add/", {"name": "  Alumni  ", "description": " Former incubatees "})
    assert response.status_code == 302
    added = category("Alumni")
    assert added.description == "Former incubatees"


@pytest.mark.django_db
def test_seeded_category_description_can_be_edited_but_not_its_name(client, admin_member) -> None:
    faculty = category("Faculty")
    client.force_login(admin_member)
    response = client.post(f"{URL}{faculty.pk}/change/", {"name": "Teachers", "description": "CSU faculty"})
    assert response.status_code == 302  # the read-only name is ignored
    faculty.refresh_from_db()
    assert (faculty.name, faculty.description) == ("Faculty", "CSU faculty")


@pytest.mark.django_db
def test_seeded_name_is_read_only_in_admin(admin_member, rf) -> None:
    request = rf.get(URL)
    request.user = admin_member
    model_admin = site._registry[ParticipantCategory]
    assert model_admin.get_readonly_fields(request, category("MSMEs")) == ("name",)


@pytest.mark.django_db
def test_seeded_category_cannot_be_renamed_by_service(admin_member) -> None:
    faculty = category("Faculty")
    with pytest.raises(ValidationError, match="standard category and cannot be renamed"):
        services.save_participant_category(admin_member, faculty, "Teachers")


@pytest.mark.django_db
def test_added_category_can_be_renamed(client, admin_member) -> None:
    typo = services.save_participant_category(admin_member, ParticipantCategory(), "Alumnii")
    client.force_login(admin_member)
    response = client.post(f"{URL}{typo.pk}/change/", {"name": "Alumni", "description": ""})
    assert response.status_code == 302
    typo.refresh_from_db()
    assert typo.name == "Alumni"


# --- Unique names (ignoring case and spaces) ----------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["faculty", "  FACULTY ", "Msmes"])
def test_duplicate_names_are_refused_in_admin(client, admin_member, name) -> None:
    client.force_login(admin_member)
    response = client.post(f"{URL}add/", {"name": name, "description": ""})
    assert response.status_code == 200
    assert "already exists" in response.context["adminform"].form.errors["name"][0]
    assert ParticipantCategory.objects.count() == 3


@pytest.mark.django_db
def test_renaming_into_an_existing_name_is_refused(admin_member) -> None:
    alumni = services.save_participant_category(admin_member, ParticipantCategory(), "Alumni")
    with pytest.raises(ValidationError, match='"Incubatees" already exists'):
        services.save_participant_category(admin_member, alumni, " incubatees")


@pytest.mark.django_db
def test_database_also_refuses_case_insensitive_duplicates(roles) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ParticipantCategory.objects.create(name=" faculty")


@pytest.mark.django_db
def test_empty_name_is_refused(admin_member) -> None:
    with pytest.raises(ValidationError, match="required"):
        services.save_participant_category(admin_member, ParticipantCategory(), "   ")


# --- Deleting -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_unused_added_category_can_be_deleted(client, admin_member) -> None:
    typo = services.save_participant_category(admin_member, ParticipantCategory(), "Alumnii")
    client.force_login(admin_member)
    response = client.post(f"{URL}{typo.pk}/delete/", {"post": "yes"})
    assert response.status_code == 302
    assert not ParticipantCategory.objects.filter(pk=typo.pk).exists()


@pytest.mark.django_db
def test_category_in_use_shows_a_clear_error(client, admin_member, director, valid_data) -> None:
    alumni = services.save_participant_category(admin_member, ParticipantCategory(), "Alumni")
    services.create_activity(director, valid_data(participant_categories=[alumni]))
    client.force_login(admin_member)

    for method in (client.get, client.post):  # opening the delete page, or confirming it
        response = method(f"{URL}{alumni.pk}/delete/", follow=True)
        assert response.status_code == 200
        assert messages_of(response) == [
            'Cannot delete "Alumni": it is used by 1 activity. Change those activities to another category first.'
        ]
    assert ParticipantCategory.objects.filter(pk=alumni.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["Faculty", "Incubatees", "MSMEs"])
def test_seeded_categories_cannot_be_deleted(client, admin_member, rf, name) -> None:
    seeded = category(name)
    request = rf.get(URL)
    request.user = admin_member
    assert site._registry[ParticipantCategory].has_delete_permission(request, seeded) is False
    with pytest.raises(services.ActivityError, match="standard category and cannot be deleted"):
        services.delete_participant_category(admin_member, seeded)
    client.force_login(admin_member)
    client.post(f"{URL}{seeded.pk}/delete/", {"post": "yes"})
    assert ParticipantCategory.objects.filter(pk=seeded.pk).exists()


@pytest.mark.django_db
def test_no_bulk_delete_action(admin_member, rf) -> None:
    request = rf.get(URL)
    request.user = admin_member
    assert "delete_selected" not in site._registry[ParticipantCategory].get_actions(request)


@pytest.mark.django_db
def test_only_admins_manage_categories(director) -> None:
    with pytest.raises(PermissionDenied):
        services.save_participant_category(director, ParticipantCategory(), "Alumni")
    with pytest.raises(PermissionDenied):
        services.delete_participant_category(director, category("Faculty"))


# --- "Seeded" is decided by name, not ID --------------------------------------------------


@pytest.mark.django_db
def test_seeded_is_decided_by_name(admin_member) -> None:
    added = services.save_participant_category(admin_member, ParticipantCategory(), "Alumni")
    assert services.is_seeded_category(added) is False
    assert all(services.is_seeded_category(category(n)) for n in ["Faculty", "Incubatees", "MSMEs"])
    assert services.is_seeded_category(ParticipantCategory(pk=999, name=" msmes ")) is True


# --- seed_data never duplicates or overwrites ---------------------------------------------


@pytest.mark.django_db
def test_seed_data_keeps_admin_changes(admin_member) -> None:
    services.save_participant_category(admin_member, ParticipantCategory(), "Alumni")
    faculty = category("Faculty")
    services.save_participant_category(admin_member, faculty, "Faculty", "Edited by Admin")

    call_command("seed_data", stdout=StringIO())

    assert ParticipantCategory.objects.count() == 4
    assert category("Faculty").description == "Edited by Admin"
    assert category("Alumni")


@pytest.mark.django_db
def test_seed_data_matches_names_ignoring_case_and_spaces() -> None:
    ParticipantCategory.objects.create(name="msmes ")  # e.g. typed by hand before seeding
    call_command("seed_data", stdout=StringIO())
    names = sorted(ParticipantCategory.objects.values_list("name", flat=True))
    assert names == ["Faculty", "Incubatees", "msmes "]  # no second "MSMEs"


# --- Nothing assumes only three categories ------------------------------------------------


@pytest.fixture
def five_categories(admin_member) -> list[ParticipantCategory]:
    for name in ["Alumni", "Students"]:
        services.save_participant_category(admin_member, ParticipantCategory(), name)
    return list(ParticipantCategory.objects.all())


@pytest.mark.django_db
def test_activities_work_with_added_categories(client, director, valid_data, five_categories) -> None:
    alumni, students = category("Alumni"), category("Students")
    client.force_login(director)

    # Create through the API with a new category, then edit it to another new one.
    payload = {
        key: (value.pk if hasattr(value, "pk") else value.isoformat() if hasattr(value, "isoformat") else str(value))
        for key, value in valid_data().items()
        if key != "participant_categories"
    }
    payload["participant_categories"] = [alumni.pk]
    response = client.post("/activities/api/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 201, response.content
    activity = Activity.objects.get()
    assert json.loads(response.content)["activity"]["participant_categories"] == ["Alumni"]

    services.update_activity(director, activity, {"participant_categories": [students]})
    assert activity.audit_logs.filter(old_value="Alumni", new_value="Students").exists()

    # List filter by the new category, and the detail.
    rows = json.loads(client.get("/activities/api/", {"participant_category": students.pk}).content)
    assert [r["participant_categories"] for r in rows["activities"]] == [["Students"]]
    assert services.list_activities({"participant_category": alumni.pk}).total == 0
    assert services.get_activity_detail(activity)["participant_categories"] == ["Students"]


@pytest.mark.django_db
def test_admin_activity_form_offers_every_category(client, admin_member, five_categories) -> None:
    client.force_login(admin_member)
    choices = client.get("/admin/activities/activity/add/").context["adminform"].form.fields["participant_categories"].queryset
    assert sorted(c.name for c in choices) == ["Alumni", "Faculty", "Incubatees", "MSMEs", "Students"]


@pytest.mark.django_db
def test_admin_list_filter_offers_every_category(client, admin_member, director, valid_data, five_categories) -> None:
    services.create_activity(director, valid_data(participant_categories=[category("Students")]))
    client.force_login(admin_member)
    html = client.get("/admin/activities/activity/").content.decode()
    for name in ["Alumni", "Faculty", "Incubatees", "MSMEs", "Students"]:
        assert name in html
