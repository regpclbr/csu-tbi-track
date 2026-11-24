"""Phase F2b: the Forms, Registrations and Evaluation tabs of the activity page and the printable attendance
sheet. The rules are tested with the services; these tests check that the pages call them, show their messages
and respect the permissions (actions: can_create_activity; viewing: any active staff member)."""

import json
import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import pages as activity_pages
from activities import services as activity_services
from activities.models import Activity
from registrations import pages, services
from registrations.models import FieldType, FormField, FormType

from .conftest import CERT, evaluation_answers, form_of, opens_at

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def tab_url(activity, tab: str, **params) -> str:
    query = "&".join([f"tab={tab}"] + [f"{key}={value}" for key, value in params.items()])
    return f"{reverse('activities:detail', args=[activity.pk])}?{query}"


@pytest.fixture
def login():
    def log_in(member) -> Client:
        client = Client()
        client.force_login(member)
        return client

    return log_in


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


def toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["toast"]


def evaluate(person, activity, **extra) -> None:
    services.submit_evaluation(services.evaluation_token(person), **CERT, answers=evaluation_answers(activity),
                               now=opens_at(activity) + timedelta(hours=1), **extra)


# --- Forms tab ---------------------------------------------------------------------------------------------------


def test_forms_tab_without_forms_offers_create_from_defaults(director, login, new_activity, defaults) -> None:
    html = login(director).get(tab_url(new_activity, "forms")).content.decode()
    assert html.count("Create from defaults") == 2 and "coming in phase" not in html
    count = pages.default_field_count(new_activity, FormType.REGISTRATION)
    assert count and f"Start from the {count} default field" in html


def test_forms_tab_is_read_only_without_can_create_activity(view_only, login, scheduled) -> None:
    response = login(view_only).get(tab_url(scheduled, "forms"))
    html = response.content.decode()
    assert response.status_code == 200 and "Affiliation / Organization" in html
    assert "Create from defaults" not in html and "Add field" not in html and "Move “" not in html
    form = form_of(scheduled)
    client = login(view_only)
    assert client.post(reverse("registrations:field_add", args=[form.pk]), {"field_label": "X", "field_type": "short_text"}).status_code == 403
    assert client.post(reverse("registrations:form_create", args=[scheduled.pk, "registration"])).status_code == 403


def test_create_from_defaults_removes_the_notice_and_shows_the_link(director, login, new_activity, defaults, move_to) -> None:
    move_to(new_activity, "Scheduled")
    client = login(director)
    detail = reverse("activities:detail", args=[new_activity.pk])
    assert activity_pages.FORM_MISSING_NOTICE in client.get(detail).content.decode()
    response = client.post(reverse("registrations:form_create", args=[new_activity.pk, "registration"]), follow=True)
    form = form_of(new_activity)
    assert response.redirect_chain[-1][0] == tab_url(new_activity, "forms")
    assert f"Registration form created with {form.fields.count()} field." in messages_of(response)  # 1 default field
    html = client.get(detail).content.decode()
    assert activity_pages.FORM_MISSING_NOTICE not in html and "Copy registration link" in html


def test_create_twice_shows_the_services_message(director, login, scheduled) -> None:
    response = login(director).post(reverse("registrations:form_create", args=[scheduled.pk, "registration"]), follow=True)
    assert "This activity already has a registration form." in messages_of(response)


