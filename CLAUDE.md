# CSU-TBI Track — rules for Claude

"CSU-TBI Track: A RAG-Based System for Activity and Document Monitoring of Technology
Business Incubator" is a capstone project. Work as a careful senior Django developer. The
owner is a student, so explain important decisions briefly and keep the code simple and readable.

## Sources of truth
- `docs/erd.png` is the final ERD: table names, field names, types and relationships.
  If anything in the ERD is unclear or contradicts itself, **stop and ask**. Don't guess.
- `docs/ASSUMPTIONS.md` lists every assumption. If a requirement is missing, state your
  assumption and add it there instead of silently deciding.

## Tech stack (fixed, don't change it)
- PostgreSQL + pgvector in Docker (`docker-compose.yml`)
- Django (latest stable, currently 6.1), Python 3.12 (venv in `.venv`)
- Django-Q2 with the database as broker (no Redis)
- Gemini Flash for answers, Gemini embedding model for embeddings, via `google-genai`.
  Keys come from environment variables only.
- WeasyPrint for PDFs, openpyxl for Excel
- Local file storage for uploads (`MEDIA_ROOT`)
- Local Django login now; Navigatu SSO is a later phase
- Frontend: Django Templates, HTMX, Alpine.js, Tailwind CSS v4 (standalone CLI, no Node.js). Built phase by
  phase (F1 done); see "Frontend" below.

## Apps
`accounts`, `activities`, `registrations`, `documents`, `certificates`, `reports`,
`startups`, `assistant`, `notifications`. The project package is `config`.

## Model decisions
- TBI_MEMBER is the custom user model (`AUTH_USER_MODEL = "accounts.TBIMember"`, email login,
  unique). STARTUP is a separate model with its own login and is NOT a Django staff user.
- `AUTH_USER_MODEL` must be set before the first full `migrate`.
- TBI_MEMBER has no PermissionsMixin. `is_staff` is a property: True only when
  `role.can_manage_users` (Admin). Passwords use Django's `password` field (also on STARTUP).
- `assistant/migrations/0001_enable_pgvector.py` enables the `vector` extension. Don't move it
  to `accounts`: accounts/0001 must create TBI_MEMBER (admin/auth depend on it).
- Embedding size lives only in `settings.EMBEDDING_DIMENSIONS` (768).
- Use `PROTECT` on foreign keys to lookup tables, signatories and templates. Never delete
  those rows; deactivate them (`is_active = False`) where the table has `is_active`.
- ROLE, ACTIVITY_STATUS, DOCUMENT_STATUS have fixed names the code relies on and no `is_active`.
- Django admin: ACTIVITY_STATUS, DOCUMENT_STATUS are view only; ACTIVITY_TYPE, DOCUMENT_TYPE
  editable but never deleted; ROLE only its two flags (through
  `accounts.services.update_role_permissions`, which keeps at least one active Admin).
- PARTICIPANT_CATEGORY is Admin-managed master data (no `is_active`): seeded names from
  `accounts/seed.py` can't be renamed/deleted; added ones can be renamed and deleted while unused.
  Names are unique ignoring case/outer spaces. Never assume a fixed number of categories.
- An activity has ONE OR MORE participant categories: `Activity.participant_categories`, a
  many-to-many through ACTIVITY_PARTICIPANT_CATEGORY (ERD change in the Step 4 review). Change
  them only through `update_activity` (one audit row "Participant Categories"). Per-category
  counts can exceed the total: always count distinct activities (filter with EXISTS, not a join).
- Checklist and Document Repository must use `Document.objects.activity_documents()`
  (excludes certificate files, i.e. `related_certificate` set).
- Primary keys and table names follow the ERD (`activity_id`, `db_table = "activity"`).
- Activities are never deleted (audit log FK is PROTECT); use status changes instead.
- Certificate status: Generated → Pending Sign-off → Approved → Issued, one step at a time;
  Pending Sign-off → Generated on rejection; Issued is final. Change it only through
  `certificates.services.set_certificate_status()`, which enforces the rules.
