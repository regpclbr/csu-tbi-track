"""An activity can be for several participant categories (ACTIVITY_PARTICIPANT_CATEGORY)."""

import json
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from activities import services
from activities.models import ActivityParticipantCategory, ParticipantCategory

from .test_admin import form_data


def cat(name: str) -> ParticipantCategory:
    return ParticipantCategory.objects.get(name=name)


def audit_rows(activity) -> list[tuple[str, str, str]]:
    return list(activity.audit_logs.order_by("log_id").values_list("field_changed", "old_value", "new_value"))


# --- Create, update, at least one ---------------------------------------------------------


@pytest.mark.django_db
def test_create_with_two_categories(director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(participant_categories=[cat("MSMEs"), cat("Faculty")]))
    assert sorted(c.name for c in activity.participant_categories.all()) == ["Faculty", "MSMEs"]
    detail = services.get_activity_detail(activity)
    assert detail["participant_categories"] == ["Faculty", "MSMEs"]  # sorted by name
    assert detail["participant_category_ids"] == [cat("Faculty").pk, cat("MSMEs").pk]
    assert audit_rows(activity) == [("Status", "", "Draft")]  # creation still writes one row


@pytest.mark.django_db
@pytest.mark.parametrize("value", [[], None])
def test_create_needs_at_least_one_category(director, valid_data, value) -> None:
    with pytest.raises(ValidationError) as excinfo:
        services.create_activity(director, valid_data(participant_categories=value))
    assert excinfo.value.message_dict["participant_categories"] == ["Choose at least one participant category."]


@pytest.mark.django_db
def test_create_without_the_key_needs_a_category(director, valid_data) -> None:
    data = valid_data()
    del data["participant_categories"]
    with pytest.raises(ValidationError, match="Choose at least one participant category"):
        services.create_activity(director, data)


@pytest.mark.django_db
def test_update_cannot_remove_every_category(director, new_activity) -> None:
    with pytest.raises(ValidationError, match="Choose at least one participant category"):
        services.update_activity(director, new_activity, {"participant_categories": []})
    assert [c.name for c in new_activity.participant_categories.all()] == ["Incubatees"]


@pytest.mark.django_db
def test_category_link_is_unique_per_activity(new_activity) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        ActivityParticipantCategory.objects.create(activity=new_activity, participant_category=cat("Incubatees"))


# --- Audit log -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_one_audit_row_with_sorted_names(director, new_activity) -> None:
    services.update_activity(director, new_activity, {"participant_categories": [cat("MSMEs"), cat("Faculty")]})
    assert audit_rows(new_activity)[-1] == ("Participant Categories", "Incubatees", "Faculty, MSMEs")

    services.update_activity(director, new_activity, {"participant_categories": [cat("Faculty")]})
    assert audit_rows(new_activity)[-1] == ("Participant Categories", "Faculty, MSMEs", "Faculty")


@pytest.mark.django_db
def test_no_audit_row_when_only_the_order_changes(director, valid_data) -> None:
    activity = services.create_activity(director, valid_data(participant_categories=[cat("Faculty"), cat("MSMEs")]))
    services.update_activity(
        director, activity, {"participant_categories": [cat("MSMEs"), cat("Faculty"), cat("MSMEs")]}
    )  # same set: different order and a repeat
    assert audit_rows(activity) == [("Status", "", "Draft")]