def test_add_a_choice_field_stores_the_options_as_a_json_list(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    form = form_of(new_activity)
    data = {"field_label": "T-shirt size", "field_type": "multiple_choice", "is_required": "on", "option": ["S", " M ", "", "L"]}
    response = login(director).post(reverse("registrations:field_add", args=[form.pk]), data, **HTMX)
    field = form.fields.get(field_label="T-shirt size")
    assert json.loads(field.options) == ["S", "M", "L"] and field.is_required
    assert toast(response) == {"level": "success", "text": "“T-shirt size” added."}
    assert "registrations/_form_card.html" in [t.name for t in response.templates]


def test_field_errors_are_shown_in_the_editor(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    form = form_of(new_activity)
    url = reverse("registrations:field_add", args=[form.pk])
    client = login(director)
    html = client.post(url, {"field_label": " ", "field_type": "short_text"}, **HTMX).content.decode()
    assert 'aria-invalid="true"' in html and "This field is required." in html
    html = client.post(url, {"field_label": "Email", "field_type": "short_text"}, **HTMX).content.decode()
    assert "is already asked on every registration" in html
    html = client.post(url, {"field_label": "Pick one", "field_type": "checkbox", "option": ["", " "]}, **HTMX).content.decode()
    assert "Give at least one option, and no empty options." in html
    assert not form.fields.filter(field_label__in=["Email", "Pick one"]).exists()


def test_edit_a_field(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    field = form_of(new_activity).fields.get(field_label="Affiliation / Organization")
    client = login(director)
    editor = client.get(reverse("registrations:field_edit", args=[field.template_id, field.pk]), **HTMX).content.decode()
    assert 'value="Affiliation / Organization"' in editor and "Save field" in editor
    client.post(reverse("registrations:field_update", args=[field.pk]), {"field_label": "Organization", "field_type": "long_text"}, **HTMX)
    field.refresh_from_db()
    assert (field.field_label, field.field_type, field.is_required) == ("Organization", FieldType.LONG_TEXT, False)


def test_remove_a_field(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    field = form_of(new_activity).fields.get(field_label="Affiliation / Organization")
    response = login(director).post(reverse("registrations:field_remove", args=[field.pk]), **HTMX)
    assert toast(response)["text"] == "“Affiliation / Organization” removed."
    assert not FormField.objects.filter(pk=field.pk).exists()


def test_move_a_field_up_and_down(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    form = form_of(new_activity)
    services.add_field(director, form, field_label="T-shirt size", field_type="short_text")  # 2 fields to move
    order = lambda: list(form.fields.order_by("sort_order").values_list("field_label", flat=True))  # noqa: E731
    first, second = order()[:2]
    client = login(director)
    client.post(reverse("registrations:field_move", args=[form.fields.get(field_label=second).pk]), {"direction": "up"}, **HTMX)
    assert order()[:2] == [second, first]
    client.post(reverse("registrations:field_move", args=[form.fields.get(field_label=second).pk]), {"direction": "down"}, **HTMX)
    assert order()[:2] == [first, second]


def test_moved_order() -> None:
    assert pages.moved_order([1, 2, 3], 2, "up") == [2, 1, 3]
    assert pages.moved_order([1, 2, 3], 2, "down") == [1, 3, 2]
    assert pages.moved_order([1, 2, 3], 1, "up") == [1, 2, 3] and pages.moved_order([1, 2, 3], 3, "down") == [1, 2, 3]


def test_a_locked_form_hides_every_control_and_refuses_changes(director, login, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    form = form_of(scheduled)
    client = login(director)
    html = client.get(tab_url(scheduled, "forms")).content.decode()
    registration_card = html[html.index('id="form-card-registration"'):html.index('id="form-card-evaluation"')]
    assert "Locked." in registration_card
    assert "Add field" not in registration_card and "Move “" not in registration_card and "Edit “" not in registration_card
    response = client.post(reverse("registrations:field_add", args=[form.pk]), {"field_label": "Late", "field_type": "short_text"}, **HTMX)
    assert toast(response) == {"level": "error", "text": services.FORM_LOCKED}
    field = form.fields.order_by("sort_order").last()
    response = client.post(reverse("registrations:field_move", args=[field.pk]), {"direction": "up"}, **HTMX)
    assert toast(response)["text"] == services.FORM_LOCKED
    assert not form.fields.filter(field_label="Late").exists()


def test_without_htmx_a_field_action_comes_back_to_the_forms_tab(director, login, new_activity, with_forms) -> None:
    with_forms(new_activity)
    form = form_of(new_activity)
    response = login(director).post(reverse("registrations:field_add", args=[form.pk]), {"field_label": "Notes", "field_type": "long_text"})
    assert response.status_code == 302 and response.url == tab_url(new_activity, "forms")


def test_preview_shows_the_fixed_fields_and_the_form_fields(director, login, scheduled) -> None:
    html = login(director).get(tab_url(scheduled, "forms")).content.decode()
    registration = html[html.index('id="preview-registration"'):]
    for text in ("Full name", "Email address", "Contact number", "Participant category", "Affiliation / Organization"):
        assert text in registration
    evaluation = html[html.index('id="preview-evaluation"'):]
    assert "Name to print on your certificate" in evaluation and "Overall rating" in evaluation and "Comments" in evaluation


# --- Registrations tab --------------------------------------------------------------------------------------------


def test_registrations_tab_shows_counts_and_rows(director, login, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com", contact_number="09171234567")
    html = login(director).get(tab_url(scheduled, "registrations")).content.decode()
    assert "1/30" in html and "ana@example.com" in html and "09171234567" in html and "Incubatees" in html
    assert 'name="registration_ids"' not in html  # Scheduled: attendance cannot be recorded yet
    assert services.attendance_closed_reason(scheduled) in html


def test_registrations_tab_is_read_only_without_can_create_activity(view_only, login, attended) -> None:
    [person] = attended(1)
    html = login(view_only).get(tab_url(person.activity, "registrations")).content.decode()
    assert person.email in html and 'name="registration_ids"' not in html and 'name="row_action"' not in html
    response = login(view_only).post(reverse("registrations:attendance", args=[person.activity_id]),
                                       {"registration_ids": [person.pk], "action": "unmark"})
    assert response.status_code == 403


def test_search_and_filters(director, login, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com", name="Ana Cruz")
    signup(scheduled, "ben@example.com", name="Ben Reyes")
    client = login(director)
    html = client.get(tab_url(scheduled, "registrations", q="ben")).content.decode()
    assert "Ben Reyes" in html and "Ana Cruz" not in html
    html = client.get(tab_url(scheduled, "registrations", attended="true")).content.decode()
    assert "No registrations match" in html
    response = client.get(tab_url(scheduled, "registrations", q="ana"), **HTMX)
    assert "registrations/_registrations_results.html" in [t.name for t in response.templates]


def test_list_registrations_search_matches_name_or_email(scheduled, signup) -> None:
    signup(scheduled, "ana@example.com", name="Ana Cruz")
    signup(scheduled, "zed@example.com", name="Ben Reyes")
    assert [r.name for r in services.list_registrations(scheduled, search="ZED")] == ["Ben Reyes"]
    assert [r.name for r in services.list_registrations(scheduled, search="ana")] == ["Ana Cruz"]


def test_registration_details_show_the_answers(director, login, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    html = login(director).get(reverse("registrations:registration_detail", args=[scheduled.pk, person.pk]), **HTMX).content.decode()
    assert "Affiliation / Organization" in html and "CSU" in html


def test_mark_attended_is_refused_until_ongoing_then_works(director, login, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    url = reverse("registrations:attendance", args=[scheduled.pk])
    client = login(director)
    data = {"registration_ids": [person.pk], "action": "mark"}
    assert services.attendance_closed_reason(scheduled) in messages_of(client.post(url, data, follow=True))
    activity_services.change_status(director, scheduled, "Ongoing")
    response = client.post(url, data, follow=True)
    assert "Marked 1 registration as attended." in messages_of(response)
    person.refresh_from_db()
    assert person.attendance_confirmed and person.attendance_days.get().method == "staff"
    assert 'name="registration_ids"' in response.content.decode()


def test_unmark_and_its_refusal_after_the_evaluation(director, login, attended) -> None:
    ana, ben = attended(2)
    activity = ana.activity
    evaluate(ana, activity)
    url = reverse("registrations:attendance", args=[activity.pk])
    client = login(director)
    response = client.post(url, {"registration_ids": [ana.pk, ben.pk], "action": "unmark"}, follow=True)
    [message] = messages_of(response)
    assert message.startswith("Cannot undo attendance for") and "already submitted the evaluation" in message
    assert "Attendance undone for 1 registration." in messages_of(client.post(url, {"registration_ids": [ben.pk], "action": "unmark"}, follow=True))
    assert "Select at least one registration first." in messages_of(client.post(url, {"action": "unmark"}, follow=True))


def test_attendance_keeps_the_filters_when_coming_back(director, login, attended) -> None:
    [person] = attended(1)
    response = login(director).post(reverse("registrations:attendance", args=[person.activity_id]),
                                    {"registration_ids": [person.pk], "action": "unmark", "filters": "q=person&attended=true&evil=1"})
    assert response.url == tab_url(person.activity, "registrations", q="person", attended="true")


def test_send_links_is_disabled_with_the_reason(director, login, scheduled, new_activity) -> None:
    html = login(director).get(tab_url(scheduled, "evaluation")).content.decode()
    assert "Resend: Links cannot be sent now. The evaluation opens on" in html
    assert re.search(r'<button type="submit" class="btn btn-primary btn-sm" disabled', html)


def test_send_links_without_an_evaluation_form(director, login, valid_data) -> None:
    activity = activity_services.create_activity(director, valid_data(title="No forms"))
    html = login(director).get(tab_url(activity, "evaluation")).content.decode()
    assert "This activity has no evaluation form yet." in html
    assert "There is no evaluation form yet: create it on the Forms tab." in html


def test_send_links_while_the_window_is_open(director, login, scheduled, signup) -> None:
    person = signup(scheduled, "ana@example.com")
    today = timezone.localdate()
    Activity.objects.filter(pk=scheduled.pk).update(start_date=today, end_date=today)  # the evaluation window opens today
    activity_services.change_status(director, scheduled, "Ongoing")
    services.set_attendance(director, scheduled, [person.pk], True, "staff")
    client = login(director)
    html = client.get(tab_url(scheduled, "evaluation")).content.decode()
    assert "Attendees who have not answered (1)" in html and person.name in html and "Not sent yet" in html
    response = client.post(reverse("registrations:send_links", args=[scheduled.pk]), follow=True)
    assert "Queued 1 evaluation link email. They go out while qcluster is running." in messages_of(response)


def test_read_functions_for_attendance_and_links(director, scheduled, signup) -> None:
    assert services.attendance_closed_reason(scheduled).startswith("Attendance opens when the activity starts on")
    assert services.evaluation_links_closed_reason(scheduled, now=opens_at(scheduled)) is None
    assert services.evaluation_links_closed_reason(scheduled, now=opens_at(scheduled) - timedelta(minutes=1)).startswith("Links cannot be sent now.")
    person = signup(scheduled, "ana@example.com")
    activity_services.change_status(director, scheduled, "Ongoing")
    assert services.attendance_closed_reason(scheduled) is None
    services.set_attendance(director, scheduled, [person.pk], True, "staff")
    assert list(services.evaluation_link_recipients(scheduled)) == [person]


# --- Attendance sheet -----------------------------------------------------------------------------------------------


def test_attendance_sheet_has_names_and_categories_but_no_emails_or_numbers(view_only, login, scheduled, signup) -> None:
    signup(scheduled, "zoe@example.com", name="Zoe Tan", contact_number="09170000001")
    signup(scheduled, "ana@example.com", name="Ana Cruz", contact_number="09170000002")
    response = login(view_only).get(reverse("registrations:attendance_sheet", args=[scheduled.pk]))
    html = response.content.decode()
    assert response.status_code == 200 and "Signature" in html and "Incubatees" in html and scheduled.venue in html
    assert html.index("Ana Cruz") < html.index("Zoe Tan")
    assert "@example.com" not in html and "0917" not in html


# --- Evaluation tab ---------------------------------------------------------------------------------------------------


def test_evaluation_tab_empty_state(director, login, scheduled) -> None:
    assert "No evaluations yet" in login(director).get(tab_url(scheduled, "evaluation")).content.decode()


def test_evaluation_tab_shows_averages_but_never_names_comments_or_text(view_only, director, login, attended) -> None:
    [person] = attended(1)
    evaluate(person, person.activity, comments="Secret comment XYZ")
    html = login(view_only).get(tab_url(person.activity, "evaluation")).content.decode()
    assert "Overall rating" in html and "5.0 / 5" in html and "rating answers" in html
    for private in (person.name, person.email, CERT["certificate_name"], CERT["certificate_email"], "Secret comment XYZ", "How to pitch."):
        assert private not in html
