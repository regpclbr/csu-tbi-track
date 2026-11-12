"""DEMO ONLY: try the Step 9 certificate flow step by step on the development database.

Every step calls the same services as the JSON API (no shortcuts), acting as --member <email> or, by default, the
first active member allowed to do that step: the System Administrator for "design" (can_manage_users), for "approve" a
member with can_approve_certificates, the Director first (ASSUMPTIONS 361), a member who can create activities for every
other step
(separation of duties, ASSUMPTIONS 339). Refuses to run when DEBUG is False.

    python manage.py certificate_walkthrough design <folder with the sample PNGs>
    python manage.py certificate_walkthrough activity
    python manage.py certificate_walkthrough generate <activity_id>      (the System already did it at Conducted)
    python manage.py certificate_walkthrough preview <certificate_id> <output.pdf>
    python manage.py certificate_walkthrough approve|regenerate|handover <activity_id>
    python manage.py certificate_walkthrough evaluate <registration_id> --name "..." --email "..."
    python manage.py certificate_walkthrough status <activity_id>
    python manage.py certificate_walkthrough save-pdf <certificate_id> <output.pdf>

Background work (rendering, issuing, emailing) is done by `python manage.py qcluster` in another
terminal, exactly as in real use; the emails are printed in THAT terminal.
"""

import inspect
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Role, TBIMember
from activities import services as activity_services
from activities.models import Activity, ActivityType, ParticipantCategory
from certificates import services
from certificates.models import Certificate
from registrations import services as registration_services
from registrations.models import FieldType, FormType, Registration

WALKTHROUGH_PEOPLE = [
    ("Ana Walkthrough", "ana.walkthrough@example.com"),
    ("Ben Walkthrough", "ben.walkthrough@example.com"),
    ("Carla Walkthrough", "carla.walkthrough@example.com"),  # registers but does not attend
]


def sample_answer(field):
    """A valid answer for any field type (only for this walkthrough)."""
    options = registration_services.parse_options(field.options)
    if field.field_type == FieldType.RATING:
        return 5
    if field.field_type == FieldType.CHECKBOX:
        return options[:1]
    if field.field_type == FieldType.MULTIPLE_CHOICE:
        return options[0]
    return "Walkthrough answer"


