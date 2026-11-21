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
  phase (F1–F6 done); see "Frontend" below.

## Apps
`accounts`, `activities`, `registrations`, `documents`, `certificates`, `reports`,
`startups`, `assistant`, `notifications`. The project package is `config`.

## Model decisions
- TBI_MEMBER is the custom user model (`AUTH_USER_MODEL = "accounts.TBIMember"`, email login,
  unique). STARTUP is a separate model with its own login and is NOT a Django staff user.
- `AUTH_USER_MODEL` must be set before the first full `migrate`.
- TBI_MEMBER has no PermissionsMixin. `is_staff` is a property: True only when
  `role.can_manage_users` (System Administrator). Passwords use Django's `password` field (also on STARTUP).
- `assistant/migrations/0001_enable_pgvector.py` enables the `vector` extension. Don't move it
  to `accounts`: accounts/0001 must create TBI_MEMBER (admin/auth depend on it).
- Embedding size lives only in `settings.EMBEDDING_DIMENSIONS` (768).
- Use `PROTECT` on foreign keys to lookup tables, signatories and templates. Never delete
  those rows; deactivate them (`is_active = False`) where the table has `is_active`.
- ROLE, ACTIVITY_STATUS, DOCUMENT_STATUS have fixed names the code relies on and no `is_active`.
- Django admin: ACTIVITY_STATUS, DOCUMENT_STATUS are view only; ACTIVITY_TYPE, DOCUMENT_TYPE
  editable but never deleted; ROLE only its four flags (through
  `accounts.services.update_role_permissions`, which keeps at least one active System Administrator and refuses
  can_manage_users together with can_create_activity, can_approve_documents or can_manage_signatories: `check_role_flags`,
  separation of duties; can_approve_certificates IS allowed with can_manage_users, ASSUMPTIONS 361, 418).
- ROLE.can_approve_documents (ASSUMPTIONS 344): approve/return documents. Migration accounts/0004 gave it to "Director"
  only; `seed_data` sets it only when creating a role (never re-applied). The System Administrator switches it in the
  Admin Console roles screen (`set_role_document_approval`) or the Django admin; no last-approver guard, only a warning.
- The role "Admin" is "System Administrator" (`Role.SYSTEM_ADMINISTRATOR`, migration accounts/0003): manages users, never
  creates activities (ASSUMPTIONS 85, 339). Never look a role up by name for a permission; use the flags. In the Django
  admin, activity data is view only without can_create_activity (`accounts.admin.ActivityDataAdminMixin`). Account and
  role changes are logged on `accounts.audit` (who, to whom / which role, old -> new; never passwords; ASSUMPTIONS 340).
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
- Activities are never deleted by normal users (status changes instead). The ONE exception (ASSUMPTIONS 389):
  `activities.deletion.delete_activity` with `can_delete_activities` (System Administrator), refused by
  `deletion_refused_reason` (Issued certificate, Approved document/evidence, cited document), title typed to confirm;
  one transaction, files removed on commit, an ACTIVITY_DELETION record (no FK to the activity) + `activities.audit`.
  The audit log stays append-only except `ActivityAuditLog.objects.delete_for_deleted_activity` (only there).
  Certificate numbers come from CERTIFICATE_NUMBER_SEQUENCE (never reused, also after undo attendance).
- ACTIVITY has `start_time` / `end_time` (Manila, nullable only for old rows the migration could not read; both or
  neither; one-day: end after start), no `time_schedule` (ASSUMPTIONS 345). Display only through
  `activities.formatting.format_time_range()` (filter `time_range`): "8:00 AM – 5:00 PM" or "Time not set".
- ACTIVITY_AUDIT_LOG.changed_by empty = System (automatic status change, certificates created automatically); show it
  with `formatting.changed_by_name()`. Certificate rows in History use the label "Certificates"
  (`activities.services.write_certificate_history`).
- Certificate status: created by the System → Generated ("Waiting for approval") → Approved (by the System, in the name of
  the member who approved the ACTIVITY, once ready; ASSUMPTIONS 369) → Issued; Approved → Generated only when the
  activity's approval is cancelled or its design / signatory / text changes; Issued stays Issued (a reissue keeps the
  status, ASSUMPTIONS 364).
  Change it only through `certificates.services.set_certificate_status()`, which enforces the rules (ASSUMPTIONS 357).
  CERTIFICATE has `approved_by` / `approved_at` / `send_failed_at` / `reissued_at`, `left_signatory` / `right_signatory`,
  `left_signatory_title` / `right_signatory_title` (the titles printed, ASSUMPTIONS 365), `render_failed_at` /
  `render_error` (plain words, ASSUMPTIONS 368), `certificate_text` (the wording printed, ASSUMPTIONS 370) (no review,
  sign-off, VP or Director columns).
- ACTIVITY has `certificates_approved_by` / `certificates_approved_at` (ASSUMPTIONS 369): the one approval of the activity's
  certificates. Set / cleared ONLY by `certificates.services.approve_activity_certificates` / `cancel_certificate_approval`
  (never `update_activity`, the form or the admin).
- CERTIFICATE_TEXT (ASSUMPTIONS 370): the wording (title, line before the name, main sentence, closing line) per type
  (participation / appreciation); `activity` empty = the default; versioned (`is_current`; a row a certificate uses never
  changes). Changed only through `save_default_text`, `save_activity_text`, `use_default_text`. 34 tables (ASSUMPTIONS 389,
  390: POSITION, ACTIVITY_DELETION, CERTIFICATE_NUMBER_SEQUENCE).
- ROLE.can_approve_certificates (ASSUMPTIONS 357, 361, 388): approve certificates (and reissue, Retry, the default
  certificate text). Migration accounts/0006 gave it to Director, Incubation Project Head, Community Development Head and
  System Administrator; seed also gives it to Admin Staff (388); seed only on create, roles screen switch
  `set_role_certificate_approval`, no last-approver guard, allowed together with can_manage_users (the System
  Administrator approves but never generates). It NO LONGER manages signatories or signature images (418).
- ROLE.can_manage_signatories (ASSUMPTIONS 418, NAVIGATU: only the TBI Director): the signatory list, signature images
  (new versions), "Remove white background", deactivate, the defaults, the Signatories page, the tab's signature upload
  and "Add a person" (tab and activity form), the signatory JSON API. Migrations accounts/0009 (column) + 0010 (guarded
  data step: the role named "Director" only, skipped if it manages users); seed Director only, on create only; roles
  screen switch `set_role_signatory_management` (audit line, amber note when nobody active has it, no last-member
  guard); NEVER together with can_manage_users (`check_role_flags`).
- Roles (seed defaults, ASSUMPTIONS 388, 418; flags are switched per role, never checked by name):

  | Role | can_create_activity | can_manage_users | can_approve_documents | can_approve_certificates | can_delete_activities | can_manage_signatories |
  |---|---|---|---|---|---|---|
  | Director | ✓ | – | ✓ | ✓ | – | ✓ |
  | Incubation Project Head | ✓ | – | – | ✓ | – | – |
  | Community Development Head | ✓ | – | – | ✓ | – | – |
  | Admin Staff | ✓ | – | – | ✓ | – | – |
  | System Administrator | – | ✓ | – | ✓ | ✓ | – (never with can_manage_users) |

  `seed_data` sets every flag except can_manage_users only when it creates a role (re-applies can_manage_users).
  Roles screen switches: can_create_activity (`set_role_activity_creation`), can_approve_documents, can_approve_certificates,
  can_manage_signatories (`set_role_signatory_management`, ASSUMPTIONS 418), can_delete_activities
  (`set_role_activity_deletion`; allowed with can_manage_users, ASSUMPTIONS 389) (toast + `accounts.audit`, amber note
  when no active member has the flag). Tests' "cannot" checks use the test-only role
  `conftest.VIEW_ONLY_ROLE` ("View Only (test)", fixture `view_only`), never Admin Staff.
- `Role.DIRECTOR` ("Director") is used only to choose who is told (`notifications.services.active_directors`: the
  new-activity notification `notify_directors_of_new_activity` and the Draft creation-document reminders, ASSUMPTIONS 417),
  never for a permission.
- CERTIFICATE_SIGNATORY is a list of people (ASSUMPTIONS 363): name, free-text position, nullable signature image,
  is_active, `default_slot` (left/right, one per slot: who a new activity starts with), `previous_version` (the row it
  replaced, ASSUMPTIONS 367). A row a certificate uses never changes: a new image or a name/position edit is a NEW row
  (`services._new_version`) that every activity on ANY older version moves to; Issued certificates keep the old row.
  The list itself (add, edit, images, deactivate, defaults) is changed only with can_manage_signatories (ASSUMPTIONS 418).
  ACTIVITY_SIGNATORY (activity, signatory, slot left/right, `position_on_certificate` = optional "Title on this
  certificate"; one per slot, never the same person twice, never emptied) is changed only through
  `set_activity_signatory` / `set_activity_signatories` / `use_current_version` (can_create_activity or
  can_approve_certificates, unchanged by 418) and `assign_default_signatories` / `fill_empty_slots_from_defaults` (System).
  Approval is refused until both images are there.
- File path columns use `max_length=255`.

## Login and permissions
- Check permissions only with `accounts/permissions.py`: `permission_required(Permission.X)` /
  `staff_login_required` on views, `require_permission()` in services. Never rely on hiding a button.
- `Permission.CREATE_ACTIVITY` (create/edit activities), `Permission.MANAGE_USERS` (Admin Console:
  users, roles, master data, certificate design), `Permission.APPROVE_DOCUMENTS` (approve/return documents),
  `Permission.APPROVE_CERTIFICATES` (approve and reissue certificates), `Permission.MANAGE_SIGNATORIES` (the signatory list,
  its images and defaults; ASSUMPTIONS 418), `Permission.DELETE_ACTIVITIES`.
  Either of several flags: `has_any_permission` / `require_any_permission`.
- My profile (`/accounts/profile/`, the sidebar name card, ASSUMPTIONS 390): own account only, `update_own_profile`
  (name, position) and `change_own_password` (validators, 10 wrong tries/hour, stays signed in, confirmation email).
  POSITION is a displayed job title only, NEVER a permission; managed in Admin Console → Positions (never deleted).
- Member changes go through `accounts/services.py` (guardrails: no self-deactivation, at least one
  active Admin, never delete). Never log passwords; temporary passwords live only in the email. The invitation links to
  `SITE_URL` + `/accounts/login/`; if it cannot be emailed the member is not created (`INVITATION_NOT_SENT`, ASSUMPTIONS 371).
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
  add/remove resource person), also from the Django admin and the demo seed. The create/edit form's "Who signs the
  certificates" goes in the same call (`signatories=` on `create_activity` / `create_activity_with_documents` /
  `update_activity`; ASSUMPTIONS 365). Never call
  `Activity.objects.create()` / `.save()` directly for user edits: the audit log would be skipped.
