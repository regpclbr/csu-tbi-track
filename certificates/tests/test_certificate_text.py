"""The editable certificate text (ASSUMPTIONS 370): placeholders, optional [ ... ] parts, the checks (unknown
placeholder, {topic} on Participation, braces, brackets, one line, length limits, required), escaping, the default
wording (the text printed before, unchanged), custom text per activity, Issued certificates keep their text, the approval
is cancelled by a change (ASSUMPTIONS 369), permissions, the live preview, the pages and migration 0013."""

import html

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from activities.models import Activity, ActivityAuditLog
from certificates import pdf, services, wording
from certificates.models import Certificate, CertificateText
from conftest import VIEW_ONLY_ROLE

from .conftest import S, appreciation_of, certificate_of

pytestmark = pytest.mark.django_db

P, A = CertificateText.CertificateType.PARTICIPATION, CertificateText.CertificateType.APPRECIATION
VALUES = {"name": "Ana Cruz", "activity": "Pitch Day", "date": "November 5, 2026", "venue": "CSU Hall",
          "topic": "Pitch Decks", "given_date": "November 5, 2026", "organization": "CSU-TBI"}


def text(**changes) -> dict:
    return {**wording.DEFAULT_WORDING[P], **changes}


def visible(markup: str) -> str:
    """The text of the printed HTML, entities resolved and spaces collapsed."""
    import re

    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", markup)).split())


def approved(activity) -> bool:
    return services.is_approved(Activity.objects.get(pk=activity.pk))


# --- Filling --------------------------------------------------------------------------------------------------------


def test_every_placeholder_is_filled() -> None:
    template = "{name} | {activity} | {date} | {venue} | {topic} | {given_date} | {organization}"
    assert wording.fill(template, VALUES) == ("Ana Cruz | Pitch Day | November 5, 2026 | CSU Hall | Pitch Decks | "
                                              "November 5, 2026 | CSU-TBI")


def test_an_optional_part_prints_only_when_its_placeholders_have_values() -> None:
    sentence = "held on {date}[ at {venue}]."
    assert wording.fill(sentence, VALUES) == "held on November 5, 2026 at CSU Hall."
    assert wording.fill(sentence, {**VALUES, "venue": ""}) == "held on November 5, 2026."
    assert wording.fill("as resource person[ on “{topic}”] during", {**VALUES, "topic": ""}) == (
        "as resource person during")


# --- Checks ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("field", "value", "message"), [
    ("main_sentence", "held at {place}.", "Unknown placeholder {place}. You can use: {name}, {activity}"),
    ("main_sentence", "on {topic}", "{topic} can only be used on a Certificate of Appreciation."),
    ("main_sentence", "held on {date", "Use { and } only around a placeholder"),
    ("main_sentence", "a } here", "Use { and } only around a placeholder"),
    ("main_sentence", "[a [b {venue}]]", "Square brackets cannot be inside other square brackets."),
    ("main_sentence", "held[ at {venue}", "A [ is not closed with ]."),
    ("main_sentence", "held at {venue}]", "A ] has no [ before it."),
    ("main_sentence", "two\nlines", "Write the main sentence on one line."),
    ("main_sentence", "x" * 401, "The main sentence can be at most 400 characters (now 401)."),
    ("title", "T" * 61, "The title can be at most 60 characters (now 61)."),
    ("line_before_name", "L" * 101, "The line before the name can be at most 100 characters (now 101)."),
    ("closing_line", "C" * 201, "The closing line can be at most 200 characters (now 201)."),
    ("title", "", "Enter the title."),
    ("main_sentence", "", "Enter the main sentence."),
])
def test_refused_text(field, value, message) -> None:
    assert wording.check_field(field, value, P).startswith(message)


def test_optional_fields_may_be_empty_and_topic_is_fine_on_appreciation() -> None:
    assert wording.check_field("line_before_name", "", P) is None
    assert wording.check_field("closing_line", "", P) is None
    assert wording.check_field("main_sentence", "on {topic}", A) is None


