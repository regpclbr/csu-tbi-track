"""`python manage.py seed_demo`: DEMO-ONLY fake data for presentations.

Creates the eight activities from the Figma prototype with fake registrations,
attendance, evaluations, resource persons, startups and documents.

- Refuses to run when DEBUG is False, so it can never touch a production database.
- All people and companies are invented. Every email ends in @demo.example.com.
- Demo staff accounts have unusable passwords (they cannot log in).
- Safe to run twice: an activity that already exists (by title) is skipped.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Role, TBIMember
from accounts.seed import load_lookups
from activities import services as activity_services
from activities.models import (
    Activity,
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

DEMO_DOMAIN = "demo.example.com"

FIRST_NAMES = [
    "Andrea", "Bianca", "Carlo", "Daniel", "Elena", "Francis", "Gabriel", "Hannah", "Isabel", "Joshua",
    "Katrina", "Lorenzo", "Marco", "Nicole", "Oscar", "Patricia", "Rafael", "Sofia", "Tristan", "Vanessa",
]
LAST_NAMES = [
    "Abad", "Bautista", "Castillo", "Domingo", "Espino", "Flores", "Garcia", "Herrera", "Ilagan", "Jimenez",
    "Lopez", "Mendoza", "Navarro", "Ocampo", "Pascual", "Quiambao", "Ramos", "Santos", "Torres", "Villanueva",
]

# Invented startups: (name, maturity_stage)
STARTUPS = [
    ("Kalinaw Agritech", "Validation"),
    ("Pantay Logistics", "Early Traction"),
    ("Sibol Health Systems", "Ideation"),
    ("Lakbay Data Labs", "Scaling"),
    ("Tahanan Solar", "Early Traction"),
    ("Habi Textile Works", "Validation"),
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
    category: str
    status: str
    start_offset_days: int  # relative to today
    length_days: int
    max_slots: int
    registrations: int
    budget: str
    venue: str
    resource_persons: list[int]  # indexes into RESOURCE_PERSONS


DEMO_ACTIVITIES = [
    DemoActivity("Startup Pitch Competition", "Forum", "Incubatees", "Conducted", -30, 1, 40, 32, "25000.00",
                 "CSU Gonzaga Function Hall", [0, 5]),
    DemoActivity("Business Model Training", "Training", "Incubatees", "Conducted", -21, 2, 30, 26, "18000.00",
                 "TBI Co-working Space", [3]),
    DemoActivity("Intellectual Property Seminar", "Seminar", "Faculty", "Conducted", -10, 1, 50, 38, "12000.00",
                 "CSU Audio-Visual Room", [2]),
    DemoActivity("Mentoring Session", "Mentoring", "Incubatees", "Ongoing", 0, 1, 15, 12, "5000.00",
                 "TBI Meeting Room", [4]),
    DemoActivity("Product Development Workshop", "Workshop", "MSMEs", "Scheduled", 7, 2, 25, 14, "20000.00",
                 "CSU Engineering Laboratory", [1]),
    DemoActivity("Market Research Training", "Training", "MSMEs", "Scheduled", 14, 1, 30, 9, "15000.00",
                 "TBI Co-working Space", [4]),
    DemoActivity("Investor Networking", "Networking", "Incubatees", "Scheduled", 25, 1, 10, 10, "30000.00",
                 "Hotel Ensenada Conference Room", [5]),  # full: shows "registration closed"
    DemoActivity("Technology Forum", "Forum", "Faculty", "Draft", 40, 1, 60, 0, "35000.00",
                 "CSU Gymnasium", []),
]

# For conducted activities: which required documents exist and their status.
# Missing types show up as gaps in the checklist.
CONDUCTED_DOCUMENTS = {
    "Startup Pitch Competition": {
        "Activity Design": "Approved",
        "Request Letter": "Approved",
        "Invitation Letter": "Approved",
        "Attendance Sheet": "Approved",
        "Accomplishment Report": "Approved",
    },
    "Business Model Training": {
        "Activity Design": "Approved",
        "Request Letter": "Approved",
        "Invitation Letter": "Approved",
        "Attendance Sheet": "For Review",
        # Accomplishment Report missing
    },
    "Intellectual Property Seminar": {
        "Activity Design": "Approved",
        "Invitation Letter": "For Review",
        # Request Letter, Attendance Sheet and Accomplishment Report missing
    },
}
UPCOMING_DOCUMENTS = {
    "Mentoring Session": {"Activity Design": "Approved", "Invitation Letter": "Approved"},
    "Product Development Workshop": {"Activity Design": "For Review"},
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

        self.stdout.write(self.style.SUCCESS(f"Demo data: {created} activities created."))
        self.stdout.write(self.style.WARNING("This is fake demo data. Do not use it in production."))

    # --- people -----------------------------------------------------------------

    def create_demo_staff(self) -> dict[str, TBIMember]:
        """One demo account per non-Admin role. Unusable passwords: they cannot log in."""
        staff = {}
        for role in Role.objects.exclude(role_name=Role.ADMIN):
            email = f"{slugify(role.role_name)}@{DEMO_DOMAIN}"
            member = TBIMember.objects.filter(email=email).first()
            if member is None:
                member = TBIMember.objects.create_user(email, f"Demo {role.role_name}", role, password=None)
            staff[role.role_name] = member
        return staff

    def create_startups(self) -> list[Startup]:
        startups = []
        for number, (name, stage) in enumerate(STARTUPS, start=1):
            startup, _ = Startup.objects.get_or_create(
                email=f"{slugify(name)}@{DEMO_DOMAIN}",
                defaults={
                    "name": name,
                    "navigatu_id": f"DEMO-NAV-{number:03d}",
                    "contact_number": f"0917{number:07d}",
                    "maturity_stage": stage,
                    "last_synced_at": timezone.now(),
                },
            )
            startups.append(startup)
        return startups

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
        creator = self.staff["Community Development Head" if spec.category == "MSMEs" else "Incubation Project Head"]

        # Created through the same services as real activities, so the demo has a real audit log.
        activity = activity_services.create_activity(
            creator,
            {
                "activity_type": ActivityType.objects.get(name=spec.activity_type),
                "participant_category": ParticipantCategory.objects.get(name=spec.category),
                "title": spec.title,
                "description": f"[DEMO] {spec.title} for {spec.category.lower()} of the CSU Technology Business Incubator.",
                "start_date": start,
                "end_date": end,
                "time_schedule": "8:00 AM - 5:00 PM",
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
            registration_form = self.create_registration_form(activity, creator)
            registrations = self.create_registrations(activity, registration_form, spec)
            if spec.status in ("Conducted", "Ongoing"):
                self.mark_attendance(registrations, share=0.8 if spec.status == "Conducted" else 0.5)
            if spec.status == "Conducted":
                evaluation_form = self.create_evaluation_form(activity, creator)
                self.create_evaluations(registrations, evaluation_form, end)

        documents = CONDUCTED_DOCUMENTS.get(spec.title) or UPCOMING_DOCUMENTS.get(spec.title) or {}
        for type_name, status_name in documents.items():
            self.create_document(activity, type_name, status_name)

    # --- forms, registrations, evaluations ----------------------------------

    def create_registration_form(self, activity: Activity, creator: TBIMember) -> FormTemplate:
        form = FormTemplate.objects.create(
            activity=activity, form_type=FormType.REGISTRATION, title=f"{activity.title} Registration", created_by=creator
        )
        FormField.objects.create(template=form, field_label="Organization / Startup", field_type=FieldType.SHORT_TEXT, sort_order=1)
        FormField.objects.create(
            template=form,
            field_label="How did you hear about this activity?",
            field_type=FieldType.MULTIPLE_CHOICE,
            options="Facebook page\nEmail invitation\nFriend or colleague\nCSU website",
            sort_order=2,
        )
        return form

    def create_evaluation_form(self, activity: Activity, creator: TBIMember) -> FormTemplate:
        form = FormTemplate.objects.create(
            activity=activity, form_type=FormType.EVALUATION, title=f"{activity.title} Evaluation", created_by=creator
        )
        FormField.objects.create(template=form, field_label="Overall rating (1-5)", field_type=FieldType.RATING, sort_order=1)
        FormField.objects.create(
            template=form, field_label="What was most useful?", field_type=FieldType.LONG_TEXT, is_required=False, sort_order=2
        )
        return form

    def create_registrations(self, activity: Activity, form: FormTemplate, spec: DemoActivity) -> list[Registration]:
        org_field, source_field = form.fields.order_by("sort_order")
        sources = source_field.options.splitlines()
        registrations = []

        # A few incubatee startups register while logged in.
        linked_startups = self.startups[:3] if spec.category == "Incubatees" else []
        people = [(s.name + " (representative)", s.email, s) for s in linked_startups]
        people += [(name, email, None) for name, email in self.fake_people(spec.registrations - len(people))]

        for name, email, startup in people:
            registration = Registration.objects.create(
                activity=activity,
                template=form,
                startup=startup,
                name=name,
                email=email,
                contact_number=f"09{self.rng.randint(100000000, 999999999)}",
            )
            # registration_date is filled automatically; move it before the deadline so it looks real.
            Registration.objects.filter(pk=registration.pk).update(
                registration_date=activity.registration_deadline - timedelta(days=self.rng.randint(1, 14))
            )
            organization = startup.name if startup else self.rng.choice(["CSU", "Freelance", "Family business", "LGU"])
            FormAnswer.objects.create(field=org_field, answer_source="registration", registration=registration, answer_value=organization)
            FormAnswer.objects.create(
                field=source_field, answer_source="registration", registration=registration, answer_value=self.rng.choice(sources)
            )
            registrations.append(registration)
        return registrations

    def mark_attendance(self, registrations: list[Registration], share: float) -> None:
        for registration in registrations:
            if self.rng.random() < share:
                registration.attendance_confirmed = True
                registration.confirmation_method = self.rng.choice(list(Registration.ConfirmationMethod))
                registration.save(update_fields=["attendance_confirmed", "confirmation_method", "updated_at"])

    def create_evaluations(self, registrations: list[Registration], form: FormTemplate, end: date) -> None:
        rating_field, comment_field = form.fields.order_by("sort_order")
        comments = ["The talks were practical.", "More hands-on time please.", "Very helpful for our startup.", ""]
        for registration in registrations:
            if not registration.attendance_confirmed or self.rng.random() > 0.7:
                continue  # only attendees evaluate, and not all of them do
            evaluation = EvaluationSubmission.objects.create(
                template=form, registration=registration, comments=self.rng.choice(comments)
            )
            EvaluationSubmission.objects.filter(pk=evaluation.pk).update(submitted_at=self.at(end + timedelta(days=1), 10))
            FormAnswer.objects.create(
                field=rating_field, answer_source="evaluation", evaluation_submission=evaluation,
                answer_value=str(self.rng.choice([3, 4, 4, 5, 5])),
            )
            FormAnswer.objects.create(
                field=comment_field, answer_source="evaluation", evaluation_submission=evaluation,
                answer_value=self.rng.choice(comments),
            )

    # --- documents ----------------------------------------------------------------

    def create_document(self, activity: Activity, type_name: str, status_name: str) -> None:
        """Save a small placeholder text file and its DOCUMENT row."""
        title = f"{type_name} - {activity.title}"
        content = f"[DEMO PLACEHOLDER]\n{title}\nThis file is fake demo data.\n".encode()
        file_name = f"{slugify(title)}.txt"
        saved_path = default_storage.save(f"demo/{slugify(activity.title)}/{file_name}", ContentFile(content))

        approved = status_name == DocumentStatus.APPROVED
        Document.objects.create(
            activity=activity,
            document_type=DocumentType.objects.get(name=type_name),
            uploaded_by=activity.created_by,
            status=DocumentStatus.objects.get(status_name=status_name),
            reviewed_by=self.staff["Director"] if approved else None,
            reviewed_at=timezone.now() if approved else None,
            title=title,
            file_name=file_name,
            file_path=saved_path,
            file_extension="txt",
            file_size_kb=max(1, math.ceil(len(content) / 1024)),
        )
