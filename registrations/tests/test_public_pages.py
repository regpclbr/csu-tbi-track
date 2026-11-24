"""Phase F3: the public pages (no login) for participants. The rules are tested with the services; these tests check
that the pages call them, show their messages, apply the same rate limit and honeypot as the JSON endpoint, never show
who registered, and never show personal data on a refused or used evaluation link."""

import json
import re
from datetime import timedelta
from unittest import mock

import pytest
from django.urls import reverse
from django.utils import timezone

from activities.models import Activity, ParticipantCategory
from registrations import public_pages, services
from registrations.models import EvaluationSubmission, FormType, Registration

from .conftest import CERT, FIXED, evaluation_answers, field_id, form_of, registration_answers
from .conftest import SIGN_JSON, SIGN_POST

pytestmark = pytest.mark.django_db


def html(response) -> str:
    return response.content.decode()


def registration_post(activity, email: str = "ana@example.com", **extra) -> dict:
    """What the registration page's form posts (valid by default)."""
    form = form_of(activity)
    data = {
        "name": "Ana Cruz",
        "email": email,
        "contact_number": "09171234567",
        **FIXED,  # Designation / Position and Office / Agency (ASSUMPTIONS 426)
        f"field_{field_id(form, 'Affiliation / Organization')}": "CSU",
        services.HONEYPOT_FIELD: "",
        services.PRIVACY_CONSENT_FIELD: "yes", **SIGN_POST,  # the privacy notice checkbox (ASSUMPTIONS 394)
    }
    data.update(extra)
    return data


def page(activity) -> str:
    return reverse("register", args=[activity.pk])


# --- Open activities list --------------------------------------------------------------------------------------


def test_open_list_shows_open_activities_as_cards(client, scheduled, new_activity, signup) -> None:
    signup(scheduled, "bea.santos@example.com", name="Bea Santos")
    response = client.get(reverse("register_list"))
    text = html(response)
    assert response.status_code == 200
    assert scheduled.title in text and "Workshop" in text and "Incubatees" in text
    assert scheduled.venue in text and "8:00 AM – 5:00 PM" in text
    assert "29 slots left" in text and "Register by" in text
    assert page(scheduled) in text
    assert "Bea Santos" not in text and "bea.santos@example.com" not in text  # never who registered


def test_open_list_leaves_out_drafts_and_closed_activities(client, new_activity, valid_data, director) -> None:
    from activities import services as activity_services

    draft = activity_services.create_activity(director, valid_data(title="Secret Draft"))
    text = html(client.get(reverse("register_list")))
    assert draft.title not in text and new_activity.title not in text
    assert "No activities are open right now" in text  # the empty state


# --- Registration page ---------------------------------------------------------------------------------------


def test_registration_page_shows_the_activity_and_the_form(client, scheduled) -> None:
    response = client.get(page(scheduled))
    text = html(response)
    assert response.status_code == 200
    assert scheduled.title in text and scheduled.venue in text and "Slots left" in text and "Philippine time" in text
    assert 'name="name"' in text and 'name="email"' in text and 'name="contact_number"' in text
    assert "Affiliation / Organization" in text and "Data Privacy consent" not in text  # replaced by the fixed checkbox
    assert f'name="{services.PRIVACY_CONSENT_FIELD}"' in text
    assert "csrfmiddlewaretoken" in text and "novalidate" in text
    # One category: a single radio, already checked (its ID is sent; services.register would also pick it).
    incubatees = ParticipantCategory.objects.get(name="Incubatees")
    radios = re.findall(r'<input type="radio" name="participant_category"[^>]*>', text)
    assert len(radios) == 1 and f'value="{incubatees.pk}"' in radios[0] and " checked" in radios[0]


def test_honeypot_is_hidden_with_css_not_type_hidden(client, scheduled) -> None:
    text = html(client.get(page(scheduled)))
    field = re.search(r'<input[^>]*name="website"[^>]*>', text).group(0)
    assert 'type="text"' in field and 'tabindex="-1"' in field and 'autocomplete="off"' in field
    assert re.search(r'<div class="hp-field" aria-hidden="true">\s*<label for="id_website"', text)


