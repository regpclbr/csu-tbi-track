"""The ONE Reports page (ASSUMPTIONS 461-462): what the page shows. Display only: every rule stays where it was
(reports.services for the figures reports, reports.generator for the drafts, registrations.attendance_forms for the
attendance forms).

- One list of what to make (`kinds`): the figures reports (PDF / Excel, GENERATED_REPORT) and the report generator's
  kinds (REPORT_DRAFT), plus, for one activity, the Attendance and Confirmation of Attendance forms. With ?activity=<id>
  (the activity page's Reports tab) only the kinds that make sense for one activity are offered and the activity is
  chosen already.
- One list of past reports (`past`): every figures report (any staff member may download them, as before) and the
  member's own drafts (a draft stays its maker's only, item 442), newest first; for one activity, its drafts and the
  report-like documents saved on it.
"""

from dataclasses import dataclass

from django.core.paginator import Paginator
from django.urls import reverse

from accounts.permissions import Permission, has_permission_for
from activities.models import Activity

from . import generator_pages, pages, services
from .content import REPORT_TYPES
from .models import ReportDraft

PAST_PER_PAGE = 20
FIGURES_PREFIX = "figures-"
FORM_ATTENDANCE, FORM_CONFIRMATION = "form-attendance", "form-confirmation"


@dataclass(frozen=True)
class KindChoice:
    key: str
    label: str
    group: str  # "figures", "written" or "forms"
    help: str = ""


# ASSUMPTIONS 467: step 1 is ONE list; each kind says in one line what it makes.
FIGURES_HELP = {
    "accomplishment": ("Accomplishment figures", "Counts and tables for a quarter, half or year (PDF or Excel)"),
    "participation": ("Participation", "Registrations, attendance and the participant mix for a period"),
    "evaluation": ("Evaluation results", "Average ratings of the activities in a period"),
    "document_compliance": ("Document compliance", "Required documents submitted, missing and overdue"),
    "certificates": ("Certificates", "Certificates generated, approved and issued in a period"),
}
FIGURES = [KindChoice(f"{FIGURES_PREFIX}{key}", FIGURES_HELP.get(key, (t.label, ""))[0], "figures",
                      FIGURES_HELP.get(key, ("", "PDF or Excel, for a period"))[1]) for key, t in REPORT_TYPES.items()]
WRITTEN_HELP = {
    ReportDraft.Kind.ACTIVITY_REPORT: "One activity, in NAVIGATU's format (for the Supply Office)",
    ReportDraft.Kind.ACCOMPLISHMENT_REPORT: "The narrative report for a quarter, half or year",
    ReportDraft.Kind.EVALUATION_SUMMARY: "Ratings and comments of one activity or a period",
    ReportDraft.Kind.REQUEST_LETTER: "A memorandum requesting an activity, to print and route",
    ReportDraft.Kind.ACTIVITY_DESIGN: "Rationale, objectives, schedule and budget, to print and route",
    ReportDraft.Kind.OTHER: "Anything else, written from the system's records",
}
WRITTEN = [KindChoice(kind, dict(ReportDraft.Kind.choices)[kind], "written", WRITTEN_HELP[kind])
           for kind in generator_pages.KIND_ORDER]
FORMS = [KindChoice(FORM_ATTENDANCE, "Attendance form", "forms", "One sheet per day, with the participants' signatures"),
         KindChoice(FORM_CONFIRMATION, "Confirmation of Attendance", "forms", "Who pre-registered, for the Supply Office")]
# The kinds that make sense for ONE activity (ASSUMPTIONS 462): never the period-only figures reports or the
# Accomplishment Report.
PER_ACTIVITY = [ReportDraft.Kind.ACTIVITY_REPORT, ReportDraft.Kind.EVALUATION_SUMMARY, FORM_ATTENDANCE, FORM_CONFIRMATION,
                ReportDraft.Kind.REQUEST_LETTER, ReportDraft.Kind.ACTIVITY_DESIGN, ReportDraft.Kind.OTHER]
DEFAULT_KIND = f"{FIGURES_PREFIX}accomplishment"
DEFAULT_ACTIVITY_KIND = ReportDraft.Kind.ACTIVITY_REPORT
# Step 2's default program name of the Accomplishment Report when the member has none remembered (editable).
DEFAULT_PROGRAM_NAME = "CSU Technology Business Incubator"


