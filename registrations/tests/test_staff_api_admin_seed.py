"""Staff API (forms, registrations, attendance, links), the Django admin, the default-field
seed, and the registrations migration."""

import json
from datetime import time, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from accounts.seed import DATA_PRIVACY_CONSENT, load_default_form_fields
from activities.models import ActivityType
from registrations import services
from registrations.models import DefaultFormField, FormTemplate, FormType, Registration

from .conftest import form_of, opens_at

API = "/registrations/api"


def post_json(client, url: str, data: dict | None = None):
    return client.post(url, json.dumps(data or {}), content_type="application/json")


def body(response) -> dict:
    return json.loads(response.content)


# --- Staff API ------------------------------------------------------------------------------


@pytest.mark.django_db
def test_forms_api(client, director, new_activity, defaults) -> None:
    client.force_login(director)
    response = post_json(client, f"{API}/activities/{new_activity.pk}/forms/", {"form_type": "registration"})
    assert response.status_code == 201
    form = body(response)["form"]
    assert form["generated_from_default"] is True and form["locked"] is False
    template_id = form["template_id"]

    added = post_json(client, f"{API}/forms/{template_id}/fields/", {"field_label": "Size", "field_type": "multiple_choice", "options": ["S", "M"]})
    assert added.status_code == 201
    size = next(f for f in body(added)["form"]["fields"] if f["field_label"] == "Size")
    assert size["options"] == ["S", "M"]

    assert post_json(client, f"{API}/fields/{size['field_id']}/", {"is_required": False}).status_code == 200
    ids = [f["field_id"] for f in body(client.get(f"{API}/forms/{template_id}/"))["form"]["fields"]]
    assert post_json(client, f"{API}/forms/{template_id}/reorder/", {"field_ids": ids[::-1]}).status_code == 200
    assert post_json(client, f"{API}/fields/{size['field_id']}/remove/").status_code == 200

    duplicate = post_json(client, f"{API}/activities/{new_activity.pk}/forms/", {"form_type": "registration"})
    assert duplicate.status_code == 400 and "already has a registration form" in body(duplicate)["error"]


