"""`python manage.py seed_testing --reset`: the USER-TESTING database (ASSUMPTIONS 464).

Fills an emptied database with realistic, invented data that shows every feature, so the UAT tasks and the
demonstration need no preparation by hand:

- the real staff accounts of the user testing (System Administrator, Director, four activity staff, two admin staff),
  each with a printed temporary password and no forced password change;
- activities of both TBIs (NAVIGATU: general startups; TARA: agriculture and aquaculture startups) in every state: a
  Draft without documents, a Scheduled one open for registration, one Ongoing TODAY with QR check-in open around
  --checkin-start, four Conducted ones (complete, waiting for certificate approval, older and complete, evaluation
  closed by hand);
- documents with real text (for the AI assistant), a scanned attendance sheet, the Director's work waiting (a document,
  certificates, submissions, assignments of every kind), reports, drafts, a confirmed paper sheet, notifications,
  History rows and (unless --skip-ai-index) the AI index.

Everything goes through the real services (activities, registrations, attendance, documents, certificates, approvals,
reports), so History, notifications and the rules are the real ones. Past events are created "as of" their own day: the
seed moves the clock that every service reads (django.utils.timezone.now, as the tests' clock fixture does) and puts
it back at the end.

Safety: only with DEBUG on and only with --reset, which EMPTIES the database Django is connected to (DATABASE_NAME /
POSTGRES_DB) and the uploads folder (MEDIA_DIR / media) after printing both names. Use it on the separate testing
database (DATABASE_NAME=csu_tbi_testing, MEDIA_DIR=media_testing), never on the development data.

Email: nothing is sent while seeding. Every email the services write during the seed goes to an in-memory mailbox
(counted at the end); queued confirmation / check-in / evaluation-link emails are dropped; all invented addresses end
in @example.com, which config.mail never really emails (EMAIL_BLOCKED_DOMAINS), so the background worker cannot reach
them later either.
"""

from __future__ import annotations

import json
import secrets
import shutil
import time as wallclock
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import override_settings
from django.utils import timezone

from accounts import sample_files as files
from accounts.models import Role, TBIMember
from accounts.seed import load_default_form_fields, load_lookups

TESTING_DOMAIN = "example.com"
LOCMEM_MAILER = {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}

# (key, name, role name, position title, email local part)
ACCOUNTS = [
    ("admin", "Regine Paclibar", Role.SYSTEM_ADMINISTRATOR, "System Administrator", "regine.paclibar"),
    ("director", "Riah E. Encarnacion, DIT", Role.DIRECTOR, "Director, TBI & Manager, NAVIGATU", "riah.encarnacion"),
    ("correa", "John Mark B. Correa", "Incubation Project Head", "Head, Incubation Program", "johnmark.correa"),
    ("barber", "Mary Pilar Barber", "Community Development Head", "Head, Community Development", "marypilar.barber"),
    ("asis", "Jenelyn Asis", "Incubation Project Head", "Manager, TARA", "jenelyn.asis"),
    ("abrea", "Teodoro Jr. Abrea", "Incubation Project Head", "Portfolio Manager", "teodoro.abrea"),
    ("ganoy", "Meledy Ganoy", "Admin Staff", "University Research Associate I", "meledy.ganoy"),
    ("laplana", "Adelberto G. Laplana", "Admin Staff", "Administrative Aide VI", "adelberto.laplana"),
]

FIRST_NAMES = ["Aileen", "Benjie", "Carmela", "Dindo", "Erlinda", "Francis", "Grace", "Hermie", "Imelda", "Jovelyn",
               "Kristine", "Leonel", "Marites", "Nonoy", "Ofelia", "Paolo", "Queenie", "Rodel", "Shiela", "Tirso",
               "Ulysses", "Venus", "Wilfredo", "Xyza", "Yolanda", "Zaldy", "Analyn", "Bryan", "Cherry", "Darwin"]
LAST_NAMES = ["Abellana", "Bagaipo", "Cagas", "Dumaguing", "Escalante", "Fuentes", "Galarrita", "Hinayon", "Igot",
              "Jamero", "Lagunday", "Maglinte", "Navarro", "Ocampo", "Plaza", "Quijano", "Rosales", "Salise",
              "Tumulak", "Uy", "Villaflor", "Yap", "Zamora", "Bacus", "Corvera", "Dagohoy", "Espinosa", "Lumacad"]
# Designation / Office per participant category (invented; DTI and partner agencies count as MSMEs, item 443).
PEOPLE_BY_CATEGORY = {
    "Faculty": [("Instructor I", "CSU College of Engineering and Geosciences"),
                ("Assistant Professor II", "CSU College of Agriculture and Agri-Industries"),
                ("Associate Professor I", "CSU College of Computing and Information Sciences")],
    "Incubatees": [("Founder", "AquaSense Caraga (TARA incubatee)"), ("Co-founder", "BukidLink (NAVIGATU incubatee)"),
                   ("Chief Technology Officer", "Lato Seaweed Ventures (TARA incubatee)"),
                   ("Founder", "Sakay Butuan (NAVIGATU incubatee)"), ("Product Lead", "Tilapia Tech (TARA incubatee)")],
    "MSMEs": [("Owner", "Agusan Coco Products"), ("Manager", "Butuan Tilapia Growers Association"),
              ("Owner", "Kape Surigao"), ("Business Counselor", "DTI Caraga – Negosyo Center"),
              ("Production Head", "Nasipit Dried Fish Processors")],
}
RESOURCE_PERSONS = [
    ("Dr. Ma. Lourdes T. Ybañez", "lourdes.ybanez", "Writing a winning startup grant proposal"),
    ("Engr. Kenneth P. Dagaraga", "kenneth.dagaraga", "IoT sensors for tilapia ponds"),
    ("Atty. Joanna R. Salinas", "joanna.salinas", "Trademarks and copyright for startups"),
    ("Mr. Ariel C. Ombao", "ariel.ombao", "Pitch deck structure and storytelling"),
    ("Ms. Hazel V. Tampos", "hazel.tampos", "Seaweed post-harvest processing"),
    ("Dr. Rene S. Lagrosas", "rene.lagrosas", "Bookkeeping and cash flow for founders"),
]


@dataclass
class Spec:
    """One activity of the testing data."""

    key: str
    title: str
    tbi: str  # "NAVIGATU" or "TARA"
    type_name: str
    categories: list[str]
    creator: str
    fund: str
    venue: str
    objectives: list[str]
    description: str
    budget: list[tuple[str, str, int, str, int, str]]  # (category, particulars, quantity, unit, days, unit price)
    resource_persons: list[int] = field(default_factory=list)
    max_slots: int = 40


