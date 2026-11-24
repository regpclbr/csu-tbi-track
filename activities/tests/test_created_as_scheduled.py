"""A new activity created as Scheduled (ASSUMPTIONS 409; since 431 automatically when its Activity Design and Request
Letter are uploaded on the New activity form), but nobody can register until its registration form exists AND staff marked it ready ("Open
registration"): the public page and API, the startup portal, the open lists and cards, the Share card link and its QR
code all follow registrations.services.registration_form_ready. Older activities keep the rule before (a form is enough)."""

import json

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from activities import attention, pages, services, share
from activities.models import Activity
from conftest import CREATION_PDF
from documents.services import NewDocument, create_activity_with_documents, creation_required_types
from notifications.models import Notification
from registrations import services as registration_services
from registrations.models import FormType, Registration

pytestmark = pytest.mark.django_db

READY_URL = "registrations:form_ready"


def required_rows() -> list[NewDocument]:
    return [NewDocument(t, SimpleUploadedFile("a.pdf", CREATION_PDF), f"{t.name} - Investor Day")
            for t in creation_required_types()]


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def created(director, valid_data) -> Activity:
    """Created through the New activity form's service, by the Director."""
    return create_activity_with_documents(director, valid_data(title="Investor Day"), required_rows())


def register_someone(activity: Activity):
    """One category: the service chooses it (ASSUMPTIONS 331)."""
    return registration_services.register(activity, name="Ana Cruz", email="ana@example.com",
                                          designation="Owner", office_agency="CSU")


def is_open(activity: Activity) -> bool:
    activity.refresh_from_db()
    return services.open_for_registration().filter(pk=activity.pk).exists()


# --- Created as Scheduled -------------------------------------------------------------------------------------------


def test_created_as_scheduled_with_two_history_rows(created, director) -> None:
    assert created.status.name == "Scheduled" and created.creation_documents_required
    rows = list(created.audit_logs.filter(field_changed="Status").order_by("log_id").values_list("field_changed", "old_value", "new_value", "changed_by"))
    assert rows == [("Status", "", "Draft", director.pk), ("Status", "Draft", "Scheduled", director.pk)]


def test_the_page_opens_the_forms_tab_with_the_next_step(director, valid_data) -> None:
    from conftest import creation_document_files, typed_schedule_fields

    from .test_pages import messages_of
    data = valid_data(title="Via the page")
    post = {
        "title": data["title"], "description": data["description"], "activity_type": data["activity_type"].pk,
        "participant_categories": [c.pk for c in data["participant_categories"]],
        **typed_schedule_fields(data), "venue": data["venue"], "max_slots": data["max_slots"],
        "budget_amount": str(data["budget_amount"]), **creation_document_files(),
    }
    response = client_for(director).post(reverse("activities:create"), post, follow=True)
    activity = Activity.objects.get(title="Via the page")
    assert response.redirect_chain[-1][0] == reverse("activities:detail", args=[activity.pk]) + "?tab=forms"
    expected = ('"Via the page" was created as Scheduled with 2 documents. Registration stays closed '
            "until you create the registration form")
    assert any(text.startswith(expected) for text in messages_of(response))


