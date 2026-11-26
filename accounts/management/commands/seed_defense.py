"""`python manage.py seed_defense --yes-this-is-a-test-database`: the FINAL DEFENSE / testing-site mock data
(ASSUMPTIONS 470).

A complete, realistic year (January to December) of the Navigatu Technology Business Incubator, so every dashboard card,
chart, checklist, report and "Needs attention" item has something to show:

- one staff account per role, ALL INVENTED (no real CSU official's name), emails `@demo.example.com`, unusable passwords
  (set one with `python manage.py changepassword <email>`), no forced password change;
- 22 activities in every status (Draft, Scheduled, Ongoing today, Conducted in every quarter) with registrations,
  per-day attendance with signatures, walk-ins, "Will not attend", evaluations with participant photos, certificates in
  every state (waiting, approved, issued, handed over), budgets, resource persons and objectives;
- EVERY document attached to an activity is a real text PDF on the CSU letterhead with content for that activity
  (accounts/defense_documents.py: Activity Design, Request Letter with a tracking number and QR code, Invitation Letter,
  Pre-registration Form, Evaluation Form, Program of Activities); the Attendance Sheet / Confirmation of Attendance are
  the system's generated forms and the Activity Reports come from the report generator; JPG event photos and one scanned
  attendance sheet; document states varied (approved, waiting, returned, missing, overdue);
- the Director's inbox (submissions, prepared certificates, a document to review, assignments), figures reports and
  report-generator drafts (Activity Reports, Accomplishment Reports per quarter, a draft Request Letter).

Everything goes through the real services, as `seed_testing` (ASSUMPTIONS 464) does: this command REUSES its helpers
(the moved clock, the in-memory mailbox, registrations, check-ins, evaluations) and leaves it unchanged. The narrative
sections of the generated reports are written by a stand-in for the AI (the seed's own text, every number from the
system through [[placeholders]]), so the seed never calls Gemini.

SAFETY. It EMPTIES the connected database and the uploads folder, then fills them. It runs only:
  - with --yes-this-is-a-test-database, AND
  - when the database name contains "test" or "demo" (never csu_tbi_track, the development database).
It works with DEBUG off (the Docker testing site): `uat exec web python manage.py seed_defense
--yes-this-is-a-test-database` writes to the container's media volume. Nothing is emailed while seeding (in-memory
mailbox, queued emails dropped) and every invented address ends in @demo.example.com, which config.mail never really
emails (EMAIL_BLOCKED_DOMAINS). Running it again rebuilds the same data: nothing is ever duplicated.
"""

from __future__ import annotations

import json
import re
import time as wallclock
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from accounts import defense_documents as docs
from accounts import sample_files as files
from accounts.management.commands import seed_testing
from accounts.management.commands.seed_testing import Clock, Spec, seeding_environment
from accounts.models import Role, TBIMember
from accounts.seed import load_default_form_fields, load_lookups

DOMAIN = "demo.example.com"
NAME_WORDS = ("test", "demo")  # the database name must contain one of these
PROTECTED_DATABASES = {"csu_tbi_track"}

# (key, name, role name, position, email local part): invented people only.
ACCOUNTS = [
    ("admin", "Carlo M. Rivera", Role.SYSTEM_ADMINISTRATOR, "System Administrator", "carlo.rivera"),
    ("director", "Dr. Elena S. Marquez", Role.DIRECTOR, "Director, Technology Business Incubator", "elena.marquez"),
    ("villanueva", "Engr. Paolo R. Villanueva", "Incubation Project Head", "Head, Incubation Program", "paolo.villanueva"),
    ("gonzaga", "Ma. Teresa L. Gonzaga", "Community Development Head", "Head, Community Development", "teresa.gonzaga"),
    ("lumbay", "Dexter A. Lumbay", "Incubation Project Head", "Portfolio Manager", "dexter.lumbay"),
    ("sumalinog", "Rhea Joy P. Sumalinog", "Admin Staff", "Administrative Aide VI", "rheajoy.sumalinog"),
    ("ocon", "Kenneth B. Ocon", "Admin Staff", "Project Technical Assistant", "kenneth.ocon"),
]
# Invented officials printed on the letters (never real CSU officials).
VICE_PRESIDENT = docs.Person("Dr. Ramon C. Alcantara", "Vice President for Research, Development, Innovation and Extension")
PRESIDENT = docs.Person("Dr. Leonardo P. Villamor", "University President")
# (name, email local part, topic, title, organization, salutation): invented; no digits in topics (report text rule).
RESOURCE_PERSONS = [
    ("Ms. Angela R. Sarmiento", "angela.sarmiento", "User testing for early prototypes", "Founder and CEO",
     "Kalinaw Design Studio", "Ms. Sarmiento"),
    ("Engr. Victor L. Dacanay", "victor.dacanay", "Go-to-market planning for hardware startups", "Product Manager",
     "Mindanao Maker Hub", "Engr. Dacanay"),
    ("Atty. Bianca T. Fernandez", "bianca.fernandez", "Patents, trademarks and copyright for startups", "IP Lawyer",
     "Fernandez Salazar Law Office", "Atty. Fernandez"),
    ("Mr. Joel P. Magbanua", "joel.magbanua", "Pitch deck structure and storytelling", "Managing Partner",
     "Agusan Angel Investors Network", "Mr. Magbanua"),
    ("Dr. Liza M. Cabrera", "liza.cabrera", "Business model canvas and customer discovery", "Associate Professor",
     "Northern Mindanao Business School", "Dr. Cabrera"),
    ("Ms. Carmina D. Uy", "carmina.uy", "Live selling and social media marketing", "Owner",
     "Lambo Crafts and Souvenirs", "Ms. Uy"),
    ("Mr. Rogelio A. Tan", "rogelio.tan", "Bookkeeping, cash flow and taxes for founders", "Certified Public Accountant",
     "Tan and Associates", "Mr. Tan"),
    ("Engr. Mylene F. Paderanga", "mylene.paderanga", "Food safety, packaging and labeling", "Food Technologist",
     "Caraga Food Innovation Center", "Engr. Paderanga"),
    ("Dr. Arnel S. Bautista", "arnel.bautista", "Technology transfer and licensing of research outputs",
     "Technology Transfer Officer", "Mindanao Innovation Partners", "Dr. Bautista"),
    ("Ms. Hannah G. Lim", "hannah.lim", "Writing a winning startup grant proposal", "Program Officer",
     "Startup Grants Philippines", "Ms. Lim"),
]
NAVIGATU_HALL = "NAVIGATU Co-working Space, Hiraya Building, CSU Main Campus"
FUNCTION_HALL = "University Function Hall, CSU Main Campus, Ampayon, Butuan City"


@dataclass
class DSpec(Spec):
    """One activity of the defense data (Spec + when, how many and which document / certificate story)."""

    month: int = 1
    day: int = 1
    days: int = 1
    start_time: time = time(8, 0)
    end_time: time = time(17, 0)
    registrations: int = 20
    attend: float = 0.85
    walk_ins: int = 1
    evaluate: float = 0.8
    photos: int = 2
    variant: str = "complete"