- File path columns use `max_length=255`.

## Login and permissions
- Check permissions only with `accounts/permissions.py`: `permission_required(Permission.X)` /
  `staff_login_required` on views, `require_permission()` in services. Never rely on hiding a button.
- `Permission.CREATE_ACTIVITY` (create/edit activities), `Permission.MANAGE_USERS` (Admin Console:
  users, roles, master data, certificate design and signatories).
- Member changes go through `accounts/services.py` (guardrails: no self-deactivation, at least one
  active Admin, never delete). Never log passwords; temporary passwords live only in the email.
- A STARTUP is never `request.user`; startup login uses `STARTUP_SESSION_KEY`.
- `/api/` URLs (`config.json.is_api_path`) answer in JSON: 401 `{"error": "Login required."}` (never a
  redirect), 403 during the first-sign-in password change, 404/500 via `config.views` handlers.
  HTML URLs keep redirecting to the login page.
- Password resets: Admin `reset_member_password` (new temporary password by email, must change it)
  and Django's self-service "forgot password" (`/accounts/password/forgot/`, one-time link, 1 hour,
  rate limited by `password_reset_allowed()`: 5 per email, 20 per IP per hour, same page when limited).
- Sessions: 8 hours, end when the browser closes; secure cookies when DEBUG is off (HTTPS needed).
- Uploaded files are never served from `/media/` (not even in DEBUG). Documents are downloaded only
  through `documents.views.download_document` (active staff login, always an attachment); certificate PDFs,
  evidence and template/signature images only through the views in `certificates/views.py` (and the
  startup portal's own-certificate download).

## Activities
- Every activity change goes through `activities/services.py` (create, update, change_status,
  add/remove resource person), also from the Django admin and the demo seed. Never call
  `Activity.objects.create()` / `.save()` directly for user edits: the audit log would be skipped.
- Status is manual only (no automatic job). Moves: Draft→Scheduled→Ongoing→Conducted, plus
  Scheduled→Draft when nobody has registered. Conducted is final and read-only.
- The audit log is append-only (enforced in the model). Format values only with
  `activities.formatting.format_audit_value()`.
- Creating: start_date not before today and registration_deadline not in the past (Manila time).
  Editing: those checks apply only to fields being changed. Only tests/demo pass `now=`.
- Offer status choices with `allowed_next_statuses()`; change status only with `change_status()`.
- Registration state is calculated, never stored: `registered_count()`, `slots_remaining()`,
  `is_registration_open()` in activities/services.py (Step 5 must reuse them).

## Registrations and evaluations
- All rules live in `registrations/services.py`: forms (`create_form`, field edits, locked once
  submitted), `validate_answers` (the only answer check), `register` (locks the activity row;
  reuses `is_registration_open`), `set_attendance`, evaluation links and `submit_evaluation`.
- REGISTRATION.participant_category must be one of the activity's categories (auto when only one).
- Options and checkbox answers are JSON lists. Evaluation links are signed (django.core.signing),
  never "type your email". Public pages never show who registered.
- Default form fields are placeholders (validate with the TBI); `seed_data` creates them once.
- Public JSON lives under `/api/` (`/api/register/<id>/`, `/api/evaluate/<token>/`,
  `/api/startups/set-password/<token>/`). The page URLs `/register/<id>/`, `/evaluate/<token>/`,
  `/startups/set-password/<token>/` are reserved for the frontend's HTML pages (placeholder until
  then); emails always link to the page URLs.
- Registration emails (confirmation, evaluation links) are Django-Q2 tasks (`registrations/tasks.py`,
  queued via `queue_confirmation` / `queue_evaluation_link`); never send them inside the request.
- The public registration view calls `guard_public_registration()` (rate limit per IP and per email,
  honeypot field `website`) before `register()`; the service itself is never rate limited.
