"""Creating certificates (by the System, ASSUMPTIONS 362) and their numbers (TBI-<Manila year>-<5 digits>). The
`conducted` fixture moves the activity before the design exists, so nothing is created until these tests ask."""

import threading
from datetime import datetime

import pytest
from django.db import connection
from django.utils import timezone

from activities import services as activity_services
from certificates import services
from certificates.models import Certificate, CertificateSignatory, CertificateTemplate
from registrations import services as registration_services

from .conftest import S

pytestmark = pytest.mark.django_db
YEAR = timezone.localdate().year


def test_only_for_conducted_activities(attended, design, director) -> None:
    people = attended(1)  # the activity is Ongoing
    with pytest.raises(services.CertificateError, match="created automatically when the activity is Conducted"):
        services.generate_missing_certificates(people[0].activity)
    assert not Certificate.objects.exists()


def test_one_per_attendee_and_one_per_resource_person(conducted, design, director) -> None:
    activity, people = conducted
    result = services.generate_missing_certificates(activity)
    assert result == {"participation_created": 2, "appreciation_created": 1, "total": 3}

    participation = Certificate.objects.filter(recipient_type="registration")
    assert {c.registration for c in participation} == set(people)  # never the registrant who did not attend
    assert all(c.delivery_method == "email" and c.status == S.GENERATED for c in participation)
    appreciation = Certificate.objects.get(recipient_type="resource_person")
    assert appreciation.resource_person.name == "Dr. Maria Santos" and appreciation.delivery_method == "hard_copy"

    template, vp, head = design
    assert all((c.certificate_template, c.right_signatory, c.left_signatory) == (template, vp, head) for c in Certificate.objects.all())
    numbers = sorted(Certificate.objects.values_list("certificate_number", flat=True))
    assert numbers == [f"TBI-{YEAR}-00001", f"TBI-{YEAR}-00002", f"TBI-{YEAR}-00003"]


def test_generating_twice_creates_nothing_new(conducted, design, director) -> None:
    activity, _ = conducted
    services.generate_missing_certificates(activity)
    assert services.generate_missing_certificates(activity) == {"participation_created": 0, "appreciation_created": 0, "total": 3}
    assert Certificate.objects.count() == 3


def test_generating_again_adds_only_the_missing_ones(conducted, design, director) -> None:
    activity, people = conducted
    services.generate_missing_certificates(activity)
    late = activity.registrations.get(name="No Show")
    registration_services.set_attendance(director, activity, [late.pk], True, "wet_signature")  # creates it
    assert services.generate_missing_certificates(activity)["participation_created"] == 0
    assert Certificate.objects.get(registration=late).certificate_number == f"TBI-{YEAR}-00004"


@pytest.mark.parametrize(
    "remove, message",
    [
        ("template", "no active certificate design"),
        ("left", "Choose the two signatories on the Certificates tab first."),
        ("right", "Choose the two signatories on the Certificates tab first."),
    ],
)
def test_missing_template_or_signatory_is_refused(conducted, design, director, remove, message) -> None:
    """ASSUMPTIONS 363: the active design and the activity's two signatories (an empty slot takes the default)."""
    from certificates.models import ActivitySignatory

    template, vp, head = design
    if remove == "template":
        CertificateTemplate.objects.filter(pk=template.pk).update(is_active=False)
    else:
        ActivitySignatory.objects.filter(activity=conducted[0], slot=remove).delete()
        CertificateSignatory.objects.filter(default_slot=remove).update(default_slot=None)
    with pytest.raises(services.CertificateError, match=message):
        services.generate_missing_certificates(conducted[0])
    assert not Certificate.objects.exists()
    assert services.auto_generate_certificates(conducted[0]) == 0  # the automatic callers never fail


def test_without_anything_the_design_is_named_first(conducted, director) -> None:
    with pytest.raises(services.CertificateError, match="no active certificate design"):
        services.generate_missing_certificates(conducted[0])


def test_existing_certificates_keep_their_signatories(generated, admin_member) -> None:
    from conftest import image_bytes

    from .conftest import upload

    services.add_signatory(admin_member, "New VP", "vp", upload("n.png", image_bytes()))
    assert set(Certificate.objects.values_list("right_signatory__name", flat=True)) == {"Dr. Vice Pres"}


def test_unmarking_attendance_deletes_a_waiting_certificate(generated, director) -> None:
    activity, people = generated
    registration_services.set_attendance(director, activity, [people[0].pk], False)
    assert not Certificate.objects.filter(registration=people[0]).exists()


def test_numbers_continue_across_activities_and_restart_each_year(generated, director, valid_data, move_to) -> None:
    def conducted_with_speaker(title: str):
        activity = activity_services.create_activity(director, valid_data(title=title))
        speaker = activity_services.create_resource_person(director, f"Speaker of {title}")
        activity_services.add_resource_person(director, activity, speaker)
        return move_to(activity, "Conducted")

    second = conducted_with_speaker("Second")  # the design exists: created at the move
    assert Certificate.objects.get(activity=second).certificate_number == f"TBI-{YEAR}-00004"

    third = activity_services.create_activity(director, valid_data(title="Third"))
    activity_services.add_resource_person(director, third, activity_services.create_resource_person(director, "Speaker 3"))
    move_to(third, "Ongoing")
    Certificate.objects.filter(activity=third).delete()
    CertificateTemplate.objects.update(is_active=False)  # nothing is created at the move
    activity_services.change_status(director, third, "Conducted")
    CertificateTemplate.objects.update(is_active=True)
    services.generate_missing_certificates(third, now=timezone.make_aware(datetime(YEAR + 1, 1, 2, 9, 0)))
    assert Certificate.objects.get(activity=third).certificate_number == f"TBI-{YEAR + 1}-00001"


@pytest.mark.django_db(transaction=True)
def test_numbers_are_unique_under_concurrency(director, valid_data, move_to) -> None:
    """Two activities generated at the same moment: the advisory lock gives every certificate its own number."""
    from conftest import _stored, image_bytes

    activities = []
    for n in range(2):
        activity = activity_services.create_activity(director, valid_data(title=f"Race {n}"))
        for k in range(5):
            person = activity_services.create_resource_person(director, f"Speaker {n}-{k}")
            activity_services.add_resource_person(director, activity, person)
        move_to(activity, "Conducted")  # no design yet: nothing is created
        activities.append(activity)
    CertificateTemplate.objects.create(name="D", file_path=_stored("t.png", image_bytes()), uploaded_by=director, is_active=True)
    for slot in ("left", "right"):  # the defaults: the activities' empty slots take them (ASSUMPTIONS 363)
        CertificateSignatory.objects.create(name=slot, position=slot, default_slot=slot,
                                            signature_image_path=_stored("s.png", image_bytes()))
    services.fill_empty_slots_from_defaults()  # as when a member sets the defaults (ASSUMPTIONS 366)

    start = threading.Barrier(2)
    errors: list[Exception] = []

    def generate(activity) -> None:
        try:
            start.wait()
            services.generate_missing_certificates(activity)
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=generate, args=(a,)) for a in activities]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    numbers = sorted(Certificate.objects.values_list("certificate_number", flat=True))
    assert numbers == [f"TBI-{YEAR}-{n:05d}" for n in range(1, 11)]  # 10 unique numbers, no gaps