def B(*lines):  # noqa: N802 - a short builder for budget lines
    return list(lines)


MEALS = "Meals/Snacks"
# The Conducted activities of the year, in every quarter (variant = the story told by its documents / certificates).
CONDUCTED = [
    DSpec("ideation", "Startup Ideation Bootcamp for Student Innovators", "NAVIGATU", "Workshop", ["Faculty", "Incubatees"],
          "villanueva", "RAF", FUNCTION_HALL,
          ["Generate startup ideas from campus research and community problems", "Validate the problem with real users",
           "Form startup teams for the incubation program"],
          "A one-day bootcamp where student and faculty innovators turn research outputs and community problems into "
          "startup ideas, guided by mentors of the incubator.",
          B((MEALS, "Lunch and two snacks", 45, "pax", 1, "300.00"), ("Honorarium", "Mentors", 2, "persons", 1, "3000.00"),
            ("Supplies", "Workshop kits", 45, "sets", 1, "60.00")), [4, 0], month=1, day=22, registrations=24),
    DSpec("ip", "Intellectual Property Awareness Seminar for Researchers and Startups", "NAVIGATU", "Seminar",
          ["Faculty", "Incubatees", "MSMEs"], "lumbay", "IGF", "CSU Engineering Building, Audio-Visual Room",
          ["Explain patents, utility models, trademarks and copyright", "Show how to search the IP databases",
           "Help each team plan the protection of its brand and inventions"],
          "A two-day seminar with an IP lawyer and a technology transfer officer on protecting the inventions, brands "
          "and creative works of startups and researchers.",
          B((MEALS, "Lunch and snacks", 40, "pax", 2, "280.00"), ("Honorarium", "Resource speakers", 2, "persons", 1, "5000.00"),
            ("Printing", "Handouts and certificates", 40, "sets", 1, "45.00")), [2, 8], month=2, day=12, days=2,
          registrations=22, variant="returned_invitation"),
    DSpec("bmc", "Mentoring Clinic: Business Model Canvas", "NAVIGATU", "Mentoring", ["Incubatees", "MSMEs"], "villanueva",
          "TRF", NAVIGATU_HALL,
          ["Complete each team's business model canvas", "Identify the riskiest assumptions of each model",
           "Plan customer discovery interviews"],
          "An afternoon of one-on-one mentoring where incubatees and MSME owners complete and test their business model "
          "canvas.",
          B((MEALS, "Afternoon snacks", 25, "pax", 1, "120.00"), ("Honorarium", "Mentor", 1, "person", 1, "3000.00")), [4],
          month=3, day=5, start_time=time(13, 0), end_time=time(17, 0), registrations=16, max_slots=25),
    DSpec("women", "Women in Tech Entrepreneurship Forum", "NAVIGATU", "Forum", ["Faculty", "Incubatees", "MSMEs"],
          "gonzaga", "BRF", FUNCTION_HALL,
          ["Share the journeys of women founders in Caraga", "Connect women-led startups with mentors and partners",
           "Encourage more women to join the incubation program"],
          "A forum celebrating women founders and innovators, with a panel of women-led startups and MSMEs and a "
          "networking session.",
          B((MEALS, "Lunch and snacks", 50, "pax", 1, "280.00"), ("Venue", "Sound system", 1, "lot", 1, "4000.00"),
            ("Honorarium", "Panel speakers", 2, "persons", 1, "3000.00")), [5, 9], month=3, day=26, registrations=26),
    DSpec("prototype", "Product Development Workshop: From Prototype to Market", "NAVIGATU", "Workshop",
          ["Incubatees", "MSMEs"], "villanueva", "RAF", NAVIGATU_HALL,
          ["Validate each team's prototype with real users", "Plan the next product iteration",
           "Prepare a go-to-market checklist"],
          "A two-day hands-on workshop where incubatees test their prototypes with users and plan the path to market.",
          B((MEALS, "Morning snacks", 40, "pax", 2, "100.00"), (MEALS, "Lunch", 40, "pax", 2, "250.00"),
            ("Honorarium", "Resource speakers", 2, "persons", 1, "4000.00")), [0, 1], month=4, day=16, days=2,
          registrations=22),
    DSpec("investors", "Investor Networking Night: Caraga Startup Showcase", "NAVIGATU", "Networking",
          ["Incubatees", "MSMEs"], "lumbay", "IGF", "Hotel function room, J.C. Aquino Avenue, Butuan City",
          ["Showcase the incubator's startups to investors", "Match startups with angel investors and partners",
           "Gather investor feedback on each pitch"],
          "An evening showcase where the incubator's startups pitch to angel investors, banks and corporate partners.",
          B((MEALS, "Dinner", 50, "pax", 1, "450.00"), ("Venue", "Function room and sound system", 1, "lot", 1, "8000.00")),
          [3], month=5, day=7, start_time=time(17, 0), end_time=time(21, 0), registrations=20, variant="cert_waiting"),
    DSpec("live", "Digital Marketing and Live Selling Training for MSMEs", "NAVIGATU", "Training", ["MSMEs", "Incubatees"],
          "gonzaga", "TRF", "CSU College of Computing and Information Sciences, Computer Laboratory",
          ["Set up a business page and an online shop", "Plan and run a live selling session",
           "Create product photos and posts with free tools"],
          "A one-day hands-on training for MSMEs and incubatees on online selling, live selling and social media "
          "marketing.",
          B((MEALS, "Lunch and snacks", 35, "pax", 1, "280.00"), ("Honorarium", "Trainer", 1, "person", 1, "4000.00")), [5],
          month=5, day=28, registrations=24, max_slots=35),
    DSpec("pitching", "Regional Startup Pitching Competition", "NAVIGATU", "Forum", ["Faculty", "Incubatees", "MSMEs"],
          "lumbay", "RAF", FUNCTION_HALL,
          ["Select the region's best student and community startups", "Give every team feedback from a panel of judges",
           "Endorse the winners to the national competition"],
          "A two-day regional competition where startup teams pitch to a panel of investors and mentors; the winners "
          "are endorsed to the national round.",
          B((MEALS, "Lunch and snacks", 60, "pax", 2, "280.00"), ("Honorarium", "Judges", 3, "persons", 1, "3500.00"),
            ("Other", "Trophies and cash prizes", 1, "lot", 1, "15000.00")), [3, 4], month=6, day=18, days=2,
          registrations=28, max_slots=60),
    DSpec("packaging", "Food Product Packaging and Labeling Training", "NAVIGATU", "Training", ["MSMEs", "Incubatees"],
          "gonzaga", "BRF", "Caraga Food Innovation Center, CSU Main Campus",
          ["Explain food safety and labeling requirements", "Design a compliant label for each product",
           "Choose packaging that keeps products safe and attractive"],
          "A training for food MSMEs and incubatees on packaging, labeling and food safety requirements.",
          B((MEALS, "Lunch and snacks", 30, "pax", 1, "280.00"), ("Supplies", "Packaging samples", 1, "lot", 1, "5000.00"),
            ("Honorarium", "Food technologist", 1, "person", 1, "4000.00")), [7], month=7, day=9, registrations=20,
          max_slots=30),
    DSpec("legal", "Startup Legal Basics: Registration, Contracts and Taxes", "NAVIGATU", "Seminar", ["Incubatees", "MSMEs"],
          "villanueva", "IGF", "CSU Engineering Building, Audio-Visual Room",
          ["Explain business registration options for startups", "Review the basic contracts a startup needs",
           "Explain the taxes and bookkeeping of a young business"],
          "A seminar on business registration, founders' agreements, contracts and taxes for startups and MSMEs.",
          B((MEALS, "Lunch and snacks", 40, "pax", 1, "280.00"), ("Honorarium", "Resource speakers", 2, "persons", 1, "4000.00")),
          [2, 6], month=7, day=30, registrations=22, variant="no_attendance_sheet"),
    DSpec("agriaqua", "Agri-Aqua Technology Commercialization Workshop", "TARA", "Workshop", ["Faculty", "Incubatees", "MSMEs"],
          "lumbay", "TRF", "TARA Hub, CSU College of Agriculture and Agri-Industries",
          ["Identify agri-aqua research outputs ready for the market", "Plan the licensing or startup route of each output",
           "Connect researchers with growers' associations"],
          "A two-day workshop of TARA on bringing agriculture and aquaculture research outputs to the market through "
          "licensing and startups.",
          B((MEALS, "Lunch and snacks", 40, "pax", 2, "260.00"), ("Transportation", "Van rental for the farm visit", 2, "vans", 1, "4500.00"),
            ("Honorarium", "Resource speakers", 2, "persons", 1, "4000.00")), [8, 0], month=8, day=20, days=2,
          registrations=22),
    DSpec("pitchdeck", "Mentoring Clinic: Pitch Deck Review", "NAVIGATU", "Mentoring", ["Incubatees", "Faculty"],
          "villanueva", "RAF", NAVIGATU_HALL,
          ["Show the ten slides investors expect", "Give each team feedback on its own deck",
           "Prepare teams for the national pitching competition"],
          "An afternoon of one-on-one pitch deck mentoring for the incubator's teams before the national competition.",
          B((MEALS, "Afternoon snacks", 25, "pax", 1, "120.00"), ("Honorarium", "Pitch coach", 1, "person", 1, "3000.00")),
          [3], month=9, day=10, start_time=time(13, 0), end_time=time(17, 0), registrations=15, max_slots=25),
    DSpec("system", "Users Training on the Incubation Monitoring System", "NAVIGATU", "Training", ["Faculty", "Incubatees"],
          "ocon", "IGF", "CSU College of Computing and Information Sciences, Computer Laboratory",
          ["Introduce the incubation monitoring system to its users", "Practice the main tasks of each user role",
           "Gather the users' questions and requests for improvement"],
          "A users training on the incubator's monitoring system for faculty mentors and incubatees, with hands-on "
          "exercises per user role.",
          B((MEALS, "Lunch and snacks", 30, "pax", 1, "250.00"),), [], month=9, day=24, registrations=18, max_slots=30),
    DSpec("branding", "MSME Digital Tools and AI-Powered Branding Training", "NAVIGATU", "Training", ["MSMEs", "Incubatees"],
          "gonzaga", "TRF", "Hiraya Hall Auditorium, CSU Main Campus",
          ["Use free digital tools for an MSME's daily work", "Create brand materials with AI-assisted design tools",
           "Prepare a simple digital marketing plan"],
          "A two-day training for MSMEs and incubatees on digital tools and AI-assisted design for branding and "
          "marketing.",
          B((MEALS, "Lunch and snacks", 45, "pax", 2, "260.00"), ("Honorarium", "Trainers", 2, "persons", 1, "4000.00")),
          [5, 0], month=10, day=15, days=2, registrations=26, variant="review_waiting"),
    DSpec("design", "Design Thinking Workshop for Startup Teams", "NAVIGATU", "Workshop", ["Faculty", "Incubatees", "MSMEs"],
          "villanueva", "RAF", NAVIGATU_HALL,
          ["Apply the design thinking process to a real problem", "Build and test a quick prototype",
           "Present the solution to a panel of mentors"],
          "A one-day design thinking workshop where mixed teams of faculty, incubatees and MSMEs solve a real problem "
          "of a partner community.",
          B((MEALS, "Lunch and snacks", 40, "pax", 1, "280.00"), ("Supplies", "Prototyping materials", 1, "lot", 1, "3500.00"),
            ("Honorarium", "Facilitator", 1, "person", 1, "4000.00")), [0], month=10, day=29, registrations=22),
    DSpec("grants", "Grant Writing Clinic for Startup Funding Programs", "NAVIGATU", "Mentoring", ["Incubatees", "Faculty"],
          "lumbay", "IGF", NAVIGATU_HALL,
          ["Explain the requirements of government startup grants", "Draft the problem, solution and budget sections",
           "Review each team's draft with a grant writing mentor"],
          "A clinic where startup teams draft their applications to government startup grant programs with a grant "
          "writing mentor.",
          B((MEALS, "Lunch and snacks", 25, "pax", 1, "280.00"), ("Honorarium", "Grant writing mentor", 1, "person", 1, "3500.00")),
          [9], month=11, day=12, registrations=16, max_slots=25, variant="scan_unread"),
]
RECENT = DSpec("finance", "Startup Financial Management Seminar", "NAVIGATU", "Seminar", ["Faculty", "Incubatees", "MSMEs"],
               "gonzaga", "IGF", FUNCTION_HALL,
               ["Read a simple income statement and cash flow", "Set up bookkeeping for a young startup",
                "Plan the first year's budget of a startup"],
               "A seminar on bookkeeping, cash flow and budgeting for startup founders and MSME owners.",
               B((MEALS, "Lunch and snacks", 40, "pax", 1, "280.00"), ("Honorarium", "Finance mentor", 1, "person", 1, "3500.00")),
               [6], start_time=time(13, 0), end_time=time(17, 0), registrations=20, variant="closed_by_hand")