- An evaluation link that is submitted or past its window shows only the closed reason.
- "Open for registration" lists use `open_activities()` + `public_activity_summary()` (public
  `/api/activities/open/`, portal `/startups/portal/api/activities/`); never a second rule.

## Documents, checklist and reminders
- All rules live in `documents/services.py`. Upload: any active staff member, any activity status
  (incl. Conducted), active types only. Files: pdf/docx/xlsx/jpg/png, checked by real content,
  max `DOCUMENT_MAX_UPLOAD_MB`. The stored path comes from `build_file_path()` (DOCUMENT_TYPE.naming_pattern
  or `FALLBACK_NAMING_PATTERN`); never use the uploaded name in a path.
- Review state is calculated from status + `reviewed_at` (no column): For Review + empty = waiting for a
  reviewer; For Review + set = returned (waiting for the uploader); Approved = final.
  `approve_document` / `return_document` need can_create_activity and never the uploader;
  `replace_document_file` / `delete_document`: uploader only, For Review only. Approved: never
  replaced, retyped or deleted. Step 8 queues embeddings at the marked place in `approve_document`.
- Use `Document.objects.activity_documents()` (via `documents_for_listing()`) everywhere until Step 9.
- `document_checklist()` is the only "what is missing" check (Step 8 reuses it). Due dates are
  computed (`due_date()`), never stored; overdue = after the due date (Manila) and not Approved.
- `send_document_reminders()` creates NOTIFICATIONs; the partial unique constraint
  `unique_document_reminder` guarantees one per (member, activity, document type, type).
  Scheduled with `setup_schedules` (idempotent), run by `qcluster`.
- Notifications: `notifications/services.py`; a member only sees/changes their own.
  NOTIFICATION.activity stays nullable (future startup notifications may have no activity).

## Startups and Navigatu
- `startups/providers.py` is the ONLY Navigatu access point: `get_provider()` returns the class named
  in `settings.STARTUP_PROVIDER` (mock now; `NavigatuStartupProvider` is a stub). Other code uses
  plain `StartupRecord`s and never reads the mock JSON.
- `sync_startups()` matches navigatu_id, then email (ignoring case); copies only Navigatu fields,
  never `password`; skips email conflicts; never deletes (deactivates). Deactivation safety guard:
  zero records or > `STARTUP_SYNC_MAX_DEACTIVATE_RATIO` → nobody deactivated; only the manual
  command's `--force` overrides it. A second run saves no row (`last_synced_at` is a bulk update).
- Startup login: `startups.services.authenticate_startup` / `login_startup` / `current_startup`,
  session key `STARTUP_SESSION_KEY`, lockout via `accounts.services` with `STARTUP_SCOPE`.
  Portal views use `startups.decorators.startup_login_required` (refuses staff sessions).
- Set-password links: signed, expiring (`STARTUP_PASSWORD_LINK_HOURS`), single use through the
  password-hash fingerprint; sent only by `can_manage_users`, never returned by the API.
- A logged-in startup registers through `registrations.services.register(..., startup=...)`
  (same rules); "my registrations" = `registrations_for()` (linked + older email matches, read only).
- Notifications services take a member or a startup as the recipient.

## AI assistant (RAG)
- Gemini is reached ONLY through `assistant/gemini.py` (`get_gateway()`). Key: `GEMINI_API_KEY` in `.env`
  (models: `GEMINI_CHAT_MODEL`, `GEMINI_EMBEDDING_MODEL`). Never log the key, questions, answers or
  document text. Tests use `assistant.testing.FakeGemini` (root conftest); a real client fails a test.
- A terminal opened before `.env` changed keeps the old values (VS Code `python.terminal.useEnvFile`;
  `load_dotenv` never overrides). Open a new terminal; `check_gemini` shows where each value comes from.