def test_old_and_api_activities_still_start_as_draft(director, valid_data) -> None:
    assert services.create_activity(director, valid_data()).status.name == "Draft"  # admin / demo seed / tests
    client = client_for(director)
    payload = {**{k: v for k, v in valid_data().items() if k not in ("activity_type", "participant_categories")}}
    data = valid_data()
    payload.update(activity_type=data["activity_type"].pk, participant_categories=[c.pk for c in data["participant_categories"]],
                   start_date=data["start_date"].isoformat(), end_date=data["end_date"].isoformat(),
                   registration_deadline=data["registration_deadline"].isoformat(),
                   evaluation_deadline=data["evaluation_deadline"].isoformat(), start_time="08:00", end_time="17:00",
                   budget_amount=str(data["budget_amount"]))
    response = client.post("/activities/api/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 201 and json.loads(response.content)["activity"]["status"] == "Draft"


def test_it_can_go_back_to_draft_while_nobody_registered(created, director) -> None:
    services.change_status(director, created, "Draft")
    assert created.status.name == "Draft"


def test_no_extra_notifications_or_emails(created, director, make_member, valid_data) -> None:
    """Creating as Scheduled and opening registration tell nobody anything new (no status-change notifications exist)."""
    head = make_member("Incubation Project Head")
    activity = create_activity_with_documents(head, valid_data(title="By the head"), required_rows())
    registration_services.create_form(head, activity, FormType.REGISTRATION)
    registration_services.mark_registration_form_ready(head, activity)
    types = sorted(Notification.objects.filter(activity=activity).values_list("type", flat=True))
    assert types == [Notification.ACTIVITY_CREATED]  # approved copies tell nobody (ASSUMPTIONS 432)
    assert mail.outbox == []


# --- Nobody can register before the form is ready -------------------------------------------------------------------


def test_closed_without_a_form_and_with_only_the_default_form(created, director) -> None:
    for step in ("no form", "default form"):
        if step == "default form":
            registration_services.create_form(director, created, FormType.REGISTRATION)  # copies the default fields
        reason = registration_services.registration_closed_reason(created)
        assert reason == registration_services.REGISTRATION_FORM_NOT_READY, step
        assert not is_open(created), step
        assert created not in registration_services.open_activities(), step
        with pytest.raises(registration_services.RegistrationError):
            register_someone(created)
    assert not Registration.objects.exists()


def test_public_page_and_api_say_not_open_before_ready(created, director, client) -> None:
    registration_services.create_form(director, created, FormType.REGISTRATION)
    page = client.get(reverse("register", args=[created.pk]))
    html = page.content.decode()
    assert page.status_code == 200 and "Registration is closed" in html and "the registration form is not ready" in html
    assert 'name="email"' not in html  # no form to fill in
    category = created.participant_categories.first()
    posted = client.post(reverse("register", args=[created.pk]), {
        "name": "Ana", "email": "ana@example.com", "participant_category": category.pk, "privacy_consent": "on"})
    assert posted.status_code in (200, 400) and not Registration.objects.exists()
    api = client.post(reverse("api_register", args=[created.pk]), json.dumps({
        "name": "Ana", "email": "ana@example.com", "participant_category": category.pk, "privacy_consent": True}),
        content_type="application/json")
    assert api.status_code >= 400 and not Registration.objects.exists()


def test_share_card_and_qr_before_and_after_ready(created, director) -> None:
    summary = pages.registration_summary(created)
    card = share.share_card(created, summary)
    assert card["registration"]["build_form_first"] and card["registration"]["url"] is None
    assert share.qr_url(created, "registration") is None
    client = client_for(director)
    assert client.get(reverse("activities:qr_code", args=[created.pk, "registration"])).status_code == 404
    html = client.get(reverse("activities:detail", args=[created.pk])).content.decode()
    assert "Build the registration form first" in html and registration_services.registration_page_url(created) not in html

    registration_services.create_form(director, created, FormType.REGISTRATION)
    assert share.share_card(created, pages.registration_summary(created))["registration"]["build_form_first"]
    assert pages.registration_summary(created)["notice"] == pages.FORM_NOT_READY_NOTICE

    registration_services.mark_registration_form_ready(director, created)
    card = share.share_card(created, pages.registration_summary(created))
    assert card["registration"]["open"] and card["registration"]["url"] == registration_services.registration_page_url(created)
    assert client.get(reverse("activities:qr_code", args=[created.pk, "registration"])).status_code == 200


def test_needs_attention_and_the_forms_tab_button(created, director, view_only) -> None:
    registration_services.create_form(director, created, FormType.REGISTRATION)
    texts = [item.text for item in attention.activity_attention(created)]
    assert attention.FORM_NOT_READY in texts and "Registration form not built yet." not in texts
    html = client_for(director).get(reverse("activities:detail", args=[created.pk]) + "?tab=forms").content.decode()
    assert "Registration stays closed." in html and reverse(READY_URL, args=[created.pk]) in html
    viewer = client_for(view_only).get(reverse("activities:detail", args=[created.pk]) + "?tab=forms").content.decode()
    assert "Registration stays closed." in viewer and reverse(READY_URL, args=[created.pk]) not in viewer


# --- Open registration ----------------------------------------------------------------------------------------------


def test_open_registration_opens_it_with_a_history_row(created, director) -> None:
    registration_services.create_form(director, created, FormType.REGISTRATION)
    response = client_for(director).post(reverse(READY_URL, args=[created.pk]), follow=True)
    assert response.redirect_chain[-1][0].endswith("?tab=forms")
    assert "Registration is open" in response.content.decode()
    assert is_open(created) and registration_services.registration_closed_reason(created) is None
    assert attention.FORM_NOT_READY not in [item.text for item in attention.activity_attention(created)]
    row = created.audit_logs.exclude(field_changed="Documents").order_by("-log_id").values_list("field_changed", "new_value", "changed_by").first()
    assert row == ("Registration form", registration_services.REGISTRATION_FORM_READY_VALUE, director.pk)
    register_someone(created)
    assert Registration.objects.count() == 1


def test_open_registration_refusals(created, director, view_only) -> None:
    with pytest.raises(registration_services.RegistrationError, match="Create the registration form first"):
        registration_services.mark_registration_form_ready(director, created)
    registration_services.create_form(director, created, FormType.REGISTRATION)
    assert client_for(view_only).post(reverse(READY_URL, args=[created.pk])).status_code == 403
    registration_services.mark_registration_form_ready(director, created)
    with pytest.raises(registration_services.RegistrationError, match="already marked as ready"):
        registration_services.mark_registration_form_ready(director, created)
    assert client_for(director).get(reverse(READY_URL, args=[created.pk])).status_code == 302  # a GET goes to the tab


def test_a_ready_form_on_a_draft_waits_for_scheduled(director, valid_data, client) -> None:
    """A Draft (made through the JSON API) with a ready form still cannot take registrations."""
    activity = services.create_activity(director, valid_data(), creation_documents_required=True)
    registration_services.create_form(director, activity, FormType.REGISTRATION)
    registration_services.mark_registration_form_ready(director, activity)
    assert registration_services.registration_closed_reason(activity) == "Registration is not open for this activity."
    assert client.get(reverse("register", args=[activity.pk])).status_code == 404


# --- Older activities keep the rule before --------------------------------------------------------------------------


def test_old_activities_open_as_soon_as_the_form_exists(director, valid_data) -> None:
    activity = services.create_activity(director, valid_data())
    services.change_status(director, activity, "Scheduled")
    card = share.share_card(activity, pages.registration_summary(activity))
    assert card["registration"]["url"] and "build_form_first" not in card["registration"]  # the link with the reason
    registration_services.create_form(director, activity, FormType.REGISTRATION)
    assert registration_services.registration_closed_reason(activity) is None and is_open(activity)
    assert attention.FORM_NOT_READY not in [item.text for item in attention.activity_attention(activity)]


def test_parity_of_the_query_and_the_reason(created, director, valid_data) -> None:
    """open_for_registration (the lists, cards, dashboard, portal) and registration_closed_reason (the pages, the API,
    register) agree in every state."""
    old = services.create_activity(director, valid_data(title="Old"))
    services.change_status(director, old, "Scheduled")
    activities = [created, old]

    def check() -> None:
        for activity in activities:
            activity.refresh_from_db()
            assert is_open(activity) == (registration_services.registration_closed_reason(activity) is None), activity.title

    check()
    for activity in activities:
        registration_services.create_form(director, activity, FormType.REGISTRATION)
    check()
    registration_services.mark_registration_form_ready(director, created)
    check()
    assert is_open(created) and is_open(old)