ONGOING = DSpec("ongoing", "Startup Investment Readiness Bootcamp", "NAVIGATU", "Training", ["Faculty", "Incubatees", "MSMEs"],
                "lumbay", "RAF", FUNCTION_HALL,
                ["Prepare startups for their first investor meetings", "Build a simple financial model and valuation",
                 "Practice the pitch with investors and get feedback"],
                "A three-day bootcamp that prepares the incubator's startups for investor meetings: financial models, "
                "valuation, term sheets and pitch practice.",
                B((MEALS, "Lunch and snacks", 40, "pax", 3, "260.00"), ("Honorarium", "Resource speakers", 3, "persons", 1, "4000.00")),
                [3, 6, 4], days=3, registrations=18)
SCHEDULED_OPEN = DSpec("scheduled", "Aquaculture Startup Bootcamp: Tilapia and Seaweed Value Chains", "TARA", "Training",
                       ["Incubatees", "MSMEs"], "lumbay", "TRF", "TARA Hub, CSU College of Agriculture and Agri-Industries",
                       ["Map the tilapia and seaweed value chains in Caraga",
                        "Identify product and service opportunities for agri-aqua startups",
                        "Connect incubatees with growers' associations and buyers"],
                       "A two-day bootcamp of TARA for agriculture and aquaculture startups and MSMEs: value chains, "
                       "market validation and a farm visit.",
                       B((MEALS, "Lunch and snacks", 40, "pax", 2, "250.00"),
                         ("Transportation", "Van rental for the farm visit", 2, "vans", 1, "4500.00"),
                         ("Honorarium", "Resource speakers", 2, "persons", 1, "3500.00")), [8, 7], days=2, registrations=10)