- Indexing rules live only in `assistant.indexing.why_not_indexable()`: Approved activity documents,
  not `ASSISTANT_EXCLUDED_DOCUMENT_TYPES` (Attendance Sheet: personal data), pdf/docx/xlsx with text.
  `index_document()` replaces a document's chunks in one transaction; failures keep old chunks.
  Approval queues it on commit; `index_documents` (and the 7:30 schedule) backfills.
- "Searchable" = has embeddings (`with_searchable()`), never stored.
- Answers: status/missing/overdue questions → record tools (`assistant/tools.py`, existing services,
  no participant data); content → `search_approved_documents` excerpts. Citations are renumbered
  and saved as AI_MESSAGE_SOURCE; no valid citation and no records → the NOT_FOUND reply.
  Excerpt text is data: always build it with `tools.excerpt_block()` (escapes `<excerpt` tags).
- `ask()` is synchronous but runs inside `gemini.time_limit(ASSISTANT_QUESTION_TIMEOUT_SECONDS)` (90 s
  for the whole question; past it: 503 kind "timeout"). Moving it to a background task is a future step.

## Certificates (Step 9)
- All rules live in `certificates/services.py`; status changes only through `set_certificate_status()`.
  PDFs only through `certificates/pdf.py` (`get_renderer()`; tests use `certificates.testing.FakeRenderer`
  from the root conftest, a real WeasyPrint render fails a test; only tests marked `real_weasyprint`
  render for real, skipped without Pango). Images go in as `data:` URIs only.
- Templates/signatories: Admin only, image checks in `validate_template_image` / `validate_signature_image`,
  files never change after upload (new row instead), served only by the Admin views (never `/media/`).
- `generate_certificates`: Conducted only, active template + VP + Director required, missing ones only,
  numbers from `_certificate_numbers()` (`TBI-<Manila year>-<00001>`, advisory lock). Each certificate keeps
  the design it was generated with.
- Sign-off actions (review, record_signoff, approve, reject, mark_handed_over) take one list of IDs or an
  activity, all or nothing, need can_create_activity. record_signoff logs who recorded it.
- The participant's printed name and email come from EVALUATION_SUBMISSION (`certificate_name`,
  `certificate_email`, checked in `registrations.services.clean_certificate_details`), never the registration.
  Approved + evaluated (whichever is last) → `prepare_certificate` (task) renders, issues, queues the email.
  Resource persons: rendered on approval, Issued at hand-over, never emailed.
- `send_certificate` locks the row (`select_for_update(of=("self",))`), sets `sent_at` only after sending.
  Retries: 5/30/120 min one-off schedules, then the 8:30 daily retry (`retry_pending_certificates`).
- Email: `settings.MAILERS` (console in DEBUG unless `EMAIL_REAL_SENDING`; `config.mail.GuardedSMTPBackend`
  never sends to `EMAIL_BLOCKED_DOMAINS`). Tests never open SMTP (root conftest).
- Evidence = DOCUMENT with `related_certificate` (`upload_evidence`), Step 6 rules, never listed/indexed.
- Startup "My certificates" = Issued certificates of `registrations_for(startup)`; anything else is 404.

## Reports and dashboard (Step 10)
- Every number comes from `reports/metrics.py` (definitions N1–N18, S1–S6: ASSUMPTIONS 196). The dashboard,
  the PDF and the Excel file call the same functions; never count anywhere else. Database aggregation only
  (subqueries/EXISTS so joins never multiply); the dashboard query count is fixed (test allows 20).
- Reports need both dates, exactly YYYY-MM-DD, end ≥ start (`metrics.parse_filters(dates_required=True)`); no conducted
  activities → "No conducted activities in this period." instead of empty tables (ASSUMPTIONS 213).
- "Period" numbers: Conducted activities with `end_date` in the range (+ optional activity type). S1 (by status)
  uses `start_date` and includes Draft. "Now" cards (S2–S6) ignore the range, respect the type; each card has `scope`.