@pytest.mark.django_db
def test_locked_form_api_error(client, director, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    field = form_of(scheduled).fields.first()
    client.force_login(director)
    response = post_json(client, f"{API}/fields/{field.pk}/remove/")
    assert response.status_code == 400 and "fields are locked" in body(response)["error"]


@pytest.mark.django_db
def test_registrations_and_attendance_api(client, director, scheduled, signup) -> None:
    ana = signup(scheduled, "ana@example.com")
    signup(scheduled, "ben@example.com", name="Ben")
    from activities import services as activity_services

    activity_services.change_status(director, scheduled, "Ongoing")
    client.force_login(director)
    marked = post_json(client, f"{API}/activities/{scheduled.pk}/attendance/", {"registration_ids": [ana.pk], "attended": True, "method": "email"})
    assert body(marked) == {"changed": 1}

    rows = body(client.get(f"{API}/activities/{scheduled.pk}/registrations/", {"attended": "true"}))
    assert rows["total"] == 1
    row = rows["registrations"][0]
    assert (row["email"], row["participant_category"], row["confirmation_method"]) == ("ana@example.com", "Incubatees", "email")
    assert {a["field_label"] for a in row["answers"]} == {"Affiliation / Organization", "Data Privacy consent"}
    assert client.get(f"{API}/activities/{scheduled.pk}/registrations/", {"attended": "maybe"}).status_code == 400


@pytest.mark.django_db
def test_send_links_api(client, director, attended, monkeypatch) -> None:
    activity = attended(1)[0].activity
    monkeypatch.setattr(services.timezone, "now", lambda: opens_at(activity) + timedelta(hours=10))
    client.force_login(director)
    assert body(post_json(client, f"{API}/activities/{activity.pk}/send-evaluation-links/")) == {"queued": 1}
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_view_only_members_gets_403_on_staff_changes(client, view_only, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    form = form_of(scheduled)
    client.force_login(view_only)
    for url, data in [
        (f"{API}/activities/{scheduled.pk}/forms/", {"form_type": "evaluation"}),
        (f"{API}/forms/{form.pk}/fields/", {"field_label": "X", "field_type": "short_text"}),
        (f"{API}/activities/{scheduled.pk}/attendance/", {"registration_ids": [person.pk], "attended": True, "method": "email"}),
        (f"{API}/activities/{scheduled.pk}/send-evaluation-links/", {}),
    ]:
        response = post_json(client, url, data)
        assert response.status_code == 403, url
        assert body(response) == {"error": "You do not have permission to create or edit activities."}
    # Reading is allowed for any staff member.
    assert client.get(f"{API}/activities/{scheduled.pk}/registrations/").status_code == 200


@pytest.mark.django_db
def test_staff_api_needs_login(client, scheduled) -> None:
    response = client.get(f"{API}/activities/{scheduled.pk}/registrations/")
    assert response.status_code == 401 and response.json() == {"error": "Login required."}


# --- Django admin ------------------------------------------------------------------------


@pytest.mark.django_db
def test_registrations_and_answers_are_view_only(admin_member, rf, scheduled, signup) -> None:
    from django.contrib.admin.sites import site

    from registrations.models import EvaluationSubmission, FormAnswer, FormField

    registration = signup(scheduled, "ana@example.com")
    request = rf.get("/admin/")
    request.user = admin_member
    for model in (Registration, FormAnswer, EvaluationSubmission, FormTemplate, FormField):
        model_admin = site._registry[model]
        assert not model_admin.has_add_permission(request), model
        assert not model_admin.has_change_permission(request, None), model
        assert not model_admin.has_delete_permission(request, None), model
    assert registration


@pytest.mark.django_db
def test_admin_attendance_actions(client, both_flags_member, director, scheduled, signup) -> None:
    """The admin actions themselves; a test-only role with both flags opens them (a real role never has both,
    ASSUMPTIONS 339)."""
    from activities import services as activity_services

    person = signup(scheduled, "ana@example.com")
    client.force_login(both_flags_member)
    url = "/admin/registrations/registration/"
    response = client.post(url, {"action": "mark_attended_email", "_selected_action": [person.pk]}, follow=True)
    assert "Attendance opens when the activity starts on" in response.content.decode()

    activity_services.change_status(director, scheduled, "Ongoing")
    client.post(url, {"action": "mark_attended_wet_signature", "_selected_action": [person.pk]})
    person.refresh_from_db()
    assert (person.attendance_confirmed, person.confirmation_method) == (True, "wet_signature")


@pytest.mark.django_db
def test_admin_create_forms_action(client, both_flags_member, new_activity, defaults) -> None:
    client.force_login(both_flags_member)
    client.post("/admin/activities/activity/", {"action": "create_forms", "_selected_action": [new_activity.pk]})
    assert sorted(new_activity.form_templates.values_list("form_type", flat=True)) == ["evaluation", "registration"]


@pytest.mark.django_db
def test_system_administrator_gets_no_attendance_action(client, admin_member, director, scheduled, signup) -> None:
    """The System Administrator cannot create activities, so the admin attendance actions do nothing."""
    from activities import services as activity_services

    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    client.force_login(admin_member)
    client.post("/admin/registrations/registration/", {"action": "mark_attended_wet_signature",
                                                       "_selected_action": [person.pk]})
    person.refresh_from_db()
    assert not person.attendance_confirmed


@pytest.mark.django_db
def test_system_administrator_gets_no_create_forms_action(client, admin_member, new_activity, defaults) -> None:
    client.force_login(admin_member)
    client.post("/admin/activities/activity/", {"action": "create_forms", "_selected_action": [new_activity.pk]})
    assert not new_activity.form_templates.exists()


@pytest.mark.django_db
def test_default_fields_editable_with_json_options(client, admin_member, defaults) -> None:
    client.force_login(admin_member)
    workshop = ActivityType.objects.get(name="Workshop")
    data = {
        "activity_type": workshop.pk, "form_type": "registration", "field_label": "Meal preference",
        "field_type": "multiple_choice", "options": '["Regular", "Vegetarian"]', "is_required": "on", "sort_order": 3,
    }
    assert client.post("/admin/registrations/defaultformfield/add/", data).status_code == 302
    bad = client.post("/admin/registrations/defaultformfield/add/", data | {"field_label": "Bad", "options": "Regular, Veg"})
    assert bad.status_code == 200 and "JSON list" in bad.content.decode()


# --- Seed: default form fields -----------------------------------------------------------


@pytest.mark.django_db
def test_seed_creates_defaults_for_every_type_once(roles) -> None:
    first = load_default_form_fields()
    assert first.created == ActivityType.objects.count() * 7  # 2 registration + 5 evaluation fields
    assert load_default_form_fields().created == 0  # safe to run twice

    consent = DefaultFormField.objects.filter(field_label="Data Privacy consent").first()
    assert (consent.field_type, services.parse_options(consent.options), consent.is_required) == (
        "checkbox", [DATA_PRIVACY_CONSENT], True,
    )
    assert "Data Privacy Act of 2012 (RA 10173)" in DATA_PRIVACY_CONSENT


@pytest.mark.django_db
def test_seed_keeps_admin_edits_to_defaults(roles) -> None:
    load_default_form_fields()
    DefaultFormField.objects.filter(field_label="Key learnings").update(is_required=False)
    out = StringIO()
    call_command("seed_data", stdout=out)
    assert "Default form fields: 0 created" in out.getvalue()
    assert not DefaultFormField.objects.filter(field_label="Key learnings", is_required=True).exists()


# --- The registrations migration fills categories -------------------------------------

BEFORE = ("registrations", "0001_initial")
AFTER = ("registrations", "0002_step5_categories_forms_json_options")
ACTIVITIES = ("activities", "0006_auditlog_changed_by_nullable")  # stays applied (the latest activities migration)
REQUIRED = ("registrations", "0003_registration_category_required")


@pytest.mark.django_db(transaction=True)
def test_migrations_fill_category_then_make_it_required(capsys) -> None:
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        old = executor.loader.project_state([BEFORE, ACTIVITIES]).apps
        role = old.get_model("accounts", "Role").objects.create(role_name="Director", can_create_activity=True)
        member = old.get_model("accounts", "TBIMember").objects.create(email="d@x.com", name="D", role=role, password="x")
        status = old.get_model("activities", "ActivityStatus").objects.create(name="Scheduled")
        kind = old.get_model("activities", "ActivityType").objects.create(name="Workshop")
        Category = old.get_model("activities", "ParticipantCategory")
        faculty, msmes = Category.objects.create(name="Faculty"), Category.objects.create(name="MSMEs")
        Activity = old.get_model("activities", "Activity")
        now = timezone.now()
        common = dict(
            created_by=member, status=status, activity_type=kind, start_date=now.date(), end_date=now.date(),
            start_time=time(8, 0), end_time=time(17, 0), venue="Hall", max_slots=10, registration_deadline=now, evaluation_deadline=now, budget_amount=0,
        )
        one = Activity.objects.create(title="One category", **common)
        two = Activity.objects.create(title="Two categories", **common)
        one.participant_categories.add(faculty)
        two.participant_categories.add(faculty, msmes)
        Form = old.get_model("registrations", "FormTemplate")
        Field = old.get_model("registrations", "FormField")
        Reg = old.get_model("registrations", "Registration")
        for activity in (one, two):
            form = Form.objects.create(activity=activity, form_type="registration", title="R", created_by=member)
            Field.objects.create(template=form, field_label="Size", field_type="multiple_choice", options="S\nM")
            Reg.objects.create(activity=activity, template=form, name="A", email=f"a{activity.pk}@x.com")

        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])

        new = executor.loader.project_state([AFTER, ACTIVITIES]).apps
        regs = new.get_model("registrations", "Registration").objects
        assert regs.get(activity_id=one.pk).participant_category_id == faculty.pk
        assert regs.get(activity_id=two.pk).participant_category_id is None
        assert set(new.get_model("registrations", "FormField").objects.values_list("options", flat=True)) == {'["S", "M"]'}
        printed = capsys.readouterr().out
        assert "Registrations: 2. Category filled: 1. Left empty (activity has several categories): 1." in printed
        assert "Form field options converted to JSON lists: 2." in printed

        # 0003 (NOT NULL) refuses while a registration has no category, and changes nothing.
        with pytest.raises(RuntimeError, match=r"1 registration\(s\) have no participant category"):
            MigrationExecutor(connection).migrate([REQUIRED])
        regs.filter(activity_id=two.pk).update(participant_category_id=msmes.pk)
        MigrationExecutor(connection).migrate([REQUIRED])
        columns = {c.name: c.null_ok for c in connection.introspection.get_table_description(connection.cursor(), "registration")}
        assert columns["participant_category_id"] is False
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_form_types() -> None:
    assert FormType.values == ["registration", "evaluation"]