SCHEDULED_FORM = DSpec("form", "Social Enterprise and Community Innovation Forum", "NAVIGATU", "Forum",
                       ["Faculty", "Incubatees", "MSMEs"], "gonzaga", "BRF", FUNCTION_HALL,
                       ["Show how startups can solve community problems", "Present social enterprises from the region",
                        "Form partnerships with local government units"],
                       "A forum on social enterprises and community innovation with partner local government units.",
                       B((MEALS, "Lunch and snacks", 50, "pax", 1, "280.00"), ("Honorarium", "Speakers", 2, "persons", 1, "3500.00")),
                       [9, 4], registrations=0)
DRAFT_EMPTY = DSpec("draft", "Startup Grant Proposal Writing Workshop", "NAVIGATU", "Workshop", ["Faculty", "Incubatees"],
                    "villanueva", "RAF", FUNCTION_HALL,
                    ["Explain the requirements of startup grant programs", "Draft each team's proposal",
                     "Review the drafts with a grant writing mentor"],
                    "A one-day workshop where startup teams write the first draft of a grant proposal with mentors.",
                    B((MEALS, "Lunch and two snacks", 40, "pax", 1, "250.00"), ("Honorarium", "Mentor", 1, "person", 1, "3000.00")),
                    [9], registrations=0)
DRAFT_HALF = DSpec("draft_half", "Year-End Incubatee Demo Day", "NAVIGATU", "Networking", ["Incubatees", "MSMEs"],
                   "lumbay", "IGF", "Hiraya Hall Auditorium, CSU Main Campus",
                   ["Present the year's results of every incubatee", "Recognize the outstanding startups of the year",
                    "Plan next year's incubation program with partners"],
                   "The year-end demo day where every incubatee presents its results to partners, investors and the "
                   "University.",
                   B((MEALS, "Lunch and snacks", 60, "pax", 1, "300.00"), ("Venue", "Sound system and LED wall", 1, "lot", 1, "8000.00")),
                   [3], registrations=0)
FUND_NAMES = {"RAF": "RAF – Regular Agency Fund", "IGF": "IGF – Internally Generated Fund", "TRF": "TRF – Trust Fund",
              "BRF": "BRF – Business Related Fund"}
EVALUATION_RATINGS = ["Overall rating of the activity", "Resource persons' knowledge of the topics",
                      "Usefulness of the activity to my startup or work", "Venue, meals and organization"]
EVALUATION_QUESTIONS = ["What did you find most useful?", "What should be improved next time?"]


class NarrativeGateway:
    """Stands in for Gemini while seeding: returns the seed's own narrative for the report generator's sections (no
    numbers: every figure comes from the system through [[placeholders]]), so the seed never calls the AI."""

    def __init__(self, command: "Command") -> None:
        self.command = command

    def generate(self, system_instruction: str, turns=None, tools=None):
        keys = re.findall(r'^- "([a-z0-9_]+)" \(', system_instruction, flags=re.MULTILINE)
        sections = {key: self.command.narrative(key) for key in keys}
        return SimpleNamespace(text=json.dumps({"sections": sections}))