def test_the_service_refuses_with_the_field_names(director) -> None:
    with pytest.raises(ValidationError) as error:
        services.save_default_text(director, P, text(main_sentence="at {place}", title=""))
    assert set(error.value.message_dict) == {"participation-main_sentence", "participation-title"}


# --- The default wording -----------------------------------------------------------------------------------------


def test_the_default_wording_prints_exactly_the_old_text(generated) -> None:
    activity, people = generated
    participant = certificate_of(people[0])
    assert participant.certificate_text.activity_id is None and participant.certificate_text.certificate_type == P
    printed = visible(pdf.certificate_html(participant, name="Ana Cruz"))
    dates = pdf._dates(activity.start_date, activity.end_date)
    assert ("Certificate of Participation is presented to Ana Cruz for participating in “"
            f"{activity.title}” held on {dates} at {activity.venue}. Given on {pdf.long_date(activity.end_date)} at "
            "CSU Technology Business Incubator.") in printed
    speaker = appreciation_of(activity)
    printed = visible(pdf.certificate_html(speaker, name="Dr. Maria Santos"))
    assert "Certificate of Appreciation is presented to Dr. Maria Santos in grateful recognition of their valuable " \
           "contribution as resource person" in printed


def test_typed_html_is_printed_as_text(generated, director) -> None:
    activity, people = generated
    services.save_activity_text(director, activity, {P: text(main_sentence="<b>Thank you</b> for {activity}.")})
    markup = pdf.certificate_html(certificate_of(people[0]), name="Ana")
    assert "&lt;b&gt;Thank you&lt;/b&gt;" in markup and "<b>Thank you</b>" not in markup


def test_a_missing_default_row_is_created_from_the_old_wording() -> None:
    CertificateText.objects.filter(activity__isnull=True).update(is_current=False)
    row = wording.default_text(P)
    assert row.is_current and wording.fill_all(row, VALUES).title == "Certificate of Participation"


# --- Custom text per activity -----------------------------------------------------------------------------------


def test_custom_text_for_one_activity(generated, director, valid_data) -> None:
    from activities import services as activity_services

    activity, people = generated
    other = activity_services.create_activity(director, valid_data())
    result = services.save_activity_text(director, activity, {P: text(title="Certificate of Completion")})
    assert result == {"changed": True, "updated": 2, "cancelled": 0}
    row = certificate_of(people[0]).certificate_text
    assert row.activity_id == activity.pk and row.title == "Certificate of Completion"
    assert appreciation_of(activity).certificate_text.activity_id is None  # the other type stays on the default
    assert services.current_texts(other)[P].activity_id is None  # another activity keeps the default
    assert services.uses_custom_text(activity)
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate text").get()
    assert (row.old_value, row.new_value) == ("Default text", "Custom text (Participation)")


def test_the_same_words_change_nothing(generated, director) -> None:
    activity, _ = generated
    assert services.save_activity_text(director, activity, {P: text(), A: wording.DEFAULT_WORDING[A]})["changed"] is False
    assert not services.uses_custom_text(activity)


def test_back_to_the_default_text(generated, director) -> None:
    activity, people = generated
    services.save_activity_text(director, activity, {P: text(title="Certificate of Completion")})
    assert services.use_default_text(director, activity)["updated"] == 2
    assert certificate_of(people[0]).certificate_text.activity_id is None
    assert not services.uses_custom_text(activity)
    assert services.use_default_text(director, activity)["changed"] is False