def test_many_categories_are_a_required_choice(client, scheduled) -> None:
    scheduled.participant_categories.add(ParticipantCategory.objects.get(name="Faculty"))  # test setup only
    text = html(client.get(page(scheduled)))
    assert text.count('name="participant_category"') == 2
    response = client.post(page(scheduled), registration_post(scheduled))
    assert response.status_code == 400 and "Choose the category you are attending as." in html(response)
    faculty = ParticipantCategory.objects.get(name="Faculty")
    response = client.post(page(scheduled), registration_post(scheduled, participant_category=str(faculty.pk)))
    assert response.status_code == 302
    assert Registration.objects.get().participant_category == faculty


def test_successful_registration_shows_the_success_page(client, scheduled) -> None:
    response = client.post(page(scheduled), registration_post(scheduled))
    assert response.status_code == 302 and response["Location"] == reverse("register_done", args=[scheduled.pk])
    registration = Registration.objects.get()
    assert registration.email == "ana@example.com" and registration.answers.count() == 1

    done = client.get(response["Location"])
    text = html(done)
    assert done.status_code == 200 and "You're registered!" in text
    assert "A confirmation email is on its way" in text and scheduled.title in text
    assert reverse("register_calendar", args=[scheduled.pk]) in text
    assert "Ana Cruz" not in text and "ana@example.com" not in text


def test_success_page_only_for_the_browser_that_registered(client, scheduled) -> None:
    response = client.get(reverse("register_done", args=[scheduled.pk]))
    assert response.status_code == 302 and response["Location"] == page(scheduled)


def test_field_errors_are_shown_under_their_fields_with_the_values_kept(client, scheduled) -> None:
    form = form_of(scheduled)
    affiliation = f"field_{field_id(form, 'Affiliation / Organization')}"
    data = registration_post(scheduled, name="  ", email="not-an-email", contact_number="09179998888", **{affiliation: ""})
    response = client.post(page(scheduled), data)
    text = html(response)
    assert response.status_code == 400 and not Registration.objects.exists()
    assert 'id="id_name-error"' in text and 'id="id_email-error"' in text and "Enter a valid email address." in text
    assert f'id="answer-{field_id(form, "Affiliation / Organization")}-error"' in text and "This field is required." in text
    assert 'value="not-an-email"' in text and 'value="09179998888"' in text  # values kept
    assert "Please check the 3 fields marked below." in text  # name, email, the form's field (the fixed two are filled)
    # Only the first field with an error gets the focus.
    assert text.count("autofocus") == 1 and re.search(r'<input id="id_name"[^>]*autofocus', text)


def test_duplicate_email_shows_the_service_message(client, scheduled, signup) -> None:
    signup(scheduled, "ana@example.com")
    response = client.post(page(scheduled), registration_post(scheduled, email="ANA@example.com"))
    assert response.status_code == 400 and "This email is already registered for this activity." in html(response)
    assert Registration.objects.count() == 1


def test_full_activity_shows_the_closed_card(client, scheduled, signup) -> None:
    signup(scheduled, "first@example.com")
    Activity.objects.filter(pk=scheduled.pk).update(max_slots=1)  # test setup only
    for response in (client.get(page(scheduled)), client.post(page(scheduled), registration_post(scheduled))):
        text = html(response)
        assert "Registration is closed: all slots are taken." in text and 'name="email"' not in text
        assert reverse("register_list") in text and scheduled.title in text
    assert Registration.objects.count() == 1


def test_deadline_passed_shows_the_closed_card(client, scheduled) -> None:
    Activity.objects.filter(pk=scheduled.pk).update(registration_deadline=timezone.now() - timedelta(hours=1))
    response = client.post(page(scheduled), registration_post(scheduled))
    assert "Registration is closed: the deadline" in html(response) and not Registration.objects.exists()


def test_scheduled_without_a_form_shows_why(client, new_activity, move_to) -> None:
    move_to(new_activity, "Scheduled")
    text = html(client.get(page(new_activity)))
    assert services.REGISTRATION_FORM_NOT_READY in text and 'name="email"' not in text