def kind_choices(activity: Activity | None) -> list[KindChoice]:
    if activity is None:
        return FIGURES + WRITTEN + FORMS
    every = {choice.key: choice for choice in WRITTEN + FORMS}
    return [every[key] for key in PER_ACTIVITY]


def choose_kind(raw: str | None, activity: Activity | None) -> str:
    """The kind on the page: the one asked for when it is offered here, else the default."""
    keys = {choice.key for choice in kind_choices(activity)}
    if raw in keys:
        return raw
    return DEFAULT_ACTIVITY_KIND if activity is not None else DEFAULT_KIND


def latest_conducted() -> Activity | None:
    """Step 2's default activity for a report about one activity: the newest Conducted activity (by its end)."""
    return (Activity.objects.filter(status__name="Conducted").order_by("-end_date", "-end_time", "-pk")
            .only("pk", "title").first())


def form_activity_choices() -> list[Activity]:
    """The activities an attendance form can be made for (Ongoing or Conducted; the generator refuses the rest with its
    own reason), newest first."""
    return list(Activity.objects.filter(status__name__in=("Ongoing", "Conducted")).select_related("status")
                .order_by("-start_date", "-pk"))


def group_of(kind: str) -> str:
    if kind.startswith(FIGURES_PREFIX):
        return "figures"
    return "forms" if kind in (FORM_ATTENDANCE, FORM_CONFIRMATION) else "written"


def kind_url(kind: str, activity: Activity | None) -> str:
    query = f"?kind={kind}" + (f"&activity={activity.pk}" if activity is not None else "")
    return reverse("reports:list") + query


def generator_data(kind: str, activity: Activity | None, member=None) -> dict:
    """The generator form's starting values for this kind (and activity) (ASSUMPTIONS 467: sensible defaults, so
    Generate works at once): the given activity, else the newest Conducted one (the first Draft / Scheduled one for a
    draft letter / design); the current quarter and a program name for the Accomplishment Report. `start_kind` (not
    `kind`) keeps it a fresh form, so the member's remembered values are filled in (generator_pages.new_form)."""
    from . import generator
    from .pages import current_quarter, today

    data = {"start_kind": kind}
    if kind in (ReportDraft.Kind.REQUEST_LETTER, ReportDraft.Kind.ACTIVITY_DESIGN):
        chosen = activity or next(iter(generator_pages.proposal_activity_choices()), None)
        data.update(proposal_scope="activity", proposal_activity=str(chosen.pk) if chosen else "")
    elif kind == ReportDraft.Kind.ACCOMPLISHMENT_REPORT:
        now = today()
        data.update(preset=current_quarter(now), year=str(now.year))
        remembered = generator.last_options(member, kind) if member is not None else {}
        if not remembered.get("program_name"):
            data["program_name"] = DEFAULT_PROGRAM_NAME
    else:
        chosen = activity or latest_conducted()
        data.update(scope="activity", activity=str(chosen.pk) if chosen else "")
    return data


# ASSUMPTIONS 463: the payment documents the system can make, and the kind that makes them on the Reports page.
PAYMENT_GENERATE_KINDS = {"Attendance Sheet": FORM_ATTENDANCE, "Confirmation of Attendance": FORM_CONFIRMATION,
                          "Activity Report": ReportDraft.Kind.ACTIVITY_REPORT}


def payment_action(document_type, activity: Activity) -> dict:
    """"Generate" (opens the Reports page with this activity and kind chosen: a suggestion, nothing is made without the
    member) for a file the system can make; else "Upload" (the activity's Documents tab with the type chosen)."""
    kind = PAYMENT_GENERATE_KINDS.get(document_type.name)
    if kind is not None:
        return {"label": "Generate", "url": kind_url(kind, activity), "kind": kind}
    url = reverse("activities:detail", args=[activity.pk])
    return {"label": "Upload", "url": f"{url}?tab=documents&type={document_type.pk}#upload", "kind": ""}


def payment_cards(member, rows) -> list[dict]:
    """documents.services.payment_missing rows as cards: one per activity with its own missing files and actions."""
    from documents.pages import checklist_state

    return [{"activity": row.activity, "overdue": row.overdue, "first_due": row.first_due,
             "items": [{"item": item, "state": checklist_state(item), "action": payment_action(item.document_type,
                                                                                               row.activity)}
                       for item in row.missing]}
            for row in rows]


