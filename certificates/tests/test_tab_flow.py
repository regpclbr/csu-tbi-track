"""The Certificates tab after the automatic flow (ASSUMPTIONS 362): every action ends on /activities/<id>/?tab=certificates
and every tab still opens afterwards (A1), the preview starts at the first row and uses the list's order and printed
names (A3), the Issued copy and the cache-busting version (A2), and who sees which approve buttons."""

import re

import pytest
from django.test import Client
from django.urls import reverse

from certificates import services
from certificates.models import Certificate, CertificateSignatory
from certificates.pages import tab_url

from .conftest import S, appreciation_of, certificate_of, upload
from conftest import image_bytes
from conftest import VIEW_ONLY_ROLE

pytestmark = pytest.mark.django_db

TABS = ["overview", "history", "forms", "registrations", "evaluation", "documents", "certificates"]


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


def detail(activity) -> str:
    return reverse("activities:detail", args=[activity.pk])


def tab_html(client, activity, **query) -> str:
    extra = "".join(f"&{key}={value}" for key, value in query.items())
    response = client.get(f"{detail(activity)}?tab=certificates{extra}")
    assert response.status_code == 200
    return response.content.decode()


def assert_every_tab_opens(client, activity) -> None:
    for tab in TABS:
        assert client.get(f"{detail(activity)}?tab={tab}").status_code == 200, tab
        assert client.get(f"{detail(activity)}?tab={tab}", HTTP_HX_REQUEST="true").status_code == 200, tab


# --- A1: every action ends on the activity's Certificates tab --------------------------------------------------------


def certificate_posts(activity, people) -> list[tuple[str, dict]]:
    zero = certificate_of(people[0])
    return [
        (reverse("activities:certificates_approve_activity", args=[activity.pk]), {}),
        (reverse("activities:certificates_approve_activity", args=[activity.pk]), {}),  # refused: already approved
        (reverse("activities:certificates_text", args=[activity.pk]), {"mode": "default"}),
        (reverse("activities:certificates_apply_design", args=[activity.pk]), {}),
        (reverse("activities:certificates_action", args=[activity.pk]), {"action": "resend", "certificate_ids": [zero.pk]}),
        (reverse("activities:certificates_action", args=[activity.pk]), {"action": "hand_over", "certificate_ids": [zero.pk]}),
        (reverse("activities:certificates_signature", args=[activity.pk, "vp"]),
         {"file": upload("vp.png", image_bytes(40, 20))}),
        (reverse("activities:certificates_signature", args=[activity.pk, "director"]), {}),  # refused: no file
    ]


def test_every_certificate_action_redirects_to_the_tab_and_every_tab_opens_after_it(generated, director) -> None:
    activity, people = generated
    client = client_for(director)
    for url, data in certificate_posts(activity, people):
        response = client.post(url, data)
        assert response.status_code == 302, url
        assert response["Location"].startswith(f"{detail(activity)}?tab=certificates"), url
        assert_every_tab_opens(client, activity)


def test_a_refused_row_action_shows_its_list_once(generated, director) -> None:
    activity, people = generated
    client = client_for(director)
    response = client.post(reverse("activities:certificates_action", args=[activity.pk]),
                           {"action": "hand_over", "certificate_ids": [certificate_of(people[0]).pk]}, follow=True)
    assert response.redirect_chain[-1][0] == tab_url(activity.pk)
    html = response.content.decode()
    assert "These certificates were refused" in html and certificate_of(people[0]).certificate_number in html
    assert "These certificates were refused" not in tab_html(client, activity)  # shown once


def test_get_on_an_action_url_goes_to_the_tab(generated, director) -> None:
    activity, people = generated
    client = client_for(director)
    for url, _data in certificate_posts(activity, people):
        response = client.get(url)
        assert response.status_code == 302 and response["Location"] == tab_url(activity.pk), url
        htmx = client.get(url, HTTP_HX_REQUEST="true")
        assert htmx["HX-Redirect"] == tab_url(activity.pk), url