- Moves: Draft→Scheduled→Ongoing→Conducted, plus Scheduled→Draft when nobody has registered. Conducted is final and
  read-only. Staff move by hand (`change_status`); the system also moves Scheduled→Ongoing at the start and
  Ongoing→Conducted after the end once attendance exists (`automatic_next_status`, `apply_automatic_statuses`,
  `refresh_status`; same `_move_status` code, SKIP LOCKED, audit row by System; ASSUMPTIONS 66, 346). They run every
  `ACTIVITY_STATUS_INTERVAL_MINUTES` (`activities.tasks.run_activity_automation`) and on activity page / list loads.
  Page loads only change statuses, never send emails. Draft is never touched. `activity_start_end()` = the moments.
  Every move to Conducted also creates the certificates (`certificates.services.auto_generate_certificates`, never blocks
  the move; ASSUMPTIONS 362).
- The audit log is append-only (enforced in the model). Format values only with
  `activities.formatting.format_audit_value()`.
- Creating: start_date not before today and registration_deadline not in the past (Manila time).
  Editing: those checks apply only to fields being changed. Only tests/demo pass `now=`.
- Offer status choices with `allowed_next_statuses()`; change status only with `change_status()` (or the automatic moves).
- "Needs attention" = `activities/attention.py` only (`activity_attention` for the page, `attention_counts` /
  `attention_items` for the list, one shared item function, fixed queries, parity test; existing rules only, ASSUMPTIONS
  347, 356: ended with nobody registered, attendance marked after the evaluation closed).
- Activities list default sort "Grouped (recommended)" (`GROUPED_SORT`, page only; the API keeps `DEFAULT_SORT`):
  `services.grouped_activities()` + `activity_group()` (Happening now = `is_happening_now()` = `happening_now()`, Needs
  follow-up, Upcoming, Drafts, Past; only Past paginated, month headings in `pages.grouped_sections`). Follow-up quick
  actions: "Mark attendance" / "Mark as Conducted" (`change_status` with `next=list`), only with can_create_activity
  (ASSUMPTIONS 353). Plain sorts break ties in their own direction (start time, then ID).
- HTMX filter forms (`form[hx-push-url]`) keep Back / Forward right through the `htmx:afterSettle` handler in
  `templates/base.html` (writes current values into attributes; browser test `config/tests/test_history_browser.py`).
- Registration state is calculated, never stored: `registered_count()`, `slots_remaining()`,
  `is_registration_open()` in activities/services.py (Step 5 must reuse them).
- "Open for registration" = `open_for_registration()` (Scheduled, deadline, slots AND a registration form): the one query
  behind the Activities card/preset, the dashboard KPI, `/register/` and the portal (`open_activities()`) (ASSUMPTIONS 77).
- Activities list cards: "Activities in total" is plain; the other three open a dialog (`components/modal.html` remote,
  `activities:card_dialog`, `pages.card_dialog` → `services.preset_list`, max 50) and never filter the list. Card number,
  dialog and the list's `?preset=` ("Show all in the list" only) all come from `preset_activities()` (ASSUMPTIONS 341).

## Registrations and evaluations
- All rules live in `registrations/services.py`: forms (`create_form`, field edits, locked once
  submitted), `validate_answers` (the only answer check), `register` (locks the activity row;
  reuses `is_registration_open`), `set_attendance` (records `attendance_marked_by/_at`, cleared on undo, logged on
  `registrations.audit` with IDs only; `attendance_closed_reason` = "Attendance opens when …"), evaluation links and
  `submit_evaluation`. General-link requests are recorded on the matching registrations only
  (`evaluation_link_requested_at`, `evaluation_link_request_result`), the last email's `evaluation_link_delivery`
  (sent / printed_dev / printed_demo) by `deliver_evaluation_link`; the Evaluation tab shows them
  (`pages.link_requests`). An email that matches nobody is never stored (ASSUMPTIONS 354).
- One participant's evaluation answers (with the name) only for can_create_activity (`INDIVIDUAL_EVALUATION_PERMISSION`,
  `evaluation_answers`; ASSUMPTIONS 402). Everyone else gets the evaluation summary without names (ASSUMPTIONS 401).
- Evaluation links: sent only through `deliver_evaluation_link` (row lock, sets `evaluation_link_sent_at` or `_failed_at`,
  one clears the other). First send is automatic, once per attendee, inside the 5-minute job
  (`send_automatic_evaluation_links`: Conducted + evaluation form + open window); failures are never retried
  automatically; staff resend with `send_evaluation_links(..., registration_ids=None)` (ASSUMPTIONS 348).
- REGISTRATION.participant_category must be one of the activity's categories (auto when only one); the forms use
  `components/category_choice.html` (one category: a preselected radio; several: required, no default).
- Options and checkbox answers are JSON lists. Evaluation links are signed (django.core.signing),
  never "type your email". Public pages never show who registered.
- Default form fields are placeholders (validate with the TBI); `seed_data` creates them once.
- Public JSON lives under `/api/` (`/api/register/<id>/`, `/api/evaluate/<token>/`,
  `/api/startups/set-password/<token>/`). The page URLs `/register/<id>/`, `/evaluate/<token>/`,
  `/startups/set-password/<token>/` are the HTML pages (phase F3); emails always link to the page URLs.
- Registration emails (confirmation, evaluation links) are Django-Q2 tasks (`registrations/tasks.py`,
  queued via `queue_confirmation` / `queue_evaluation_link`) or run inside the 5-minute job; never send them inside the request.
- The public registration view calls `guard_public_registration()` (rate limit per IP and per email,
  honeypot field `website`) before `register()`; the service itself is never rate limited.
- An evaluation link that is submitted or past its window shows only the closed reason.
- Privacy notice (ASSUMPTIONS 394; retention wording 414): `/privacy/` + `public/_privacy_consent.html` (required checkbox `privacy_consent`) on the
  public registration page, the portal registration page and the evaluation review step. The one rule is
  `services.check_privacy_consent`, called by every public entry point (HTML, `/api/register/`, `/api/evaluate/`, portal page
  and API) before `register()` / `submit_evaluation()`; the services themselves never ask. Nothing is stored (no column).
- "Open for registration" lists use `open_activities()` + `public_activity_summary()` (public
  `/api/activities/open/`, portal `/startups/portal/api/activities/`); never a second rule.
- Shared links start with `SITE_URL`, never the request's Host: `registration_page_url()`, `evaluation_request_url()`,
  `evaluation_link()`. The Share card (`activities/share.py`: Scheduled = registration link, Open/Closed; Ongoing/Conducted =
  evaluation link while open; Draft = none) and its QR codes (`activities:qr_code`, segno PNG, staff login, 404 for any link
  the card does not show) (ASSUMPTIONS 331–337).
- General evaluation link `/evaluation/<id>/` (one per activity): the email used to register → `request_evaluation_link()`
  queues the existing personal link for attendees who have not evaluated; `evaluation_request_state()` is its window (the
  personal links' window + a form). Always the same neutral page (`EVALUATION_REQUEST_SENT`), also when
  `guard_evaluation_request()` refuses (rate limit per email/IP, honeypot). Security logs never contain an email (IP only);
  rate-limit cache keys hold an HMAC of the value, never the email (`config.ratelimit._key`, ASSUMPTIONS 355).

## Documents, checklist and reminders
- All rules live in `documents/services.py`. Upload: any active staff member, any activity status
  (incl. Conducted), active types only. Files: pdf/docx/xlsx/jpg/png, checked by real content,
  max `DOCUMENT_MAX_UPLOAD_MB`. The stored path comes from `build_file_path()` (DOCUMENT_TYPE.naming_pattern
  or `FALLBACK_NAMING_PATTERN`); never use the uploaded name in a path.
- Review state is calculated from status + `reviewed_at` (no column): For Review + empty = waiting for a
  reviewer; For Review + set = returned (waiting for the uploader); Approved = final.
  `approve_document` / `return_document` need can_approve_documents (403 on the page, API and review-tab URLs without
  it); approving your own upload is allowed, returning it is not (ASSUMPTIONS 344); every approval/return is logged on
  `documents.audit`. Upload and replace call `notify_approvers()` in the same transaction: a "document_waiting_for_review"
  NOTIFICATION for every active approver except the uploader. Reminders stay with the activity's creator;
  `replace_document_file` / `delete_document`: uploader only, For Review only. Approved: never
  replaced, retyped or deleted. Step 8 queues embeddings at the marked place in `approve_document`.
- Use `Document.objects.activity_documents()` (via `documents_for_listing()`) everywhere until Step 9.
- Documents with a new activity: only `create_activity_with_documents()` (one transaction with `create_activity` + `upload_document`,
  all or nothing, stored files removed on failure, errors as `NewActivityError`); offered types only from
  `types_allowed_at_creation()` (not due after the end date, not in `DOCUMENT_TYPES_AFTER_ACTIVITY`) (ASSUMPTIONS 327–328).
- Activity Design + Request Letter at creation (ASSUMPTIONS 405, 417): `creation_required_types()` (`DOCUMENT_TYPES_REQUIRED_AT_CREATION`,
  active + required) are the form's two fixed, OPTIONAL rows (`required-<type id>-file|title`, sent only when filled in,
  `NewDocumentRow.is_filled_in`, first in the row list). `ACTIVITY.creation_documents_required` (set only at creation: the form and the JSON API; admin, `seed_demo`,
  tests and every row before 2026-11-19 = False = old rules; never editable). Flagged: Draft → Scheduled needs both UPLOADED
  (`scheduling_refused_reason` in `_move_status`), never overdue once uploaded (`is_creation_type` in `_required_item`, same
  rule in `reports.metrics._overdue`, parity test), the only one of a type is never deleted outside Draft
  (`_delete_refused_reason`). A title is required with a file on the New activity form only
  (`creation_title_error` in `check_new_documents`, ASSUMPTIONS 410). Tests posting the New activity form add
  `conftest.creation_document_files()` (files + titles) when they need the files, and always `create_as`.