def attendance_forms(member, activity: Activity | None, kind: str) -> dict:
    """Step 2 of an attendance form (ASSUMPTIONS 467): for one activity (the Reports tab) its button and refusal (the
    Documents tab's own rule, registrations.attendance_forms.refused_reason); on the general page the activity picker
    (default: the newest Ongoing / Conducted one). Generating stays registrations.attendance_forms.generate_form."""
    from documents.pages import _attendance_forms
    from registrations import attendance_forms as forms

    wanted = forms.ATTENDANCE if kind == FORM_ATTENDANCE else forms.CONFIRMATION
    choices = form_activity_choices() if activity is None else []
    chosen = activity or (choices[0] if choices else None)
    items = [item for item in _attendance_forms(chosen) if item["kind"] == wanted] if chosen is not None else []
    allowed = (has_permission_for(member, Permission.CREATE_ACTIVITY, chosen) if chosen is not None
               else has_permission_for(member, Permission.CREATE_ACTIVITY))
    return {"items": items, "choices": choices, "chosen": chosen, "form_kind": wanted, "can_generate": allowed}


# --- Past reports --------------------------------------------------------------------------------------------------------


def draft_row(draft: ReportDraft) -> dict:
    from . import generator

    latest = generator.latest_version(draft)
    return {"draft": draft, "kind": draft.get_kind_display(), "scope": generator_pages.scope_text(draft),
            "latest": latest, "tone": generator_pages.STATE_TONES.get(latest.state, "neutral") if latest else "neutral",
            "url": reverse("reports:generator_draft", args=[draft.pk]), "when": draft.updated_at}


def past(member, activity: Activity | None, page: str | int = 1) -> dict:
    """services.past_reports as rows, PAST_PER_PAGE a page (figures rows keep their polling, item 301)."""
    entries = services.past_reports(member, activity)
    paginator = Paginator(entries, PAST_PER_PAGE)
    page_obj = paginator.get_page(page)
    reports = [entry["object"] for entry in page_obj.object_list if entry["type"] == "figures"]
    failed = services.failed_report_ids([report.pk for report in reports])
    rows = []
    for entry in page_obj.object_list:
        if entry["type"] == "figures":
            rows.append({"type": "figures", **pages.history_row(entry["object"], services.report_state(entry["object"],
                                                                                                        failed))})
        elif entry["type"] == "draft":
            rows.append({"type": "draft", **draft_row(entry["object"])})
        else:
            document = entry["object"]
            rows.append({"type": "document", "document": document, "when": document.uploaded_at,
                         "url": reverse("documents:detail", args=[document.pk])})
    return {"page_query": f"activity={activity.pk}" if activity is not None else "", "rows": rows, "result": {"page": page_obj.number, "num_pages": paginator.num_pages,
                                     "total": paginator.count, "has_previous": page_obj.has_previous(),
                                     "has_next": page_obj.has_next()}}


def page_context(request, kind: str, activity: Activity | None, *, figures_form=None, generator_form=None) -> dict:
    """Everything the one Reports page shows for the chosen kind (and activity)."""
    member = request.user
    group = group_of(kind)
    context = {
        "page_title": "Reports" if activity is None else f"Reports: {activity.title}",
        "page_subtitle": ("Choose what to make, the activity or period, and generate: every number comes from the "
                          "system" if activity is None else
                          "The reports that make sense for this activity, and the ones already made"),
        "kind": kind, "group": group, "activity": activity,
        "kinds": [{"choice": choice, "url": kind_url(choice.key, activity), "current": choice.key == kind}
                  for choice in kind_choices(activity)],
        "can_generate": services.can_generate(member),
        "poll_seconds": pages.POLL_SECONDS,
        **past(member, activity, request.GET.get("page", "1")),
    }
    if group == "figures":
        form = figures_form or pages.empty_form(pages.today(), kind[len(FIGURES_PREFIX):])
        context.update({"form": form, **pages.form_options(pages.today(), form)})
    elif group == "written":
        context["gen_form"] = generator_form or generator_pages.new_form(generator_data(kind, activity, member),
                                                                         member=member)
        context["gen_form"]["kind_fixed"] = True
    else:
        context["attendance"] = attendance_forms(member, activity, kind)
    return context