@pytest.mark.parametrize(("name", "args", "tab"), [
    ("activities:change_status", [], "overview"),
    ("activities:add_resource_person", [], "overview"),
    ("activities:document_upload", [], "documents"),
    ("registrations:form_create", ["registration"], "forms"),
    ("registrations:attendance", [], "registrations"),
    ("registrations:send_links", [], "evaluation"),
])
def test_other_tabs_post_only_urls_redirect_on_get(generated, director, name, args, tab) -> None:
    activity, _ = generated
    response = client_for(director).get(reverse(name, args=[activity.pk, *args]))
    assert response.status_code == 302 and response["Location"] == f"{detail(activity)}?tab={tab}"


def test_tab_links_never_depend_on_the_current_path(generated, director) -> None:
    activity, _ = generated
    html = tab_html(client_for(director), activity)
    assert 'href="?' not in html and 'hx-get="?' not in html
    assert f'href="{detail(activity)}?tab=overview"' in html


def test_an_upload_error_fixes_the_address_bar(generated, director) -> None:
    activity, _ = generated
    response = client_for(director).post(reverse("activities:document_upload", args=[activity.pk]), {})
    html = response.content.decode()
    assert response.status_code == 400 and 'href="?' not in html
    assert f'history.replaceState(null, "", "{detail(activity)}?tab\\u003Ddocuments")' in html


# --- A3: the preview follows the list ------------------------------------------------------------------------------


def preview_items(html: str) -> list[dict]:
    import json

    raw = re.search(r'<script id="certificate-previews" type="application/json">(.*?)</script>', html, re.S).group(1)
    return json.loads(raw)


def test_preview_starts_at_the_first_row_in_the_same_order(generated, director, evaluate) -> None:
    activity, people = generated
    evaluate(people[1], name="Regine Paclibar")  # the printed name differs from the registration name
    html = tab_html(client_for(director), activity)
    items = preview_items(html)
    numbers = list(Certificate.objects.filter(activity=activity).order_by("certificate_number").values_list("certificate_number", flat=True))
    assert [item["number"] for item in items] == numbers
    rows = re.findall(r'<th scope="row"[^>]*>(TBI-[^<]+)</th>', html)
    assert rows == numbers  # the list below uses exactly the same order
    assert 'x-data="{ i: 0,' in html and f'src="{items[0]["url"]}"' in html
    renamed = next(item for item in items if item["pk"] == certificate_of(people[1]).pk)
    assert renamed["recipient"] == "Regine Paclibar" and renamed["registered_as"] == people[1].name
    assert "registered as " + people[1].name in html


def test_preview_query_selects_the_certificate(generated, director) -> None:
    activity, people = generated
    target = appreciation_of(activity)
    html = tab_html(client_for(director), activity, preview=target.pk)
    index = [item["pk"] for item in preview_items(html)].index(target.pk)
    assert f'x-data="{{ i: {index},' in html


# --- A2: versions, caching and the Issued copy ---------------------------------------------------------------------


def test_replacing_a_signature_changes_the_preview_url(generated, director) -> None:
    activity, people = generated
    client = client_for(director)
    before = preview_items(tab_html(client, activity))[0]["url"]
    client.post(reverse("activities:certificates_signature", args=[activity.pk, "left"]),
                {"file": upload("dir.png", image_bytes(40, 20))})
    after = preview_items(tab_html(client, activity))[0]["url"]
    assert before != after and before.split("?")[0] == after.split("?")[0]


def test_previews_and_downloads_are_never_cached(generated, director) -> None:
    activity, _ = generated
    certificate = appreciation_of(activity)
    client = client_for(director)
    for name in ("certificates:preview", "certificates:download"):
        assert client.get(reverse(name, args=[certificate.pk]))["Cache-Control"] == "private, no-store"