SPECS = {
    "draft": Spec(
        "draft", "Startup Grant Proposal Writing Workshop", "NAVIGATU", "Workshop", ["Faculty", "Incubatees"], "abrea",
        "RAF", "CSU Main Campus – University Function Hall",
        ["Explain the requirements of DOST and DTI startup grant programs",
         "Draft the problem, solution and budget sections of a proposal",
         "Review each team's draft with a grant writing mentor"],
        "A one-day workshop where student and faculty startup teams write the first draft of a startup grant proposal "
        "with mentors from NAVIGATU.",
        [("Meals/Snacks", "Lunch and two snacks", 40, "pax", 1, "250.00"),
         ("Honorarium", "Grant writing mentor", 1, "person", 1, "3000.00"),
         ("Supplies", "Proposal templates and printing", 40, "set", 1, "35.00")], [0]),
    "scheduled": Spec(
        "scheduled", "Aquaculture Startup Bootcamp: Tilapia and Seaweed Value Chains", "TARA", "Training",
        ["Incubatees", "MSMEs"], "asis", "TRF", "TARA Hub, CSU College of Agriculture and Agri-Industries",
        ["Map the tilapia and seaweed value chains in Caraga",
         "Identify product and service opportunities for agri-aqua startups",
         "Connect incubatees with growers' associations and buyers"],
        "A two-day bootcamp of TARA for agriculture and aquaculture startups and MSMEs: value chains, market "
        "validation and a field visit to a tilapia farm.",
        [("Meals/Snacks", "Lunch and snacks", 40, "pax", 2, "220.00"),
         ("Transportation", "Van rental for the farm visit", 2, "van", 1, "4500.00"),
         ("Honorarium", "Resource speakers", 2, "person", 1, "3500.00")], [1, 4]),
    "ongoing": Spec(
        "ongoing", "Pitch Deck Clinic for Student Startups", "NAVIGATU", "Mentoring", ["Faculty", "Incubatees", "MSMEs"],
        "correa", "IGF", "NAVIGATU Co-working Space, CSU Main Campus",
        ["Show the ten slides investors expect in a pitch deck", "Give each team feedback on its own deck",
         "Prepare teams for the regional startup pitching competition"],
        "Two afternoons of one-on-one pitch deck mentoring for NAVIGATU teams before the regional pitching competition.",
        [("Meals/Snacks", "Afternoon snacks", 40, "pax", 2, "120.00"),
         ("Honorarium", "Pitch coach", 1, "person", 2, "2500.00")], [3]),
    "complete": Spec(
        "complete", "Smart Farming Technologies Forum for Agri-Startups", "TARA", "Forum", ["Faculty", "Incubatees", "MSMEs"],
        "asis", "BRF", "CSU Audio-Visual Room, Main Campus",
        ["Present low-cost IoT sensors for ponds and farms", "Share results of TARA's pilot farms",
         "Match agri-startups with farmer cooperatives"],
        "A two-day forum on smart farming: IoT pond sensors, drone mapping and farm management apps, with a showcase "
        "of TARA incubatees.",
        [("Meals/Snacks", "Lunch and snacks", 45, "pax", 2, "230.00"),
         ("Venue", "Sound system and LED wall", 1, "lot", 2, "5000.00"),
         ("Honorarium", "Resource speakers", 2, "person", 1, "4000.00"),
         ("Printing", "Tarpaulin and programs", 1, "lot", 1, "2500.00")], [1, 4]),
    "waiting": Spec(
        "waiting", "Intellectual Property and Branding Seminar for Startups", "NAVIGATU", "Seminar",
        ["Faculty", "Incubatees", "MSMEs"], "correa", "RAF", "CSU Engineering Building, Room 301",
        ["Explain patents, utility models, trademarks and copyright", "Show how to search the IPOPHL databases",
         "Help each team plan the protection of its brand"],
        "A seminar with the CSU Innovation and Technology Support Office on protecting a startup's inventions and brand.",
        [("Meals/Snacks", "Lunch and snacks", 40, "pax", 1, "250.00"),
         ("Honorarium", "IP lawyer", 1, "person", 1, "5000.00")], [2]),
    "older": Spec(
        "older", "Seaweed Processing and Product Development Training", "TARA", "Training", ["Incubatees", "MSMEs"],
        "asis", "TRF", "TARA Food Innovation Laboratory, CSU Main Campus",
        ["Teach good manufacturing practices for seaweed products", "Develop two seaweed-based food prototypes",
         "Prepare product labels that meet FDA requirements"],
        "Hands-on training for seaweed farmers' associations and TARA incubatees on drying, processing and packaging.",
        [("Meals/Snacks", "Lunch and snacks", 30, "pax", 2, "220.00"),
         ("Supplies", "Seaweed and laboratory supplies", 1, "lot", 1, "8000.00"),
         ("Honorarium", "Food technologist", 1, "person", 2, "3000.00")], [4], max_slots=30),
    "closed": Spec(
        "closed", "Startup Financial Literacy Workshop", "NAVIGATU", "Workshop", ["Faculty", "Incubatees", "MSMEs"],
        "barber", "IGF", "CSU Main Campus – University Function Hall",
        ["Read a simple income statement and cash flow", "Set up bookkeeping for a young startup",
         "Plan the first year's budget of a startup"],
        "A one-day workshop on bookkeeping, cash flow and budgeting for startup founders and MSME owners.",
        [("Meals/Snacks", "Lunch and snacks", 40, "pax", 1, "250.00"),
         ("Honorarium", "Finance mentor", 1, "person", 1, "3500.00")], [5]),
}


class SeedError(CommandError):
    pass


# --- Clock, tasks and email while seeding ----------------------------------------------------------------------------


class Clock:
    """Moves django.utils.timezone.now (the one clock every service reads) and keeps it ticking."""

    def __init__(self) -> None:
        self.real = timezone.now
        self.offset = timedelta(0)

    def install(self, present: datetime) -> None:
        self.offset = present - self.real()
        timezone.now = lambda: self.real() + self.offset

    def uninstall(self) -> None:
        timezone.now = self.real

    @contextmanager
    def at(self, moment: datetime):
        previous = self.offset
        self.offset = moment - self.real()
        try:
            yield
        finally:
            self.offset = previous


# Background tasks run at once while seeding (PDFs, certificate emails into the in-memory mailbox, reports, drafts);
# every other task (confirmation, check-in and evaluation-link emails, chat titles, indexing) is dropped: the index is
# built once at the end.
RUN_NOW = {
    "certificates.tasks.run_prepare_certificate",
    "certificates.tasks.run_send_certificate",
    "reports.tasks.run_build_report",
    "reports.tasks.run_generate_draft",
    "reports.tasks.run_read_paper_sheet",
}


@contextmanager
def seeding_environment(stdout):
    """Tasks as above, schedules (certificate email retries) not created, every email into the in-memory mailbox."""
    import django_q.tasks as q_tasks
    from django.utils.module_loading import import_string

    real_async, real_schedule = q_tasks.async_task, q_tasks.schedule
    dropped = {"count": 0}

    def async_task(func, *args, **kwargs):
        name = func if isinstance(func, str) else f"{func.__module__}.{func.__name__}"
        if name in RUN_NOW:
            target = import_string(name) if isinstance(func, str) else func
            options = {key: value for key, value in kwargs.items()
                       if key not in ("task_name", "group", "hook", "q_options", "timeout", "save", "cached", "sync")}
            return target(*args, **options)
        dropped["count"] += 1
        return None

    q_tasks.async_task = async_task
    q_tasks.schedule = lambda *args, **kwargs: None
    mailers = {"default": LOCMEM_MAILER, "smtp": LOCMEM_MAILER}
    try:
        with override_settings(MAILERS=mailers):
            mail.outbox = []
            yield dropped
    finally:
        q_tasks.async_task, q_tasks.schedule = real_async, real_schedule


# --- The command -----------------------------------------------------------------------------------------------------


