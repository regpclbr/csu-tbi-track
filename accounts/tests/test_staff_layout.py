"""Phase F1 layout: sidebar (ADMIN only with can_manage_users), top bar, logout form, coming-soon pages,
restyled login pages, HTML error pages, HTMX + CSRF, and the self-hosted static files."""

from datetime import time
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, RequestFactory
from django.urls import reverse

from config.views import server_error
from documents.models import Document, DocumentStatus, DocumentType

pytestmark = pytest.mark.django_db

HOME = reverse("home")
HTMX = {"HTTP_HX_REQUEST": "true"}


def logged_in(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def director(make_member):
    return make_member("Director")


# --- Sidebar -------------------------------------------------------------------------------------------------


def test_admin_section_only_with_can_manage_users(admin_member, director, view_only) -> None:
    console = reverse("accounts:console")
    admin_html = logged_in(admin_member).get(HOME).content.decode()
    assert "Admin Console" in admin_html and console in admin_html and ">System</p>" in admin_html
    for member in (director, view_only):
        html = logged_in(member).get(HOME).content.decode()
        assert "Admin Console" not in html and console not in html


def test_workspace_items_and_the_active_one(director) -> None:
    html = logged_in(director).get(HOME).content.decode()
    for label in ("Dashboard", "Activities", "Documents", "Certificates", "Startups", "Reports", "AI Assistant"):
        assert f'<span class="flex-1">{label}</span>' in html
    active = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', html)
    assert active == [HOME]
    html = logged_in(director).get(reverse("documents:list")).content.decode()
    # The sidebar item and (F4) the page's own "All documents" tab: both are the current page, nothing else is.
    assert set(re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', html)) == {reverse("documents:list")}


def test_member_name_role_and_initials(director) -> None:
    html = logged_in(director).get(HOME).content.decode()
    assert "Test Director" in html and ">TD</span>" in html and "Director" in html


def test_documents_badge_counts_documents_waiting_for_my_review(director, make_member, roles) -> None:
    from datetime import date, timedelta
    from decimal import Decimal

    from django.core.files.base import ContentFile
    from django.utils import timezone

    from activities.models import Activity, ActivityStatus, ActivityType

    head = make_member("Incubation Project Head")
    activity = Activity.objects.create(
        created_by=director, status=ActivityStatus.objects.get(name="Scheduled"), activity_type=ActivityType.objects.get(name="Workshop"),
        title="A", start_date=date(2026, 12, 1), end_date=date(2026, 12, 1), start_time=time(8, 0), end_time=time(17, 0), venue="Hall", max_slots=5,
        registration_deadline=timezone.now() + timedelta(days=5), evaluation_deadline=timezone.now() + timedelta(days=50),
        budget_amount=Decimal("1.00"),
    )
    document = Document(activity=activity, document_type=DocumentType.objects.get(name="Activity Design"), uploaded_by=director,
                        status=DocumentStatus.objects.get(status_name=DocumentStatus.FOR_REVIEW), title="AD",
                        file_name="a.pdf", file_extension="pdf", file_size_kb=1)
    document.file_path.save("a.pdf", ContentFile(b"%PDF-1.4"), save=False)
    document.save()
    # Only can_approve_documents (the Director) gets the badge, own uploads included (ASSUMPTIONS 344).
    assert "1<span class=\"sr-only\"> documents waiting for your review</span>" in logged_in(director).get(HOME).content.decode()
    assert "documents waiting for your review" not in logged_in(head).get(HOME).content.decode()


# --- Logout is a POST form -------------------------------------------------------------------------------------


def test_logout_is_a_post_form_and_get_is_refused(director) -> None:
    client = logged_in(director)
    html = client.get(HOME).content.decode()
    assert re.search(rf'<form method="post" action="{reverse("accounts:logout")}">\s*<input type="hidden" name="csrfmiddlewaretoken"', html)
    assert client.get(reverse("accounts:logout")).status_code == 405
    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302 and client.get(HOME).status_code == 302


# --- Top bar --------------------------------------------------------------------------------------------------


def test_search_goes_to_the_search_page_with_q(director) -> None:
    client = logged_in(director)
    html = client.get(HOME).content.decode()
    assert f'action="{reverse("search")}"' in html and 'name="q"' in html  # phase F5: global search
    page = client.get(reverse("search"), {"q": "pitch"})
    assert page.status_code == 200 and 'value="pitch"' in page.content.decode()


def test_ask_ai_and_the_bell(director) -> None:
    html = logged_in(director).get(HOME).content.decode()
    assert f'href="{reverse("assistant:chat")}"' in html and "Ask AI" in html
    assert f'hx-get="{reverse("notifications:dropdown")}"' in html


def test_unread_count_on_the_bell(director) -> None:
    from notifications.models import Notification

    for i in range(3):
        Notification.objects.create(member=director, message=f"m{i}", type="x")
    assert ", 3 unread" in logged_in(director).get(HOME).content.decode()


# --- Notifications dropdown ------------------------------------------------------------------------------------


def test_dropdown_shows_only_my_latest_notifications(director, view_only) -> None:
    from notifications.models import Notification

    Notification.objects.create(member=director, message="Mine", type="x")
    Notification.objects.create(member=view_only, message="Not mine", type="x")
    html = logged_in(director).get(reverse("notifications:dropdown"), **HTMX).content.decode()
    assert "Mine" in html and "Not mine" not in html and "Mark all as read" in html
    assert 'id="bell-count" hx-swap-oob="true"' in html


def test_mark_all_as_read_is_post_only_and_updates_the_count(director) -> None:
    from notifications.models import Notification

    Notification.objects.create(member=director, message="One", type="x")
    client = logged_in(director)
    assert client.get(reverse("notifications:dropdown_read_all")).status_code == 405
    html = client.post(reverse("notifications:dropdown_read_all"), **HTMX).content.decode()
    assert not Notification.objects.filter(is_read=False).exists()
    assert "Mark all as read" not in html and "unread" not in html


def test_dropdown_empty_state(director) -> None:
    assert "No notifications yet" in logged_in(director).get(reverse("notifications:dropdown")).content.decode()


def test_dropdown_needs_login() -> None:
    response = Client().get(reverse("notifications:dropdown"), **HTMX)
    assert response.status_code == 401 and response["HX-Redirect"].startswith(reverse("accounts:login"))


# --- No placeholder pages are left (phase F6 built the last one) -----------------------------------------------


def test_no_coming_soon_page_is_left(admin_member) -> None:
    import config.views

    assert not hasattr(config.views, "coming_soon") and not hasattr(config.views, "COMING_SOON_PAGES")
    for url_name in ("reports:list", "assistant:chat", "startups:list", "accounts:console"):
        response = logged_in(admin_member).get(reverse(url_name))
        assert response.status_code == 200 and "is coming soon" not in response.content.decode()
        assert Client().get(reverse(url_name)).status_code == 302


def test_admin_console_needs_can_manage_users(admin_member, director) -> None:
    url = reverse("accounts:console")
    assert logged_in(admin_member).get(url).status_code == 200
    response = logged_in(director).get(url)
    assert response.status_code == 403 and "use the Admin Console" in response.content.decode()


def test_new_activity_page_needs_can_create_activity(director, view_only) -> None:
    url = reverse("activities:create")
    assert logged_in(director).get(url).status_code == 200
    assert logged_in(view_only).get(url).status_code == 403


# --- Login and password pages (restyled, same behaviour) ----------------------------------------------------------


def test_login_page_is_styled_and_shows_the_error(director) -> None:
    client = Client()
    page = client.get(reverse("accounts:login"))
    assert "layouts/auth_split.html" in [t.name for t in page.templates]
    assert "<title>Sign in · CSU-TBI Track</title>" in page.content.decode()
    response = client.post(reverse("accounts:login"), {"email": "nobody@example.com", "password": "wrong"})
    html = response.content.decode()
    assert "Invalid email or password." in html and 'role="alert"' in html
    assert 'autocomplete="username"' in html and 'autocomplete="current-password"' in html
    assert reverse("accounts:password_forgot") in html and "Forgot password?" in html


def test_login_page_matches_the_design_text(client) -> None:
    from django.utils import timezone

    html = client.get(reverse("accounts:login")).content.decode()
    for text in ("Welcome back", "Sign in with your account to access your dashboard.", "All in one place.",
                 "Technology Business Incubator at Caraga State University",
                 f"© {timezone.localdate().year} CSU Technology Business Incubator"):
        assert text in html, text
    assert "Cagayan" not in html
    assert "Startups sign in through the startup portal" not in html  # removed; F6 adds a startup-portal link


def test_password_field_has_a_show_hide_button_with_a_changing_label(client) -> None:
    html = client.get(reverse("accounts:login")).content.decode()
    button = re.search(r'<button type="button"[^>]*@click="show = !show"[^>]*>', html).group(0)
    assert ":aria-label=\"show ? 'Hide password' : 'Show password'\"" in button and 'aria-label="Show password"' in button
    assert 'type="password"' in html  # the real type until Alpine switches it


ILLUSTRATION_LABEL = ('aria-label="Illustration: an activity dashboard, a progress ring, a calendar with completed days, '
                      'and an approved document"')


@pytest.mark.parametrize("url_name", ["accounts:login", "accounts:password_forgot", "accounts:password_forgot_done"])
def test_every_sign_in_page_has_the_animated_panel(client, url_name) -> None:
    html = client.get(reverse(url_name)).content.decode()
    assert ILLUSTRATION_LABEL in html and 'role="img"' in html  # inline SVG keeps the design's label
    assert html.count('class="li-enter') == 5  # four cards + the connector enter one by one
    assert html.count('class="li-enter li-card anim"') == 4  # the four cards also lift on hover
    assert html.count('class="rise block"') == 2 and 'class="rise block text-glacial-sky"' in html  # Glacial Sky (412)


def test_illustration_drops_the_leftover_background_circles() -> None:
    """The design SVG (the reference) keeps its two faint circles; the inline copy leaves them out."""
    root = Path(settings.BASE_DIR)
    reference = (root / "docs/design/login-illustration.svg").read_text(encoding="utf-8")
    inline = (root / "templates/partials/login_illustration.html").read_text(encoding="utf-8")
    assert 'cx="505" cy="395" r="70"' in reference and 'cx="505" cy="395" r="70"' not in inline
    assert 'cx="40" cy="40" r="36"' not in inline
    assert ILLUSTRATION_LABEL in reference and ILLUSTRATION_LABEL in inline


def test_animations_are_switched_off_for_reduced_motion() -> None:
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").replace(" ", "")
    assert "@media(prefers-reduced-motion:reduce){.anim,.rise{animation:none!important" in css


def test_first_sign_in_page_explains_the_change(make_member) -> None:
    member = make_member("Director", email="new@example.com", must_change_password=True)
    client = logged_in(member)
    assert client.get(HOME).url == reverse("accounts:password_change")
    html = client.get(reverse("accounts:password_change")).content.decode()
    assert "This is your first sign-in" in html and "<aside" not in html  # no sidebar before the change


def test_first_sign_in_htmx_request_gets_hx_redirect(make_member) -> None:
    member = make_member("Director", email="new@example.com", must_change_password=True)
    response = logged_in(member).get(HOME, **HTMX)
    assert response["HX-Redirect"] == reverse("accounts:password_change")


def test_password_pages_render(client) -> None:
    for name in ("accounts:password_forgot", "accounts:password_forgot_done", "accounts:password_reset_complete"):
        response = client.get(reverse(name))
        assert response.status_code == 200 and "layouts/auth_split.html" in [t.name for t in response.templates]
    invalid = client.get(reverse("accounts:password_reset_confirm", args=["bad", "bad-token"]), follow=True)
    assert "This link is not valid" in invalid.content.decode()


# --- Error pages -------------------------------------------------------------------------------------------------


def test_403_is_an_html_page_with_the_reason_and_the_api_stays_json(director) -> None:
    client = logged_in(director)
    page = client.get(reverse("accounts:console"))
    assert page.status_code == 403 and page["Content-Type"].startswith("text/html")
    assert "errors/403.html" in [t.name for t in page.templates]
    api = client.get(reverse("accounts:api_members"))
    assert api.status_code == 403 and api.json() == {"error": "You do not have permission to use the Admin Console."}


def test_404_is_an_html_page_and_the_api_stays_json(director) -> None:
    client = logged_in(director)
    page = client.get("/no-such-page/")
    assert page.status_code == 404 and "Page not found" in page.content.decode()
    assert client.get("/api/no-such-thing/").json() == {"error": "Not found."}


def test_500_page_needs_no_request_context() -> None:
    response = server_error(RequestFactory().get("/"))
    assert response.status_code == 500 and "Something went wrong" in response.content.decode()


# --- HTMX and CSRF --------------------------------------------------------------------------------------------------


def test_staff_pages_send_the_csrf_token_with_htmx(director) -> None:
    html = logged_in(director).get(HOME).content.decode()
    assert re.search(r"""hx-headers='\{"X-CSRFToken": "[A-Za-z0-9]{64}"\}'""", html)


def test_an_htmx_post_works_with_the_token_header_and_fails_without(director) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(director)
    token = re.search(r'"X-CSRFToken": "([A-Za-z0-9]+)"', client.get(HOME).content.decode()).group(1)
    url = reverse("notifications:dropdown_read_all")
    assert client.post(url, **HTMX).status_code == 403
    assert client.post(url, HTTP_X_CSRFTOKEN=token, **HTMX).status_code == 200


def test_login_and_public_pages_print_no_per_request_token_in_hx_headers(client) -> None:
    assert "hx-headers" not in client.get(reverse("accounts:password_forgot_done")).content.decode()


# --- Self-hosted static files ---------------------------------------------------------------------------------------


STATIC = Path(settings.BASE_DIR) / "static"


def test_built_css_and_vendor_files_exist() -> None:
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "--color-sidebar:#204654" in css.lower().replace(" ", "")  # Teal Waters (412); run: python manage.py tailwind build
    for name in ("vendor/htmx-2.0.11.min.js", "vendor/alpine-3.17.4.min.js", "fonts/InterVariable.woff2",
                 "img/favicon-32.png", "img/apple-touch-icon-180.png", "img/logo-96.png"):
        assert (STATIC / name).exists(), name


def test_templates_load_nothing_from_a_cdn() -> None:
    folders = [Path(settings.BASE_DIR) / "templates", *Path(settings.BASE_DIR).glob("*/templates")]
    offenders = []
    for folder in folders:
        for template in folder.rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            if re.search(r'<(script|link)[^>]+(src|href)="(https?:)?//', text):
                offenders.append(str(template))
    assert offenders == []


def test_every_select_in_our_templates_uses_the_shared_select_style() -> None:
    """One select style (.field-select: our chevron, same right padding). Django admin templates (templates/admin/)
    keep the admin's own look; list boxes (multiple / size) have no arrow, so they must not use it."""
    base = Path(settings.BASE_DIR)
    folders = [base / "templates", *base.glob("*/templates")]
    offenders = []
    for folder in folders:
        for template in folder.rglob("*.html"):
            if "admin" in template.relative_to(folder).parts:
                continue
            for tag in re.findall(r"<select\b[^>]*>", template.read_text(encoding="utf-8")):
                classes = (re.search(r'class="([^"]*)"', tag) or [None, ""])[1].split()
                list_box = re.search(r"\s(multiple|size=)", tag)
                # A list box must not use it; every other select must, without its own padding (pr-*/py-*).
                if ("field-select" in classes) == bool(list_box) or any(re.match(r"p[ry]-", c) for c in classes):
                    offenders.append(f"{template.relative_to(base)}: {tag}")
    assert offenders == []


def test_select_and_date_input_styles_in_the_built_css() -> None:
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").replace(" ", "")
    rule = ";".join(re.findall(r"\.field-select\{([^}]*)\}", css))  # the minifier may split it in two
    assert "appearance:none" in rule and "stroke='%2362767D'" in rule  # slate-500, re-tinted toward Teal Waters (412)
    assert "background-position:right.875remcenter" in rule  # the same place on every select
    # selects, date and time inputs share one height with the buttons: 40 px (time inputs since 345, 40 px since 411)
    assert re.search(r"select\.field-input,input\[type=\"?date\"?\]\.field-input,input\[type=\"?time\"?\]\.field-input\{[^}]*height:calc\(var\(--spacing\)\*10\)", css)
    # ASSUMPTIONS 411, 412: input borders #738890 (3.7:1 on white, 3.6:1 on the panel; slate-300 was 1.5:1, below 3:1)
    assert "--color-line-strong:#738890" in css.lower()
    assert "border-color:var(--color-line-strong)" in ";".join(re.findall(r"\.field-input\{([^}]*)\}", css))
    # The glass inputs set only the colour, so the chevron of a select on a glass card stays.
    glass = re.search(r"\.auth-card\.field-input,\.public-card\.field-input\{([^}]*)\}", css).group(1)
    assert "background-color:#ffffffd9" in glass.lower() and "background:" not in glass
    assert "border-color:var(--color-line-strong)" in glass  # 412: a real 3:1 input border on the glass too


def test_hover_styles_exist_and_motion_stops_for_reduced_motion() -> None:
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").replace(" ", "")
    card_hover = re.search(r"\.login-illustration\.li-card:hover\{([^}]*)\}", css).group(1)
    assert "transform:translateY(-6px)" in card_hover and "drop-shadow" in card_hover
    assert ".btn-arrow:hoversvg:last-child{transform:translate(4px)}" in css
    assert ".hover-lift{" in css and ".hover-row{" in css
    reduced = css[css.index("@media(prefers-reduced-motion:reduce)"):]
    assert ".login-illustration.li-card:hover,.btn-arrow:hoversvg:last-child,.auth-card:hover{transform:none}" in reduced
    card = re.search(r"\.auth-card:hover\{([^}]*)\}", css).group(1)
    assert "translateY(-4px)" in card and "box-shadow" in card


def test_sign_in_pages_use_the_full_screen_gradient_and_the_glass_card(client) -> None:
    """ASSUMPTIONS 257: one gradient behind the whole page (Teal Waters since 412: #204654 -> #2F6170, no bright end), a
    frosted glass card (white 88% + blur, white 92% without backdrop-filter), dark text in the card and on the footer pill."""
    html = client.get(reverse("accounts:login")).content.decode()
    assert 'class="auth-bg ' in html and "auth-panel" not in html
    assert re.search(r'class="rise auth-card [^"]*"', html) and "auth-footnote" in html
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").lower()
    assert re.search(r"\.auth-bg\{background:linear-gradient\(135deg,#204654[^}]*#2f6170 100%\)\}", css)
    for old in ("#60cdee", "#1f2f6e", "#41598c", "#22378f"):  # the blue gradients are gone (ASSUMPTIONS 411, 412)
        assert old not in css
    card = re.search(r"\.auth-card(?:,\.public-card)?\{([^}]*)\}", css).group(1)  # F3: the public pages share the glass
    assert "background:#ffffffeb" in card and "border-radius:24px" in card  # the fallback: white 92%
    glass = css[css.index("@supports ((-webkit-backdrop-filter:blur(1px))"):]
    glass = glass[:glass.index("}")]
    assert "-webkit-backdrop-filter:blur(20px)" in glass and ";backdrop-filter:blur(20px)" in glass
    assert "background:#ffffffe0" in glass  # white 88% (412: muted text 5.6:1 or more anywhere on the teal gradient)



def test_app_shell_is_a_gradient_frame_with_a_rounded_panel_and_pill_buttons(director) -> None:
    """ASSUMPTIONS 258: the sidebar sits on the login gradient (Teal Waters since 412); the content is a light rounded panel."""
    html = logged_in(director).get(HOME).content.decode()
    assert re.search(r'<body class="h-full app-bg', html)
    assert 'id="content-panel"' in html and "lg:rounded-[28px]" in html and "sidebar-glass" in html
    active = re.search(r'<a href="/"\s+class="([^"]*)"\s+aria-current="page"', html).group(1)
    assert "bg-white/18" in active and "shadow-[inset_3px_0_0_#FFFFFF]" in active
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").lower()
    assert re.search(r"\.app-bg\{background:linear-gradient\(135deg,#204654[^}]*#2f6170 100%\)\}", css)
    assert "--color-panel:#fafbed" in css  # Morning Mist 60% behind the white cards (412)
    assert ".app-shell .btn{border-radius:9999px}" in css
    assert re.search(r"\.card\{[^}]*border-radius:24px", css) and "#20465412" in css  # card radius + soft teal shadow


def test_page_header_holds_the_title_once_and_the_primary_action(director) -> None:
    """ASSUMPTIONS 411: the page title (h1) and the page's own description are in the page header, not in the top bar
    (so the title never appears twice); the page's one primary action is on its right; Ask AI is a secondary button."""
    html = logged_in(director).get(HOME).content.decode()
    topbar = html[html.index('<header class="sticky top-0'):html.index("</header>")]
    assert "<h1" not in topbar and 'class="btn btn-secondary hidden sm:inline-flex"' in topbar  # Ask AI
    header = re.search(r'<div class="page-header">(.*?)<div class="page-header-actions">(.*?)</div>', html, re.S)
    assert '<h1 class="page-title' in header.group(1) and ">Dashboard</h1>" in header.group(1)
    assert "Here&#x27;s what&#x27;s happening at CSU-TBI" in header.group(1) or "Here's what's happening" in header.group(1)
    assert 'class="btn btn-primary"' in header.group(2) and "New activity" in header.group(2)
    assert html.count("<h1") == 1


def test_every_staff_page_has_its_own_description(director) -> None:
    """ASSUMPTIONS 411: no page shows the Dashboard's line ("Here's what's happening at CSU-TBI")."""
    client = logged_in(director)
    seen = set()
    for name in ("activities:list", "activities:create", "documents:list", "certificates:list", "reports:list",
                 "assistant:chat", "notifications:list", "startups:list", "search", "accounts:profile"):
        html = client.get(reverse(name)).content.decode()
        description = re.search(r'<p class="page-description">(.*?)</p>', html, re.S).group(1).strip()
        assert description and "happening at CSU-TBI" not in description, name
        assert description not in seen, name
        seen.add(description)


def test_only_the_primary_button_is_solid_primary_on_the_dashboard(director) -> None:
    """ASSUMPTIONS 411: the welcome card and the spotlight are white cards (a thin brand line / a tinted icon), their
    buttons are secondary; Needs attention and Recent changes share a row; upcoming activities join the charts grid."""
    html = logged_in(director).get(HOME).content.decode()
    assert 'class="welcome-banner card' in html and "on-dark" not in html.split('id="main"')[1]
    assert html.count("btn-primary") == 1  # "New activity" in the page header
    assert 'title="Recent changes"' not in html or "View all" in html
    attention = html.index('id="needs-attention"')
    assert attention < html.index('id="changes-title"') < html.index('id="upcoming-title"') < html.index('id="categories-title"')
    css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text(encoding="utf-8").lower()
    assert re.search(r"\.spotlight\{background:#fff\}", css) and "linear-gradient(90deg,#204654 0%,#a9e0f1 100%) top/100% 3px" in css


# --- Colour palette (ASSUMPTIONS 412) ------------------------------------------------------------------------------

SOURCE_CSS = Path(settings.BASE_DIR) / "frontend" / "tailwind" / "app.css"


def theme_tokens() -> dict[str, str]:
    """The `--color-*` tokens of the @theme block in the Tailwind source (the one place colours are defined)."""
    theme = re.search(r"@theme \{(.*?)\n\}", SOURCE_CSS.read_text(encoding="utf-8"), re.S).group(1)
    return {name: value.upper() for name, value in re.findall(r"--color-([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6});", theme)}


def contrast(foreground: str, background: str) -> float:
    """WCAG 2 contrast ratio of two #RRGGBB colours."""
    def luminance(colour: str) -> float:
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    light, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_the_four_palette_colours_are_the_brand_tokens() -> None:
    tokens = theme_tokens()
    assert tokens["teal-waters"] == "#204654" and tokens["glacial-sky"] == "#A9E0F1"
    assert tokens["morning-mist"] == "#F7F9E1" and tokens["spring-meadow"] == "#CAFFA6"
    assert tokens["primary"] == tokens["sidebar"] == tokens["heading"] == tokens["teal-waters"]
    assert tokens["page"] == tokens["morning-mist"] and tokens["panel"] == "#FAFBED"  # owner: the lighter mix


@pytest.mark.parametrize("foreground, background, minimum", [
    ("ink", "panel", 4.5), ("muted", "panel", 4.5), ("helper", "panel", 4.5), ("muted", "page", 4.5),
    ("primary", "panel", 4.5), ("link", "panel", 4.5), ("link", "primary-soft", 4.5), ("primary", "primary-soft", 4.5),
    ("heading", "panel", 4.5), ("sidebar-muted", "sidebar", 4.5), ("info", "info-soft", 4.5),
    ("success", "success-soft", 4.5), ("warning", "warning-soft", 4.5), ("danger", "danger-soft", 4.5),
    ("teal-waters", "spring-meadow", 4.5), ("glacial-sky", "brand-calm", 4.5),
    ("line-strong", "panel", 3.0), ("line-strong", "page", 3.0), ("focus", "panel", 3.0),
])
def test_palette_pairs_pass_wcag_aa(foreground, background, minimum) -> None:
    """Rule 4 of the palette change: every text / background pair passes AA, input borders and the focus ring 3:1."""
    tokens = theme_tokens()
    assert contrast(tokens[foreground], tokens[background]) >= minimum


@pytest.mark.parametrize("token", ["ink", "muted", "helper", "primary", "link", "danger", "line-strong", "focus"])
def test_text_and_borders_also_pass_on_white_cards(token) -> None:
    minimum = 3.0 if token in ("line-strong", "focus") else 4.5
    assert contrast(theme_tokens()[token], "#FFFFFF") >= minimum


def test_white_text_passes_on_the_whole_gradient_and_the_solid_buttons() -> None:
    tokens = theme_tokens()
    for background in ("teal-waters", "brand-calm", "primary-hover", "primary-pressed", "danger", "success"):
        assert contrast("#FFFFFF", tokens[background]) >= 4.5, background


def test_links_stay_apart_from_body_text() -> None:
    """A link in Teal Waters looked like body text (found in the palette preview): links use their own colour."""
    tokens = theme_tokens()
    assert tokens["link"] not in (tokens["ink"], tokens["primary"]) and contrast(tokens["link"], tokens["ink"]) > 2
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.link\{[^}]*color:var\(--color-link\)", css)


def test_spring_meadow_only_as_small_accents(director) -> None:
    """Spring Meadow is never a large background, a button or a gradient end: the sidebar count, the "happening now"
    dot (with a dark ring, the lime alone is 1.1:1 on white) and the Approved tint (a light mix of it)."""
    source = SOURCE_CSS.read_text(encoding="utf-8")
    assert not re.search(r"(?i)gradient\([^;]*(#CAFFA6|spring-meadow)", source)  # never a gradient end
    assert not re.search(r"\.btn[^{]*\{[^}]*spring-meadow", source)  # never a button
    used = set()
    for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
        if "spring-meadow" in path.read_text(encoding="utf-8"):
            used.add(path.name)
    assert used <= {"nav_item.html", "_body.html"}, used  # the count badge and the dashboard's ping halo
    html = logged_in(director).get(HOME).content.decode()
    assert "bg-spring-meadow" not in html.split('id="main"')[1].replace("bg-spring-meadow opacity-75", "")


def test_missing_documents_stay_grey() -> None:
    """Owner decision (412): Missing keeps the neutral grey badge, so it stays apart from the red Overdue by colour."""
    from documents.pages import MISSING_LABEL

    assert MISSING_LABEL == ("Missing", "neutral")


def test_no_rounded_vertical_scroll_box_inside_the_panel() -> None:
    """A vertical scroll box with its own rounded corners inside the scrolling content panel made Chromium draw a small
    square over the panel's rounded top-left corner (activity page, resource-person list; ASSUMPTIONS 412). Round a
    wrapper instead and let a square list scroll inside its padding."""
    offenders = []
    for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
        if path.parts[-2] in ("layouts",):  # the panel itself
            continue
        for classes in re.findall(r'class="([^"]*)"', path.read_text(encoding="utf-8")):
            names = classes.split()
            if "overflow-y-auto" in names and any(n.startswith("rounded") for n in names):
                offenders.append(f"{path.name}: {classes}")
    assert not offenders, offenders


def test_the_logo_tiles_are_teal_waters() -> None:
    from PIL import Image

    for name, size in (("img/logo-96.png", 96), ("img/favicon-32.png", 32), ("img/apple-touch-icon-180.png", 180)):
        image = Image.open(STATIC / name).convert("RGB")
        assert image.size == (size, size) and image.getpixel((0, 0)) == (0x20, 0x46, 0x54), name
    assert (Path(settings.BASE_DIR) / "docs" / "design" / "logo-navy-old.png").exists()  # the old tile, for reference