def test_an_issued_certificate_shows_its_sent_copy(generated, director, evaluate, run_tasks,
                                                   django_capture_on_commit_callbacks) -> None:
    activity, people = generated
    evaluate(people[0])
    with django_capture_on_commit_callbacks(execute=True):
        services.approve_activity_certificates(director, activity)
    certificate = certificate_of(people[0])
    assert certificate.status == S.ISSUED
    client = client_for(director)
    response = client.get(reverse("certificates:preview", args=[certificate.pk]))
    stored = certificate.file_path.open("rb").read()
    assert b"".join(response.streaming_content) == stored and response["Content-Disposition"].startswith("inline")
    item = next(i for i in preview_items(tab_html(client, activity)) if i["pk"] == certificate.pk)
    assert item["issued_note"] == "Issued — this copy was already sent and is not changed."
    # A new signature leaves it alone
    client.post(reverse("activities:certificates_signature", args=[activity.pk, "director"]),
                {"file": upload("dir.png", image_bytes(40, 20))})
    certificate.refresh_from_db()
    assert certificate.status == S.ISSUED and certificate.file_path.open("rb").read() == stored


def test_replace_sends_approved_back_to_waiting(generated, director, design) -> None:
    activity, _ = generated
    services.approve_activity_certificates(director, activity)
    client_for(director).post(reverse("activities:certificates_signature", args=[activity.pk, "left"]),
                              {"file": upload("dir.png", image_bytes(40, 20))})
    assert appreciation_of(activity).status == S.GENERATED


# --- Who sees the approve button (ASSUMPTIONS 369: one per activity) ---------------------------------------------


def test_view_only_members_see_no_approve_buttons_at_all(generated, make_member, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    html = tab_html(client_for(make_member(VIEW_ONLY_ROLE)), activity)
    assert "Approve certificates for this activity" not in html and "Needs approval" in html
    assert 'name="certificate_ids"' not in html or "type=\"checkbox\"" not in html
    assert 'Approve<span class="sr-only"' not in html


def test_the_preview_has_no_approve_button(generated, director) -> None:
    activity, _ = generated
    html = tab_html(client_for(director), activity)
    assert 'Approve<span class="sr-only"' not in html and "Not ready for approval" not in html
    assert all("approve_url" not in item and "can_approve" not in item for item in preview_items(html))
    assert "Approve certificates for this activity" in html and 'id="approve-activity-dialog"' in html


def test_the_approval_is_disabled_with_the_reason_for_approvers_only(generated, director, make_member, design) -> None:
    activity, _ = generated
    CertificateSignatory.objects.filter(pk=design[1].pk).update(signature_image_path=None)
    html = tab_html(client_for(director), activity)
    assert 'disabled aria-describedby="approval-reason"' in html and "Approve certificates for this activity" in html
    assert 'id="approve-activity-dialog"' not in html  # no confirm dialog while closed
    assert "Add the signature of Dr. Vice Pres (Vice President) first." in html
    staff_html = tab_html(client_for(make_member(VIEW_ONLY_ROLE)), activity)
    assert "Approve certificates for this activity" not in staff_html


def test_list_has_no_checkboxes_or_approve_selected(generated, director) -> None:
    activity, _ = generated
    html = tab_html(client_for(director), activity)
    # The only checkboxes are the upload dialogs' "Keep the background" (ASSUMPTIONS 366): no row checkboxes.
    assert html.count('type="checkbox"') == html.count('name="keep_background"')
    assert "Approve selected" not in html and "Generate certificates" not in html


def test_view_only_members_cannot_post_approvals(generated, make_member, evaluate) -> None:
    activity, people = generated
    evaluate(people[0])
    client = client_for(make_member(VIEW_ONLY_ROLE))
    assert client.post(reverse("activities:certificates_approve_activity", args=[activity.pk])).status_code == 403
    assert not Certificate.objects.filter(status=S.APPROVED).exists()


def test_preview_page_without_javascript_has_no_approve(generated, director) -> None:
    activity, _ = generated
    url = reverse("certificates:preview_dialog", args=[appreciation_of(activity).pk])
    html = client_for(director).get(url).content.decode()
    assert 'Approve<span class="sr-only">' not in html and "Download PDF" in html


def test_worker_notice_when_qcluster_is_not_running(generated, director, monkeypatch) -> None:
    from accounts import system_status

    activity, _ = generated
    services.approve_activity_certificates(director, activity)  # Approved, its PDF not rendered yet
    monkeypatch.setattr(system_status, "qcluster_status", lambda: system_status.StatusItem("q", "q", system_status.WARNING, ""))
    html = tab_html(client_for(director), activity)
    assert "Certificates are waiting for the background worker." in html and "Being prepared" in html