class Command(BaseCommand):
    help = ("USER TESTING ONLY: empty the connected database and uploads folder (--reset) and fill them with realistic "
            "invented data showing every feature. Refuses to run without DEBUG or without --reset.")

    def add_arguments(self, parser) -> None:
        parser.add_argument("--reset", action="store_true",
                            help="Required: empty the CONNECTED database and the uploads folder first.")
        parser.add_argument("--testing-date", default="", help="The day of the user testing, YYYY-MM-DD (default today).")
        parser.add_argument("--checkin-start", default="13:00",
                            help="HH:MM, start time of today's Ongoing activity; QR check-in is open from 60 minutes "
                                 "before to 60 minutes after it (default 13:00).")
        parser.add_argument("--skip-ai-index", action="store_true",
                            help="Do not index the files for the AI assistant now (run `python manage.py "
                                 "index_documents` later).")

    # --- entry ----------------------------------------------------------------------------------------------------

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_testing is for the user testing only and refuses to run when DEBUG is False.")
        if not options["reset"]:
            raise CommandError("seed_testing EMPTIES the connected database and uploads folder. Run it with --reset "
                               "(and only on the testing database: DATABASE_NAME=csu_tbi_testing).")
        self.testing_date = self._parse_date(options["testing_date"])
        self.checkin_start = self._parse_time(options["checkin_start"])
        self.started = wallclock.monotonic()
        self.step_started = self.started
        self.passwords: dict[str, str] = {}

        media_root = Path(settings.MEDIA_ROOT).resolve()
        project = Path(settings.BASE_DIR).resolve()
        if media_root == project or media_root in project.parents or media_root.parent == media_root:
            raise CommandError(f"MEDIA_ROOT {media_root} is the project folder, a folder above it or a drive; "
                               "refusing to empty it. Set MEDIA_DIR=media_testing in .env.")
        database = connection.settings_dict["NAME"]
        self.stdout.write(self.style.WARNING(
            f"seed_testing --reset: EMPTYING database \"{database}\" and uploads folder \"{media_root}\" now."))
        self._wipe(media_root)

        self.clock = Clock()
        local_now = timezone.localtime(self.clock.real())
        self.present = timezone.make_aware(datetime.combine(self.testing_date, local_now.time().replace(microsecond=0)))
        self.clock.install(self.present)
        try:
            with seeding_environment(self.stdout) as dropped:
                self._seed()
                emails = len(mail.outbox)
        finally:
            self.clock.uninstall()
        index = self._index(options["skip_ai_index"])
        call_command("setup_schedules", stdout=self._quiet())
        self._summary(database, media_root, emails, dropped["count"], index)

    # --- helpers --------------------------------------------------------------------------------------------------

    def _parse_date(self, value: str) -> date:
        if not value:
            return timezone.localdate()
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise CommandError("--testing-date must be YYYY-MM-DD.") from error

    def _parse_time(self, value: str) -> time:
        try:
            hour, minute = (int(part) for part in value.split(":"))
            parsed = time(hour, minute)
        except ValueError as error:
            raise CommandError("--checkin-start must be HH:MM (24-hour), for example 13:00.") from error
        if parsed < time(1, 0) or parsed > time(20, 59):
            raise CommandError("--checkin-start must be between 01:00 and 20:59 (the activity runs 3 hours from it).")
        return parsed

    def _quiet(self):
        from io import StringIO

        return StringIO()

    def progress(self, text: str) -> None:
        now = wallclock.monotonic()
        self.stdout.write(f"  [{now - self.started:6.1f}s] {text} ({now - self.step_started:.1f}s)")
        self.step_started = now

    def at(self, day: date, hour: int, minute: int = 0):
        return self.clock.at(timezone.make_aware(datetime.combine(day, time(hour, minute))))

    def moment(self, day: date, hour: int, minute: int = 0) -> datetime:
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    def _wipe(self, media_root: Path) -> None:
        call_command("flush", interactive=False, verbosity=0)
        if media_root.exists():
            for child in media_root.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        media_root.mkdir(parents=True, exist_ok=True)
        call_command("createcachetable", verbosity=0)
        self.progress("database and uploads folder emptied")

    # --- the seed -------------------------------------------------------------------------------------------------

    def _seed(self) -> None:
        load_lookups()
        load_default_form_fields()
        self.progress("lookups and default form fields")
        self.members = self._accounts()
        self.progress("8 accounts with positions")
        self._certificate_design()
        self.progress("certificate design, signatories, Director's linked signature")
        self.people = self._people_pool()
        self.resource_persons = self._resource_persons()
        T = self.testing_date
        self.quarter_start = date(T.year, 3 * ((T.month - 1) // 3) + 1, 1)
        self.previous_quarter = self._quarter_before(self.quarter_start)
        self.quarter_before_that = self._quarter_before(self.previous_quarter[0])
        self.activities = {}
        self._needs_review_type()
        self._conducted_complete()
        self._conducted_waiting()
        self._conducted_older()
        self._conducted_closed_by_hand()
        self._ongoing_today()
        self._scheduled_open()
        self._draft()
        self._director_work()
        self._reports()
        self._analyze()

    @staticmethod
    def _quarter_before(start: date) -> tuple[date, date]:
        end = start - timedelta(days=1)
        return date(end.year, 3 * ((end.month - 1) // 3) + 1, 1), end

    # --- accounts and design --------------------------------------------------------------------------------------

    def _accounts(self) -> dict[str, TBIMember]:
        from accounts import services as account_services

        members = {}
        for key, name, role_name, _position, local in ACCOUNTS:
            role = Role.objects.get(role_name=role_name)
            password = f"Tbi-{secrets.token_hex(3)}-{secrets.token_hex(2)}"
            members[key] = TBIMember.objects.create_user(f"{local}@{TESTING_DOMAIN}", name, role, password=password,
                                                        must_change_password=False)
            self.passwords[key] = password
        admin = members["admin"]
        for key, _name, _role, position, _local in ACCOUNTS:
            row = account_services.add_position(admin, position)
            account_services.set_member_position(admin, members[key], row.pk)
        return members

    def _upload(self, name: str, content: bytes) -> SimpleUploadedFile:
        return SimpleUploadedFile(name, content)

    def _certificate_design(self) -> None:
        from io import BytesIO

        from certificates import services as certificate_services
        from certificates.management.commands.make_sample_certificate_images import draw_signature, draw_template

        admin, director = self.members["admin"], self.members["director"]
        buffer = BytesIO()
        draw_template(buffer)
        certificate_services.upload_template(admin, "CSU-TBI certificate (sample design)",
                                             self._upload("design.png", buffer.getvalue()), activate=True)
        left = certificate_services.add_signatory(director, "Riah E. Encarnacion, DIT",
                                                  "Director, TBI & Manager, NAVIGATU",
                                                  self._upload("riah-signature.png", files.director_signature_png()))
        buffer = BytesIO()
        draw_signature(buffer, 7)
        right = certificate_services.add_signatory(director, "Dr. Ramon T. Salcedo",
                                                   "Vice President for Research, Innovation and Extension",
                                                   self._upload("salcedo-signature.png", buffer.getvalue()))
        certificate_services.set_default_signatories(director, left, right)
        certificate_services.link_signatory_account(director, left, director)
        self.signatories = {"left": left, "right": right}

    def _people_pool(self) -> list[tuple[str, str]]:
        pairs = [(first, last) for last in LAST_NAMES for first in FIRST_NAMES]
        step = 37  # spread the names (deterministic, no repeats while the pool lasts)
        ordered = [pairs[(index * step) % len(pairs)] for index in range(len(pairs))]
        return [(f"{first} {last}", f"{first}.{last}{index}@{TESTING_DOMAIN}".lower().replace(" ", ""))
                for index, (first, last) in enumerate(ordered, start=1)]

    def _next_people(self, count: int) -> list[tuple[str, str]]:
        taken, self.people = self.people[:count], self.people[count:]
        return taken

    def _resource_persons(self):
        from activities import services as activity_services

        return [activity_services.create_resource_person(self.members["correa"], name, f"{local}@{TESTING_DOMAIN}",
                                                         f"0917{index:07d}")
                for index, (name, local, _topic) in enumerate(RESOURCE_PERSONS, start=1)]

    def _needs_review_type(self) -> None:
        """ASSUMPTIONS 432: uploads are approved copies unless the type needs review (a Django admin switch). The
        Invitation Letter is switched on here so a document waits for the Director."""
        from documents.models import DocumentType

        DocumentType.objects.filter(name="Invitation Letter").update(needs_review=True)

    # --- activity building blocks -----------------------------------------------------------------------------------

    def _data(self, spec: Spec, start: date, end: date, start_time: time, end_time: time,
              deadline: datetime, evaluation_deadline: datetime | None) -> dict:
        from activities.models import ActivityType, BudgetCategory, FundSource, ParticipantCategory

        categories = {row.name: row for row in BudgetCategory.objects.all()}
        return {
            "activity_type": ActivityType.objects.get(name=spec.type_name),
            "participant_categories": list(ParticipantCategory.objects.filter(name__in=spec.categories)),
            "title": spec.title,
            "description": f"{spec.tbi}: {spec.description}",
            "objectives": "\n".join(spec.objectives),
            "start_date": start, "end_date": end, "start_time": start_time, "end_time": end_time,
            "venue": spec.venue, "max_slots": spec.max_slots,
            "registration_deadline": deadline, "evaluation_deadline": evaluation_deadline,
            "fund_source": FundSource.objects.get(code=spec.fund),
            "fund_account": f"{spec.tbi} – Training and Extension Services",
            "budget_lines": [{"category": categories[cat], "particulars": particulars, "quantity": quantity,
                              "unit": unit, "days": days, "unit_price": Decimal(price)}
                             for cat, particulars, quantity, unit, days, price in spec.budget],
        }

    def _letter_pdf(self, kind: str, spec: Spec, start: date, end: date) -> bytes:
        dates = start.strftime("%B %d, %Y") if start == end else f"{start:%B %d} to {end:%B %d, %Y}"
        total = sum(Decimal(price) * quantity * days for _c, _p, quantity, _u, days, price in spec.budget)
        creator = next(name for key, name, *_ in ACCOUNTS if key == spec.creator)
        if kind == "Request Letter":
            return files.make_pdf(f"Request Letter – {spec.title}", [
                "MEMORANDUM",
                "FOR: Riah E. Encarnacion, DIT, Director, TBI & Manager, NAVIGATU",
                f"FROM: {creator}",
                f"SUBJECT: Request to conduct the {spec.title}",
                f"May I request your approval to conduct the {spec.title} on {dates} at the {spec.venue}. The activity "
                f"is organized by {spec.tbi} of the CSU Technology Business Incubator for "
                f"{', '.join(spec.categories)}.",
                f"The total budget requested is PHP {total:,.2f}, charged to the {spec.fund} fund. The expected number "
                f"of participants is {spec.max_slots}.",
                "Attached is the Activity Design for your reference. Thank you.",
                "Noted:", "", "Riah E. Encarnacion, DIT", "Director, TBI & Manager, NAVIGATU"])
        if kind == "Activity Design":
            lines = [f"## Rationale", spec.description, "## Objectives",
                     *[f"{index}. {text}" for index, text in enumerate(spec.objectives, start=1)],
                     "## Schedule and venue", f"{dates}, {spec.venue}.",
                     "## Participants", f"{spec.max_slots} participants: {', '.join(spec.categories)}.",
                     "## Budget", f"Source of fund: {spec.fund}. Total: PHP {total:,.2f}."]
            lines += [f"- {particulars}: {quantity} {unit} x {days} day(s) x PHP {Decimal(price):,.2f}"
                      for _cat, particulars, quantity, unit, days, price in spec.budget]
            lines += ["## Expected outputs", "Attendance sheets, evaluation results, an activity report and photos.",
                      "Prepared by:", creator]
            return files.make_pdf(f"Activity Design – {spec.title}", lines)
        if kind == "Invitation Letter":
            return files.make_pdf(f"Invitation – {spec.title}", [
                "Dear Partner,",
                f"The CSU Technology Business Incubator ({spec.tbi}) invites you and your team to the {spec.title} on "
                f"{dates} at the {spec.venue}.",
                spec.description,
                "Please register through the link in our announcement. Slots are limited.",
                "Sincerely,", creator])
        raise ValueError(kind)

    def _report_pdf(self, spec: Spec, start: date, end: date, attended: int, average: str) -> bytes:
        return files.make_pdf(f"Activity Report – {spec.title}", [
            "## I. Activity details",
            f"Title: {spec.title}. Date: {start:%B %d, %Y}" + (f" to {end:%B %d, %Y}" if end != start else "") + ". "
            f"Venue: {spec.venue}. Organized by {spec.tbi}, CSU Technology Business Incubator.",
            "## II. Highlights",
            f"{attended} participants attended. The resource speakers covered: " + "; ".join(spec.objectives) + ".",
            f"The participants rated the activity {average} out of 5 on average. Most comments asked for more "
            "hands-on time and a follow-up session.",
            "## III. Issues and recommendations",
            "Registration at the venue was slow on the first day; the QR check-in will be used for all participants "
            "next time. A follow-up mentoring session is recommended within three months.",
            "Prepared by:", next(name for key, name, *_ in ACCOUNTS if key == spec.creator)])

    def _create(self, spec: Spec, start: date, end: date, start_time: time, end_time: time, *,
                created_on: date, documents: tuple[str, ...] = ("Activity Design", "Request Letter"),
                evaluation_days: int | None = 7, prepare: bool = False):
        """Create through the New activity form's service, as of `created_on` (both creation documents = Scheduled)."""
        from activities import services as activity_services
        from documents.models import DocumentType
        from documents.services import NewDocument, create_activity_with_documents
        from registrations import services as registration_services
        from registrations.models import FormType

        creator = self.members[spec.creator]
        deadline = self.moment(start - timedelta(days=1), 17)
        evaluation_deadline = self.moment(end + timedelta(days=evaluation_days), 17) if evaluation_days else None
        rows = [NewDocument(DocumentType.objects.get(name=name),
                            self._upload(f"{name.lower().replace(' ', '-')}.pdf",
                                         self._letter_pdf(name, spec, start, end)),
                            f"{name} – {spec.title}")
                for name in documents]
        with self.at(created_on, 9):
            activity = create_activity_with_documents(
                creator, self._data(spec, start, end, start_time, end_time, deadline, evaluation_deadline), rows,
                prepare_certificates=prepare)
            for index in spec.resource_persons:
                activity_services.add_resource_person(creator, activity, self.resource_persons[index],
                                                      RESOURCE_PERSONS[index][2])
            if activity.status.name == "Scheduled":
                registration_services.create_form(creator, activity, FormType.REGISTRATION)
                registration_services.create_form(creator, activity, FormType.EVALUATION)
                registration_services.mark_registration_form_ready(creator, activity)
        self.activities[spec.key] = activity
        return activity

    def _answers(self, form, seed: int, organization: str = "") -> dict:
        from registrations import services as registration_services
        from registrations.models import FieldType

        answers = {}
        for index, field in enumerate(form.fields.order_by("sort_order", "pk")):
            options = registration_services.parse_options(field.options)
            if field.field_type == FieldType.RATING:
                answers[str(field.pk)] = [5, 4, 5, 4, 3, 5, 4][(seed + index) % 7]
            elif field.field_type == FieldType.CHECKBOX:
                answers[str(field.pk)] = options[:1]
            elif field.field_type == FieldType.MULTIPLE_CHOICE:
                answers[str(field.pk)] = options[(seed + index) % len(options)] if options else ""
            elif field.field_type == FieldType.SHORT_TEXT:
                answers[str(field.pk)] = organization or "CSU"
            else:
                answers[str(field.pk)] = ["The examples from Caraga startups were very useful.",
                                          "More hands-on time and a follow-up session, please.",
                                          "Clear talks; the mentors answered all our questions.",
                                          "Please share the slides and templates after the session.",
                                          "" if not field.is_required else "Good."][seed % 5]
        return answers

    def _signature(self, kind: str, seed: int, name: str) -> dict:
        if kind == "draw":
            return {"kind": "draw", "drawn": files.drawn_signature_data_url(seed)}
        if kind == "upload":
            return {"kind": "upload", "file": self._upload(f"signature-{seed}.jpg", files.uploaded_signature_jpg(seed))}
        return {"kind": "typed", "typed_name": name, "confirmed": True}

    def _register(self, activity, count: int, *, first_day: date, last_day: date):
        """Pre-registrations through register(), spread from first_day to last_day, each with a signature (draw /
        upload / typed in turn) and the fixed fields."""
        from registrations import services as registration_services
        from registrations.models import FormType

        form = registration_services.get_form(activity, FormType.REGISTRATION)
        categories = [c.name for c in activity.participant_categories.order_by("name")]
        plan = self._category_plan(categories, count)
        registrations = []
        span = max(0, (last_day - first_day).days)
        for index, ((name, email), category) in enumerate(zip(self._next_people(count), plan)):
            designation, office = PEOPLE_BY_CATEGORY[category][index % len(PEOPLE_BY_CATEGORY[category])]
            day = first_day + timedelta(days=(index * 3) % (span + 1))
            with self.at(day, 8 + index % 8, (index * 7) % 60):
                registrations.append(registration_services.register(
                    activity, name=name, email=email, contact_number=f"09{170000000 + index * 7919:09d}"[:11],
                    designation=designation, office_agency=office,
                    participant_category=activity.participant_categories.get(name=category).pk,
                    answers=self._answers(form, index, office),
                    signature=self._signature(("draw", "upload", "typed")[index % 3], activity.pk * 100 + index, name)))
        return registrations

    @staticmethod
    def _category_plan(categories: list[str], count: int) -> list[str]:
        """Faculty at most about 20 %, the rest Incubatees and MSMEs (NAVIGATU's mix guideline, item 424)."""
        others = [c for c in categories if c != "Faculty"] or categories
        plan = []
        for index in range(count):
            if "Faculty" in categories and index % 5 == 4:
                plan.append("Faculty")
            else:
                plan.append(others[index % len(others)])
        return plan

    def _check_in(self, activity, registrations, day: date, share: float, start_time: time, staff_every: int = 4):
        """Attendance for one day: most by QR (check_in, 5-40 minutes before the start), every `staff_every`-th by
        staff (set_attendance, the Registrations tab's service). Returns the registrations that attended."""
        from registrations import attendance
        from registrations import services as registration_services

        chosen = registrations[: max(1, round(len(registrations) * share))]
        creator = activity.created_by
        for index, registration in enumerate(chosen):
            minutes = 5 + (index * 7) % 35
            moment = self.moment(day, start_time.hour, start_time.minute) - timedelta(minutes=minutes)
            with self.clock.at(moment):
                if index % staff_every == staff_every - 1:
                    registration_services.set_attendance(creator, activity, [registration.pk], True, day=day)
                else:
                    attendance.check_in(activity, registration.email)
        return chosen

    def _walk_ins(self, activity, day: date, start_time: time, count: int) -> list:
        from registrations import attendance

        made = []
        categories = [c.name for c in activity.participant_categories.order_by("name")]
        for index, (name, email) in enumerate(self._next_people(count)):
            category = [c for c in categories if c != "Faculty"][index % max(1, len(categories) - 1)] \
                if len(categories) > 1 else categories[0]
            designation, office = PEOPLE_BY_CATEGORY[category][(index + 2) % len(PEOPLE_BY_CATEGORY[category])]
            moment = self.moment(day, start_time.hour, start_time.minute) + timedelta(minutes=10 + index * 6)
            with self.clock.at(moment):
                result = attendance.register_walk_in(
                    activity, name=name, email=email, contact_number="0918" + f"{index:07d}", designation=designation,
                    office_agency=office, participant_category=activity.participant_categories.get(name=category).pk,
                    signature=self._signature("typed" if index % 2 else "draw", activity.pk * 1000 + index, name))
            made.append(result.registration)
        return made

    def _evaluate(self, activity, attendees, day: date, share: float, photos: int = 0):
        """Evaluations through submit_evaluation (the personal link's service), some with participant photos."""
        from registrations import services as registration_services
        from registrations.models import FormType

        form = registration_services.get_form(activity, FormType.EVALUATION)
        submitted = []
        for index, registration in enumerate(attendees[: max(1, round(len(attendees) * share))]):
            attachments = []
            if index < photos:
                attachments = [self._upload(f"photo-{index + 1}.jpg",
                                            files.event_photo_jpg(activity.pk * 10 + index, activity.title))]
            with self.at(day, 9 + index % 8, (index * 11) % 60):
                submitted.append(registration_services.submit_evaluation(
                    registration_services.evaluation_token(registration), certificate_name=registration.name,
                    certificate_email=registration.email,
                    comments=["Very practical and well organized.", "We want a follow-up session.",
                              "The venue was a bit warm, but the talks were great.", ""][index % 4],
                    answers=self._answers(form, index), attachments=attachments))
        return submitted

    def _tick_photos(self, activity, member, how_many: int, day: date) -> None:
        from registrations import evaluation_photos

        with self.at(day, 10):
            for attachment in evaluation_photos.attachments_for(activity).order_by("pk")[:how_many]:
                evaluation_photos.set_use_in_reports(member, attachment, True)

    def _run_activity(self, activity, start: date, end: date, start_time: time, end_time: time,
                      registrations, share: float, walk_ins: int) -> list:
        """Ongoing at the start, attendance each day (+ walk-ins on day 1), Conducted after the end."""
        from activities import services as activity_services

        with self.clock.at(self.moment(start, start_time.hour, start_time.minute) + timedelta(minutes=1)):
            activity_services.refresh_status(activity)
        attended: dict[int, object] = {}
        day = start
        while day <= end:
            for registration in self._check_in(activity, registrations, day, share if day == start else share - 0.1,
                                               start_time):
                attended[registration.pk] = registration
            if day == start and walk_ins:
                for registration in self._walk_ins(activity, day, start_time, walk_ins):
                    attended[registration.pk] = registration
            day += timedelta(days=1)
        with self.clock.at(self.moment(end, end_time.hour, end_time.minute) + timedelta(minutes=30)):
            activity_services.refresh_status(activity)  # Conducted (attendance exists): certificates by the System
        activity.refresh_from_db()
        return list(attended.values())

    def _upload_document(self, member, activity, type_name: str, content: bytes, file_name: str, title: str,
                         on: date):
        from documents.models import DocumentType
        from documents.services import upload_document

        with self.at(on, 10):
            return upload_document(member, activity, DocumentType.objects.get(name=type_name),
                                   self._upload(file_name, content), title)

    def _generate_forms(self, activity, member, on: date, kinds=("attendance", "confirmation")) -> None:
        from registrations import attendance_forms

        with self.at(on, 14):
            for kind in kinds:
                attendance_forms.generate_form(member, activity, kind)

    def _approve_certificates(self, activity, on: date) -> None:
        from certificates import services as certificate_services

        with self.at(on, 9, 30):
            certificate_services.approve_activity_certificates(self.members["director"], activity)

    # --- the activities ---------------------------------------------------------------------------------------------

    def _conducted_complete(self) -> None:
        """(d) previous quarter, TARA, complete: per-day attendance, walk-ins, evaluations with photos (some ticked),
        generated forms, Activity Report, Event Photos, certificates approved / issued and emailed / one handed over,
        a confirmed paper sheet (from a scan)."""
        from certificates import services as certificate_services
        from certificates.models import Certificate

        spec = SPECS["complete"]
        q_start, _q_end = self.previous_quarter
        start = q_start + timedelta(days=40)
        end = start + timedelta(days=1)
        activity = self._create(spec, start, end, time(8, 0), time(17, 0), created_on=start - timedelta(days=30),
                                documents=("Activity Design", "Request Letter"))
        registrations = self._register(activity, 26, first_day=start - timedelta(days=25), last_day=start - timedelta(days=3))
        attendees = self._run_activity(activity, start, end, time(8, 0), time(17, 0), registrations, 0.85, walk_ins=3)
        self._evaluate(activity, attendees, end + timedelta(days=1), 0.8, photos=4)
        self._tick_photos(activity, self.members["asis"], 2, end + timedelta(days=2))
        asis = self.members["asis"]
        self._generate_forms(activity, asis, end + timedelta(days=2))
        self._upload_document(asis, activity, "Activity Report", self._report_pdf(spec, start, end, len(attendees), "4.5"),
                              "activity-report.pdf", f"Activity Report – {spec.title}", end + timedelta(days=3))
        self._upload_document(asis, activity, "Event Photos", files.event_photo_jpg(501, spec.title), "event-photos.jpg",
                              f"Event Photos – {spec.title}", end + timedelta(days=3))
        # The scanned paper sheet of day 1 (the TBI's paper copy), read and confirmed (item 443).
        rows = [(r.name, r.designation, r.office_agency) for r in registrations[:8]]
        scan = self._upload_document(asis, activity, "Attendance Sheet",
                                     files.scanned_attendance_jpg(spec.title, f"Day 1 – {start:%B %d, %Y}", rows),
                                     "attendance-day-1-scan.jpg", "Attendance Sheet – Day 1 (scanned paper copy)",
                                     end + timedelta(days=3))
        self._confirm_paper_sheet(scan, rows, start, end + timedelta(days=4))
        self._approve_certificates(activity, end + timedelta(days=4))
        appreciation = Certificate.objects.filter(activity=activity, recipient_type="resource_person").order_by("pk").first()
        if appreciation is not None:
            with self.at(end + timedelta(days=6), 15):
                certificate_services.mark_handed_over(asis, certificate_ids=[appreciation.pk])
        self.progress(f"(d) Conducted, complete: {spec.title}")

    def _confirm_paper_sheet(self, document, rows, day: date, on: date) -> None:
        """Read the scan's rows (the seed knows what it printed on the invented scan, so no AI call), correct one row
        and confirm, through reports.paper_sheets."""
        from reports import paper_sheets

        reply = json.dumps({"rows": [{"name": name, "designation": designation, "office": office}
                                     for name, designation, office in rows]})

        class KnownRows:  # stands in for Gemini for this one invented scan
            def read_file(self, data, mime_type, instruction=""):
                return reply

        member = self.members["asis"]
        real_gateway = paper_sheets.get_gateway
        paper_sheets.get_gateway = lambda: KnownRows()
        try:
            with self.at(on, 10):
                sheet = paper_sheets.start_from_document(member, document, "attendance", day=day)
        finally:
            paper_sheets.get_gateway = real_gateway
        sheet.refresh_from_db()
        with self.at(on, 10, 20):
            first = sheet.rows.order_by("row_number").first()
            changes = [{"row_id": row.pk, "name": row.name + (" Jr." if row.pk == first.pk else ""),
                        "designation": row.designation, "office": row.office,
                        "category_id": row.participant_category_id or self._category_id("MSMEs"), "excluded": False}
                       for row in sheet.rows.all()]
            paper_sheets.save_rows(member, sheet, changes)
            paper_sheets.confirm(member, sheet)

    @staticmethod
    def _category_id(name: str) -> int:
        from activities.models import ParticipantCategory

        return ParticipantCategory.objects.get(name=name).pk

    def _conducted_waiting(self) -> None:
        """(d) previous quarter, NAVIGATU: certificates waiting for the Director's approval, the Attendance Sheet
        MISSING (For payment: Generate), photos ticked, an Invitation Letter waiting for review (assigned)."""
        spec = SPECS["waiting"]
        q_start, q_end = self.previous_quarter
        start = min(q_start + timedelta(days=62), q_end - timedelta(days=5))
        activity = self._create(spec, start, start, time(8, 30), time(16, 30), created_on=start - timedelta(days=21))
        registrations = self._register(activity, 22, first_day=start - timedelta(days=18), last_day=start - timedelta(days=2))
        attendees = self._run_activity(activity, start, start, time(8, 30), time(16, 30), registrations, 0.8, walk_ins=2)
        self._evaluate(activity, attendees, start + timedelta(days=1), 0.75, photos=3)
        self._tick_photos(activity, self.members["correa"], 1, start + timedelta(days=2))
        self._upload_document(self.members["abrea"], activity, "Invitation Letter",
                              self._letter_pdf("Invitation Letter", spec, start, start), "invitation.pdf",
                              f"Invitation Letter – {spec.title} (partner agencies)", start + timedelta(days=2))
        self.progress(f"(d) Conducted, certificates waiting, Attendance Sheet missing: {spec.title}")

    def _conducted_older(self) -> None:
        """(d) the quarter before: complete, certificates issued."""
        spec = SPECS["older"]
        q_start, _ = self.quarter_before_that
        start = q_start + timedelta(days=50)
        end = start + timedelta(days=1)
        activity = self._create(spec, start, end, time(8, 0), time(17, 0), created_on=start - timedelta(days=28))
        registrations = self._register(activity, 18, first_day=start - timedelta(days=20), last_day=start - timedelta(days=2))
        attendees = self._run_activity(activity, start, end, time(8, 0), time(17, 0), registrations, 0.9, walk_ins=1)
        self._evaluate(activity, attendees, end + timedelta(days=1), 1.0, photos=2)
        asis = self.members["asis"]
        self._generate_forms(activity, asis, end + timedelta(days=2))
        self._upload_document(asis, activity, "Activity Report", self._report_pdf(spec, start, end, len(attendees), "4.6"),
                              "activity-report.pdf", f"Activity Report – {spec.title}", end + timedelta(days=3))
        self._upload_document(asis, activity, "Event Photos", files.event_photo_jpg(777, spec.title), "photos.jpg",
                              f"Event Photos – {spec.title}", end + timedelta(days=3))
        self._approve_certificates(activity, end + timedelta(days=3))
        self.progress(f"(d) Conducted, older quarter, certificates issued: {spec.title}")

    def _conducted_closed_by_hand(self) -> None:
        """(e) a few days ago: the evaluation closed by hand; Activity Report and Event Photos missing (For payment:
        Generate / Upload); a scanned Attendance Sheet not read yet ("Read the rows with AI" during the testing)."""
        from activities import services as activity_services

        spec = SPECS["closed"]
        start = self.testing_date - timedelta(days=3)
        activity = self._create(spec, start, start, time(13, 0), time(17, 0), created_on=start - timedelta(days=20),
                                evaluation_days=10)
        registrations = self._register(activity, 20, first_day=start - timedelta(days=16), last_day=start - timedelta(days=1))
        attendees = self._run_activity(activity, start, start, time(13, 0), time(17, 0), registrations, 0.8, walk_ins=2)
        self._evaluate(activity, attendees, start + timedelta(days=1), 0.5)
        barber = self.members["barber"]
        rows = [(r.name, r.designation, r.office_agency) for r in attendees[:10]]
        self._upload_document(barber, activity, "Attendance Sheet",
                              files.scanned_attendance_jpg(spec.title, f"{start:%B %d, %Y}", rows),
                              "attendance-scan.jpg", "Attendance Sheet (scanned paper copy)", start + timedelta(days=1))
        with self.at(start + timedelta(days=2), 9):
            activity_services.set_evaluation_closed_by_hand(barber, activity, True)
        self._approve_certificates(activity, start + timedelta(days=2))
        self.progress(f"(e) Conducted, evaluation closed by hand: {spec.title}")

    def _ongoing_today(self) -> None:
        """(c) Ongoing: yesterday and today from --checkin-start for 3 hours. Pre-registrations (some "Will not
        attend"), day 1 attendance with the three kinds of signature and a walk-in; certificates prepared and approved
        early by the Director, so a participant who checks in, evaluates and is marked Conducted gets the email."""
        from activities import services as activity_services
        from certificates import services as certificate_services
        from registrations import services as registration_services

        spec = SPECS["ongoing"]
        start, end = self.testing_date - timedelta(days=1), self.testing_date
        start_time = self.checkin_start
        end_time = (datetime.combine(end, start_time) + timedelta(hours=3)).time()
        if end_time < start_time:
            end_time = time(23, 59)
        activity = self._create(spec, start, end, start_time, end_time, created_on=start - timedelta(days=20),
                                evaluation_days=14, prepare=True)
        self._approve_certificates(activity, start - timedelta(days=5))
        registrations = self._register(activity, 18, first_day=start - timedelta(days=16), last_day=start - timedelta(days=2))
        for registration in registrations[-3:]:  # "I can no longer attend"
            with self.at(start - timedelta(days=1), 19):
                registration_services.cancel_registration(registration)
        with self.clock.at(self.moment(start, start_time.hour, start_time.minute) + timedelta(minutes=1)):
            activity_services.refresh_status(activity)
        self._check_in(activity, registrations[:-3], start, 0.75, start_time)
        self._walk_ins(activity, start, start_time, 1)
        assert certificate_services.is_approved(activity)
        self.progress(f"(c) Ongoing today, check-in {self._window_text(start_time)}: {spec.title}")

    def _window_text(self, start_time: time) -> str:
        opens = datetime.combine(self.testing_date, start_time) - timedelta(minutes=settings.PARTICIPANT_CHECKIN_EARLY_MINUTES)
        closes = datetime.combine(self.testing_date, start_time) + timedelta(minutes=settings.PARTICIPANT_CHECKIN_LATE_MINUTES)
        return f"{opens:%H:%M}-{closes:%H:%M}"

    def _scheduled_open(self) -> None:
        """(b) Scheduled in a week, registration OPEN (form ready, deadline ahead, slots left), certificates prepared
        (waiting for the Director), an Invitation Letter waiting for review."""
        spec = SPECS["scheduled"]
        start = self.testing_date + timedelta(days=7)
        activity = self._create(spec, start, start + timedelta(days=1), time(8, 0), time(17, 0),
                                created_on=self.testing_date - timedelta(days=12), evaluation_days=10)
        self._register(activity, 9, first_day=self.testing_date - timedelta(days=10), last_day=self.testing_date - timedelta(days=1))
        from certificates import services as certificate_services

        with self.at(self.testing_date - timedelta(days=2), 11):
            certificate_services.prepare_activity_certificates(self.members["asis"], activity)
        self._upload_document(self.members["correa"], activity, "Invitation Letter",
                              self._letter_pdf("Invitation Letter", spec, start, start + timedelta(days=1)),
                              "invitation.pdf", f"Invitation Letter – {spec.title}", self.testing_date - timedelta(days=1))
        self.progress(f"(b) Scheduled, registration open: {spec.title}")

    def _draft(self) -> None:
        """(a) a Draft without documents (upload the Request Letter and Activity Design: Scheduled automatically)."""
        spec = SPECS["draft"]
        start = self.testing_date + timedelta(days=30)
        self._create(spec, start, start, time(8, 0), time(17, 0), created_on=self.testing_date - timedelta(days=1),
                     documents=())
        self.progress(f"(a) Draft without documents: {spec.title}")

    # --- the Director's work --------------------------------------------------------------------------------------

    def _submission_pdf(self, title: str, author: str, body: list[str]) -> SimpleUploadedFile:
        content = files.make_pdf(title, [*body, "Prepared by:", author, "", "Noted:", "", "", "Riah E. Encarnacion, DIT",
                                         "Director, TBI & Manager, NAVIGATU"])
        return self._upload(title.lower().replace(" ", "-")[:60] + ".pdf", content)

    def _director_work(self) -> None:
        """Submissions (waiting, returned, unapproved with a reason), the document waiting, and one assignment of
        every kind (one done, one overdue, one allowing "May stamp my signature")."""
        from approvals import assignments
        from approvals import services as approval_services
        from approvals.models import WorkAssignment

        T, m, director = self.testing_date, self.members, self.members["director"]
        complete, waiting = self.activities["complete"], self.activities["waiting"]
        with self.at(T - timedelta(days=2), 10):
            approval_services.submit(m["asis"], self._submission_pdf(
                "TARA Quarterly Terminal Report", "Jenelyn Asis",
                ["TARA conducted the Smart Farming Technologies Forum and the Seaweed Processing Training this year.",
                 "Seven agri-aqua startups are now incubated; two received DOST grants."]),
                "TARA Quarterly Terminal Report", note="For your approval and signature, Ma'am.", activity=complete)
        with self.at(T - timedelta(days=6), 10):
            returned = approval_services.submit(m["abrea"], self._submission_pdf(
                "Request for Mentors Honoraria", "Teodoro Jr. Abrea",
                ["Request to release the honoraria of the four pitch coaches of the Pitch Deck Clinic."]),
                "Request for Mentors' Honoraria")
        with self.at(T - timedelta(days=5), 15):
            approval_services.return_submission(director, returned, "Please attach the signed attendance of the "
                                                                    "mentors and the computation per session.")
        with self.at(T - timedelta(days=9), 10):
            unapproved = approval_services.submit(m["barber"], self._submission_pdf(
                "Community Extension Plan 2027", "Mary Pilar Barber",
                ["Planned community extension activities of the TBI for 2027, with the partner LGUs and agencies."]),
                "Community Extension Plan 2027")
        with self.at(T - timedelta(days=8), 9):
            approval_services.approve_submission(director, unapproved)
        with self.at(T - timedelta(days=4), 16):
            approval_services.unapprove_submission(director, unapproved, "The budget table changed after the planning "
                                                                         "meeting. Please update it and submit again.")
        with self.at(T - timedelta(days=1), 10):
            signing = approval_services.submit(m["ganoy"], self._submission_pdf(
                "Monthly Activity Summary November", "Meledy Ganoy",
                ["Summary of the TBI activities, documents and certificates for November."]),
                "Monthly Activity Summary – November")
        Kind = WorkAssignment.Kind
        invitation = waiting.documents.filter(document_type__name="Invitation Letter").first()
        with self.at(T - timedelta(days=1), 11):
            assignments.assign_work(director, Kind.ACTIVITY, m["ganoy"], activity=self.activities["ongoing"],
                                    note="Please handle the walk-ins and the attendance today.", due_date=T)
            assignments.assign_work(director, Kind.CERTIFICATE_APPROVAL, m["asis"], activity=waiting,
                                    note="Please check the names and approve.", due_date=T + timedelta(days=3))
            assignments.assign_work(director, Kind.SIGNATORY_CHOICE, m["barber"], activity=self.activities["scheduled"],
                                    note="Choose the signatories for the bootcamp certificates.")
            assignments.assign_work(director, Kind.DOCUMENT_REVIEW, m["correa"], document=invitation,
                                    due_date=T + timedelta(days=2))
            assignments.assign_work(director, Kind.SUBMISSION_REVIEW, m["correa"], submission=signing,
                                    note="You may stamp my signature after checking it.", allow_signing=True)
            assignments.assign_work(director, Kind.SIGNATORIES, m["laplana"],
                                    note="Add the new VP's signature when it arrives.", due_date=T + timedelta(days=7))
        with self.at(T - timedelta(days=10), 9):
            assignments.assign_work(director, Kind.REPORT, m["barber"],
                                    report_title=f"Accomplishment Report – Q{(self.previous_quarter[0].month - 1) // 3 + 1} "
                                                 f"{self.previous_quarter[0].year}",
                                    note="For the university's quarterly report.", due_date=T - timedelta(days=3))
            text_task = assignments.assign_work(director, Kind.CERTIFICATE_TEXT, m["asis"],
                                                note="Use the wording from the Office of the President's memo.")
        with self.at(T - timedelta(days=7), 14):
            assignments.mark_done(m["asis"], text_task)
        self.progress("Director's work: 4 submissions, 8 assignments (every kind, one done, one overdue)")

    # --- reports ----------------------------------------------------------------------------------------------------

    def _reports(self) -> None:
        from reports import generator
        from reports import services as report_services

        T, m = self.testing_date, self.members
        q_start, q_end = self.previous_quarter
        with self.at(T - timedelta(days=4), 10):
            report_services.request_report(m["ganoy"], {
                "report_type": "accomplishment", "format": "pdf", "date_range_start": q_start.isoformat(),
                "date_range_end": q_end.isoformat(),
                "notes": "Highlight the TARA activities for agri-aqua startups."})
            report_services.request_report(m["ganoy"], {
                "report_type": "participation", "format": "excel", "date_range_start": q_start.isoformat(),
                "date_range_end": q_end.isoformat()})
            report_services.request_report(m["correa"], {
                "report_type": "evaluation", "format": "pdf", "date_range_start": date(T.year, 1, 1).isoformat(),
                "date_range_end": date(T.year, 12, 31).isoformat()})
        self.progress("3 figures reports (accomplishment PDF, participation Excel, evaluation PDF)")
        complete = self.activities["complete"]
        with self.at(T - timedelta(days=3), 13):
            draft = generator.create_draft(m["asis"], kind="activity_report", activity_id=complete.pk,
                                           message="Write the Activity Report for the Supply Office.")
        with self.at(T - timedelta(days=3), 13, 30):
            generator.request_revision(m["asis"], draft, "Make the highlights shorter and mention the farm visit.")
        with self.at(T - timedelta(days=2), 9):
            generator.create_draft(m["correa"], kind="accomplishment_report", start=q_start, end=q_end,
                                   options={"program_name": "CSU Technology Business Incubator (NAVIGATU and TARA)"},
                                   message="Accomplishment report for the quarter.")
            generator.create_draft(m["abrea"], kind="request_letter", activity_id=self.activities["draft"].pk,
                                   options={"memo_for_name": "Riah E. Encarnacion, DIT",
                                            "memo_for_title": "Director, TBI & Manager, NAVIGATU",
                                            "memo_from_name": "Teodoro Jr. Abrea", "memo_from_title": "Portfolio Manager"},
                                   message="Draft the request letter for this workshop.")
        self.progress("drafts: Activity Report (2 versions + chat), Accomplishment Report, Request Letter")

    def _analyze(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE")

    # --- after the seed ---------------------------------------------------------------------------------------------

    def _index(self, skip: bool) -> str:
        from assistant import gemini, indexing

        if skip:
            return "skipped (--skip-ai-index): run `python manage.py index_documents` later"
        if not gemini.is_configured():
            return "skipped: no GEMINI_API_KEY in .env (the AI assistant still lists activities and files)"
        self.step_started = wallclock.monotonic()
        try:
            counts = indexing.index_pending_documents()
        except Exception as error:  # noqa: BLE001 - the seed is done; indexing can be run again by hand
            return f"failed ({type(error).__name__}); run `python manage.py index_documents` again later"
        self.progress("AI index")
        return ", ".join(f"{key}: {value}" for key, value in counts.items())

    def _summary(self, database: str, media_root: Path, emails: int, dropped: int, index: str) -> None:
        from activities.models import Activity

        out = self.stdout
        out.write("")
        out.write(self.style.SUCCESS(f"Testing data ready in database \"{database}\", uploads in \"{media_root}\" "
                                     f"(testing date {self.testing_date}, {wallclock.monotonic() - self.started:.0f} s)."))
        out.write("")
        out.write("Accounts (temporary passwords: shown only now, no forced change):")
        out.write(f"  {'Name':<28} {'Email (sign-in)':<36} {'Password':<16} Role / position")
        for key, name, role, position, local in ACCOUNTS:
            out.write(f"  {name:<28} {local + '@' + TESTING_DOMAIN:<36} {self.passwords[key]:<16} {role} / {position}")
        out.write("")
        out.write("Activities:")
        for activity in Activity.objects.select_related("status").order_by("start_date"):
            out.write(f"  #{activity.pk:<4} {activity.status.name:<10} {activity.start_date}  {activity.title}")
        ongoing = self.activities["ongoing"]
        out.write("")
        out.write(f"QR check-in for \"{ongoing.title}\" today: {self._window_text(self.checkin_start)} "
                  f"(activity {ongoing.start_time:%H:%M}-{ongoing.end_time:%H:%M}).")
        out.write(f"AI index: {index}.")
        out.write(f"Emails while seeding: {emails} written to an in-memory mailbox, {dropped} queued tasks dropped; "
                  "nothing was sent. Invented addresses (@example.com) are never really emailed.")
        from config.mail import prints_emails

        out.write("Email now: " + ("PRINTED in the terminal (DEBUG and EMAIL_REAL_SENDING=False). For the participants' "
                                   "phones set EMAIL_REAL_SENDING=True in .env, open a new terminal, restart runserver "
                                   "and qcluster." if prints_emails() else
                                   "REAL sending (SMTP). Keep qcluster running for certificate and evaluation emails."))
        out.write(self.style.WARNING("Invented test data. Never use it on the development or a production database."))