- Document compliance is SQL with the rules of `document_checklist()`; `test_compliance_parity.py` must keep passing.
- Report types: **accomplishment and participation are the main ones**; also evaluation, document_compliance,
  certificates (`reports/content.py`: `REPORT_TYPES`, `SECTIONS`). `included_sections` = JSON list in the string column.
- `request_report` (can_create_activity) saves the row with an empty `file_path` and queues task `report-<id>`;
  `build_report` writes `reports/<year>/report-<id>-<type>.<ext>`. State is calculated (ready/failed/pending).
  Reports are never deleted; generating again = new row. Download only via `reports.views.download_report`.
- PDF via `certificates.pdf.get_renderer()` (fake in tests); Excel cells only through `excel.safe_cell`
  (text starting with = + - @ tab CR stays text).
- AI narrative only through `reports/narrative.py`: Gemini gets figures, activity titles and dates only
  (`for_ai=False` columns removed), the exact prompt goes in `ai_prompt`, a failure never stops the report.

## Frontend (Phase F1 onwards)
- Design reference: `docs/design/dashboard.png` (and `logo.png`). Desktop-first staff pages (1280 px+, usable at
  1024 px; below that the sidebar hides behind a menu button). Public pages (F3) must also work on phones.
- Pages are server-rendered views that call the existing services; HTMX only for partial updates (an HTMX request
  gets a partial template, `accounts.permissions.is_htmx()`). Never repeat a business rule in a template or in JS.
- Staff pages extend `templates/layouts/app.html` and pass `page_title`; sign-in and password pages extend
  `layouts/auth_split.html` (design `docs/design/login.png`); error pages extend `layouts/auth.html`.
  Login pages: one full-screen gradient #22378F → #60CDEE (`.auth-bg`) and a frosted glass card (`.auth-card`: white 70%
  + 20px blur, white 92% without backdrop-filter), dark text inside; white text only on the dark end; at 1366×768 and
  1440×900 the login page fits without scrolling (ASSUMPTIONS 257).
  The university is Caraga State University. The illustration is inline (`partials/login_illustration.html`;
  `docs/design/login-illustration.svg` is the unchanged reference); its CSS animations end with the final picture as the
  natural state and are all off under `prefers-reduced-motion` (classes `anim`, `rise`; ASSUMPTIONS 253).
- App shell (`layouts/app.html`, ASSUMPTIONS 258): the login gradient is the frame (`.app-bg`, 135°, dark to 30%, so the
  whole sidebar stays on the dark part); the sidebar sits on it (white text, active = white 18% pill + white left
  indicator, hover = white 10%, member in a `.sidebar-glass` card); the content is a light panel (`bg-panel` #F7F9FC,
  28px corners, 16px from the edges) that scrolls on its own, with the top bar inside it. Buttons are pill-shaped in the
  shell (`.app-shell .btn`). Cards: 24px corners (`.card`), KPI cards 20px (`.kpi-card`), soft shadow `shadow-card`
  (0 10px 30px rgba(34,55,143,.08)), `.hover-lift` on hover. White text only on dark gradients (sidebar, banner text
  side, `.spotlight`, `.date-tile-gradient`); measure it when changing a gradient.
- `layout` (sidebar items, badges, unread count, Manila date) comes from `config.frontend`
  (one list of nav items, each with an optional `Permission`).
- Reuse the components in `templates/components/` (kpi_card, section_header + `.card`, badge, status_badge, button
  classes `btn btn-primary|danger|warning|secondary|ghost`, empty_state, form_field, form_errors, pagination, toasts,
  progress_bar, date_tile, spinner, icon). Hover feedback: `.hover-lift` (cards), `.hover-row` (list rows), `.btn-arrow`
  (button whose arrow slides); movement only under `motion-safe` (ASSUMPTIONS 254). Icons only through `components/icon.html` (add Lucide 1.52.0 SVGs there).
  Display filters: `{% load ui %}` (`config/ui_tags.py`: number, percent, rating, relative_time, ...).