- "Create as" (ASSUMPTIONS 417, amends 405/409): the New form's required radio Draft (default) / Scheduled
  (`ActivityPageForm.creation_status`, `pages.create_as_section`, `activities/_create_as.html`) →
  `create_activity_with_documents(..., status=)` (only `CREATION_STATUSES`). Scheduled = `change_status` in the same
  transaction; a refusal rolls back everything and is `NewActivityError.status_error` (`scheduling_refused_reason(...,
  on_new_form=True)`) under the field. After an error everything typed is kept and the files to choose again are named
  (`new_documents_context.choose_again`). Draft → the Overview with what is still missing (`missing_creation_types`).
- Draft reminders (417): `send_document_reminders` also covers flagged Drafts, ONLY their Activity Design and Request Letter
  (`is_creation_type`), same rule and timing, to the creator AND every active Director
  (`notifications.services.active_directors`); Scheduled and later stay creator-only; other types / unflagged Drafts get none.
  Needs attention (`activities.attention`) already shows them on Drafts. The dashboard (S3, `document_attention`) and the
  compliance report keep leaving Drafts out.
- Every upload waits for the Director (`notify_approvers`, in-app only, never the uploader); the Director may approve their
  own upload (ASSUMPTIONS 344, 406).
- New activities (the form) chosen as Scheduled: `create_activity_with_documents` → `change_status` in the same
  transaction (History: Draft, then Draft → Scheduled); the page opens the Forms tab (ASSUMPTIONS 409, 417; Draft is the
  form's default). API / admin / demo: Draft. On flagged activities the registration form counts only once marked ready (`FORM_TEMPLATE.ready_at`,
  `mark_registration_form_ready`, Forms tab "Open registration"): the one rule `registrations.services.registration_form_ready`
  in `registration_closed_reason` and (as a query) `open_for_registration` (parity test). Until then the Share card shows
  `share.BUILD_FORM_FIRST` and no link / QR (404).
- `document_checklist()` is the only "what is missing" check (Step 8 reuses it). Due dates are
  computed (`due_date()`), never stored; overdue = after the due date (Manila) and not Approved.
  The due rule lives ONLY in the DOCUMENT_TYPE row (`due_reference` + `due_offset_days`); never hard-code a number of days.
  Activity Design and Request Letter: start_date - 5 (NAVIGATU's 5-day rule, ASSUMPTIONS 416; data migration documents/0005
  moves only a row still on start_date / -10); Attendance Sheet: end_date + 0 (placeholder).
- "Required" is only `DOCUMENT_TYPE.is_required` (per type, never per activity); every count reads it (ASSUMPTIONS 386).
  The Invitation Letter and the Accomplishment Report are OPTIONAL (data migrations documents/0003 and 0004, one row
  each; uploaded ones stay, listed under Other documents). Still required: Activity Design, Request Letter, Attendance Sheet.
  `seed_data` sets `is_required` / the due rule only when it creates a type (reruns never undo admin changes) and adds the
  optional catch-all type `DocumentType.OTHER` ("Other"), whose documents need a title (`services.title_error`, the one
  title rule, also on the New activity form). The assistant's routing never matches "Other" from a question.