def test_issued_certificates_keep_their_text(generated, director, approve_all, evaluate, run_tasks,
                                             django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    with django_capture_on_commit_callbacks(execute=True):
        evaluate(people[0])
        approve_all(activity)
    issued = certificate_of(people[0])
    assert issued.status == S.ISSUED
    services.save_activity_text(director, activity, {P: text(title="Certificate of Completion")})
    assert certificate_of(people[0]).certificate_text_id == issued.certificate_text_id
    assert certificate_of(people[1]).certificate_text.title == "Certificate of Completion"
    assert services.outdated_ids(activity) == set()  # text alone never makes it "Issued with an old signature"


def test_changing_the_text_cancels_the_approval(generated, director) -> None:
    activity, _ = generated
    services.approve_activity_certificates(director, activity)
    result = services.save_activity_text(director, activity, {A: {**wording.DEFAULT_WORDING[A], "title": "Thanks"}})
    assert result["cancelled"] == 1 and not approved(activity)
    assert appreciation_of(activity).status == S.GENERATED
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Certificate approval").order_by("pk").last()
    assert row.new_value == "Needs approval (cancelled: certificate text changed)"


def test_new_certificates_get_the_activity_text(conducted, design, director) -> None:
    activity, people = conducted
    services.save_activity_text(director, activity, {P: text(title="Certificate of Completion")})
    services.generate_missing_certificates(activity)
    assert certificate_of(people[0]).certificate_text.title == "Certificate of Completion"


# --- The default text page's service -------------------------------------------------------------------------------


def test_changing_the_default_updates_activities_on_the_default(generated, director) -> None:
    activity, people = generated
    services.approve_activity_certificates(director, activity)
    impact = services.default_text_impact(P)
    assert impact == {"activities": 1, "certificates": 2, "approved": 1, "early": 0}
    assert "the approval of 1 activity is cancelled" in services.default_text_impact_text(impact)
    result = services.save_default_text(director, P, text(closing_line="Given on {given_date}."))
    assert result == {"changed": True, "activities": 1, "updated": 2, "cancelled": 1}
    assert certificate_of(people[0]).certificate_text.closing_line == "Given on {given_date}."
    assert not approved(activity)
    assert CertificateText.objects.filter(certificate_type=P, activity__isnull=True, is_current=True).count() == 1


def test_an_activity_with_custom_text_keeps_it_when_the_default_changes(generated, director) -> None:
    activity, people = generated
    services.save_activity_text(director, activity, {P: text(title="Certificate of Completion")})
    services.approve_activity_certificates(director, activity)
    assert services.save_default_text(director, P, text(title="New default"))["activities"] == 0
    assert certificate_of(people[0]).certificate_text.title == "Certificate of Completion" and approved(activity)


def test_unchanged_default_saves_nothing(director) -> None:
    assert services.save_default_text(director, P, text())["changed"] is False


# --- Permissions ---------------------------------------------------------------------------------------------------


def test_view_only_members_change_no_text(generated, view_only) -> None:
    activity, _ = generated
    with pytest.raises(PermissionDenied):
        services.save_activity_text(view_only, activity, {P: text(title="X")})
    with pytest.raises(PermissionDenied):
        services.use_default_text(view_only, activity)
    with pytest.raises(PermissionDenied):
        services.save_default_text(view_only, P, text(title="X"))


def test_a_creator_without_the_approval_flag_edits_activity_text_but_not_the_default(generated, make_member,
                                                                                   roles) -> None:
    activity, _ = generated
    roles[VIEW_ONLY_ROLE].can_create_activity = True
    roles[VIEW_ONLY_ROLE].save(update_fields=["can_create_activity"])
    creator = make_member(VIEW_ONLY_ROLE, email="creator.text@example.com")
    assert services.save_activity_text(creator, activity, {P: text(title="Certificate of Completion")})["changed"]
    with pytest.raises(PermissionDenied):
        services.save_default_text(creator, P, text(title="X"))


# --- Preview and pages ------------------------------------------------------------------------------------------


def test_the_preview_fills_the_activity_values_or_shows_the_errors(generated) -> None:
    activity, people = generated
    preview = services.wording_preview(activity, P, text(main_sentence="Thanks, {name}, for {activity}."))
    assert preview["errors"] == {} and preview["text"].main_sentence == f"Thanks, {people[0].name}, for {activity.title}."
    preview = services.wording_preview(activity, P, text(main_sentence="at {place}"))
    assert preview["text"] is None and "Unknown placeholder {place}" in preview["errors"]["main_sentence"]
    sample = services.wording_preview(None, A, wording.DEFAULT_WORDING[A])
    assert "Building a Startup Pitch" in sample["text"].main_sentence


def test_the_tab_shows_the_text_section_and_saves(generated, director) -> None:
    activity, people = generated
    client = Client()
    client.force_login(director)
    page = client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=certificates").content.decode()
    assert "Certificate text" in page and "Use the default text" in page and "{given_date}" in page
    data = {"mode": "custom", **{f"{P}-{k}": v for k, v in text(title="Certificate of Completion").items()},
            **{f"{A}-{k}": v for k, v in wording.DEFAULT_WORDING[A].items()}}
    response = client.post(reverse("activities:certificates_text", args=[activity.pk]), data)
    assert response.status_code == 302
    assert certificate_of(people[0]).certificate_text.title == "Certificate of Completion"


def test_a_refused_text_shows_the_error_under_the_field(generated, director) -> None:
    activity, _ = generated
    client = Client()
    client.force_login(director)
    data = {"mode": "custom", **{f"{P}-{k}": v for k, v in text(main_sentence="at {place}").items()}}
    response = client.post(reverse("activities:certificates_text", args=[activity.pk]), data)
    assert response.status_code == 400
    page = response.content.decode()
    assert "Unknown placeholder {place}" in page and 'value="at {place}"' not in page and "at {place}</textarea>" in page


def test_the_preview_endpoint(generated, director) -> None:
    activity, _ = generated
    client = Client()
    client.force_login(director)
    data = {"type": P, **{f"{P}-{k}": v for k, v in text(title="<i>Hello</i>").items()}}
    page = client.post(reverse("activities:certificates_text_preview", args=[activity.pk]), data).content.decode()
    assert "&lt;i&gt;Hello&lt;/i&gt;" in page


def test_view_only_members_see_the_text_read_only(generated, view_only) -> None:
    activity, _ = generated
    client = Client()
    client.force_login(view_only)
    page = client.get(reverse("activities:detail", args=[activity.pk]) + "?tab=certificates").content.decode()
    assert "This activity uses the default text." in page and "Save the certificate text" not in page
    assert client.post(reverse("activities:certificates_text", args=[activity.pk]), {"mode": "default"}).status_code == 403
    assert client.get(reverse("certificates:text")).status_code == 403


def test_the_default_text_page(director) -> None:
    client = Client()
    client.force_login(director)
    page = client.get(reverse("certificates:text")).content.decode()
    assert "Certificate of Participation" in page and "Save the default text" in page
    data = {f"{P}-{k}": v for k, v in text(title="Certificate of Attendance").items()}
    assert client.post(reverse("certificates:text_save", args=[P]), data).status_code == 302
    assert wording.default_text(P).title == "Certificate of Attendance"
    data[f"{P}-title"] = ""
    response = client.post(reverse("certificates:text_save", args=[P]), data)
    assert response.status_code == 400 and "Enter the title." in response.content.decode()


# --- Migration 0013 ------------------------------------------------------------------------------------------------


def migrate(targets) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


@pytest.mark.django_db(transaction=True)
def test_migration_gives_every_certificate_the_default_and_approves_nothing(generated, capsys) -> None:
    activity, people = generated
    latest = list(MigrationExecutor(connection).loader.graph.leaf_nodes())  # every app: going back also unapplies approvals / notifications (ASSUMPTIONS 437)
    try:
        migrate([("certificates", "0012_certificate_text")])
        migrate([("certificates", "0013_certificate_text_data")])
    finally:
        migrate(latest)
    assert "2 Participation and 1 Appreciation got the default wording" in capsys.readouterr().out
    for certificate in Certificate.objects.all():
        assert certificate.certificate_text.activity_id is None
        assert certificate.certificate_text.certificate_type == certificate.text_type
    assert not approved(activity)
