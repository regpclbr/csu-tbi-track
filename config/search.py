"""Global search (phase F5): the top bar's search box opens /search/?q=, with the first results of the Activities and
Documents lists (the Startups group went with the startup portal, ASSUMPTIONS 458). Each group uses its list's own
service (the same search as the list page), so a "See all" link opens that list filtered by the same words.
"""

from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from accounts.permissions import staff_login_required
from activities.services import list_activities
from documents.services import list_documents

GROUP_SIZE = 5
MAX_QUERY_LENGTH = 100


def search_groups(q: str) -> list[dict]:
    """Activities (title) and Documents (title or file name; never certificate evidence):
    the first GROUP_SIZE of each, with the total and the link to the full list."""
    query = urlencode({"q": q})
    activities = list_activities({"search": q}, per_page=GROUP_SIZE)
    documents = list_documents({"q": q}, per_page=GROUP_SIZE)
    return [
        {"key": "activities", "label": "Activities", "icon": "calendar", "result": activities,
         "see_all": f"{reverse('activities:list')}?{query}"},
        {"key": "documents", "label": "Documents", "icon": "folder", "result": documents,
         "see_all": f"{reverse('documents:list')}?{query}"},
    ]


@staff_login_required
@require_GET
def search_page(request: HttpRequest) -> HttpResponse:
    q = request.GET.get("q", "").strip()[:MAX_QUERY_LENGTH]
    groups = search_groups(q) if q else []
    context = {
        "page_title": "Search",
        "page_subtitle": f'Results for "{q}"' if q else "Search activities and documents",
        "q": q,
        "groups": groups,
        "total": sum(group["result"].total for group in groups),
    }
    return render(request, "pages/search.html", context)