- Documents tab: "Upload other document" → `?tab=documents&focus=type#upload` (dropdown autofocus); dropdown grouped
  Required / Other documents (`pages.documents_tab` `upload_groups`); `?type=<id>` pre-selects (required rows' Upload).
  Document rows (`documents/_document_item.html`, ASSUMPTIONS 387): title block `min-w-0 grow basis-64`, badges + buttons
  one `ml-auto flex-wrap` group; never `min-w-0 flex-1` alone in a wrapping row (it squeezes the title to letters).
  Documents page cards (ASSUMPTIONS 408, 411): header + lines share one grid (`grid-cols-subgrid`), State `minmax(9rem,11rem)`,
  Actions a fixed `21rem` (columns line up across activity cards); `documents/_actions.html` always wraps (never `flex-nowrap`: buttons covered the State badges).
- `send_document_reminders()` creates NOTIFICATIONs; the partial unique constraint
  `unique_document_reminder` guarantees one per (member, activity, document type, type).
  Scheduled with `setup_schedules` (idempotent), run by `qcluster`.
- Notifications: `notifications/services.py`; a member only sees/changes their own. Creating an activity
  (`save_new_activity`) notifies every active Director in the same transaction ("activity_created"), except when a
  Director creates it; no email (ASSUMPTIONS 361).
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
- Routing is OUR code (`assistant/routing.py`, ASSUMPTIONS 381, 383), in this order: missing/overdue/waiting/returned →
  Gemini with the existing record tools only (`tools.STATUS_TOOL_SPECS`); says/summary/according to (+ a file word or
  type) → `search_approved_documents` (`CONTENT_TOOL_SPECS`); file word or DOCUMENT_TYPE + activity/period/type →
  find files; activity word + period/status/count/list → activities; else Gemini with every tool. Types are matched
  against their tables (never a fixed list). Add a phrasing test in `assistant/tests/test_routing.py` for every change.
- Activities and find-files answers are written by `assistant/listings.py` without Gemini (work without the key; the page
  shows "The AI is turned off…" via `gemini.is_configured()`). Periods only through `assistant/periods.py` (`parse_period`; the
  Reports form uses `parse_range`, which adds quarters/halves/ranges, "annual"/"yearly", a year said anywhere (`context=`) and
  kind `ASK` for "quarterly"/"semi-annual" without which one (ASSUMPTIONS 399), and leaves `parse_period` unchanged) (Manila,
  weeks Monday–Sunday, month without a year = this year unless clearly past/future); Gemini never computes dates (the
  `list_activities_by_period` / `find_files` tools take the user's words; when only they ran, the reply is our text).
  Activities = the list's rules (`activities.services.activities_in_range`, `activity_group`, `happening_now`), drafts
  only when asked, max 20 + "N more". Files = `documents_for_listing()` (any review state; attendance sheets listed and
  downloadable, their content never sent to Gemini), grouped by activity, max 20, empty → `document_checklist()` missing.
- Links and file cards come from markers in the saved text (`[activity:<id>]`, `[file:<id>]`, `[activities-list:…]`,
  `[documents-list:…]`): `assistant.pages` reads digits/dates only, builds URLs with `reverse()`, only for rows that exist;
  staff titles go through `listings.safe_text`. Found files are NEVER saved as AI_MESSAGE_SOURCE (its document FK is
  PROTECT and would block deleting a For Review file). Cited documents: one "Sources" card per document, `[n]` → the card,
  Open = `documents:detail`, Download = `documents:download` (never `/media/`) (ASSUMPTIONS 382).
- Log one line per answer: chat id, message id, path, tool names only (never question, answer or document text).
- Answers: status/missing/overdue questions → record tools (`assistant/tools.py`, existing services,
  no participant data); content → `search_approved_documents` excerpts. Citations are renumbered
  and saved as AI_MESSAGE_SOURCE; no valid citation and no records → the NOT_FOUND reply.
  Excerpt text is data: always build it with `tools.excerpt_block()` (escapes `<excerpt` tags).
- `ask()` is synchronous but runs inside `gemini.time_limit(ASSISTANT_QUESTION_TIMEOUT_SECONDS)` (90 s
  for the whole question; past it: 503 kind "timeout"). Moving it to a background task is a future step.
- Chats (ASSUMPTIONS 377–378): `delete_chat` / `rename_chat` (own chats only, 404 otherwise; rename 1–80 characters, keeps
  `updated_at`). Title: the first question at once; after the FIRST answer `queue_chat_title` (on commit) → task
  `run_generate_chat_title` → `generate_chat_title` (first question + answer start only, `TITLE_INSTRUCTION`, `clean_title`: ≤ 6
  words) saved by compare-and-set only while the title is still the first question (no flag column, no ERD change). Any failure,
  no key or a rename keeps the current title; the task never raises or logs text. The page refetches My chats once, 6 s later
  (`assistant:chat_list`).
- The answer instruction asks for short paragraphs and simple lists, no tables or headings; the `[n]` citation rule is unchanged.

## Certificates (Step 9)
- All rules live in `certificates/services.py`; status changes only through `set_certificate_status()`.
  PDFs only through `certificates/pdf.py` (`get_renderer()`; tests use `certificates.testing.FakeRenderer`
  from the root conftest, a real WeasyPrint render fails a test; only tests marked `real_weasyprint`
  render for real, skipped without Pango). Images go in as `data:` URIs only.
- Templates: Admin only (`validate_template_image`). Signatories (ASSUMPTIONS 363, 418): can_manage_signatories only, on the
  Signatories page `/certificates/signatories/` (`add_signatory`, `edit_signatory`, `replace_signature`,
  `deactivate_signatory` (refused for an active default), `set_default_signatories`) and the tab's Signatures section
  (choose per slot and the title, `upload_signature`, "Add a person"); images PNG or JPG (`validate_signature_image`), a
  white background made transparent on upload unless "Keep the background" (`prepare_signature_file`, Pillow only in
  `certificates/imaging.py`; preview before saving `signature_preview`; "Remove white background" =
  `remove_signature_background`, a new version; ASSUMPTIONS 366); files never change after upload; served only by `certificates/views.py` (never `/media/`; signature images to can_manage_users,
  can_approve_certificates or can_manage_signatories). A new version refreshes the not-Issued certificates of EVERY activity using the person
  (`_refresh_certificates`: PDF removed, Approved → Generated); its dialog shows `version_impact_text()` first. Logged on
  `certificates.audit` (IDs only). The Admin Console only shows the defaults; the Django admin is view only for both tables.
  Viewing signature images: can_manage_users, can_approve_certificates or can_manage_signatories.
  Signatories page rows wrap (text `min-w-0 grow basis-48`, buttons `ml-auto flex-wrap`; never `sm:flex-nowrap` with
  `flex-1` text: it squeezed names to one letter per line, ASSUMPTIONS 407; opt-in `certificates/tests/test_signatories_browser.py`).
- Certificates are created only by the System (ASSUMPTIONS 362): `generate_missing_certificates(activity, registration_ids=)`
  (no member; Conducted only, active template + both saved slots required (`design_fields(activity)`; an empty slot is
  never filled here, ASSUMPTIONS 366), missing ones only, safe to rerun,
  one History row by System "N created automatically (…)"), called through `auto_generate_certificates` from the move to
  Conducted, from `set_attendance` (marked after Conducted) and the 5-minute job (`generate_pending_certificates`). No
  Generate button or endpoint. Numbers from `_certificate_numbers()` (`TBI-<Manila year>-<00001>`, advisory lock). Each
  certificate keeps the design it was generated with. Undoing attendance deletes a certificate that is still waiting
  (`delete_certificate_for_undone_attendance`, History row, number gap); refused once approved or evaluated.
- Who signs (ASSUMPTIONS 365-367): the printed title = `services.printed_title()` (the slot's "Title on this
  certificate", else the position), stored on each certificate (`design_fields`). Only saved slots count
  (`activity_signatories`; no default fallback): a new activity gets the defaults, an empty slot stays empty until
  chosen or a default is set (`fill_empty_slots_from_defaults`: empty slots of activities without certificates only,
  History row by System). Needs attention: "Choose who signs the certificates (left/right)" and "A signatory on this
  activity is inactive" (normal warnings, only while not every certificate is Issued). Inactive person on a slot:
  amber note + "Use the current version" (`current_version_of`).
- "Issued with an old signature" is computed only by `services.outdated_q()` (Issued, left or right row or its stored
  title is not the activity's current one). `reissue_certificates` (can_approve_certificates; preview button + "Reissue all outdated
  certificates (N)" with a confirm dialog): same number, still Issued, `reissued_at`, file/sent cleared, re-rendered by
  `prepare_certificate` (same file name: the old PDF is replaced only when the new one is written), participants emailed
  "Updated copy", resource persons "Not handed over (updated copy to print)" until Mark handed over (new `issued_at`).
  The Needs attention line is `informational` (never counted: `attention.warning_count`) (ASSUMPTIONS 364).
- "Ready" is computed only by `services.readiness()` / `readiness_map()` (both signature images; participants also the
  evaluation; labels "Waiting for signatures", "Waiting for evaluation", "Evaluation closed without an answer") and
  `ready_for_approval_q()` (the 5-minute catch-up). "Needs certificate approval" (counts: S5, Needs attention, the
  dashboard) is only `services.needs_approval_q()` (waiting + the activity not approved). Never store either.
  The state SHOWN (page header, tab card) is only `services.approval_state(activity, counts)` (ASSUMPTIONS 373; `counts`
  = all the activity's certificates per status, `approval_counts`): "Approved by … on …" / "Needs approval" (orange, only
  while certificates wait; agrees with `needs_approval_q`) / "All certificates issued (N)" (green + short note) /
  "Certificates are created automatically when the activity is Conducted." / grey "Not approved — …" (old data).
  `offers_approval` False (all issued, none yet) = no approve button at all and no reason line.
- Approval is per ACTIVITY (ASSUMPTIONS 369): `approve_activity_certificates` (can_approve_certificates, "Approve
  certificates for this activity" + confirm dialog; refused with `approval_closed_reason`) stores who/when on the activity
  and History; then `approve_ready_certificates` (System) approves each certificate when it is ready: at the approval,
  after each evaluation (`evaluation_submitted`), for certificates created later and in the 5-minute job
  (`approve_pending_certificates`). `cancel_certificate_approval` (History, Approved → waiting, Issued untouched) runs on
  a signatory or title change (`_apply_slots`), a new version of a person (`_new_version`), `regenerate_certificates` and
  any text change. There is NO per-certificate approve (no service, URL or button). With can_create_activity:
  `regenerate_certificates` ("Apply the current design": not-Issued ones, back to waiting), `mark_handed_over`,
  `resend_certificates` (row actions).
- Wording (ASSUMPTIONS 370): `certificates/wording.py` (placeholders {name} {activity} {date} {venue} {topic}
  {given_date} {organization}, `[ … ]` optional parts, limits 60/100/400/200, `check_field`, `fill`), filled in
  `pdf.certificate_html` from the certificate's own `certificate_text`; plain text, escaped by the template. Default text:
  `/certificates/text/` (can_approve_certificates, impact dialog). Per activity: the tab's "Certificate text"
  (can_create_activity or can_approve_certificates), live preview `wording_preview`. A change re-renders not-Issued
  certificates and cancels the approval; it never makes an Issued one outdated.
- Preview (`preview_pdf`): the same template as the final PDF with both signatures, a small "PREVIEW" corner mark only
  while waiting for approval (ASSUMPTIONS 358). An Issued certificate's preview is its stored final PDF
  (`shows_final_copy`, "Issued — this copy was already sent and is not changed."). `certificates:preview` is the second
  `xframe_options_sameorigin` view (the tab's large preview iframe); Download PDF gives the preview until the final PDF
  exists. Preview/download links carry `?v=` (`preview_version`) and the responses `Cache-Control: private, no-store`.
- The participant's printed name and email come from EVALUATION_SUBMISSION (`certificate_name`,
  `certificate_email`, checked in `registrations.services.clean_certificate_details`), never the registration.
  Approved + evaluated (whichever is last) → `prepare_certificate` (task) renders, issues, queues the email.
  Resource persons: rendered on approval, Issued at hand-over, never emailed.
- An Approved (or reissued) certificate without a PDF shows why: `services.pdf_state()` (approved before the evaluation
  rule — waiting / closed, PDF failed — Retry with `render_error`, waiting for the background worker, PDF being prepared;
  labels `pages.PDF_STATE_LABELS`). Failures are recorded by `record_render_failure` in plain words
  (`render_error_text`, never the raw message); Retry = `retry_certificate_pdfs` (can_approve_certificates); the 5-minute
  job re-queues renders stalled for 10 minutes (`queue_stalled_preparations`) (ASSUMPTIONS 368).
- `send_certificate` locks the row (`select_for_update(of=("self",))`), sets `sent_at` only after sending; a failure sets
  `send_failed_at` ("Send failed" + Resend on the tab, ASSUMPTIONS 360).
  Retries: 5/30/120 min one-off schedules, then the 8:30 daily retry (`retry_pending_certificates`).
- Email: `settings.MAILERS` (console in DEBUG unless `EMAIL_REAL_SENDING`; `config.mail.GuardedSMTPBackend`
  never sends to `EMAIL_BLOCKED_DOMAINS`). Tests never open SMTP (root conftest).
- "… was emailed" success messages go through `config.mail.email_sent_message()` (console mode: "Email printed in the
  terminal …"; the mode check `prints_emails()` is also System status's) (ASSUMPTIONS 342).
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
- Period presets (ASSUMPTIONS 399): This month, Q1–Q4, First / Second half, Whole year (+ a Year choice), Custom dates. Dates
  only from `services.preset_range(key, year)`; Alpine only looks up the server's table (`pages.date_presets`). Title and
  download name come from the dates (`content.period_name` / `report_title`, `services.download_name`; no column):
  "Accomplishment Report - Q3 2026", "Semi-annual … - January to June 2026", "Annual … - 2026", else "… - <range>".
- Participants by category per activity (ASSUMPTIONS 400): `metrics.participants_by_category` + `metrics.shares` (0.1 %,
  largest remainder, adds up to 100.0 %, None when the total is 0). Section `category_by_activity` (default in
  Accomplishment and Participation). The Overview card is a donut (ASSUMPTIONS 403): `activities.pages.category_donut`
  (attended, else registered + note; whole % via `shares(..., units=100)`; 0 = legend row without a slice; aria-label with
  the numbers) through `components/donut.html` + `reports.dashboard_page.donut_segments`, shared with the dashboard donut.
- One activity's evaluations (ASSUMPTIONS 401–402), made on request, never stored, `private, no-store`: the evaluation summary
  (`metrics.activity_evaluation_summary`, `services.evaluation_summary_file`, `reports:evaluation_summary`, PDF/Excel, any staff,
  text answers without names sorted A–Z, mean only: NO interpretation scale until the TBI gives one) and one participant's
  evaluation (`registrations.services.evaluation_answers` + `INDIVIDUAL_EVALUATION_PERMISSION` = can_create_activity,
  `services.individual_evaluation_file`, `registrations:evaluation_pdf`, PDF, no email; the dialog shows the answers only with
  that flag; the System Administrator gets 403). The summary is a `ReportContent` with `meta_lines` (the report PDF/Excel
  code). One participant (ASSUMPTIONS 404): `evaluation_answers` returns `ratings` / `average`
  (`metrics.participant_average`) / `answers` (non-empty, Comments last); the dialog top has no email or contact number
  (they and the registration answers are in "Show contact details and registration answers", closed when the evaluation
  shows); the PDF is its own PORTRAIT template (`reports.pdf.individual_evaluation_html`), the only portrait report PDF.
- `request_report` (can_create_activity) saves the row with an empty `file_path` and queues task `report-<id>`;
  `build_report` writes `reports/<year>/report-<id>-<type>.<ext>`. State is calculated (ready/failed/pending).
  Reports are never deleted; generating again = new row. Download only via `reports.views.download_report`.
- PDF via `certificates.pdf.get_renderer()` (fake in tests); Excel cells only through `excel.safe_cell`
  (text starting with = + - @ tab CR stays text).
- PDF layout (ASSUMPTIONS 385): landscape A4 for every report (except one participant's evaluation: portrait, 404); `table-layout: fixed` with widths from `reports.pdf.pdf_columns`
  (keep `PAGE_CONTENT_WIDTH_MM` in step with `@page` in `report_pdf.html`), never below 8 pt. Start + End print as one Date
  column (`Column.key`, `formatting.format_date_span`), the time as `compact_time_range` — PDF only, Excel keeps every column.
  After changing the report template or a column, run the opt-in real-WeasyPrint check `test_report_pdf_layout.py` (Commands).
- Excel layout (`reports/excel.py`, ASSUMPTIONS 338, all report types): "Report info" + short sheet names, a 5-row title
  block + blank row, real Excel tables (unique names without spaces, non-empty unique string headers, totals/notes
  BELOW the range, no merges inside), totals only from `Table.total` (= `period_summary`, never summed rows), date-times
  through `excel_datetime` (Manila, no tzinfo), money `"₱"#,##0.00`, print setup on every sheet. Layout only: never count here.
- AI narrative only through `reports/narrative.py`: Gemini gets figures, activity titles and dates only
  (`for_ai=False` columns removed), the exact prompt goes in `ai_prompt`, a failure never stops the report.
- "Describe the report you want" (ASSUMPTIONS 384): Gemini only READS the typed request (`reports/request_reader.py`, one
  tool) and returns form settings; `services.fill_report_form` checks each against `REPORT_TYPES` / `SECTIONS` /
  `GeneratedReport.Format` / active ACTIVITY_TYPE rows, dates only via `assistant.periods.parse_range` (Gemini's
  `period_words` only if they are in the request), notes are our sentences (quotes only from the request). Nothing saved,
  text never logged; same permission as generating (`services.GENERATE_REPORT_PERMISSION`), 30 per member per hour
  (`config.ratelimit`), hidden without the key. `POST /reports/fill/` swaps `#report-form` (`templates/reports/_form.html`).
  Page = type, check, generate: "What report do you need?" (Enter sends, Shift + Enter new line), `pages.EXAMPLE_REQUESTS`
  chips, a summary card after a fill, the full form (`_form_fields.html`, the ONLY named fields) under "More settings" (closed;
  open after a refused Generate; AI off = shown open, no prompt box). Alpine may only mirror values between the summary
  card and the full form (approved exception), plus the keyboard, chips and More settings; never a rule. Sections combine:
  our code adds "Activities conducted" / "Certificates" when the request mentions them (activity type names are the filter)
  and gives the fixed Conducted-only note for upcoming / planned / scheduled. Must fit 1366×768
  (opt-in `reports/tests/test_report_form_browser.py`).
- Template comments: `{# #}` on ONE line only (a multi-line one is printed on the page); otherwise `{% comment %}`
  (`config/tests/test_template_comments.py`).

## Frontend (Phase F1 onwards)
- Design (ASSUMPTIONS 413, after the UAT "too bright, spacing inconsistent, important buttons hard to find, no hierarchy"):
  the ORIGINAL identity (`docs/design/dashboard.png`, `login.png`, `logo.png`: navy -> blue gradient frame around a rounded
  light panel, frosted glass sign-in / public cards, pill buttons, Inter, the navy logo tile, the Dashboard's filled
  banner + "Next activity" card) made calmer. The teal / cream palette of 412 was rejected: never bring it back.
  Colours: **option B** (`docs/design/options/`), only between `OPTION START` / `OPTION END` in the `@theme` block of
  `frontend/tailwind/app.css`: frame `#1E2F73` -> `#3B8AA6`, primary `#2A4494`, link `#2C5AA8`, light text on the gradient
  `on-dark-muted` `#D3E8F0`, headline accent `on-dark-accent`. Everything else refers to them (sidebar, focus ring,
  shadows, `.app-bg`, `.auth-bg`, `.welcome-banner`, `.spotlight`, `.date-tile-gradient`). Panel `#F4F6F9` (neutral light
  grey) with white cards; Tailwind's own slate scale. Status tokens `info` / `warning` / `success` / `danger` / `neutral`
  (+ `-soft`, `-line`); use them, not Tailwind `amber-50/200`, `red-50/200`, `blue-*`, `green-*`. Report PDFs and the Excel
  header use `#2A4494`; logo tiles (`static/img/`) = `docs/design/logo.png` on `#1E2F73` (originals: `*-navy-old.png`).
  Chart colours (status bar, `reports.dashboard_page.CATEGORY_COLORS`) are checked for colour blindness (dE >= 15).
  Measure every new colour pair (text 4.5:1, large text / icons / input borders / accent bars 3:1);
  `accounts/tests/test_staff_layout.py` recomputes them from the tokens. Desktop-first staff pages (1280 px+, usable at
  1024 px; below that the sidebar hides behind a menu button). Public pages (F3) must also work on phones.
- Pages are server-rendered views that call the existing services; HTMX only for partial updates (an HTMX request
  gets a partial template, `accounts.permissions.is_htmx()`). Never repeat a business rule in a template or in JS.
- Staff pages extend `templates/layouts/app.html` and pass `page_title` AND `page_subtitle` (the page's own one-line
  description; never a copy of another page's); sign-in and password pages extend `layouts/auth_split.html` (design
  `docs/design/login.png`); error pages extend `layouts/auth.html`.
  Sign-in pages: one full-screen gradient (`.auth-bg`, the frame tokens) and a frosted glass card (`.auth-card`: white 88% +
  20px blur, white 92% without backdrop-filter; inputs with the `line-strong` border), dark text inside; white text only
  on the gradient's dark part; at 1366×768 and 1440×900 the login page fits without scrolling (ASSUMPTIONS 257).
  The university is Caraga State University. The illustration is inline (`partials/login_illustration.html`;
  `docs/design/login-illustration.svg` is the unchanged reference, its bright accents calmed); its CSS animations end with
  the final picture as the natural state and are all off under `prefers-reduced-motion` (classes `anim`, `rise`; ASSUMPTIONS 253).
- App shell (`layouts/app.html`, ASSUMPTIONS 258, 413): the sign-in gradient is the frame (`.app-bg`, 135°, dark to 30%, so
  the whole sidebar stays on the dark part); the sidebar sits on it (white text, active = white 18% pill + white left
  indicator, hover = white 10%, count badge white with primary text, member in a `.sidebar-glass` card); the content is a
  light panel (`bg-panel`, 28px corners, 16px from the edges) that scrolls on its own, with the top bar inside it (review
  label, search, Ask AI as a secondary button, bell; never the page title). Buttons are pill-shaped (`.app-shell .btn`).
  Never give a vertical scroll box inside the panel its own rounded corners (Chromium drew a square over the panel's
  corner, ASSUMPTIONS 412): round a wrapper, scroll a square list inside it (a test scans the templates).
  Cards: 24px corners (`.card`), KPI cards 20px (`.kpi-card`), soft navy shadow `shadow-card`, `.hover-lift` on hover.
  White text only on the dark frame and the Dashboard's gradient blocks; buttons on those blocks are `btn-on-dark`.
- Type scale (413): page title 24 px bold (`.page-title`, one h1), section / card heading 18 px semibold (`.card-title` =
  `.section-title`), body 14 px (`text-sm`), caption 12 px (`.caption` / `text-xs`). No `text-[11px]` / `[13px]` / `[15px]`
  on staff pages (test). Big numbers are data; sign-in / public hero headlines are display text; public and portal body 16 px.
- Spacing scale (413, enforced by `test_spacing_scale_in_every_template` / `_in_the_component_classes`): padding, margins,
  gaps and `space-*` only 4 · 8 · 12 · 16 · 24 · 32 px (steps 1 2 3 4 6 8; 48 / 64 px only as page gutters); never half
  steps (`-0.5`, `-1.5`, `-2.5`, `-3.5`), 20 / 28 / 36 / 40 px or `[..px]`. Page padding 16/24/32 px, card padding 24 px
  (KPI cards and list rows 16 px), 24 px between cards and below the page header, 16 px between repeated cards of one
  list and between KPI cards, 16 px card heading → content and between form fields, 8 px label → input, help text and
  between buttons. Exceptions: a field-input's icon clearance (`pl-10`, `pr-12`), `scroll-mt-*`, badge / tag inner 2 px,
  print / PDF templates, the Django admin.
- Page header (`layouts/app.html`): h1 + `page_subtitle`; `{% block page_actions %}` = the page's buttons (secondary first,
  the ONE primary last, rare / destructive ones in a "⋯" menu); `{% block page_header %}` replaces it (activity page,
  document page, New / Edit activity); `{% block page_description %}` (Dashboard: the date). A card never repeats the page title.
- Buttons (413): pills, 40 px (`btn-sm` 32 px), four levels: `btn-primary` ONE per page or card (the main action: page
  header top right, the bottom of a form, or a tab card's header); `btn-secondary` (outline) other actions and a list row's
  main action; `btn-ghost` (= `btn-tertiary`, `btn-icon` icon-only) minor actions; `btn-danger` = RED TEXT for Delete /
  Return / Remove / Deactivate, never filled, never the biggest button; solid red only to confirm (`.modal .btn-danger`, or
  `btn-danger-solid` on a confirm page). `btn-warning` is retired (a test forbids it). The activity page's Edit is
  secondary (each tab's main action is the primary). Page table: `docs/design/ui-revision/README.md` section 9.
- Needs attention / pending approvals stand out (413): `.has-accent` + `.accent-danger|warning|info` (4 px inset bar) on the
  Dashboard's Needs attention card (first after the filters; `DashboardPage.attention_tone`, its one primary =
  `attention_primary_index`, the first Review / Approve), the activity page's notice (+ count badge) and the approval KPI
  cards (`kpi_card.html` `alert=`) while something waits.
- Badges: tint + same-hue border; the five document states (Missing grey, Overdue red + icon, Waiting blue, Returned
  amber, Approved green) must stay distinguishable by colour (Missing stays grey, never red). Segmented controls
  `.segmented` / `.segmented-item` (+ `is-selected` or `aria-pressed`, pill); notices `.notice notice-warning|danger|info|success`.
  KPI cards: one structure (`components/kpi_card.html`: label row, number + badge on one baseline, optional note).
- Opt-in browser check after layout changes: `documents/tests/test_page_buttons_browser.py` (no overlapping or
  hidden buttons at 1366 / 1280 / 1024 / 390 px).
- `layout` (sidebar items, badges, unread count, Manila date) comes from `config.frontend`
  (one list of nav items, each with an optional `Permission`).
- Reuse the components in `templates/components/` (kpi_card, section_header + `.card`, badge, status_badge, button
  classes `btn btn-primary|secondary|ghost|danger`, empty_state, form_field, form_errors, pagination, toasts,
  progress_bar, date_tile, spinner, icon, modal (+ `remote=True` for HTMX-loaded content, `modal_header`), copy_button, choice_pills, options_editor, answer_field, file_drop,
  category_choice). Hover feedback: `.hover-lift` (cards), `.hover-row` (list rows), `.btn-arrow`
  (button whose arrow slides); movement only under `motion-safe` (ASSUMPTIONS 254). Icons only through `components/icon.html` (add Lucide 1.52.0 SVGs there).
  Every `<select>` is `field-input field-select` (one chevron, same right padding, fixed height shared with date inputs;
  never on `multiple`/`size` list boxes; a test scans the templates, ASSUMPTIONS 324). KPI cards are compact (325).
  Display filters: `{% load ui %}` (`config/ui_tags.py`: number, percent, rating, relative_time, ...).
- Everything is self-hosted (`static/vendor/VERSIONS.md`); never link a CDN (a test checks it).
- Tailwind v4: tokens in the `@theme` block of `frontend/tailwind/app.css` (no tailwind.config.js). A new app's
  `templates/` folder must be added there as an `@source`. Run `python manage.py tailwind build` after template
  changes and commit `static/css/app.css` (ASSUMPTIONS 235). Text contrast at least 4.5:1, input borders 3:1.
- CSRF for HTMX: `hx-headers` on the staff layout only (never in `base.html`: public pages must not print a
  per-request token, ASSUMPTIONS 247). HTMX without login: 401 + `HX-Redirect` (`staff_login_required`).
- HTML error pages: `templates/errors/403|404|500.html` (500 is rendered without the request). `/api/` stays JSON.
- Dashboard (`/`, `reports.views.dashboard_page` + `reports/dashboard_page.py`): numbers only from
  `reports.services.dashboard()` (= the API) and existing services (ASSUMPTIONS 237–244). The spotlight card is chosen
  only by `reports.services.dashboard_spotlight()` ("Happening now" = `activities.services.happening_now()`, else S2's
  next; the load runs `apply_automatic_statuses()` first; ASSUMPTIONS 351).
- Activities pages (F2a, ASSUMPTIONS 261–269): `activities/page_views.py` (thin) + `activities/pages.py` (display only:
  filters, month grid, tabs, registration summary). The registration link and the amber notice come only from
  `pages.registration_summary()` (= `registration_closed_reason()`). Modals are a native `<dialog>` (`components/modal.html`);
  also `components/copy_button.html` and `components/choice_pills.html` (multi-select as pills). Screens: `docs/design/screens/`.
- Forms / Registrations / Evaluation tabs (F2b, ASSUMPTIONS 270–277): `registrations/page_views.py` (actions) +
  `registrations/pages.py` (display only). Attendance controls follow `attendance_closed_reason()`, the links button
  `evaluation_links_closed_reason()` (the same functions the services use). A locked form shows no edit controls,
  not even move up/down (271). The attendance sheet never shows emails or contact numbers. Participant-facing fields:
  `components/answer_field.html` (F3 reuses it). A scroll box with sr-only text inside must be `relative`.
- Attendance and evaluation links UI (2026-11-06, ASSUMPTIONS 275–276, 347–349): Registrations tab = one "Confirmed by"
  choice (wet signature default) + row "Mark attended"/"Undo" (`row_action=mark:<id>`) + bulk buttons; before Ongoing only
  the `attendance_closed_reason()` text. Evaluation tab = `registrations/_evaluation_links.html` (explanation from the rules,
  counts, attendees who have not answered, resend). Activity page: `activities/_attention.html` below the title.
- Public pages (F3, ASSUMPTIONS 278–288, no login, phones first): `registrations/public_views.py` + `public_pages.py`
  (display/parsing only), `startups/page_views.py`, templates in `templates/public/` on `layouts/public.html` (`.public-card`
  glass without hover lift, `.public-shell`: pill buttons, 44 px targets). Plain forms with CSRF + `novalidate`, no JS needed.
  Every public registration POST (HTML and JSON) goes through `registrations.public.guard_registration_request()`.
  Evaluation links: `services.evaluation_state()` is the one rule; the review step uses `services.check_evaluation()`
  (also inside `submit_evaluation`). Refusal cards show the service's reason, never the name or email. Token pages use
  `config.headers.private_token_page` (Referrer-Policy same-origin, never no-referrer: it breaks CSRF on the forms; noindex, no-store) and pass `private_page`. Footer contact:
  `TBI_CONTACT_EMAIL` (placeholder).
- Documents and certificates pages (F4, ASSUMPTIONS 290–300): `documents/page_views.py` + `documents/pages.py`,
  `certificates/page_views.py` + `certificates/pages.py`. Buttons follow `documents.services.allowed_actions()`; the four
  states (Missing / Waiting for review / Returned / Approved) come only from `documents.pages`. Return / replace / delete use
  the shared `documents/_dialog.html` (form loaded with HTMX, its own page without JS). Certificate row actions: one service
  call, refusals listed from `CertificateError.problems` (kept in the session for the next view of the tab,
  `certificates.pages.keep_problems`; certificate actions always redirect). A documents upload error re-renders the activity
  page with `activities.page_views.render_activity_page(..., extra)` (the address bar is fixed with `history.replaceState`). The certificate preview and
  `documents:view` (the document preview) are the only inline files. New components: `file_drop`. (Since 2026-11-11 the
  certificate tab's Sign-off actions are replaced by the Director's approval: see "Certificates tab" below.)
- Documents page (ASSUMPTIONS 352): default "By activity" (`documents.services.overview_activities` +
  `activity_document_overview` = `document_checklist()`'s rule for many activities, parity test; `document_state_counts`
  for the summary cards), "All documents" = the flat list. Filters in the URL; no rules in `documents/_activity_card.html`.
- Certificates page (ASSUMPTIONS 372): default "By activity (recommended)" = `certificates.services.certificates_by_activity`
  (newest activity first by END date then end time, `NEWEST_ACTIVITY_FIRST`; 10 ACTIVITIES a page, never split; counts per
  group), "All certificates (newest first)" (`?view=all`) = `list_certificates_newest`; search `?q=` = `search_q` (number,
  names, evaluation email, activity title). The API keeps `list_certificates`. View only. Rows built once per page
  (`pages._rows`): the query count must not grow with the number of activities (tested).
- Certificates tab (ASSUMPTIONS 357–370, 373): the "Certificate approval" card (`pages.approval_panel`: `approval_state`'s
  states for everyone; the button only where the state offers approval; the primary "Approve certificates for this activity" + confirm dialog ONLY for
  can_approve_certificates, disabled with the reason when refused); "Apply the current design" for can_create_activity;
  Signatures (`pages.signatures_panel`); Certificate text (`pages.text_panel`, `certificates/_text_section.html`); the
  preview (`pages.preview_items`: exactly the list's certificates in the same order, printed name + "registered as", starts
  at the first row or `?preview=<id>`; no Approve button); the list (no checkboxes): Preview, Mark handed over, Resend,
  Retry, PDF, Evidence. S5 and the dashboard count ACTIVITIES that need certificate approval ("2 activities need
  certificate approval"); Needs attention: the signature reason or "Certificates need approval.". When approved work
  waits and qcluster is down, the tab says so (`pages.worker_notice`).
- Activity page links (ASSUMPTIONS 362): every tab link, pager and in-tab link starts with `activity_url` (never `?tab=`
  alone). POST-only actions under `/activities/<id>/…` use `config.links.post_only_or_tab(tab)` instead of `require_POST`
  (a GET goes to that tab); every action ends on `/activities/<id>/?tab=<tab>` (`config.links.activity_tab_url`).
- Document page Back link (ASSUMPTIONS 359): list links add `?next={{ request|here|urlencode:"" }}` (`config.links`), and
  `documents.pages.back_link` accepts only internal paths (`is_internal_path`).
- Document preview (ASSUMPTIONS 329–330): Preview in `documents/_actions.html` opens the shared `#document-preview` dialog (HTMX,
  `documents.page_views.preview`): PDF iframe / image / "not available" + Download, kind from `preview_kind()` (confirmed stored type,
  never the name). The file comes only from `documents:view` (Download's login rule, inline, nosniff, stored bytes checked again), one of
  the TWO `xframe_options_sameorigin` views (with `certificates:preview`, ASSUMPTIONS 358); everything else stays
  `X-Frame-Options: DENY`. No CSP is set; adding one must allow it.
- Reports, Assistant, Startups, Notifications and search pages (F5, ASSUMPTIONS 301–311): `reports/page_views.py` + `reports/pages.py`
  (form: quick dates, sections preset by type, the AI switch adds `ai_summary`; a pending history row polls `reports:row` every 4 s and
  stops when ready/failed or after 15 min), `assistant/page_views.py` + `assistant/pages.py`, `startups/staff_views.py` + `startups/pages.py`
  (the public set-password page stays in `startups/page_views.py`), `notifications/views.py` (`/notifications/`), `config/search.py`
  (`/search/`, the top bar's box: the lists' own services, 5 per group). **Model answers are untrusted:** render them only through
  `assistant.pages.answer_html()` (escapes everything FIRST, then only bold, italics, `#`–`###` as a bold line, bullet/numbered
  lists, `<br>` and our `[n]` links; ASSUMPTIONS 376); never `|safe`, raw HTML, links or images from the model. Widening the subset
  needs an owner decision and attack tests. A first question that fails removes its new chat (`services.discard_if_empty`).
- AI Assistant: "My chats" is 240 px and can be hidden (Alpine + localStorage `tbi.assistant.chats`, default open, ASSUMPTIONS 326);
  its header holds only the heading and the hide button, "New chat" below (379). Enter sends, Shift + Enter new line, Send disabled
  while empty/waiting (374); the chat follows new messages only while at the bottom, else "Jump to latest" (375). Only the keyboard
  and scrolling are Alpine (opt-in browser test `assistant/tests/test_chat_browser.py`). Each row's "⋯" menu opens the page's shared
  Rename / Delete dialogs (data attributes, never titles inside JS); both POST and end on the open chat (`current`) (377).
  The page fits the window from 1024×600 (`fit-screen:` / `short-screen:` variants in `frontend/tailwind/app.css`; overrides
  the `panel_scroll` and `main_class` blocks of `layouts/app.html`, which other pages leave at their defaults) (380).
- Startup portal pages (F6, ASSUMPTIONS 312–319): `startups/portal_views.py` + `startups/portal_pages.py`, `templates/portal/` on
  `layouts/portal.html` (sidebar `config.frontend.PORTAL`, context `portal`). Every page uses `startups.decorators.portal_page_required`
  (staff: 403; nobody: portal login). Login through `authenticate_startup`; `next` only within `/startups/portal/`. Registering only through
  `register_as_startup`; evaluation links only when `evaluation_state()` is open. Portal pages must work at phone width.
- Admin Console (F6, ASSUMPTIONS 320–322): `accounts/console_views.py` + `accounts/console_pages.py`, `templates/console/`, all
  `permission_required(MANAGE_USERS)`. Members through `accounts.services` (`list_members` for the list), designs through
  `certificates.services` (no delete URL, ever; the default signatories are shown read only, ASSUMPTIONS 363), System status through `accounts/system_status.py` (read only; never calls Gemini, never
  shows a secret; the PDF check goes through `get_renderer()`).
- Modals: always `<dialog class="modal">` (centred by `.modal`: fixed, inset 0, `margin: auto !important`, max 85dvh,
  scrolls inside; never give a dialog margins). Browser check, opt-in: `BROWSER_TESTS=1`, `config/tests/test_modals_browser.py`
  (ASSUMPTIONS 343).
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
- Required-document reminders go to the activity's creator as an in-app NOTIFICATION (a flagged Draft's Activity Design /
  Request Letter also to every active Director, ASSUMPTIONS 417). Never create the same reminder twice.
- Navigatu is accessed only through the `StartupProvider` class (mock for now).

## Working rules
- Business logic goes in each app's `services.py`. Keep views thin.
- API views return `config.json.JsonResponse` (never Django's directly), so date-times are shown
  in Manila time with the offset (`+08:00`), not UTC.
- The client IP comes only from `config.network.client_ip()` (`TRUSTED_PROXY_HEADER` behind a proxy).
- Request values meaning true/false go through `config.parsing.parse_bool()` (true/false only, else 400).
- Lookup lists for the frontend come from `/api/lookups/` (`config/lookups.py`). Master data and role
  flags are managed only in the Django admin (no Admin Console screens for them), except can_approve_documents and can_approve_certificates, which
  the Admin Console roles screen also switches (ASSUMPTIONS 344, 357).
- Numbers about evaluations (also one activity's, `/reports/api/activities/<id>/evaluation/`) come
  from `reports/metrics.py`.
- Write pytest-django tests for every business rule (`<app>/tests/test_*.py`).
- Tests run in parallel (pytest-xdist, `pytest.ini`): each worker has its own test database, `tmp_path` media
  folder and an empty in-memory cache per test (root `conftest.py`). Never share state between tests (module
  globals, files outside `tmp_path`, fixed ports); after a migration change run `pytest --create-db` once.
- Tests never depend on the real time of day: pass `now=` or use the `clock` fixture (root conftest); a deadline
  "later today" is the end of the day, never "now + N hours" (ASSUMPTIONS 289).
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
- Faster tests (2026-10-27): pytest-xdist, 2 workers, kept test databases, fresh cache per test (260).
- Frontend F2a (2026-10-27): Activities list + calendar, create/edit, activity page with Overview and History (261–269).
- Frontend F2b (2026-10-27): Forms, Registrations and Evaluation tabs, attendance sheet (270–277).
- Frontend F3 (2026-10-27): public pages: open activities, registration, evaluation with review step, startup set password (278–288).
- Tests and the time of day (2026-10-28): fake clock, walkthrough fix (289).
- Frontend F4 (2026-10-28): documents tab, repository + review queue, document page, certificates tab and page, links (290–300).
- Frontend F5 (2026-10-28): reports page with polling, AI assistant chat, startups pages, notifications page, global search (301–311).
- Frontend F6 (2026-10-28): startup portal pages and the Admin Console (members, roles, certificate design, system status) (312–323).
- UI polish (2026-10-29): shared select style, compact KPI cards, collapsible My chats panel (324–326).
- Documents at creation + preview (2026-10-30): optional documents on the New activity form, in-page document preview (327–330).
- System Administrator (2026-10-31): role renamed, no can_create_activity, never both flags, audit log, SYSTEM sidebar (339–340).
- Category + share (2026-10-31): preselected single category, Share card with QR codes, general evaluation link (331–337).
- Excel report layout (2026-10-31): title block, real Excel tables, formats, totals, print setup, Report info (338).
- Activity cards (2026-11-01): card dialogs with the card's activities, one "open for registration" rule, dev-mode email messages (77, 341–342).
- Document approval (2026-11-05): ROLE.can_approve_documents (Director only), self-approval, review notifications, roles screen switch (344).
- Times and automation (2026-11-06): start/end time, automatic status, needs attention, automatic evaluation links, attendance marker (345–350).
- Happening now + documents by activity (2026-11-06): dashboard spotlight, Documents page cards by activity (351–352).
- Grouped list + link requests (2026-11-08): grouped Activities list with follow-up actions, Back/Forward filter fix,
  evaluation link request log, HMAC rate-limit keys, two Needs attention items (353–356).
- Certificate approval (2026-11-11): Director-only approval per activity (can_approve_certificates), statuses Generated →
  Approved → Issued, in-system preview with signatures, regenerate, failed send + Resend, S5 by activity, document Back
  link (357–360).
- Approvers and signatures (2026-11-12): Director, both Project Heads and the System Administrator approve and add
  signatures on the Certificates tab (nullable signature image, PNG/JPG), large preview with Previous / Next, one Needs
  attention line, new-activity notification to the Director (361).
- Automatic certificates (2026-11-12): created by the System at Conducted (and attendance marked later, 5-minute
  catch-up), computed "ready for approval", Approve on the preview + "Approve all ready certificates", Apply the current
  design, ready-only counts, undo attendance deletes a waiting certificate, tab URL fixes, Issued preview = sent copy,
  `?v=` + no-store (362).
- Signatories per activity + reissue (2026-11-13): a list of signatories with free-text positions and versions, left/right
  per activity (ACTIVITY_SIGNATORY), defaults, Signatories page, impact dialog; "Issued with an old signature" and reissue
  (`reissued_at`), informational Needs attention line (363–364).
- Who signs + titles (2026-11-14): "Who signs the certificates" on the activity form, "Title on this certificate"
  (stored on each certificate), the system sticks to the defaults, white backgrounds removed with a preview, linked
  versions + "Use the current version", the real reason an Approved certificate has no PDF + Retry (365–368).
- Approval per activity + certificate text (2026-11-14): one approval per activity stored on ACTIVITY, automatic approval
  and email after each later evaluation, cancelled by any change to what is printed; editable wording (CERTIFICATE_TEXT,
  placeholders, default + per activity, live preview); invitation link and failed-invitation message (369–371).
- Certificates page by activity (2026-11-14): newest activity first, groups with the approval state and counts, "All
  certificates (newest first)", search box, no Activity column in a group, nothing cut off (372).
- Approval state (2026-11-15): "Needs approval" only while certificates wait, otherwise a grey "Not approved — new
  certificates will wait for approval"; counts unchanged; formula-like titles confirmed as plain text and Excel text (373).
  Then "All certificates issued (N)" and "no certificates yet" states without any approve button (373 note).
- AI Assistant chat (2026-11-15): Enter to send, auto-scroll + "Jump to latest", a safe Markdown subset in answers (replaces 305),
  delete and rename chats, titles generated in the background (Django-Q2), "My chats" heading never cut off (374–379). Then the page
  fits the window: no page scroll from 1024×600, only the messages and the chat list scroll (380).
- AI Assistant routing and files (2026-11-16): routing by our code, activities by period (Manila, Monday–Sunday weeks,
  drafts only when asked), find files, source file cards with Open / Download, works without the Gemini key for these,
  path log (381–383). No database or ERD change.
- Describe the report (2026-11-16): "Fill in the form" on Reports → New report from a typed request, Gemini reads only,
  every value checked, `parse_range` (quarters, halves, month ranges) in Manila, 30 per hour (384). Then the chat-like
  card: prompt box, example chips, summary card, "More settings"; multi-line `{# #}` fix + test. No database or ERD change.
- Report PDF layout (2026-11-16): landscape A4, fixed column widths, one Date column, compact time, nothing cut off; opt-in
  real-WeasyPrint layout test with demo data (385). No database or ERD change.
- Other documents (2026-11-16): Invitation Letter optional (data migration documents/0003, one DOCUMENT_TYPE row), "Other"
  type with a required title, seed_data create-only rules, "Upload other document" + grouped dropdown (386). Then the
  Accomplishment Report optional too (documents/0004) and document rows that never squeeze the title (387). No ERD change.
- Admin Staff rights (2026-11-16): create and edit activities and approve certificates (seed defaults, create-only),
  roles screen switch for can_create_activity, "View Only (test)" role in tests (388). No database or ERD change.
- Delete activity + My profile (2026-11-16): can_delete_activities (System Administrator), refused with real records,
  ACTIVITY_DELETION record, certificate number sequence; My profile (name, position, password), POSITION table (389–390).
  Database: accounts 0007–0008, activities 0008, certificates 0015–0016; 34 tables.
- Client review version (2026-11-16): Docker Compose production setup, written for one Azure VM but never used there (only
  the Nov 17 UAT used it, once, on the laptop through a Cloudflare quick tunnel) (Caddy HTTPS, gunicorn, qcluster, pgvector), release
  step, deploy checks, token-free logs, noindex/robots, "Review version" label, privacy notice + required consent checkbox,
  startup sync switch, create_first_admin / create_review_startup, Gelasio font, backups, docs/DEPLOY.md (391–398).
  No database or ERD change.
- Corrections after UAT, review round 1 (2026-11-17, NAVIGATU comments): quarterly / semi-annual / annual period presets with a
  Year choice and period titles and file names (399), participants by category per activity (400), evaluation summary per
  activity (401), one participant's evaluation for can_create_activity only (402). No database or ERD change, no migration.
- Display redesign after UAT (2026-11-18): participants by category as a donut on the Overview (403); one participant's
  evaluation with average, rating dots, answer cards, collapsed contact details, portrait PDF (404). No database change.
- Documents after UAT (2026-11-18, NAVIGATU): Activity Design and Request Letter required on the New activity form, needed
  before Scheduled, never overdue once uploaded (405); every upload waits for the Director, own uploads self-approved (406);
  Signatories rows no longer squeezed (407). Database: activities 0009 (`ACTIVITY.creation_documents_required`); 34 tables.
  Then (2026-11-19) Documents page buttons never cover the State badges, checked at 1366/1280/1024/800/390 px (408); new
  activities created as Scheduled, registration closed until the form is marked ready ("Open registration") (409).
  Database: also registrations 0008 (`FORM_TEMPLATE.ready_at`).
  Then their titles are required on the New activity form (`creation_title_error`; the Documents tab keeps
  the title optional) (410).
- UI revision after the UAT (2026-11-20, NAVIGATU tester: too bright, spacing, button hierarchy): same identity (gradient
  frame, floating panel, glass, pills) with a calmer gradient (#22378F → #5E86C0) and calmer tokens, softer badges, one
  spacing scale, four button levels, a page header with each page's own description, one KPI card structure; fixes: input
  border contrast, Members row, Roles switches and headers, Documents columns, 390 px overflow (411). A flat redesign was
  tried and rejected by the owner the same day; then the frame got darker (#1F2F6E → #41598C), the dashboard blocks became
  white cards, only primary buttons stay solid, and the Dashboard / Overview grids were rebalanced. No database, URL or permission change.
- Colour palette after UAT (2026-11-20, owner; defense Dec 9; REPLACED by 413 on 2026-11-21): Teal Waters / Glacial Sky / Morning Mist / Spring Meadow replace
  the blue (412): tokens in `@theme`, gradient #204654 → #2F6170, a separate link colour, softer badges (Missing stays grey),
  chart colours checked for colour blindness, logo tiles re-exported on Teal Waters, report PDF / Excel in Teal Waters, the
  panel-corner square on the activity page fixed. Same layout; no database, URL or permission change.
- Back to the original design, calmer (2026-11-21, owner after the UAT): the teal / cream palette (412) removed; the
  original gradient frame, filled dashboard blocks and navy logo in option B (`#1E2F73` -> `#3B8AA6`, primary `#2A4494`),
  neutral grey panel, softer status colours, type scale, red-text destructive buttons, one primary per page or card,
  Needs attention / pending approvals with an accent bar (413, Part 1). Part 2: every template on the spacing scale
  (650 off-scale uses in 146 files) + template scan tests. No database change.
- Privacy retention (2026-11-21, team decision): data "kept on the researchers' computer until you ask for it to be
  deleted" replaces "deleted no later than November 30"; notice version November 21, 2026; docs no longer say the review
  version ran on Azure (414). Text only.
- No deployment (2026-11-21, team): all further testing and the defense demo run only on the researchers' laptop; no
  Azure, no Cloudflare tunnel, no public address; DEPLOY.md's guides labelled "not used" (415). Docs only.
- Activity Design 5-day rule (2026-11-21, NAVIGATU): due 5 days before the start (was 10), like the Request Letter; seed -5
  (create-only), guarded data migration documents/0005 (only start_date / -10 -> -5, reversible), demo workshop starts in
  4 days so an overdue Activity Design still shows (416). No schema or ERD change.
- Create as Draft / Scheduled (2026-11-21, NAVIGATU UAT): the New activity form's "Create as" (default Draft), Activity Design
  and Request Letter optional (Scheduled needs both, through `change_status` in the same transaction), refusal under the
  field, typed values kept and files to re-choose named; Draft reminders for those two types to the creator and the
  Director; dashboard numbers unchanged (417, amends 405 and 409). No database, ERD or migration change.
- Signatories by the Director only (2026-11-21, NAVIGATU UAT): new ROLE flag can_manage_signatories (Director only; roles
  screen switch, audit line, amber note; never with can_manage_users) for the signatory list, signature images, defaults,
  the Signatories page and "Add a person"; choosing an activity's signatories and approving unchanged (418).
  Database: accounts 0009 (column) + 0010 (guarded data step); ERD: ROLE.can_manage_signatories; still 34 tables.

## Client review version: deployment files (ASSUMPTIONS 391–398, 415, `docs/DEPLOY.md`; NOT USED)
- NO deployment (team decision 2026-11-21, ASSUMPTIONS 415): all further testing and the defense demo run ONLY on the
  researchers' laptop; no Azure, no Cloudflare tunnel, no public address. The Nov 17 UAT ran once on the laptop (Docker
  project `csu_tbi_uat`, `deploy/docker-compose.laptop.yml`, `Caddyfile.laptop`) behind a Cloudflare quick tunnel; its
  database and backups stay on the laptop (privacy notice, 414). The Azure VM guide (Azure for Students, B2ats v2, ~1 GB)
  and the tunnel guide stay in DEPLOY.md as optional sections labelled "not used". No Railway. The files below still
  exist and are tested; keep them working, but never suggest deploying.
- `Dockerfile` (requirements.txt only; Pango, DejaVu, Gelasio from `deploy/fonts/gelasio/`, tzdata; user `app`) and
  `deploy/docker-compose.prod.yml` (db never published, release → web (gunicorn) + worker (qcluster), caddy = HTTPS + /static/).
  Always run compose through `deploy/compose.sh` (server `.env` at `/opt/csu-tbi/.env`, root only). Scripts in `deploy/`
  (committed, LF line endings via `.gitattributes`; run them with `sh`), never `tools/`.
- Server-only settings come from env: `BEHIND_HTTPS_PROXY` (proxy SSL header + short HSTS), `CSRF_TRUSTED_ORIGINS` from an https
  SITE_URL, `STARTUP_SYNC_ENABLED` (False on the server; `DailySchedule.enabled_setting`), `REVIEW_VERSION_LABEL`
  (`config.frontend.review_label`), `Q_CLUSTER_WORKERS`. `config/checks.py` runs in `check --deploy` (release step).
- Every response has `X-Robots-Tag: noindex` (`config.headers.NoIndexMiddleware`); `/robots.txt`, `/healthz/` in `config/views.py`.
- Logs never contain link tokens: `config.logfilters.HideTokensFilter` on the console handler. No access logs (Caddy, gunicorn).
- Every email carries `SITE_URL` (also certificate and password-changed emails; tested in `accounts/tests/test_review_clean_seed.py`).
- Clean database: `seed_data` only, `create_first_admin` (interactive, once), `create_review_startup` (one TEST startup).

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
pip install -r requirements-dev.txt       # development: also pytest, pytest-django, pytest-xdist and Playwright
python -m playwright install chromium     # once, development only: the browser for page screenshots
python manage.py tailwind install         # once: the pinned Tailwind CLI into tools/ (checked by SHA-256)
python manage.py tailwind build           # static/css/app.css (after template changes; commit the result)
python manage.py tailwind watch           # rebuild on every template change while working (Ctrl+C stops)
docker compose up -d
python manage.py migrate
python manage.py createcachetable   # once: table for login lockout counters
python manage.py seed_data      # lookups + Admin account (SEED_ADMIN_* in .env); safe to rerun
python manage.py seed_demo      # DEMO ONLY, fake data, needs DEBUG=True; safe to rerun; one account per role
python manage.py changepassword director@demo.example.com   # give a demo account a password (typed, never stored)
python manage.py setup_schedules          # once (safe to rerun): 7:00 sync, 7:30 indexing, 8:00 reminders, 8:30 certificate retry, every 5 min statuses + evaluation links
python manage.py send_document_reminders  # run the reminders once by hand (testing)
python manage.py sync_startups            # sync startups from the provider now (--force: override the guard)
python manage.py index_documents          # index approved documents not indexed yet (--document <id>: redo one)
python manage.py check_gemini             # models in use (and where from), models the key can use, tiny real calls
python manage.py check_pdf                # WeasyPrint renders a test PDF in memory: OK or the exact error
python manage.py send_test_email you@example.com [--smtp]   # one test email (--smtp: really send it, even in DEBUG)
python manage.py create_first_admin       # SERVER: the first System Administrator of a clean database (asks; once only)
python manage.py create_review_startup --email you@example.com   # SERVER: one TEST startup, set-password link emailed
python manage.py retry_certificates       # render/issue ready certificates and send unsent emails now (the 8:30 retry)
python manage.py make_sample_certificate_images <folder>    # DEMO: invented background + 2 signatures (PNG)
python manage.py certificate_walkthrough <step> ...         # DEMO ONLY (DEBUG): certificate flow step by step (approve = as the Director)
python manage.py show_dashboard [--start --end --activity-type]   # dashboard numbers as JSON (same as the API)
python manage.py generate_report --type accomplishment --format pdf --start 2026-01-01 --end 2026-06-30   # build now (--report <id>: a pending one)
python manage.py qcluster       # background worker (separate terminal; works on Windows; Ctrl+C stops)
pytest                                    # all tests, 2 workers in parallel (pytest.ini) on the kept test databases (--reuse-db)
pytest activities                         # one app (or a file: pytest activities/tests/test_services.py)
pytest -n 0 -x activities/tests/test_services.py   # one process, stop at the first failure (debugging, pdb works)
$env:BROWSER_TESTS = "1"; pytest -n 0 -s config/tests/test_modals_browser.py; Remove-Item Env:BROWSER_TESTS   # modals in Chromium
$env:BROWSER_TESTS = "1"; pytest -n 0 -s documents/tests/test_document_rows_browser.py; Remove-Item Env:BROWSER_TESTS   # document rows: titles wrap by words, badges and buttons never overlap
$env:BROWSER_TESTS = "1"; pytest -n 0 -s certificates/tests/test_signatories_browser.py; Remove-Item Env:BROWSER_TESTS   # Signatories page: names never squeezed
$env:PDF_LAYOUT_TESTS = "1"; pytest -n 0 -s reports/tests/test_report_pdf_layout.py; Remove-Item Env:PDF_LAYOUT_TESTS   # report PDFs (+ evaluation summary, one evaluation) with real WeasyPrint: nothing cut off (PDF_LAYOUT_OUT=<folder>: PDFs + PNGs)
pytest --create-db                        # rebuild the test databases: once after adding or changing migrations
$env:TEST_FAKE_NOW = "2026-10-28T23:30"; pytest; Remove-Item Env:TEST_FAKE_NOW   # whole suite at a chosen Manila time
```