class Command(seed_testing.Command):
    help = ("FINAL DEFENSE / TESTING SITE ONLY: empty the connected database and uploads folder and fill them with a "
            "year of invented, realistic data on the CSU letterhead. Needs --yes-this-is-a-test-database and a "
            "database whose name contains 'test' or 'demo'.")

    def add_arguments(self, parser) -> None:
        parser.add_argument("--yes-this-is-a-test-database", action="store_true", dest="confirmed",
                            help="Required: the connected database and uploads folder are EMPTIED and refilled.")
        parser.add_argument("--today", default="", help="The day the data is shown, YYYY-MM-DD (default today); the "
                                                        "Ongoing activity runs the day before, this day and the next.")
        parser.add_argument("--index", action="store_true",
                            help="Index the files for the AI assistant now (needs GEMINI_API_KEY; otherwise run "
                                 "`python manage.py index_documents` later).")

    # --- safety ---------------------------------------------------------------------------------------------------

    @staticmethod
    def refused_reason(database: str, confirmed: bool) -> str | None:
        """Why this command must not run on this database, or None."""
        if not confirmed:
            return ("seed_defense EMPTIES the connected database and uploads folder. Run it only on a test database, "
                    "with --yes-this-is-a-test-database.")
        name = (database or "").lower()
        if name in PROTECTED_DATABASES or not any(word in name for word in NAME_WORDS):
            return (f"Refusing to run on database \"{database}\": its name must contain \"test\" or \"demo\" (for "
                    "example csu_tbi_testing), and it is never the development database csu_tbi_track.")
        return None

    def handle(self, *args, **options) -> None:
        database = connection.settings_dict["NAME"]
        if reason := self.refused_reason(database, options["confirmed"]):
            raise CommandError(reason)
        self.testing_date = self._parse_date(options["today"])
        self.started = wallclock.monotonic()
        self.step_started = self.started
        media_root = Path(settings.MEDIA_ROOT).resolve()
        project = Path(settings.BASE_DIR).resolve()
        if media_root == project or media_root in project.parents or media_root.parent == media_root:
            raise CommandError(f"MEDIA_ROOT {media_root} is the project folder, a folder above it or a drive; refusing "
                               "to empty it.")
        self.stdout.write(self.style.WARNING(
            f"seed_defense: EMPTYING database \"{database}\" and uploads folder \"{media_root}\" now."))
        self._wipe(media_root)

        self.clock = Clock()
        local_now = timezone.localtime(self.clock.real())
        self.present = timezone.make_aware(datetime.combine(self.testing_date, local_now.time().replace(microsecond=0)))
        self.clock.install(self.present)
        from reports import generator

        real_gateway = generator.get_gateway
        generator.get_gateway = lambda: NarrativeGateway(self)
        try:
            with seeding_environment(self.stdout) as dropped:
                self._seed()
                emails = len(mail.outbox)
        finally:
            generator.get_gateway = real_gateway
            self.clock.uninstall()
        index = self._index(not options["index"])
        call_command("setup_schedules", stdout=self._quiet())
        self._summary(database, media_root, emails, dropped["count"], index)

    def _parse_date(self, value: str) -> date:
        if not value:
            return timezone.localdate()
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise CommandError("--today must be YYYY-MM-DD.") from error

    # --- the seed -------------------------------------------------------------------------------------------------

    def _seed(self) -> None:
        load_lookups()
        load_default_form_fields()
        self.progress("lookups and default form fields")
        self.members = self._accounts()
        self.progress(f"{len(ACCOUNTS)} invented accounts with positions")
        self._certificate_design()
        self.progress("certificate design, invented signatories, the Director's linked signature")
        self.people = self._people_pool()
        self.resource_persons = self._resource_persons()
        self.activities: dict = {}
        self.specs_by_activity: dict[int, DSpec] = {}
        self.tracking = 100
        self.narrative_spec: DSpec | None = None
        self.narrative_kind = ""
        self._needs_review_type()
        for spec in CONDUCTED:
            self._conducted(spec, self._past(spec.month, spec.day))
        self._conducted(RECENT, self.testing_date - timedelta(days=4))
        self._ongoing()
        self._scheduled()
        self._drafts()
        self._director_work()
        self._reports()
        self._analyze()

    def _past(self, month: int, day: int) -> date:
        """This year's date, or last year's when it would not be in the past yet (the data stays valid on any day)."""
        when = date(self.testing_date.year, month, day)
        return when if when + timedelta(days=8) < self.testing_date else date(self.testing_date.year - 1, month, day)

    def _accounts(self) -> dict[str, TBIMember]:
        from accounts import services as account_services

        members = {}
        for key, name, role_name, _position, local in ACCOUNTS:
            members[key] = TBIMember.objects.create_user(f"{local}@{DOMAIN}", name, Role.objects.get(role_name=role_name),
                                                        password=None, must_change_password=False)
        admin = members["admin"]
        for key, _name, _role, position, _local in ACCOUNTS:
            row = account_services.add_position(admin, position)
            account_services.set_member_position(admin, members[key], row.pk)
        return members

    def _person(self, key: str) -> docs.Person:
        name, position = next((n, p) for k, n, _r, p, _l in ACCOUNTS if k == key)
        return docs.Person(name, position)

    def _certificate_design(self) -> None:
        from io import BytesIO

        from certificates import services as certificate_services
        from certificates.management.commands.make_sample_certificate_images import draw_signature, draw_template

        admin, director = self.members["admin"], self.members["director"]
        buffer = BytesIO()
        draw_template(buffer)
        certificate_services.upload_template(admin, "CSU-TBI certificate (sample design)",
                                             self._upload("design.png", buffer.getvalue()), activate=True)
        signatures = []
        for seed in (3, 11):
            buffer = BytesIO()
            draw_signature(buffer, seed)
            signatures.append(buffer.getvalue())
        director_person = self._person("director")
        left = certificate_services.add_signatory(director, director_person.name, director_person.position,
                                                  self._upload("director-signature.png", signatures[0]))
        right = certificate_services.add_signatory(director, VICE_PRESIDENT.name, VICE_PRESIDENT.position,
                                                   self._upload("vp-signature.png", signatures[1]))
        certificate_services.set_default_signatories(director, left, right)
        certificate_services.link_signatory_account(director, left, director)
        self.signatories = {"left": left, "right": right}

    def _people_pool(self) -> list[tuple[str, str]]:
        pairs = [(first, last) for last in seed_testing.LAST_NAMES for first in seed_testing.FIRST_NAMES]
        step = 37
        ordered = [pairs[(index * step) % len(pairs)] for index in range(len(pairs))]
        return [(f"{first} {last}", f"{first}.{last}{index}@{DOMAIN}".lower().replace(" ", ""))
                for index, (first, last) in enumerate(ordered, start=1)]

    def _resource_persons(self):
        from activities import services as activity_services

        return [activity_services.create_resource_person(self.members["villanueva"], name, f"{local}@{DOMAIN}",
                                                         f"0917{index:07d}")
                for index, (name, local, *_rest) in enumerate(RESOURCE_PERSONS, start=1)]

    # --- documents on the letterhead ------------------------------------------------------------------------------

    def _details(self, spec: DSpec, start: date, end: date) -> docs.Details:
        return docs.Details(
            title=spec.title, tbi=spec.tbi, type_name=spec.type_name, start=start, end=end,
            start_time=spec.start_time, end_time=spec.end_time, venue=spec.venue, categories=spec.categories,
            objectives=spec.objectives, description=spec.description, fund_source=FUND_NAMES[spec.fund],
            fund_account=f"{spec.tbi} – Training and Extension Services", budget=spec.budget, max_slots=spec.max_slots,
            prepared_by=self._person(spec.creator), director=self._person("director"), vice_president=VICE_PRESIDENT,
            president=PRESIDENT,
            resource_persons=[docs.ResourcePerson(RESOURCE_PERSONS[i][0], RESOURCE_PERSONS[i][2], RESOURCE_PERSONS[i][3],
                                                  RESOURCE_PERSONS[i][4], RESOURCE_PERSONS[i][5])
                              for i in spec.resource_persons])

    def _by_yesterday(self, day: date) -> date:
        """Never later than yesterday (nothing is recorded "in the future" of the moment the data is shown)."""
        return min(day, self.testing_date - timedelta(days=1))

    def _next_tracking(self, year: int) -> str:
        self.tracking += 1
        return docs.tracking_number(year, self.tracking)

    def _letter_pdf(self, kind: str, spec: Spec, start: date, end: date) -> bytes:
        """The creation documents (called by the shared _create): Activity Design / Request Letter on the letterhead."""
        details = self._details(spec, start, end)
        if kind == "Activity Design":
            return docs.activity_design(details)
        if kind == "Request Letter":
            letter_date = start - timedelta(days=30)
            return docs.request_letter(details, self._next_tracking(letter_date.year), letter_date)
        raise ValueError(kind)

    def _create(self, spec: Spec, start: date, end: date, start_time: time, end_time: time, *,
                created_on: date, documents: tuple[str, ...] = ("Activity Design", "Request Letter"),
                evaluation_days: int | None = 7, prepare: bool = False, form_ready: bool = True):
        """Create through the New activity form's service as of `created_on` (both creation documents = Scheduled),
        with this command's resource persons; `form_ready` False leaves "Open registration" for the testers."""
        from activities import services as activity_services
        from documents.models import DocumentType
        from documents.services import NewDocument, create_activity_with_documents
        from registrations import services as registration_services
        from registrations.models import FormType

        creator = self.members[spec.creator]
        deadline = self.moment(start - timedelta(days=1), 17)
        evaluation_deadline = self.moment(end + timedelta(days=evaluation_days), 17) if evaluation_days else None
        rows = [NewDocument(DocumentType.objects.get(name=name),
                            self._upload(f"{name.lower().replace(' ', '-')}.pdf", self._letter_pdf(name, spec, start, end)),
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
                if form_ready:
                    registration_services.mark_registration_form_ready(creator, activity)
        self.activities[spec.key] = activity
        self.specs_by_activity[activity.pk] = spec
        return activity

    def _office_documents(self, activity, spec: DSpec, start: date, end: date, *, invitation: str | None) -> None:
        """Invitation Letters (one per resource person; `invitation` = the review story), the Pre-registration Form,
        the Evaluation Form and the Program of Activities, each uploaded on its own day."""
        from documents import services as document_services
        from documents.models import DocumentType

        details = self._details(spec, start, end)
        creator, director = self.members[spec.creator], self.members["director"]
        day = self._by_yesterday
        if invitation and details.resource_persons:
            person = details.resource_persons[0]
            on = day(start - timedelta(days=20))
            letter = self._upload_document(creator, activity, "Invitation Letter",
                                           docs.invitation_letter(details, person, self._next_tracking(on.year), on),
                                           "invitation-letter.pdf", f"Invitation Letter – {person.name}", on)
            if invitation == "approved":
                with self.at(on + timedelta(days=1), 10):
                    document_services.approve_document(director, letter)
            elif invitation == "returned":
                with self.at(on + timedelta(days=1), 15):
                    document_services.return_document(director, letter, "Please add the time and the venue map, and "
                                                                        "use the resource person's full title.")
        form_fields = [field.label for field in self._registration_form_fields(activity)]
        self._upload_document(creator, activity, "Pre-registration Form",
                              docs.pre_registration_form(details, form_fields), "pre-registration-form.pdf",
                              f"Pre-registration Form – {spec.title}", day(start - timedelta(days=18)))
        self._upload_document(creator, activity, "Evaluation Form",
                              docs.evaluation_form(details, EVALUATION_RATINGS, EVALUATION_QUESTIONS),
                              "evaluation-form.pdf", f"Evaluation Form – {spec.title}", day(start - timedelta(days=3)))
        self._upload_document(creator, activity, DocumentType.OTHER, docs.program_of_activities(details),
                              "program-of-activities.pdf", f"Program of Activities – {spec.title}",
                              day(start - timedelta(days=7)))

    @staticmethod
    def _registration_form_fields(activity):
        from registrations import services as registration_services
        from registrations.models import FormType

        form = registration_services.get_form(activity, FormType.REGISTRATION)
        return list(form.fields.order_by("sort_order", "pk")) if form else []

    def _activity_report(self, activity, spec: DSpec, on: date) -> None:
        """The Activity Report through the report generator (narrative by the stand-in, figures from the system),
        saved to the activity's documents (an approved copy)."""
        from reports import generator

        member = self.members[spec.creator]
        self.narrative_spec, self.narrative_kind = spec, "activity_report"
        with self.at(on, 13):
            draft = generator.create_draft(member, kind="activity_report", activity_id=activity.pk,
                                           message="Write the Activity Report for the Supply Office.")
        version = generator.latest_version(draft)
        with self.at(on, 14):
            generator.save_to_documents(member, version)

    # --- the activities ---------------------------------------------------------------------------------------------

    def _conducted(self, spec: DSpec, start: date) -> None:
        from activities import services as activity_services
        from certificates import services as certificate_services
        from certificates.models import Certificate

        end = start + timedelta(days=spec.days - 1)
        variant = spec.variant
        activity = self._create(spec, start, end, spec.start_time, spec.end_time, created_on=start - timedelta(days=32),
                                evaluation_days=10)
        invitation = {"returned_invitation": "returned", "review_waiting": "waiting"}.get(variant, "approved")
        self._office_documents(activity, spec, start, end, invitation=invitation)
        registrations = self._register(activity, spec.registrations, first_day=start - timedelta(days=25),
                                       last_day=start - timedelta(days=2))
        if variant == "closed_by_hand":
            for registration in registrations[-2:]:
                with self.at(start - timedelta(days=1), 19):
                    from registrations import services as registration_services

                    registration_services.cancel_registration(registration)
            registrations = registrations[:-2]
        attendees = self._run_activity(activity, start, end, spec.start_time, spec.end_time, registrations, spec.attend,
                                       walk_ins=spec.walk_ins)
        self._evaluate(activity, attendees, end + timedelta(days=1), spec.evaluate, photos=spec.photos)
        creator = self.members[spec.creator]
        if spec.photos:
            self._tick_photos(activity, creator, min(2, spec.photos), end + timedelta(days=2))
        if variant != "no_attendance_sheet":
            self._generate_forms(activity, creator, end + timedelta(days=2))
        if variant not in ("closed_by_hand", "scan_unread"):
            self._upload_document(creator, activity, "Event Photos", files.event_photo_jpg(activity.pk * 7, spec.title),
                                  "event-photos.jpg", f"Event Photos – {spec.title}", end + timedelta(days=2))
            self._activity_report(activity, spec, end + timedelta(days=3))
        if variant == "scan_unread":
            rows = [(r.name, r.designation, r.office_agency) for r in attendees[:10]]
            self._upload_document(creator, activity, "Attendance Sheet",
                                  files.scanned_attendance_jpg(spec.title, f"{start:%B %d, %Y}", rows),
                                  "attendance-scan.jpg", "Attendance Sheet (scanned paper copy)", end + timedelta(days=1))
        if variant == "closed_by_hand":
            with self.at(end + timedelta(days=2), 9):
                activity_services.set_evaluation_closed_by_hand(creator, activity, True)
        if variant not in ("cert_waiting", "review_waiting"):
            self._approve_certificates(activity, self._by_yesterday(end + timedelta(days=4)))
            appreciation = Certificate.objects.filter(activity=activity, recipient_type="resource_person").order_by("pk").first()
            if appreciation is not None and variant == "complete":
                with self.at(end + timedelta(days=6), 15):
                    certificate_services.mark_handed_over(creator, certificate_ids=[appreciation.pk])
        self.progress(f"Conducted {start:%b %d}: {spec.title} ({variant})")

    def _ongoing(self) -> None:
        """Yesterday, today and tomorrow, 8:00 AM - 5:00 PM: day 1 attendance (QR, staff, a walk-in), three "Will not
        attend", certificates prepared and approved early by the Director."""
        from activities import services as activity_services
        from registrations import services as registration_services

        spec = ONGOING
        start = self.testing_date - timedelta(days=1)
        end = start + timedelta(days=2)
        activity = self._create(spec, start, end, spec.start_time, spec.end_time, created_on=start - timedelta(days=25),
                                evaluation_days=14, prepare=True)
        self._office_documents(activity, spec, start, end, invitation="approved")
        self._approve_certificates(activity, start - timedelta(days=5))
        registrations = self._register(activity, spec.registrations, first_day=start - timedelta(days=20),
                                       last_day=start - timedelta(days=2))
        for registration in registrations[-3:]:
            with self.at(start - timedelta(days=1), 19):
                registration_services.cancel_registration(registration)
        with self.clock.at(self.moment(start, 8, 1)):
            activity_services.refresh_status(activity)
        self._check_in(activity, registrations[:-3], start, 0.8, spec.start_time)
        self._walk_ins(activity, start, spec.start_time, 1)
        self.progress(f"Ongoing {start:%b %d}-{end:%b %d}: {spec.title}")

    def _scheduled(self) -> None:
        from certificates import services as certificate_services

        spec = SCHEDULED_OPEN
        start = self.testing_date + timedelta(days=10)
        end = start + timedelta(days=1)
        activity = self._create(spec, start, end, spec.start_time, spec.end_time,
                                created_on=self.testing_date - timedelta(days=14), evaluation_days=10)
        self._office_documents(activity, spec, start, end, invitation="waiting")
        self._register(activity, spec.registrations, first_day=self.testing_date - timedelta(days=12),
                       last_day=self.testing_date - timedelta(days=1))
        with self.at(self.testing_date - timedelta(days=2), 11):
            certificate_services.prepare_activity_certificates(self.members[spec.creator], activity)
        self.progress(f"Scheduled {start:%b %d}, registration open, certificates prepared: {spec.title}")
        spec = SCHEDULED_FORM
        start = self.testing_date + timedelta(days=20)
        self._create(spec, start, start, spec.start_time, spec.end_time, created_on=self.testing_date - timedelta(days=3),
                     form_ready=False)
        self.progress(f"Scheduled {start:%b %d}, registration form not opened yet: {spec.title}")

    def _drafts(self) -> None:
        spec = DRAFT_EMPTY
        start = self.testing_date + timedelta(days=30)
        self._create(spec, start, start, spec.start_time, spec.end_time, created_on=self.testing_date - timedelta(days=1),
                     documents=())
        self.progress(f"Draft without documents: {spec.title}")
        spec = DRAFT_HALF
        start = self.testing_date + timedelta(days=9)
        self._create(spec, start, start, spec.start_time, spec.end_time, created_on=self.testing_date - timedelta(days=6),
                     documents=("Activity Design",))
        self.progress(f"Draft with the Activity Design only (Request Letter due soon): {spec.title}")

    # --- the Director's work and reports ----------------------------------------------------------------------------

    def _submission(self, key: str, heading: str, paragraphs: list[str]):
        content = docs.memo_document(heading, paragraphs, self._person(key), self._person("director"))
        return self._upload(re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:60] + ".pdf", content)

    def _director_work(self) -> None:
        from approvals import assignments
        from approvals import services as approval_services
        from approvals.models import WorkAssignment

        T, m, director = self.testing_date, self.members, self.members["director"]
        with self.at(T - timedelta(days=2), 10):
            approval_services.submit(m["lumbay"], self._submission("lumbay", "Quarterly Incubation Status Report", [
                "The incubator's startups continued their product development, customer discovery and fundraising "
                "activities during the quarter, with mentoring from the TBI and partner investors.",
                "The attached figures and the activity reports in the system support this summary."]),
                "Quarterly Incubation Status Report", note="For your approval and signature, Ma'am.",
                activity=self.activities["pitching"])
        with self.at(T - timedelta(days=6), 10):
            returned = approval_services.submit(m["gonzaga"], self._submission("gonzaga", "Request for Speakers' Honoraria", [
                "Request to release the honoraria of the resource speakers of the Digital Marketing and Live Selling "
                "Training for MSMEs."]), "Request for Speakers' Honoraria")
        with self.at(T - timedelta(days=5), 15):
            approval_services.return_submission(director, returned, "Please attach the signed attendance of the speakers "
                                                                    "and the computation per session.")
        with self.at(T - timedelta(days=1), 10):
            signing = approval_services.submit(m["sumalinog"], self._submission("sumalinog", "Monthly Activity Summary", [
                "Summary of the TBI's activities, documents and certificates for the month, prepared from the "
                "CSU-TBI Track system."]), "Monthly Activity Summary")
        Kind = WorkAssignment.Kind
        branding = self.activities["branding"]
        invitation = branding.documents.filter(document_type__name="Invitation Letter").first()
        with self.at(T - timedelta(days=1), 11):
            assignments.assign_work(director, Kind.ACTIVITY, m["sumalinog"], activity=self.activities["ongoing"],
                                    note="Please handle the walk-ins and the attendance today.", due_date=T)
            assignments.assign_work(director, Kind.CERTIFICATE_APPROVAL, m["gonzaga"], activity=self.activities["investors"],
                                    note="Please check the names and approve.", due_date=T + timedelta(days=3))
            if invitation is not None:
                assignments.assign_work(director, Kind.DOCUMENT_REVIEW, m["villanueva"], document=invitation,
                                        due_date=T + timedelta(days=2))
            assignments.assign_work(director, Kind.SUBMISSION_REVIEW, m["villanueva"], submission=signing,
                                    note="You may stamp my signature after checking it.", allow_signing=True)
        with self.at(T - timedelta(days=10), 9):
            assignments.assign_work(director, Kind.REPORT, m["gonzaga"], report_title="Accomplishment Report – Third Quarter",
                                    note="For the University's quarterly report.", due_date=T - timedelta(days=3))
        self.progress("Director's work: 3 submissions, 5 assignments (one overdue)")

    def _quarter(self, number: int) -> tuple[date, date]:
        year = self.testing_date.year
        start = date(year, 3 * (number - 1) + 1, 1)
        end = date(year + (number == 4), (3 * number) % 12 + 1, 1) - timedelta(days=1)
        return start, end

    def _reports(self) -> None:
        from reports import generator
        from reports import services as report_services

        T, m = self.testing_date, self.members
        quarters = [self._quarter(n) for n in (1, 2, 3)]
        quarters = [(n + 1, start, end) for n, (start, end) in enumerate(quarters) if end < T]
        with self.at(T - timedelta(days=4), 10):
            for _number, start, end in quarters:
                report_services.request_report(m["sumalinog"], {
                    "report_type": "accomplishment", "format": "pdf", "date_range_start": start.isoformat(),
                    "date_range_end": end.isoformat()})
            if quarters:
                report_services.request_report(m["sumalinog"], {
                    "report_type": "participation", "format": "excel", "date_range_start": quarters[0][1].isoformat(),
                    "date_range_end": quarters[-1][2].isoformat()})
            report_services.request_report(m["villanueva"], {
                "report_type": "evaluation", "format": "pdf", "date_range_start": date(T.year, 1, 1).isoformat(),
                "date_range_end": date(T.year, 12, 31).isoformat()})
        self.progress("figures reports (accomplishment per quarter, participation Excel, evaluation PDF)")
        self.narrative_kind = "accomplishment_report"
        last_draft = None
        for number, start, end in quarters:
            with self.at(min(end + timedelta(days=10), T - timedelta(days=1)), 9):
                last_draft = generator.create_draft(
                    m["gonzaga"], kind="accomplishment_report", start=start, end=end,
                    options={"program_name": "Navigatu Technology Business Incubator Program"},
                    message=f"Accomplishment report for quarter {number}.")
        if last_draft is not None:
            with self.at(T - timedelta(days=1), 15):
                generator.submit_to_director(m["gonzaga"], generator.latest_version(last_draft),
                                             note="The quarterly accomplishment report for your signature, Ma'am.")
        with self.at(T - timedelta(days=1), 16):
            generator.create_draft(m["villanueva"], kind="request_letter", activity_id=self.activities["draft"].pk,
                                   options={"memo_for_name": PRESIDENT.name, "memo_for_title": PRESIDENT.position,
                                            "memo_thru_name": VICE_PRESIDENT.name,
                                            "memo_thru_title": VICE_PRESIDENT.position,
                                            "memo_from_name": self._person("director").name,
                                            "memo_from_title": self._person("director").position},
                                   message="Draft the request letter for this workshop.")
        self.progress("generator drafts: Accomplishment Reports per quarter (one submitted), a draft Request Letter")

    # --- the stand-in narrative (no numbers: figures come from [[placeholders]]) ------------------------------------

    def narrative(self, key: str) -> str:
        if self.narrative_kind == "accomplishment_report":
            return self._accomplishment_text(key)
        if self.narrative_kind == "activity_report" and self.narrative_spec is not None:
            return self._activity_report_text(key, self.narrative_spec)
        return ""

    def _speakers(self, spec: DSpec) -> str:
        people = [RESOURCE_PERSONS[i] for i in spec.resource_persons]
        if not people:
            return "The sessions were facilitated by the TBI staff."
        parts = [f"{name} ({title}, {organization}) on {topic.lower()}" for name, _l, topic, title, organization, _s in people]
        return "The program featured " + "; ".join(parts) + "."

    def _activity_report_text(self, key: str, spec: DSpec) -> str:
        objectives = [item[0].lower() + item[1:] for item in spec.objectives]
        if key == "highlights":
            return (f"The Technology Business Incubator of Caraga State University, through {spec.tbi}, conducted the "
                    f"[[title]] on [[period_covered]] at the [[venue]]. {spec.description}\n\n"
                    f"{self._speakers(spec)} The participants also took part in a workshop where they applied the "
                    f"lessons to their own startups and enterprises, followed by an open forum.\n\n"
                    f"A total of [[attended]] participants attended out of the [[registered]] who registered, and "
                    f"[[evaluations]] of them submitted the evaluation. The activity aimed to {objectives[0]}, to "
                    f"{objectives[1]} and to {objectives[2]}.")
        if key == "accomplishments":
            return "\n".join([f"Successfully organized and conducted the [[title]] at the [[venue]]",
                              *[f"Enabled the participants to {item}" for item in objectives],
                              "Recorded the attendance through the QR check-in with the participants' signatures"])
        if key == "problems":
            return ("The activity was implemented as planned. A few participants arrived after the check-in window and "
                    "were added by the staff as walk-ins; the hands-on sessions needed more time than planned, so the "
                    "open forum was shortened slightly. These did not affect the achievement of the objectives.")
        if key == "recommendations":
            return "\n".join(["Conduct a follow-up mentoring session for the participating teams",
                              "Share the presentations and templates with the participants after the activity",
                              "Allot more time to the hands-on workshop in the next run",
                              "Invite partner agencies to support the teams' next steps"])
        return ""

    def _accomplishment_text(self, key: str) -> str:
        match = re.fullmatch(r"activity_(\d+)_(highlights|objectives|accomplishments)", key)
        if match:
            spec = self.specs_by_activity.get(int(match.group(1)))
            if spec is None:
                return ""
            objectives = [item[0].lower() + item[1:] for item in spec.objectives]
            if match.group(2) == "highlights":
                return f"{spec.description} {self._speakers(spec)}"
            if match.group(2) == "objectives":
                return "\n".join(spec.objectives)
            return "\n".join(f"The participants were able to {item}" for item in objectives)
        texts = {
            "introduction": ("During the period, the Technology Business Incubator of Caraga State University conducted "
                             "[[activities_conducted]] activities for its incubatees, partner MSMEs and faculty "
                             "innovators, reaching [[attended]] participants. The activities covered startup ideation, "
                             "intellectual property, product development, digital marketing and investor readiness, "
                             "with resource persons from the startup ecosystem and partner agencies."),
            "outputs": "\n".join(["Conducted the planned trainings, seminars, workshops and mentoring clinics",
                                  "Recorded the attendance of every activity with the QR check-in and signatures",
                                  "Gathered and summarized the participants' evaluations",
                                  "Issued certificates of participation and appreciation",
                                  "Prepared the activity reports and payment documents for the Supply Office"]),
            "problems": ("Some activities needed more time for the hands-on sessions than planned, and a few "
                         "participants registered at the venue as walk-ins. These were addressed during the activities "
                         "and did not affect the achievement of the objectives."),
            "recommendations": "\n".join(["Continue the mentoring of the incubatees after each activity",
                                          "Strengthen the partnerships with investors and partner agencies",
                                          "Keep using the QR check-in and online evaluation for every activity",
                                          "Plan the next quarter's activities based on the participants' feedback"]),
            "assessment": ("Overall, the period's activities were implemented as planned and achieved their objectives. "
                           "The participants rated the activities highly and asked for follow-up sessions, which shows "
                           "the value of the incubator's programs to the startup community of the region."),
        }
        return texts.get(key, "")

    # --- after the seed ---------------------------------------------------------------------------------------------

    def _index(self, skip: bool) -> str:
        if skip:
            return ("not indexed: when GEMINI_API_KEY is set, run `python manage.py index_documents` so the AI assistant "
                    "can read the documents")
        return super()._index(False)

    def _summary(self, database: str, media_root: Path, emails: int, dropped: int, index: str) -> None:
        from activities.models import Activity
        from documents.models import Document

        out = self.stdout
        out.write("")
        out.write(self.style.SUCCESS(f"Defense data ready in database \"{database}\", uploads in \"{media_root}\" "
                                     f"(today = {self.testing_date}, {wallclock.monotonic() - self.started:.0f} s)."))
        out.write("")
        out.write("Accounts (all invented; NO password yet: set one with `python manage.py changepassword <email>`):")
        for _key, name, role, position, local in ACCOUNTS:
            out.write(f"  {name:<28} {local + '@' + DOMAIN:<36} {role} / {position}")
        out.write("")
        out.write("Activities:")
        for activity in Activity.objects.select_related("status").order_by("start_date"):
            out.write(f"  #{activity.pk:<4} {activity.status.name:<10} {activity.start_date}  {activity.title}")
        out.write("")
        out.write(f"Documents: {Document.objects.count()} (letterhead PDFs, generated forms, Activity Reports, photos).")
        out.write(f"AI index: {index}.")
        out.write(f"Emails while seeding: {emails} written to an in-memory mailbox, {dropped} queued tasks dropped; "
                  "nothing was sent. @demo.example.com is never really emailed.")
        out.write(self.style.WARNING("Invented demonstration data: every generated document says \"SAMPLE – FOR SYSTEM "
                                     "DEMONSTRATION\". Never run this on the development or a production database."))