def test_draft_or_unknown_activity_is_not_available(client, new_activity) -> None:
    for url in (page(new_activity), reverse("register", args=[999999]), reverse("register_done", args=[new_activity.pk])):
        response = client.get(url)
        text = html(response)
        assert response.status_code == 404 and "This activity is not available" in text
        assert new_activity.title not in text and reverse("register_list") in text
    assert client.post(page(new_activity), {"email": "a@example.com"}).status_code == 404


@pytest.mark.parametrize("status", ["Ongoing", "Conducted"])
def test_ongoing_or_conducted_shows_title_dates_venue_and_closed(client, scheduled, move_to, status) -> None:
    from activities import services as activity_services

    director = scheduled.created_by
    activity_services.change_status(director, scheduled, "Ongoing")
    if status == "Conducted":
        activity_services.change_status(director, scheduled, "Conducted")
    response = client.get(page(scheduled))
    text = html(response)
    assert response.status_code == 200
    assert scheduled.title in text and scheduled.venue in text and "Date" in text
    assert services.REGISTRATION_ENDED in text and reverse("register_list") in text
    assert 'name="email"' not in text and "Slots left" not in text and scheduled.description not in text
    assert client.post(page(scheduled), registration_post(scheduled)).status_code == 200
    assert not Registration.objects.exists()


def test_pages_never_show_who_registered(client, scheduled, signup) -> None:
    signup(scheduled, "bea.santos@example.com", name="Bea Santos")
    for url in (page(scheduled), reverse("register_list"), reverse("register_calendar", args=[scheduled.pk])):
        text = html(client.get(url))
        assert "Bea Santos" not in text and "bea.santos@example.com" not in text


# --- Rate limit and honeypot: the same shared check as the JSON endpoint -----------------------------------


@pytest.fixture
def small_limits(settings):
    settings.REGISTRATION_MAX_ATTEMPTS_PER_IP = 3
    settings.REGISTRATION_MAX_ATTEMPTS_PER_EMAIL = 2
    settings.REGISTRATION_RATE_WINDOW_MINUTES = 60


def api_post(client, activity, email: str, ip: str = "203.0.113.5"):
    body = {"name": "Ana Cruz", "email": email, **FIXED, "answers": registration_answers(activity),
            services.PRIVACY_CONSENT_FIELD: True, "signature": SIGN_JSON}
    return client.post(f"/api/register/{activity.pk}/", json.dumps(body), content_type="application/json", REMOTE_ADDR=ip)


def test_html_post_is_rate_limited_per_email(client, scheduled, small_limits) -> None:
    assert client.post(page(scheduled), registration_post(scheduled)).status_code == 302
    assert client.post(page(scheduled), registration_post(scheduled)).status_code == 400  # already registered, counts
    response = client.post(page(scheduled), registration_post(scheduled))
    assert response.status_code == 429
    assert "Too many registration attempts. Please try again in 60 minutes." in html(response)


def test_html_post_is_rate_limited_per_ip(client, scheduled, small_limits) -> None:
    for number in range(3):
        assert client.post(page(scheduled), registration_post(scheduled, f"p{number}@example.com"), REMOTE_ADDR="203.0.113.9").status_code == 302
    response = client.post(page(scheduled), registration_post(scheduled, "p9@example.com"), REMOTE_ADDR="203.0.113.9")
    assert response.status_code == 429 and Registration.objects.count() == 3
    # Another address is not blocked.
    assert client.post(page(scheduled), registration_post(scheduled, "p9@example.com"), REMOTE_ADDR="198.51.100.7").status_code == 302


def test_html_and_json_share_one_counter(client, scheduled, small_limits) -> None:
    assert api_post(client, scheduled, "ana@example.com").status_code == 201
    assert api_post(client, scheduled, "ana@example.com").status_code == 400
    assert client.post(page(scheduled), registration_post(scheduled, "ana@example.com")).status_code == 429


def test_json_attempts_count_after_html_ones(client, scheduled, small_limits) -> None:
    for number in range(3):
        client.post(page(scheduled), registration_post(scheduled, f"p{number}@example.com"), REMOTE_ADDR="203.0.113.5")
    assert api_post(client, scheduled, "new@example.com").status_code == 429


