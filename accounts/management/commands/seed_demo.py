"""`python manage.py seed_demo`: DEMO-ONLY fake data for presentations.

Creates the eight activities from the Figma prototype, plus (Step 10) seven earlier Conducted
activities spread over several months, types and categories, with fake registrations,
attendance, evaluations, resource persons, startups, documents and certificates, so every
dashboard card and chart has something to show. One of them is titled "=HYPERLINK(...)"
on purpose, to show that the Excel report writes it as text, never as a formula.

- Refuses to run when DEBUG is False, so it can never touch a production database.
- All people and companies are invented. Every email ends in @demo.example.com.
- One demo staff account per role (System Administrator included), with unusable passwords: set one with
  `python manage.py changepassword <email>` to log in during a demo (never stored in code or .env).
- Safe to run twice: an activity that already exists (by title) is skipped; certificates are
  only created for a demo activity that has none yet.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Role, TBIMember
from accounts.seed import load_default_form_fields, load_lookups
from activities import services as activity_services
from registrations import services as registration_services
from certificates import services as certificate_services
from certificates.management.commands.make_sample_certificate_images import draw_signature, draw_template
from certificates.models import Certificate, CertificateTemplate
from activities.models import (
    Activity,
    ActivityParticipantCategory,
    ActivityStatus,
    ActivityType,
    ParticipantCategory,
    ResourcePerson,
)
from documents.models import Document, DocumentStatus, DocumentType
from registrations.models import (
    EvaluationSubmission,
    FieldType,
    FormAnswer,
    FormField,
    FormTemplate,
    FormType,
    Registration,
)
from startups.models import Startup
from startups.providers import MockStartupProvider
from startups.services import sync_startups

DEMO_DOMAIN = "demo.example.com"

FIRST_NAMES = [
    "Andrea", "Bianca", "Carlo", "Daniel", "Elena", "Francis", "Gabriel", "Hannah", "Isabel", "Joshua",
    "Katrina", "Lorenzo", "Marco", "Nicole", "Oscar", "Patricia", "Rafael", "Sofia", "Tristan", "Vanessa",
]
LAST_NAMES = [
    "Abad", "Bautista", "Castillo", "Domingo", "Espino", "Flores", "Garcia", "Herrera", "Ilagan", "Jimenez",
    "Lopez", "Mendoza", "Navarro", "Ocampo", "Pascual", "Quiambao", "Ramos", "Santos", "Torres", "Villanueva",
]

# Invented resource persons: (name, topic)
RESOURCE_PERSONS = [
    ("Dr. Liza Marquez", "Pitch deck essentials"),
    ("Engr. Paolo Dizon", "Prototype to product"),
    ("Atty. Grace Salonga", "Patents and trademarks"),
    ("Prof. Ramon Aquino", "Business Model Canvas"),
    ("Ms. Celine Vergara", "Customer discovery"),
    ("Mr. Victor Lacson", "Talking to investors"),
]


@dataclass
class DemoActivity:
    title: str
    activity_type: str
    categories: list[str]  # one or more participant categories
    status: str
    start_offset_days: int  # relative to today
    length_days: int
    max_slots: int
    registrations: int
    budget: str
    venue: str
    resource_persons: list[int]  # indexes into RESOURCE_PERSONS


FORMULA_TEST_TITLE = '=HYPERLINK("https://example.com","Formula test")'

DEMO_ACTIVITIES = [
    DemoActivity("Startup Pitch Competition", "Forum", ["Incubatees", "MSMEs"], "Conducted", -30, 1, 40, 32, "25000.00",
                 "CSU Gonzaga Function Hall", [0, 5]),
    DemoActivity("Business Model Training", "Training", ["Incubatees"], "Conducted", -21, 2, 30, 26, "18000.00",
                 "TBI Co-working Space", [3]),
    DemoActivity("Intellectual Property Seminar", "Seminar", ["Faculty"], "Conducted", -10, 1, 50, 38, "12000.00",
                 "CSU Audio-Visual Room", [2]),
    DemoActivity("Mentoring Session", "Mentoring", ["Incubatees"], "Ongoing", 0, 1, 15, 12, "5000.00",
                 "TBI Meeting Room", [4]),
    # Starts in 4 days: its Activity Design and Request Letter were due yesterday (start - 5, ASSUMPTIONS 416) and are not
    # uploaded (uploads are approved copies since 432), so Needs attention shows an overdue Activity Design.
    DemoActivity("Product Development Workshop", "Workshop", ["MSMEs"], "Scheduled", 4, 2, 25, 14, "20000.00",
                 "CSU Engineering Laboratory", [1]),
    DemoActivity("Market Research Training", "Training", ["MSMEs"], "Scheduled", 14, 1, 30, 9, "15000.00",
                 "TBI Co-working Space", [4]),
    DemoActivity("Investor Networking", "Networking", ["Incubatees"], "Scheduled", 25, 1, 10, 10, "30000.00",
                 "Hotel Ensenada Conference Room", [5]),  # full: shows "registration closed"
    DemoActivity("Technology Forum", "Forum", ["Faculty", "Incubatees"], "Draft", 40, 1, 60, 0, "35000.00",
                 "CSU Gymnasium", []),
    # Step 10: earlier Conducted activities (several months, types and categories) for the dashboard and reports.
    DemoActivity("Design Thinking Workshop", "Workshop", ["Faculty", "MSMEs"], "Conducted", -235, 2, 40, 34, "22000.00",
                 "CSU Engineering Laboratory", [1, 4]),
    DemoActivity("Startup Finance Seminar", "Seminar", ["Incubatees"], "Conducted", -200, 1, 35, 28, "14000.00",
                 "CSU Audio-Visual Room", [5]),
    DemoActivity("Women in Tech Forum", "Forum", ["Faculty", "Incubatees", "MSMEs"], "Conducted", -165, 1, 80, 61,
                 "30000.00", "CSU Gymnasium", [0, 2]),
    DemoActivity("Digital Marketing Training", "Training", ["MSMEs"], "Conducted", -130, 2, 30, 27, "16000.00",
                 "TBI Co-working Space", [4]),
    DemoActivity("Founder Mentoring Clinic", "Mentoring", ["Incubatees"], "Conducted", -95, 1, 15, 13, "6000.00",
                 "TBI Meeting Room", [3]),
    DemoActivity("Industry-Academe Networking Night", "Networking", ["Faculty", "MSMEs"], "Conducted", -60, 1, 50, 41,
                 "28000.00", "Hotel Ensenada Conference Room", [5]),
    # Deliberately starts with "=": the Excel report must show it as plain text, never run it as a formula.
    DemoActivity(FORMULA_TEST_TITLE, "Seminar", ["Faculty"], "Conducted", -45, 1, 20, 8, "1000.00",
                 "CSU Audio-Visual Room", []),
]

# For conducted activities: which required documents exist and their status.
# Missing types show up as gaps in the checklist.
CONDUCTED_DOCUMENTS = {
    "Startup Pitch Competition": {
        "Activity Design": "Approved",
        "Request Letter": "Approved",
        "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved",
        "Activity Report": "Approved",
    },
    "Business Model Training": {
        "Activity Design": "Approved",
        "Request Letter": "Approved",
        "Invitation Letter": "Approved",
        "Attendance Sheet": "For Review",
        # Activity Report missing
    },
    "Intellectual Property Seminar": {
        "Activity Design": "Approved",
        "Invitation Letter": "For Review",
        # Request Letter, Attendance Sheet and Activity Report missing
    },
    "Design Thinking Workshop": {
        "Activity Design": "Approved", "Request Letter": "Approved", "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved", "Activity Report": "Approved",
    },
    "Startup Finance Seminar": {
        "Activity Design": "Approved", "Request Letter": "Approved", "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved", "Activity Report": "For Review",
    },
    "Women in Tech Forum": {
        "Activity Design": "Approved", "Request Letter": "Approved", "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved", "Activity Report": "Approved",
    },
    "Digital Marketing Training": {
        "Activity Design": "Approved", "Invitation Letter": "Approved", "Attendance Sheet": "For Review",
        # Request Letter and Activity Report missing
    },
    "Founder Mentoring Clinic": {
        "Activity Design": "Approved", "Request Letter": "Approved", "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved", "Activity Report": "Approved",
    },
    "Industry-Academe Networking Night": {
        "Activity Design": "Approved", "Invitation Letter": "For Review",
    },
}

# Certificates per Conducted demo activity: "generated" ones wait for approval (ASSUMPTIONS 357, 361).
# "approved" ones are rendered, issued and emailed by `python manage.py retry_certificates` (or qcluster).
DEMO_CERTIFICATES = {
    "Startup Pitch Competition": "approved",
    "Business Model Training": "generated",
    "Intellectual Property Seminar": "generated",
    "Design Thinking Workshop": "approved",
    "Startup Finance Seminar": "approved",
    "Women in Tech Forum": "approved",
    "Digital Marketing Training": "approved",
    "Founder Mentoring Clinic": "generated",
    "Industry-Academe Networking Night": "generated",
}
UPCOMING_DOCUMENTS = {
    "Mentoring Session": {"Activity Design": "Approved", "Invitation Letter": "Approved"},
    "Product Development Workshop": {},  # nothing uploaded yet: both overdue (ASSUMPTIONS 416, 432)
}


class Command(BaseCommand):
    help = "DEMO ONLY: create fake activities, registrations, startups and documents. Refuses to run if DEBUG=False."

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_demo is demo-only and refuses to run when DEBUG is False.")

        self.rng = random.Random(2026)  # same "random" data every time
        self.today = timezone.localdate()

        with transaction.atomic():
            load_lookups()
            load_default_form_fields()
            self.staff = self.create_demo_staff()
            self.startups = self.create_startups()
            self.resource_persons = self.create_resource_persons()

            created = 0
            for spec in DEMO_ACTIVITIES:
                if Activity.objects.filter(title=spec.title).exists():
                    self.stdout.write(f"  skipped (already exists): {spec.title}")
                    continue
                self.create_activity(spec)
                created += 1
                self.stdout.write(f"  created: {spec.title}")
            certificates = self.create_certificates()
            self.update_statistics()

        self.stdout.write(self.style.SUCCESS(f"Demo data: {created} activities created, {certificates} certificates created."))
        if certificates:
            self.stdout.write(
                "Approved participant certificates are rendered, issued and emailed (printed: demo addresses are never "
                "really sent) by `python manage.py retry_certificates` or by qcluster."
            )
        self.stdout.write(self.style.WARNING("This is fake demo data. Do not use it in production."))

    # --- people -----------------------------------------------------------------

    def create_demo_staff(self) -> dict[str, TBIMember]:
        """One demo account per role (System Administrator included), e.g. director@demo.example.com, to show what
        each role may do. Unusable passwords: nobody can log in until a password is set on this machine with
        `python manage.py changepassword <email>` (typed in the terminal, never stored in code or .env).
        must_change_password is off so a demo is not interrupted (ASSUMPTIONS 339)."""
        staff = {}
        for role in Role.objects.order_by("role_name"):
            email = f"{slugify(role.role_name)}@{DEMO_DOMAIN}"
            member = TBIMember.objects.filter(email=email).first()
            if member is None:
                member = TBIMember.objects.create_user(
                    email, f"Demo {role.role_name}", role, password=None, must_change_password=False
                )
            staff[role.role_name] = member
        return staff

    def create_startups(self) -> list[Startup]:
        """Demo startups come from a mock Navigatu sync (the same path as real data). Safe to run twice."""
        result = sync_startups(provider=MockStartupProvider(), deactivate_missing=False)
        self.stdout.write(
            f"  startups synced from the mock provider: {result['created']} created, {result['updated']} updated"
        )
        return list(Startup.objects.filter(is_active=True, email__endswith=f"@{DEMO_DOMAIN}").order_by("navigatu_id"))

    def create_resource_persons(self) -> list[ResourcePerson]:
        persons = []
        for number, (name, _topic) in enumerate(RESOURCE_PERSONS, start=1):
            person, _ = ResourcePerson.objects.get_or_create(
                email=f"resource.person{number}@{DEMO_DOMAIN}",
                defaults={"name": name, "contact_number": f"0918{number:07d}"},
            )
            persons.append(person)
        return persons

    def fake_people(self, count: int) -> list[tuple[str, str]]:
        """`count` different fake (name, email) pairs."""
        pairs = self.rng.sample([(f, l) for f in FIRST_NAMES for l in LAST_NAMES], count)
        return [(f"{first} {last}", f"{first}.{last}@{DEMO_DOMAIN}".lower()) for first, last in pairs]

    # --- activities -------------------------------------------------------------

    def at(self, day: date, hour: int) -> datetime:
        return timezone.make_aware(datetime.combine(day, time(hour, 0)))

    def create_activity(self, spec: DemoActivity) -> None:
        start = self.today + timedelta(days=spec.start_offset_days)
        end = start + timedelta(days=spec.length_days - 1)
        msme_only = spec.categories == ["MSMEs"]
        creator = self.staff["Community Development Head" if msme_only else "Incubation Project Head"]

        # Created through the same services as real activities, so the demo has a real audit log.
        activity = activity_services.create_activity(
            creator,
            {
                "activity_type": ActivityType.objects.get(name=spec.activity_type),
                "participant_categories": list(ParticipantCategory.objects.filter(name__in=spec.categories)),
                "title": spec.title,
                "description": f"[DEMO] {spec.title} for {' and '.join(spec.categories)} of the CSU Technology Business Incubator.",
                "start_date": start,
                "end_date": end,
                "start_time": time(8, 0),
                "end_time": time(17, 0),
                "venue": spec.venue,
                "max_slots": spec.max_slots,
                "registration_deadline": self.at(start - timedelta(days=2), 17),
                "evaluation_deadline": self.at(end + timedelta(days=7), 17),
                "budget_amount": Decimal(spec.budget),
            },
            # Demo only: validate "as of" two weeks before the start, so past (Conducted)
            # activities pass the "start date and deadline not in the past" rule.
            now=self.at(start - timedelta(days=14), 8),
        )
        for index in spec.resource_persons:
            activity_services.add_resource_person(
                creator, activity, self.resource_persons[index], RESOURCE_PERSONS[index][1]
            )
        # Walk the allowed status moves up to the wanted status (Draft -> Scheduled -> Ongoing -> Conducted).
        path = [ActivityStatus.SCHEDULED, ActivityStatus.ONGOING, ActivityStatus.CONDUCTED]
        for status_name in path[: path.index(spec.status) + 1] if spec.status in path else []:
            activity_services.change_status(creator, activity, status_name)

        if spec.status != "Draft":
            # Both forms come from the default fields, through the same service staff use.
            registration_form = registration_services.create_form(creator, activity, FormType.REGISTRATION)
            evaluation_form = registration_services.create_form(creator, activity, FormType.EVALUATION)
            registrations = self.create_registrations(activity, registration_form, spec)
            if spec.status in ("Conducted", "Ongoing"):
                self.mark_attendance(registrations, share=0.8 if spec.status == "Conducted" else 0.5)
            if spec.status == "Conducted":
                self.create_evaluations(registrations, evaluation_form, end)

        documents = CONDUCTED_DOCUMENTS.get(spec.title) or UPCOMING_DOCUMENTS.get(spec.title) or {}
        for type_name, status_name in documents.items():
            self.create_document(activity, type_name, status_name)

    # --- forms, registrations, evaluations ----------------------------------
    # Demo registrations are written directly (many demo activities are already closed for
    # registration), but every answer goes through registrations.services.validate_answers.

    def fake_answer(self, field: FormField, organization: str = ""):
        """A plausible raw answer for any field type."""
        options = registration_services.parse_options(field.options)
        if field.field_type == FieldType.RATING:
            return self.rng.choice([3, 4, 4, 5, 5])
        if field.field_type == FieldType.CHECKBOX:
            return options[:1]  # e.g. a consent question on an older form
        if field.field_type == FieldType.MULTIPLE_CHOICE:
            return self.rng.choice(options)
        if field.field_type == FieldType.SHORT_TEXT:
            return organization or self.rng.choice(["CSU", "Freelance", "Family business", "LGU"])
        if not field.is_required and self.rng.random() < 0.4:
            return ""
        return self.rng.choice(
            ["The talks were practical.", "More hands-on time please.", "Very helpful for our startup.",
             "Clear examples and good pacing."]
        )

    def save_answers(self, form: FormTemplate, raw: dict, **parent) -> None:
        source = FormAnswer.AnswerSource.REGISTRATION if "registration" in parent else FormAnswer.AnswerSource.EVALUATION
        for field, value in registration_services.validate_answers(form, raw).items():
            FormAnswer.objects.create(field=field, answer_source=source, answer_value=value, **parent)

    def create_registrations(self, activity: Activity, form: FormTemplate, spec: DemoActivity) -> list[Registration]:
        categories = list(activity.participant_categories.order_by("name"))
        incubatees = next((c for c in categories if c.name == "Incubatees"), None)
        fields = list(form.fields.all())
        registrations = []

        # A few incubatee startups register while logged in.
        linked_startups = self.rng.sample(self.startups, min(3, len(self.startups))) if incubatees else []
        people = [(s.name + " (representative)", s.email, s) for s in linked_startups]
        people += [(name, email, None) for name, email in self.fake_people(spec.registrations - len(people))]

        for name, email, startup in people:
            registration = Registration.objects.create(
                activity=activity,
                template=form,
                startup=startup,
                participant_category=incubatees if startup else self.rng.choice(categories),
                name=name,
                email=email,
                contact_number=f"09{self.rng.randint(100000000, 999999999)}",
                # The fixed fields of every registration (ASSUMPTIONS 426).
                designation="Founder" if startup else self.rng.choice(
                    ["Owner", "Faculty member", "Student", "Project officer", "Freelancer"]),
                office_agency=startup.name if startup else self.rng.choice(
                    ["CSU", "Freelance", "Family business", "LGU", "DTI Caraga"]),
            )
            # registration_date is filled automatically; move it before the deadline so it looks real.
            Registration.objects.filter(pk=registration.pk).update(
                registration_date=activity.registration_deadline - timedelta(days=self.rng.randint(1, 14))
            )
            raw = {str(f.pk): self.fake_answer(f, startup.name if startup else "") for f in fields}
            self.save_answers(form, raw, registration=registration)
            registrations.append(registration)
        return registrations

    def mark_attendance(self, registrations: list[Registration], share: float) -> None:
        for registration in registrations:
            if self.rng.random() < share:
                registration.attendance_confirmed = True
                registration.confirmation_method = self.rng.choice(list(Registration.ConfirmationMethod))
                registration.save(update_fields=["attendance_confirmed", "confirmation_method", "updated_at"])

    def create_evaluations(self, registrations: list[Registration], form: FormTemplate, end: date) -> None:
        fields = list(form.fields.all())
        comments = ["The talks were practical.", "More hands-on time please.", "Very helpful for our startup.", ""]
        for registration in registrations:
            if not registration.attendance_confirmed or self.rng.random() > 0.7:
                continue  # only attendees evaluate, and not all of them do
            evaluation = EvaluationSubmission.objects.create(
                template=form,
                registration=registration,
                comments=self.rng.choice(comments),
                certificate_name=registration.name,
                certificate_email=registration.email,
            )
            EvaluationSubmission.objects.filter(pk=evaluation.pk).update(submitted_at=self.at(end + timedelta(days=1), 10))
            self.save_answers(form, {str(f.pk): self.fake_answer(f) for f in fields}, evaluation_submission=evaluation)

    # --- documents ----------------------------------------------------------------

    def create_document(self, activity: Activity, type_name: str, status_name: str) -> None:
        """Save a small placeholder text file and its DOCUMENT row."""
        title = f"{type_name} - {activity.title}"
        content = f"[DEMO PLACEHOLDER]\n{title}\nThis file is fake demo data.\n".encode()
        file_name = f"{slugify(title)}.txt"
        saved_path = default_storage.save(f"demo/{slugify(activity.title)}/{file_name}", ContentFile(content))

        document_type = DocumentType.objects.get(name=type_name)
        if not document_type.needs_review:  # ASSUMPTIONS 432: uploads are approved copies (Approved by System)
            status_name = DocumentStatus.APPROVED
        approved = status_name == DocumentStatus.APPROVED
        Document.objects.create(
            activity=activity,
            document_type=document_type,
            uploaded_by=activity.created_by,
            status=DocumentStatus.objects.get(status_name=status_name),
            reviewed_by=self.staff["Director"] if approved and document_type.needs_review else None,
            reviewed_at=timezone.now() if approved else None,
            title=title,
            file_name=file_name,
            file_path=saved_path,
            file_extension="txt",
            file_size_kb=max(1, math.ceil(len(content) / 1024)),
        )

    # --- database statistics --------------------------------------------------------------

    def update_statistics(self) -> None:
        """ANALYZE the tables just filled, so PostgreSQL knows their real size right away.

        Without it, a table last measured while empty is planned as "1 row", and the report queries can
        nest full scans of every table (seen in the test suite: one query ran for over 20 minutes).
        ANALYZE may run inside this transaction and also counts the rows it inserted.
        """
        models = [Activity, ActivityParticipantCategory, Registration, EvaluationSubmission, FormTemplate, FormField,
                  FormAnswer, Document, Certificate, Startup, ResourcePerson]
        tables = ", ".join(connection.ops.quote_name(model._meta.db_table) for model in models)
        with connection.cursor() as cursor:
            cursor.execute(f"ANALYZE {tables}")

    # --- certificates (Step 10: so the certificate cards have numbers) -----------------

    def demo_design(self) -> bool:
        """Use the active design and the default signatories (ASSUMPTIONS 363); add invented ones only when missing:
        the design as the first active System Administrator, two people and the defaults as the first active member
        who can manage signatories (the Director, ASSUMPTIONS 418)."""
        defaults = certificate_services.default_signatories()
        if certificate_services.active_template() is not None and all(defaults.values()):
            return True
        admin = TBIMember.objects.filter(role__can_manage_users=True, is_active=True).order_by("pk").first()
        manager = TBIMember.objects.filter(role__can_manage_signatories=True, is_active=True).order_by("pk").first()
        if admin is None or manager is None:
            self.stdout.write(self.style.WARNING(
                "  certificates skipped: no active design or default signatories, and no active System Administrator "
                "or member who manages signatories to add them (run seed_data)."
            ))
            return False

        def png(draw, *args) -> SimpleUploadedFile:
            buffer = BytesIO()
            draw(buffer, *args)
            return SimpleUploadedFile("demo.png", buffer.getvalue())

        if certificate_services.active_template() is None:
            certificate_services.upload_template(admin, "[DEMO] Sample design", png(draw_template), activate=True)
        if not all(defaults.values()):
            left = defaults["left"] or certificate_services.add_signatory(
                manager, "[DEMO] TBI Director", "Director, CSU Technology Business Incubator", png(draw_signature, 11))
            right = defaults["right"] or certificate_services.add_signatory(
                manager, "[DEMO] Dr. Vice President", "Vice President for Research, Development and Extension",
                png(draw_signature, 3))
            certificate_services.set_default_signatories(manager, left, right)
        self.stdout.write("  certificate design: invented demo design and/or default signatories added (none was set)")
        return True

    def create_certificates(self) -> int:
        """Create the demo certificates (as the System would at the move to Conducted) and approve the ready ones of
        some activities as the demo Director, through the real services. Only for demo activities that have no
        certificate yet, so running twice adds nothing."""
        todo = [
            (activity, target)
            for title, target in DEMO_CERTIFICATES.items()
            if (activity := Activity.objects.filter(title=title, status__name=ActivityStatus.CONDUCTED).first())
            and not Certificate.objects.filter(activity=activity).exists()
        ]
        if not todo or not self.demo_design():
            return 0
        created = 0
        for activity, target in todo:
            created += certificate_services.generate_missing_certificates(activity)["total"]
            if target == "approved":
                try:  # one approval per activity (ASSUMPTIONS 369); the ready ones are approved at once
                    certificate_services.approve_activity_certificates(self.staff["Director"], activity)
                except certificate_services.CertificateError:
                    pass
            self.stdout.write(f"  certificates ({target}): {activity.title}")
        return created