@pytest.mark.django_db
def test_category_change_rolls_back_with_its_audit_row(director, new_activity, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("database error")

    monkeypatch.setattr(services, "_write_audit", fail)
    with pytest.raises(RuntimeError):
        services.update_activity(director, new_activity, {"participant_categories": [cat("Faculty")]})
    assert [c.name for c in new_activity.participant_categories.all()] == ["Incubatees"]


@pytest.mark.django_db
def test_conducted_activity_categories_are_locked(director, new_activity, move_to) -> None:
    move_to(new_activity, "Conducted")
    with pytest.raises(services.ActivityError, match="read-only"):
        services.update_activity(director, new_activity, {"participant_categories": [cat("Faculty")]})


# --- List filter: an activity matches if it has the chosen category ------------------------


@pytest.mark.django_db
def test_list_filter_matches_any_of_the_categories(director, valid_data, register) -> None:
    both = services.create_activity(
        director, valid_data(title="For both", participant_categories=[cat("Faculty"), cat("MSMEs")])
    )
    services.create_activity(director, valid_data(title="Faculty only", participant_categories=[cat("Faculty")]))
    register(both, 3)

    def rows(category: str):
        return {a.title: a.registered for a in services.list_activities({"participant_category": cat(category).pk}).items}

    assert rows("Faculty") == {"For both": 3, "Faculty only": 0}  # each once; counts not doubled
    assert rows("MSMEs") == {"For both": 3}
    assert rows("Incubatees") == {}
    assert services.list_activities().total == 2  # totals count distinct activities


# --- API: a list of IDs in and out ---------------------------------------------------------


@pytest.mark.django_db
def test_api_takes_and_returns_a_list_of_ids(client, director, new_activity) -> None:
    client.force_login(director)
    ids = [cat("Faculty").pk, cat("MSMEs").pk]
    response = client.post(
        f"/activities/api/{new_activity.pk}/update/",
        json.dumps({"participant_categories": ids}),
        content_type="application/json",
    )
    assert response.status_code == 200
    activity = json.loads(response.content)["activity"]
    assert activity["participant_category_ids"] == sorted(ids)
    assert activity["participant_categories"] == ["Faculty", "MSMEs"]

    row = json.loads(client.get("/activities/api/").content)["activities"][0]
    assert (row["participant_category_ids"], row["participant_categories"]) == (sorted(ids), ["Faculty", "MSMEs"])


@pytest.mark.django_db
def test_api_rejects_empty_or_unknown_categories(client, director, new_activity) -> None:
    client.force_login(director)
    url = f"/activities/api/{new_activity.pk}/update/"
    empty = client.post(url, json.dumps({"participant_categories": []}), content_type="application/json")
    assert empty.status_code == 400
    assert json.loads(empty.content)["errors"]["participant_categories"] == ["Choose at least one participant category."]
    unknown = client.post(url, json.dumps({"participant_categories": [9999]}), content_type="application/json")
    assert unknown.status_code == 400


# --- Django admin ----------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_multi_select_add_and_edit(client, admin_member, valid_data) -> None:
    client.force_login(admin_member)
    values = valid_data(participant_categories=[cat("Faculty"), cat("MSMEs")])
    assert client.post("/admin/activities/activity/add/", form_data(values=values)).status_code == 302

    from activities.models import Activity

    activity = Activity.objects.get()
    assert sorted(c.name for c in activity.participant_categories.all()) == ["Faculty", "MSMEs"]

    data = form_data(activity) | {"participant_categories": [cat("Incubatees").pk]}
    assert client.post(f"/admin/activities/activity/{activity.pk}/change/", data).status_code == 302
    assert audit_rows(activity)[-1] == ("Participant Categories", "Faculty, MSMEs", "Incubatees")


@pytest.mark.django_db
def test_admin_requires_a_category(client, admin_member, new_activity) -> None:
    client.force_login(admin_member)
    data = form_data(new_activity)
    data.pop("participant_categories")
    response = client.post(f"/admin/activities/activity/{new_activity.pk}/change/", data)
    assert response.status_code == 200
    assert response.context["adminform"].form.errors["participant_categories"] == [
        "Choose at least one participant category."
    ]


@pytest.mark.django_db
def test_admin_shows_categories_and_locks_them_when_conducted(client, admin_member, new_activity, move_to) -> None:
    client.force_login(admin_member)
    html = client.get("/admin/activities/activity/").content.decode()
    assert "Participant categories" in html and "Incubatees" in html

    move_to(new_activity, "Conducted")
    response = client.get(f"/admin/activities/activity/{new_activity.pk}/change/")
    form = response.context["adminform"].form
    assert "participant_categories" not in form.fields  # read-only, shown as text
    assert '<div class="readonly">Incubatees</div>' in response.content.decode()


@pytest.mark.django_db
def test_admin_list_filter_by_category(client, admin_member, director, valid_data) -> None:
    services.create_activity(director, valid_data(title="Faculty one", participant_categories=[cat("Faculty")]))
    services.create_activity(director, valid_data(title="MSME one", participant_categories=[cat("MSMEs")]))
    client.force_login(admin_member)
    response = client.get("/admin/activities/activity/", {"participant_categories__participant_category_id__exact": cat("MSMEs").pk})
    titles = [a.title for a in response.context["cl"].result_list]
    assert titles == ["MSME one"]


# --- The migration copies each activity's old category ------------------------------------

BEFORE = ("activities", "0003_alter_participantcategory_options_and_more")
AFTER = ("activities", "0004_activity_participant_categories")


@pytest.mark.django_db(transaction=True)
def test_migration_copies_each_activity_category() -> None:
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])  # empty database: going back works
        old = executor.loader.project_state([BEFORE]).apps
        role = old.get_model("accounts", "Role").objects.create(role_name="Director", can_create_activity=True)
        member = old.get_model("accounts", "TBIMember").objects.create(
            email="d@example.com", name="D", role=role, password="x"
        )
        status = old.get_model("activities", "ActivityStatus").objects.create(name="Draft")
        kind = old.get_model("activities", "ActivityType").objects.create(name="Workshop")
        Category = old.get_model("activities", "ParticipantCategory")
        faculty, msmes = Category.objects.create(name="Faculty"), Category.objects.create(name="MSMEs")
        OldActivity = old.get_model("activities", "Activity")
        now = timezone.now()
        common = dict(
            created_by=member, status=status, activity_type=kind, start_date=now.date(), end_date=now.date(),
            time_schedule="8-5", venue="Hall", max_slots=10, registration_deadline=now,
            evaluation_deadline=now, budget_amount=Decimal("0"),
        )
        a1 = OldActivity.objects.create(title="One", participant_category=faculty, **common)
        a2 = OldActivity.objects.create(title="Two", participant_category=msmes, **common)

        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])

        new = executor.loader.project_state([AFTER]).apps
        links = new.get_model("activities", "ActivityParticipantCategory").objects
        assert links.count() == 2
        assert set(links.values_list("activity_id", "participant_category_id")) == {(a1.pk, faculty.pk), (a2.pk, msmes.pk)}
        columns = [c.name for c in connection.introspection.get_table_description(connection.cursor(), "activity")]
        assert "participant_category_id" not in columns
    finally:
        MigrationExecutor(connection).migrate(leaves)