- Everything is self-hosted (`static/vendor/VERSIONS.md`); never link a CDN (a test checks it).
- Tailwind v4: tokens in the `@theme` block of `frontend/tailwind/app.css` (no tailwind.config.js). A new app's
  `templates/` folder must be added there as an `@source`. Run `python manage.py tailwind build` after template
  changes and commit `static/css/app.css` (ASSUMPTIONS 235). Text contrast at least 4.5:1 (KPI teal: large text only).
- CSRF for HTMX: `hx-headers` on the staff layout only (never in `base.html`: public pages must not print a
  per-request token, ASSUMPTIONS 247). HTMX without login: 401 + `HX-Redirect` (`staff_login_required`).
- HTML error pages: `templates/errors/403|404|500.html` (500 is rendered without the request). `/api/` stays JSON.
- Dashboard (`/`, `reports.views.dashboard_page` + `reports/dashboard_page.py`): numbers only from
  `reports.services.dashboard()` (= the API) and existing services (ASSUMPTIONS 237–244).
- Pages of later phases use `config.views.coming_soon` under their final URL names (`COMING_SOON_PAGES`); remove an
  entry when its page is built.
- Accessibility: real `<button>`/`<a>`/`<label>`, visible focus, `aria-label` on icon-only buttons, a text table or
  list for every chart, an empty state for every list, a loading indicator (`htmx-indicator`) for HTMX swaps.

## Business rules (enforce in services.py, not in views)
- Registration closes at `max_slots` or `registration_deadline`. No walk-ins, no approval
  step: everyone who registers is in.
- Evaluation opens after the activity's `end_date`, only for registrations with
  `attendance_confirmed = True`, and closes at `evaluation_deadline`. Never anonymous.
- A certificate is emailed only when its status is Approved, its delivery method is email,
  and its evaluation has been submitted. Participants get a Certificate of Participation by
  email; resource persons get a Certificate of Appreciation in hard copy.
- Only Approved documents get embeddings. Answers must cite source documents. Questions
  about what is missing are answered from database checks, not from documents.
- Every edit to an ACTIVITY writes an ACTIVITY_AUDIT_LOG row with the field name and the
  readable old and new values.
- Required-document reminders go to the activity's creator as an in-app NOTIFICATION.
  Never create the same reminder twice.
- Navigatu is accessed only through the `StartupProvider` class (mock for now).