def test_honeypot_filled_in_is_refused_on_the_page(client, scheduled) -> None:
    response = client.post(page(scheduled), registration_post(scheduled, **{services.HONEYPOT_FIELD: "http://spam.example"}))
    assert response.status_code == 400 and services.HONEYPOT_MESSAGE in html(response)
    assert not Registration.objects.exists()


@pytest.mark.parametrize("endpoint", ["html", "json"])
def test_both_endpoints_use_client_ip(client, scheduled, settings, endpoint) -> None:
    """The guard gets the address from config.network.client_ip (here the trusted proxy header)."""
    settings.TRUSTED_PROXY_HEADER = "X-Forwarded-For"
    with mock.patch.object(services, "guard_public_registration", wraps=services.guard_public_registration) as guard:
        if endpoint == "html":
            client.post(page(scheduled), registration_post(scheduled), REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.77")
        else:
            body = {"name": "Ana Cruz", "email": "ana@example.com", **FIXED, "answers": registration_answers(scheduled),
                    "website": ""}
            client.post(f"/api/register/{scheduled.pk}/", json.dumps(body), content_type="application/json",
                        REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.77")
    guard.assert_called_once_with("203.0.113.77", "ana@example.com", "")


# --- Add to calendar (.ics) -------------------------------------------------------------------------------------


def test_calendar_file_has_the_activity_and_no_personal_data(client, scheduled, signup) -> None:
    Activity.objects.filter(pk=scheduled.pk).update(title="Pitching, Funding; and You", venue="CSU Hall, Room 2")
    scheduled.refresh_from_db()
    signup(scheduled, "bea.santos@example.com", name="Bea Santos")
    response = client.get(reverse("register_calendar", args=[scheduled.pk]))
    assert response.status_code == 200 and response["Content-Type"] == "text/calendar; charset=utf-8"
    assert response["Content-Disposition"] == f'attachment; filename="tbi-activity-{scheduled.pk}.ics"'
    body = response.content.decode()
    assert body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and body.endswith("END:VCALENDAR\r\n")
    unfolded = body.replace("\r\n ", "")
    # 8:00 AM to 5:00 PM Manila = 00:00 to 09:00 UTC (a timed event; all-day only without times)
    assert f"DTSTART:{scheduled.start_date:%Y%m%d}T000000Z" in unfolded
    assert f"DTEND:{scheduled.end_date:%Y%m%d}T090000Z" in unfolded
    assert "SUMMARY:Pitching\\, Funding\\; and You" in unfolded and "LOCATION:CSU Hall\\, Room 2" in unfolded
    assert f"UID:activity-{scheduled.pk}@127.0.0.1" in unfolded and "Time: 8:00 AM – 5:00 PM" in unfolded
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert "Bea Santos" not in unfolded and "bea.santos" not in unfolded


def test_calendar_file_of_a_draft_is_not_available(client, new_activity) -> None:
    assert client.get(reverse("register_calendar", args=[new_activity.pk])).status_code == 404


def test_ics_text_escaping_and_folding() -> None:
    assert public_pages._ics_text("a\\b;c,d\ne") == "a\\\\b\\;c\\,d\\ne"
    folded = public_pages._fold("X" * 200)
    assert all(len(part) <= 75 for part in folded.split("\r\n")) and folded.replace("\r\n ", "") == "X" * 200


# --- Evaluation page -------------------------------------------------------------------------------------------


def open_window(activity) -> None:
    """Test setup: the activity ended today and its evaluation is open for a week."""
    today = timezone.localdate()
    Activity.objects.filter(pk=activity.pk).update(start_date=today, end_date=today, evaluation_deadline=timezone.now() + timedelta(days=7))


def close_window(activity) -> None:
    day = timezone.localdate() - timedelta(days=10)
    Activity.objects.filter(pk=activity.pk).update(start_date=day, end_date=day, evaluation_deadline=timezone.now() - timedelta(hours=1))


def eval_url(person) -> str:
    return reverse("evaluate", args=[services.evaluation_token(person)])


def evaluation_post(activity, step: str = "review", **extra) -> dict:
    data = {"step": step, "comments": "Great.", services.PRIVACY_CONSENT_FIELD: "yes", **SIGN_POST, **CERT}
    for key, value in evaluation_answers(activity).items():
        data[f"field_{key}"] = str(value)
    data.update(extra)
    return data


def assert_private(response) -> None:
    assert response["Referrer-Policy"] == "same-origin"  # not no-referrer: browsers would send Origin: null and CSRF fails
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in response["Cache-Control"]
    text = html(response)
    assert '<meta name="referrer" content="same-origin">' in text and '<meta name="robots" content="noindex">' in text


def assert_no_person(response, person) -> None:
    text = html(response)
    assert person.name not in text and person.email not in text
    assert CERT["certificate_name"] not in text and CERT["certificate_email"] not in text


@pytest.fixture
def person(attended, scheduled):
    """One attended person; the evaluation is open now."""
    attendee = attended(1)[0]
    open_window(scheduled)
    return attendee


def test_evaluation_form_is_prefilled_and_private(client, person, scheduled) -> None:
    response = client.get(eval_url(person))
    text = html(response)
    assert response.status_code == 200
    assert_private(response)
    assert scheduled.title in text
    assert re.search(r'name="certificate_name"[^>]*value="Ana Cruz"', text)
    assert re.search(r'name="certificate_email"[^>]*value="person0@example.com"', text)
    assert "Overall rating" in text and "Key learnings" in text and 'name="comments"' in text
    assert text.index('name="certificate_name"') < text.index("Overall rating") < text.index('name="comments"')


@pytest.mark.parametrize("token", ["not-a-real-token", "1:tampered"])
def test_invalid_link_shows_a_card(client, person, token) -> None:
    response = client.get(reverse("evaluate", args=[token]))
    assert response.status_code == 404 and "This evaluation link is not valid." in html(response)
    assert_private(response)
    assert_no_person(response, person)


def test_not_attended_shows_a_card_without_the_name(client, scheduled, signup, director) -> None:
    from activities import services as activity_services

    absent = signup(scheduled, "absent@example.com", name="Carlo Reyes")
    activity_services.change_status(director, scheduled, "Ongoing")
    open_window(scheduled)
    response = client.get(eval_url(absent))
    text = html(response)
    assert response.status_code == 200 and services.NOT_ATTENDED in text
    assert "Carlo Reyes" not in text and "absent@example.com" not in text and 'name="certificate_name"' not in text


def test_not_yet_open_shows_the_opening_date(client, attended, scheduled) -> None:
    attendee = attended(1)[0]  # the activity ends in about a month
    response = client.get(eval_url(attendee))
    text = html(response)
    assert "The evaluation is not open yet" in text and "The evaluation opens on" in text
    assert f"{scheduled.end_date:%b} {scheduled.end_date.day}, {scheduled.end_date.year} 12:00 AM" in text
    assert_no_person(response, attendee)


def test_closed_shows_a_card_without_personal_data(client, attended, scheduled) -> None:
    attendee = attended(1)[0]
    close_window(scheduled)
    response = client.get(eval_url(attendee))
    assert "The evaluation closed on" in html(response)
    assert_no_person(response, attendee)
    assert client.post(eval_url(attendee), evaluation_post(scheduled, step="confirm")).status_code == 200
    assert not EvaluationSubmission.objects.exists()


def test_no_evaluation_form_shows_a_card(client, person, scheduled) -> None:
    form_of(scheduled, FormType.EVALUATION).delete()
    response = client.get(eval_url(person))
    assert services.EVALUATION_FORM_NOT_READY in html(response) and 'name="certificate_name"' not in html(response)


def test_review_step_shows_name_email_and_answers_and_saves_nothing(client, person, scheduled) -> None:
    response = client.post(eval_url(person), evaluation_post(scheduled, certificate_name="  Ana  Dela Cruz ", certificate_email=" Ana.Cert@Example.com "))
    text = html(response)
    assert response.status_code == 200 and not EvaluationSubmission.objects.exists()
    assert_private(response)
    assert "Your certificate will be issued to:" in text and "It will be sent to:" in text
    assert re.search(r'id="review-name">Ana  Dela Cruz</p>', text)  # trimmed, capitals kept
    assert re.search(r'id="review-email">ana.cert@example.com</p>', text)  # as it will be stored
    assert "5 out of 5" in text and "How to pitch." in text and "Suggestions" in text and "No answer" in text
    assert 'name="step" value="edit"' in text and 'name="step" value="confirm"' in text
    assert 'type="hidden" name="certificate_name" value="  Ana  Dela Cruz "' in text


def test_review_step_with_errors_goes_back_to_the_form(client, person, scheduled) -> None:
    rating = field_id(form_of(scheduled, FormType.EVALUATION), "Overall rating")
    response = client.post(eval_url(person), evaluation_post(scheduled, certificate_name="", **{f"field_{rating}": "9"}))
    text = html(response)
    assert response.status_code == 400 and not EvaluationSubmission.objects.exists()
    assert "Enter the name to print on your certificate." in text and "Choose a rating from 1 to 5." in text
    assert "Your certificate will be issued to:" not in text
    assert re.search(r'<input id="id_certificate_name"[^>]*autofocus', text) and text.count("autofocus") == 1


def test_edit_goes_back_to_the_form_with_the_values_kept(client, person, scheduled) -> None:
    rating = field_id(form_of(scheduled, FormType.EVALUATION), "Overall rating")
    response = client.post(eval_url(person), evaluation_post(scheduled, step="edit", certificate_name="Ana D. Cruz"))
    text = html(response)
    assert response.status_code == 200 and not EvaluationSubmission.objects.exists()
    assert re.search(r'name="certificate_name"[^>]*value="Ana D. Cruz"', text)
    assert re.search(rf'name="field_{rating}" value="5"[^>]*checked', text) and "Great.</textarea>" in text


def test_final_submit_validates_again(client, person, scheduled) -> None:
    """The review page's hidden values can be changed before "Confirm and submit": the server checks them again."""
    rating = field_id(form_of(scheduled, FormType.EVALUATION), "Overall rating")
    for changed in ({"certificate_email": "not-an-email"}, {f"field_{rating}": "9"}, {"certificate_name": " "}):
        response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm", **changed))
        assert response.status_code == 400 and 'name="step" value="review"' in html(response)
    assert not EvaluationSubmission.objects.exists()


def test_confirm_submits_and_thanks_without_personal_data(client, person, scheduled) -> None:
    response = client.post(eval_url(person), evaluation_post(scheduled, step="confirm"))
    token = services.evaluation_token(person)
    assert response.status_code == 302 and response["Location"] == reverse("evaluate_done", args=[token])
    submission = EvaluationSubmission.objects.get()
    assert submission.certificate_name == CERT["certificate_name"] and submission.comments == "Great."

    thanks = client.get(response["Location"])
    assert thanks.status_code == 200 and "Thank you!" in html(thanks) and "once the TBI has approved it" in html(thanks)
    assert_private(thanks)
    assert_no_person(thanks, person)

    again = client.get(eval_url(person))  # the link after submitting: only the reason
    assert services.ALREADY_SUBMITTED in html(again) and 'name="certificate_name"' not in html(again)
    assert_no_person(again, person)
    assert client.post(eval_url(person), evaluation_post(scheduled, step="confirm")).status_code == 200
    assert EvaluationSubmission.objects.count() == 1


def test_thanks_page_before_submitting_goes_to_the_form(client, person) -> None:
    token = services.evaluation_token(person)
    response = client.get(reverse("evaluate_done", args=[token]))
    assert response.status_code == 302 and response["Location"] == reverse("evaluate", args=[token])
    assert client.get(reverse("evaluate_done", args=["bad-token"])).status_code == 404


def test_public_pages_without_a_token_keep_the_default_referrer_policy(client, scheduled) -> None:
    response = client.get(page(scheduled))
    # Every response says noindex since ASSUMPTIONS 392 (config.headers.NoIndexMiddleware); no token-page headers here.
    assert response["Referrer-Policy"] == "same-origin" and response["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" not in response.get("Cache-Control", "")
    assert '<meta name="robots" content="noindex">' in html(response)


def test_public_pages_load_only_self_hosted_files(client, scheduled, person) -> None:
    for url in (reverse("register_list"), page(scheduled), eval_url(person)):
        text = html(client.get(url))
        for src in re.findall(r'(?:src|href)="([^"]+)"', text):
            assert not src.startswith(("http://", "https://", "//")) or src.startswith("mailto:"), src