class Command(BaseCommand):
    help = "DEMO ONLY: try the certificate flow step by step (design, activity, generate, approve, evaluate, ...)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("step", choices=["design", "activity", "generate", "preview", "approve", "regenerate",
                                             "handover", "evaluate", "status", "save-pdf"])
        parser.add_argument("values", nargs="*", help="The step's values (IDs, folder, position, output file).")
        parser.add_argument("--member", help="Act as this staff member (default: the first active member allowed to do the step).")
        parser.add_argument("--name", help="evaluate: the name to print on the certificate.")
        parser.add_argument("--email", help="evaluate: the address to send the certificate to.")

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("certificate_walkthrough is demo-only and refuses to run when DEBUG is False.")
        step, values = options["step"], options["values"]
        self.member = self.find_member(options["member"], step)
        self.options = options
        method = getattr(self, "step_" + step.replace("-", "_"))
        try:
            inspect.signature(method).bind(*values)
        except TypeError as error:
            raise CommandError(f"Wrong arguments for '{step}'. See: python manage.py certificate_walkthrough --help") from error
        try:
            method(*values)
        except (services.CertificateError, registration_services.RegistrationError) as error:
            raise CommandError(str(error)) from None

    # --- helpers ---

    def find_member(self, email: str | None, step: str) -> TBIMember:
        members = TBIMember.objects.filter(is_active=True).select_related("role")
        flag = ({"role__can_manage_users": True} if step == "design"
                else {"role__can_approve_certificates": True} if step == "approve"
                else {"role__can_create_activity": True})
        if email:
            member = members.filter(email__iexact=email).first()
        else:  # "approve": the Director's account first (a demo choice only; the permission is the flag)
            allowed = members.filter(**flag).order_by("pk")
            member = (allowed.filter(role__role_name=Role.DIRECTOR).first() if step == "approve" else None) or allowed.first()
        if member is None:
            raise CommandError("No active member found (run seed_data, or pass --member <email>).")
        return member

    def activity(self, activity_id) -> Activity:
        return Activity.objects.get(pk=int(activity_id))

    def say(self, text: str) -> None:
        self.stdout.write(self.style.SUCCESS(text))

    # --- steps ---

    def step_design(self, folder) -> None:
        folder = Path(folder)
        read = lambda name: SimpleUploadedFile(name, (folder / name).read_bytes())  # noqa: E731
        template = services.upload_template(self.member, "Walkthrough sample design", read("sample-template.png"), activate=True)
        vp = services.add_signatory(self.member, "Dr. Sample Vice President", "vp", read("sample-signature-vp.png"))
        director = services.add_signatory(self.member, "Engr. Sample Director", "director", read("sample-signature-director.png"))
        self.say(f"Template {template.pk} '{template.name}' is the active design ({template.file_path.name}).")
        self.say(f"Signatory {vp.pk}: {vp.name} (VP), active. Signatory {director.pk}: {director.name} (Director), active.")

    def step_activity(self) -> None:
        """A Conducted activity ending today: 3 registrations (2 attended) and 1 resource person.

        One clock reading for the whole step (today = its Manila date), passed to the services that take `now`.
        Registration stays open until the end of today: a deadline like "now + 1 hour" would fall on tomorrow
        after 23:00 and be refused (the deadline must be on or before the start date)."""
        now = timezone.now()
        today = timezone.localdate(now)
        stamp = timezone.localtime(now).strftime("%Y-%m-%d %H:%M")
        activity = activity_services.create_activity(self.member, {
            "title": f"[Walkthrough] Certificates {stamp}",
            "description": "Created by certificate_walkthrough (demo).",
            "activity_type": ActivityType.objects.filter(is_active=True).order_by("pk").first(),
            "participant_categories": [ParticipantCategory.objects.order_by("pk").first()],
            "start_date": today, "end_date": today,
            "start_time": time(9, 0), "end_time": time(16, 0), "venue": "CSU TBI Hall", "max_slots": 10,
            "registration_deadline": timezone.make_aware(datetime.combine(today, time.max)),  # 23:59:59.999999 today
            "evaluation_deadline": timezone.make_aware(datetime.combine(today + timedelta(days=7), time(17, 0))),
            "budget_amount": Decimal("0.00"),
        }, now=now)
        registration_services.create_form(self.member, activity, FormType.REGISTRATION)
        registration_services.create_form(self.member, activity, FormType.EVALUATION)
        activity_services.change_status(self.member, activity, "Scheduled")
        form = registration_services.get_form(activity, FormType.REGISTRATION)
        answers = {str(f.pk): sample_answer(f) for f in form.fields.all()}
        people = [registration_services.register(activity, name=n, email=e, answers=answers, now=now) for n, e in WALKTHROUGH_PEOPLE]
        speaker = activity_services.create_resource_person(self.member, f"Dr. Walkthrough Speaker {stamp}")
        activity_services.add_resource_person(self.member, activity, speaker, topic="Pitch Decks That Work")
        activity_services.change_status(self.member, activity, "Ongoing")
        registration_services.set_attendance(self.member, activity, [people[0].pk, people[1].pk], True, "email")
        activity_services.change_status(self.member, activity, "Conducted")
        self.say(f"Activity {activity.pk} '{activity.title}' is Conducted.")
        for person in people:
            attended = "attended" if person.pk in (people[0].pk, people[1].pk) else "did NOT attend"
            self.stdout.write(f"  registration {person.pk}: {person.name} <{person.email}> - {attended}")
        self.stdout.write(f"  resource person: {speaker.name} (topic: Pitch Decks That Work)")

    def step_generate(self, activity_id) -> None:
        """Normally nothing to do: the System creates the certificates when the activity becomes Conducted
        (ASSUMPTIONS 362). Creates the missing ones, e.g. when the design was set up after that."""
        result = services.generate_missing_certificates(self.activity(activity_id))
        self.say(f"Created {result['participation_created']} Certificate(s) of Participation and "
                 f"{result['appreciation_created']} of Appreciation automatically. The activity now has {result['total']}.")
        self.step_status(activity_id)

    def step_preview(self, certificate_id, output) -> None:
        certificate = Certificate.objects.get(pk=int(certificate_id))
        Path(output).write_bytes(services.preview_pdf(self.member, certificate))
        self.say(f"Preview of {certificate.certificate_number} written to {Path(output).resolve()}")

    def step_approve(self, activity_id) -> None:
        changed = services.approve_certificates(self.member, activity=self.activity(activity_id))
        self.say(f"Approved {changed} ready certificate(s) (both signatures; participants after their evaluation). "
                 "qcluster now renders them, issues and emails the participants'.")

    def step_regenerate(self, activity_id) -> None:
        changed = services.regenerate_certificates(self.member, activity=self.activity(activity_id))
        self.say(f"Applied the current design to {changed} certificate(s): approved ones wait for approval again.")

    def step_handover(self, activity_id) -> None:
        self.say(f"Marked {services.mark_handed_over(self.member, activity=self.activity(activity_id))} hard-copy certificate(s) Issued.")

    def step_evaluate(self, registration_id) -> None:
        registration = Registration.objects.get(pk=int(registration_id))
        form = registration_services.get_form(registration.activity, FormType.EVALUATION)
        submission = registration_services.submit_evaluation(
            registration_services.evaluation_token(registration),
            certificate_name=self.options["name"] or registration.name,
            certificate_email=self.options["email"] or registration.email,
            answers={str(f.pk): sample_answer(f) for f in form.fields.all()},
        )
        self.say(f"Evaluation saved for {registration.name}: certificate name '{submission.certificate_name}', "
                 f"email {submission.certificate_email}.")

    def step_status(self, activity_id) -> None:
        certificates = Certificate.objects.filter(activity_id=int(activity_id)).select_related("registration", "resource_person").order_by("certificate_number")
        for c in certificates:
            evaluation = services.get_submitted_evaluation(c)
            who = c.registration.name if c.registration_id else c.resource_person.name
            sent_to = f" -> {evaluation.certificate_name} <{evaluation.certificate_email}>" if evaluation else ""
            self.stdout.write(
                f"  {c.pk:>4}  {c.certificate_number}  {c.get_status_display():<16} {c.delivery_method:<9} {who}{sent_to}  "
                f"pdf={'yes' if c.file_path else 'no'}  issued={'yes' if c.issued_at else 'no'}  sent={'yes' if c.sent_at else 'no'}"
            )

    def step_save_pdf(self, certificate_id, output) -> None:
        certificate = Certificate.objects.get(pk=int(certificate_id))
        if not certificate.file_path:
            raise CommandError("This certificate has no final PDF yet (is qcluster running?).")
        with certificate.file_path.open("rb") as handle:
            Path(output).write_bytes(handle.read())
        self.say(f"{certificate.certificate_number} copied to {Path(output).resolve()}")
