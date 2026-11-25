"""The data migration of ASSUMPTIONS 363 (certificates 0006-0008): existing VP / Director signatories become list
entries with their printed positions, every activity gets its signatories, existing certificates map to left
(Director) / right (VP), and no Issued PDF changes.

The test builds data with the current models, migrates the certificates app back to 0005 (the old VP / Director
columns), adds an old-style "replaced VP signature" there, then migrates forward again. It needs a transactional
database (PostgreSQL refuses the ALTER TABLE in the same transaction as the data updates, see 0008)."""

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from activities import services as activity_services
from certificates import services
from certificates.models import ActivitySignatory, Certificate, CertificateSignatory

from .conftest import S, appreciation_of, certificate_of

BEFORE = [("certificates", "0005_signature_image_optional")]


def migrate(targets) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def latest() -> list:
    executor = MigrationExecutor(connection)
    return list(executor.loader.graph.leaf_nodes())  # every app: going back also unapplies approvals / notifications (ASSUMPTIONS 437)


@pytest.mark.django_db(transaction=True)
def test_existing_vp_director_data_keeps_working(generated, design, director, valid_data) -> None:
    activity, people = generated
    _, vp, head = design
    issued = certificate_of(people[0])
    pdf_name = default_storage.save(f"certificates/2026/{issued.certificate_number}.pdf", ContentFile(b"%PDF-1.7 sent copy"))
    Certificate.objects.filter(pk=issued.pk).update(status=S.ISSUED, file_path=pdf_name)
    empty = activity_services.create_activity(director, valid_data(title="No certificates yet"))
    ActivitySignatory.objects.all().delete()  # before this change there were no saved slots
    CertificateSignatory.objects.update(default_slot=None)

    try:
        migrate(BEFORE)
        old = MigrationExecutor(connection).loader.project_state(BEFORE).apps
        OldSignatory = old.get_model("certificates", "CertificateSignatory")
        OldCertificate = old.get_model("certificates", "Certificate")
        assert set(OldSignatory.objects.values_list("position", flat=True)) == {"vp", "director"}
        assert OldCertificate.objects.get(pk=issued.pk).vp_signatory_id == vp.pk  # left/right went back to vp/director
        # The old "Replace signature" on the tab: a new VP row; the activity's not-Issued certificates use it.
        OldSignatory.objects.filter(pk=vp.pk).update(is_active=False)
        new_vp = OldSignatory.objects.create(name=vp.name, position="vp", is_active=True,
                                             signature_image_path="signatures/vp-new.png")
        OldCertificate.objects.filter(activity_id=activity.pk).exclude(status="issued").update(vp_signatory=new_vp)
    finally:
        migrate(latest())

    vp.refresh_from_db()
    head.refresh_from_db()
    new_vp = CertificateSignatory.objects.get(pk=new_vp.pk)
    assert (vp.position, head.position, new_vp.position) == ("Vice President", "Director", "Vice President")
    assert services.default_signatories() == {"left": head, "right": new_vp}  # the active ones
    issued.refresh_from_db()
    assert (issued.left_signatory_id, issued.right_signatory_id) == (head.pk, vp.pk)  # Director left, VP right
    assert issued.status == S.ISSUED and issued.file_path.name == pdf_name
    with default_storage.open(pdf_name) as handle:
        assert handle.read() == b"%PDF-1.7 sent copy"  # the Issued PDF is untouched
    waiting = certificate_of(people[1])
    assert (waiting.left_signatory_id, waiting.right_signatory_id) == (head.pk, new_vp.pk)
    assert appreciation_of(activity).right_signatory_id == new_vp.pk
    # The activity gets what its certificates that are not Issued use; the activity without certificates gets the
    # active Director and VP.
    assert services.saved_activity_signatories(activity) == {"left": head, "right": new_vp}
    assert services.saved_activity_signatories(empty) == {"left": head, "right": new_vp}
    # The Issued certificate was printed with the replaced VP signature: it is (correctly) outdated, nothing else is.
    assert services.outdated_ids(activity) == {issued.pk}


@pytest.mark.django_db(transaction=True)
def test_an_activity_with_only_issued_certificates_is_not_outdated(generated, design) -> None:
    activity, _ = generated
    Certificate.objects.filter(activity=activity).update(status=S.ISSUED)
    ActivitySignatory.objects.all().delete()
    CertificateSignatory.objects.update(default_slot=None)
    try:
        migrate(BEFORE)
    finally:
        migrate(latest())
    _, vp, head = design
    assert services.saved_activity_signatories(activity) == {"left": head, "right": vp}
    assert services.outdated_ids(activity) == set()
