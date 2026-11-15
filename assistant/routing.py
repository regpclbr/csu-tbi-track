"""Which path answers a question (ASSUMPTIONS 381, 383). Decided by OUR code before Gemini is asked, in this order:

1. document status: missing / overdue / waiting / returned / pending / due / checklist ... -> Gemini with the
   existing record tools only (document_checklist() and friends);
2. document content: says / mention / summary / according to ... (with a file word or document type, or no activity
   word) -> Gemini with search_approved_documents (+ find_activities); the answer shows source cards;
3. find files: a file word (files, documents, copy, download, attachment, ...) or a DOCUMENT_TYPE name, together with
   an activity title, a period or a document type -> our code lists the files (no Gemini);
4. activities: an activity word (activities, events, schedule, happening, an ACTIVITY_TYPE name, ...) together with a
   period, a status, "how many" or a list word -> our code lists the activities (no Gemini);
5. anything else -> Gemini with every tool (also list_activities_by_period and find_files, whose dates our code works
   out from the user's own words).

Activity and document types are matched against their tables (singular or plural), never a fixed list.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from activities.models import Activity, ActivityStatus, ActivityType
from documents.models import DocumentType

from .periods import FUTURE_CUES, Period, parse_period, period_words

ACTIVITIES, FIND_FILES = "activities", "find_files"
DOCUMENT_STATUS, DOCUMENT_CONTENT, GEMINI_ALL_TOOLS = "document_status", "document_content", "gemini_all_tools"
PATHS = (ACTIVITIES, FIND_FILES, DOCUMENT_STATUS, DOCUMENT_CONTENT, GEMINI_ALL_TOOLS)

DOCUMENT_STATUS_WORDS = re.compile(
    r"\b(missing|overdue|late|waiting|pending|returned|due|lacking|unapproved|outstanding|checklist|incomplete|"
    r"not (?:yet )?(?:uploaded|submitted|approved|complete)|for review|to review|needs? (?:a )?review|still need(?:s|ed)?|"
    r"not been (?:uploaded|submitted|approved))\b"
)
CONTENT_WORDS = re.compile(
    r"\b(say|says|said|saying|mention|mentions|mentioned|contents?|summary|summari[sz]e|describe|describes|"
    r"explain|explains|according to|written|objectives?|budget|key points|talk about|talks about|discuss|discusses|"
    r"stated?|states|purpose|recommendations?|findings|outcomes?|results?|highlights|lessons|what is in|what's in|inside)\b"
)
FILE_WORDS = re.compile(
    r"\b(files?|documents?|docs?|cop(?:y|ies)|soft ?cop(?:y|ies)|download|downloads|attachments?|attached|"
    r"uploads?|uploaded|pdfs?|scans?|scanned)\b"
)
UPLOADED_WORDS = re.compile(r"\b(uploaded|upload|uploads|submitted)\b")
ACTIVITY_WORDS = re.compile(
    r"\b(activit(?:y|ies)|events?|schedules?|scheduled|calendar|happening|upcoming|conducted|held|programs?|"
    r"sessions?|what'?s on|going on)\b"
)
COUNT_WORDS = re.compile(r"\b(how many|number of|count|total)\b")
LIST_WORDS = re.compile(
    r"\b(list|show|what are|what were|what is|what's|whats|which|give me|any|all|tell me|are there|were there|"
    r"is there|was there|do we have|did we have|have we|see)\b"
)
# Activity status words. "scheduled" is NOT a status here: people say "scheduled" for "planned".
STATUS_WORDS = [
    (re.compile(r"\bdrafts?\b"), ActivityStatus.DRAFT),
    (re.compile(r"\b(ongoing|in progress)\b"), ActivityStatus.ONGOING),
    (re.compile(r"\b(conducted|held|done|completed|finished|took place)\b"), ActivityStatus.CONDUCTED),
]
DRAFT_WORDS = re.compile(r"\bdrafts?\b")
# "including drafts" adds drafts to the list; it is not a request for drafts only.
WITH_DRAFTS = re.compile(r"\b(including|include|with|plus|and|also|even)\s+(?:the\s+)?drafts?\b")

# Words that never name an activity (removed before matching activity titles).
STOPWORDS = set("""
a an the of for in on at to and or with from by about this that these those it its is are was were be been am do does
did can could would will shall should may might must please kindly me my we our us you your i give show list send get
find need want see view open download downloads copy copies soft file files document documents doc docs attachment
attachments attached upload uploads uploaded pdf pdfs scan scans scanned activity activities event events month week
year day today tomorrow yesterday what which where when who how many all any some there here have has had just also
only again latest last next new old recent recently current currently s
""".split())


@dataclass
class Route:
    """The path and everything our code read from the question."""

    path: str
    period: Period | None = None
    status: str | None = None  # an ACTIVITY_STATUS name
    include_drafts: bool = False
    activity_type: ActivityType | None = None
    document_type: DocumentType | None = None
    activity_ids: list[int] | None = None  # activities named in the question (None = no name given)
    name_words: list[str] = field(default_factory=list)
    uploaded: bool = False  # the period is about the upload date ("documents uploaded last week")
    count: bool = False


def normalize(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").replace("‘", "'").split())


def _type_pattern(name: str) -> re.Pattern:
    """A type name, singular or plural, any case: "Request Letter" -> request letter(s); "Activity" -> activit(y|ies)."""
    words = [re.escape(word) for word in name.lower().split()]
    last = words[-1]
    if last.endswith("y") and len(last) > 2:
        words[-1] = f"(?:{last}|{last[:-1]}ies)"
    else:
        words[-1] = f"{last}(?:s|es)?"
    return re.compile(r"\b" + r"\s+".join(words) + r"\b")


def match_type(text: str, types) -> object | None:
    """The type whose name appears in the text (the longest name wins), or None."""
    found = [t for t in types if _type_pattern(t.name).search(text)]
    return max(found, key=lambda t: len(t.name)) if found else None


def remove_type(text: str, type_object) -> str:
    return _type_pattern(type_object.name).sub(" ", text) if type_object else text


def status_in(text: str) -> str | None:
    text = WITH_DRAFTS.sub(" ", text)
    for pattern, status in STATUS_WORDS:
        if pattern.search(text):
            if status == ActivityStatus.CONDUCTED and FUTURE_CUES.search(text):
                continue  # "where will it be held" is not about Conducted activities
            return status
    return None


def name_words(text: str, activity_types, document_types) -> list[str]:
    """The words that may name an activity: period words, type names, file words and STOPWORDS removed, and only words
    that appear in some activity title (other words are treated as noise)."""
    text = period_words().sub(" ", text)
    for type_object in list(activity_types) + list(document_types):
        text = remove_type(text, type_object)
    text = re.sub(r"'s\b", " ", text)
    words = [w for w in re.findall(r"[a-z0-9]+", text) if w not in STOPWORDS and (len(w) >= 3 or w.isdigit())]
    vocabulary = set()
    for title in Activity.objects.values_list("title", flat=True):
        vocabulary.update(re.findall(r"[a-z0-9]+", title.lower()))
    return [w for w in dict.fromkeys(words) if w in vocabulary]


def activities_named(words: list[str]) -> list[int]:
    """Activities whose title contains every word (any case)."""
    activities = Activity.objects.all()
    for word in words:
        activities = activities.filter(title__icontains=word)
    return list(activities.values_list("pk", flat=True))


def route(question: str, now: datetime) -> Route:
    """Decide the path (module docstring). Reads the activity and document types (two small queries) and, for files,
    the activity titles."""
    text = normalize(question)
    if DOCUMENT_STATUS_WORDS.search(text):
        return Route(DOCUMENT_STATUS)

    activity_types = list(ActivityType.objects.all())
    document_types = list(DocumentType.objects.all())
    document_type = match_type(text, document_types)
    without_doc_type = remove_type(text, document_type)
    activity_type = match_type(without_doc_type, activity_types)
    has_file_word = bool(FILE_WORDS.search(text))
    has_activity_word = bool(ACTIVITY_WORDS.search(without_doc_type)) or activity_type is not None

    if CONTENT_WORDS.search(text) and (has_file_word or document_type or not has_activity_word):
        return Route(DOCUMENT_CONTENT)

    period = parse_period(text, now)
    status = status_in(without_doc_type)
    include_drafts = bool(DRAFT_WORDS.search(text))

    if has_file_word or document_type:
        words = name_words(text, activity_types, document_types)
        activity_ids = activities_named(words) if words else None
        if activity_ids is not None or period or document_type:
            return Route(
                FIND_FILES, period=period, document_type=document_type, activity_type=activity_type,
                activity_ids=activity_ids, name_words=words, uploaded=bool(UPLOADED_WORDS.search(text)),
                include_drafts=True,
            )

    count = bool(COUNT_WORDS.search(text))
    if has_activity_word and (period or status or count or LIST_WORDS.search(text)):
        return Route(
            ACTIVITIES, period=period, status=status, include_drafts=include_drafts or status == ActivityStatus.DRAFT,
            activity_type=activity_type, count=count,
        )
    return Route(GEMINI_ALL_TOOLS)