## Working rules
- Business logic goes in each app's `services.py`. Keep views thin.
- API views return `config.json.JsonResponse` (never Django's directly), so date-times are shown
  in Manila time with the offset (`+08:00`), not UTC.
- The client IP comes only from `config.network.client_ip()` (`TRUSTED_PROXY_HEADER` behind a proxy).
- Request values meaning true/false go through `config.parsing.parse_bool()` (true/false only, else 400).
- Lookup lists for the frontend come from `/api/lookups/` (`config/lookups.py`). Master data and role
  flags are managed only in the Django admin (no Admin Console screens for them).
- Numbers about evaluations (also one activity's, `/reports/api/activities/<id>/evaluation/`) come
  from `reports/metrics.py`.
- Write pytest-django tests for every business rule (`<app>/tests/test_*.py`).
- Never put secrets in code. Never commit `.env`.
- Use clear names that match the ERD. Use type hints and short docstrings.
- After each step, report what changed, how to run it, and what to check. Then stop.
- Don't commit unless asked.

## Roadmap (one step at a time, only when asked)
0. Project setup — done
1. Models matching the ERD, constraints, admin — done (review changes applied)
2. Seed data command + fake demo data command (safe to run twice) — done
3. Staff login, role permissions, first-sign-in password change — done
4. Activities, status changes, audit log — done (incl. many categories per activity)
5. Public registration, attendance, evaluation flow — done
6. Documents, checklist, reminders (Django-Q2 scheduled task) — done
7. Startups module with a mock StartupProvider — done
8. RAG pipeline: parse, chunk, embed on approval, retrieve, answer with sources — done
9. Certificates (WeasyPrint), sign-off flow, automatic emails — done
10. Reports and dashboard numbers — done
- Backend review before the frontend (2026-10-23): batch 1 (ASSUMPTIONS 214–224) and batch 2 (225–232) applied.
- Frontend F1 (2026-10-24): design foundation, staff layout, login pages, dashboard, error pages (ASSUMPTIONS 233–254).
- Restyle (2026-10-25): login gradient + glass card (257), app shell frame + dashboard layout (258).
- Dashboard polish (2026-10-27): full-width KPI row, compact dates (`date_span`), 2-line upcoming titles (259).

## Setup: WeasyPrint on Windows (once per machine)
WeasyPrint needs the Pango/GObject system libraries (the pip package alone fails with
`cannot load library 'libgobject-2.0-0'`). Install them with MSYS2:
```powershell
winget install --id MSYS2.MSYS2 -e                                          # installs to C:\msys64
C:\msys64\usr\bin\bash.exe -lc "pacman -Syu --noconfirm"                    # run again if it asks for a restart
C:\msys64\usr\bin\bash.exe -lc "pacman -S --noconfirm mingw-w64-x86_64-pango"
setx WEASYPRINT_DLL_DIRECTORIES "C:\msys64\mingw64\bin"
```
Then **close VS Code and open a new terminal**: a terminal opened before `setx` does not see the
variable (same for `runserver` and `qcluster`, which renders the PDFs). Check with
`python manage.py check_pdf`. Tests never need Pango (the renderer is faked).

## Commands
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt           # what the app needs to run (the server needs only this)
pip install -r requirements-dev.txt       # development: also pytest, pytest-django and Playwright
python -m playwright install chromium     # once, development only: the browser for page screenshots
python manage.py tailwind install         # once: the pinned Tailwind CLI into tools/ (checked by SHA-256)
python manage.py tailwind build           # static/css/app.css (after template changes; commit the result)
python manage.py tailwind watch           # rebuild on every template change while working (Ctrl+C stops)
docker compose up -d
python manage.py migrate
python manage.py createcachetable   # once: table for login lockout counters
python manage.py seed_data      # lookups + Admin account (SEED_ADMIN_* in .env); safe to rerun
python manage.py seed_demo      # DEMO ONLY, fake data, needs DEBUG=True; safe to rerun
python manage.py setup_schedules          # once (safe to rerun): 7:00 sync, 7:30 indexing, 8:00 reminders, 8:30 certificate retry
python manage.py send_document_reminders  # run the reminders once by hand (testing)
python manage.py sync_startups            # sync startups from the provider now (--force: override the guard)
python manage.py index_documents          # index approved documents not indexed yet (--document <id>: redo one)
python manage.py check_gemini             # models in use (and where from), models the key can use, tiny real calls
python manage.py check_pdf                # WeasyPrint renders a test PDF in memory: OK or the exact error
python manage.py send_test_email you@example.com [--smtp]   # one test email (--smtp: really send it, even in DEBUG)
python manage.py retry_certificates       # render/issue ready certificates and send unsent emails now (the 8:30 retry)
python manage.py make_sample_certificate_images <folder>    # DEMO: invented background + 2 signatures (PNG)
python manage.py certificate_walkthrough <step> ...         # DEMO ONLY (DEBUG): try the certificate flow step by step
python manage.py show_dashboard [--start --end --activity-type]   # dashboard numbers as JSON (same as the API)
python manage.py generate_report --type accomplishment --format pdf --start 2026-01-01 --end 2026-06-30   # build now (--report <id>: a pending one)
python manage.py qcluster       # background worker (separate terminal; works on Windows; Ctrl+C stops)
pytest
```
