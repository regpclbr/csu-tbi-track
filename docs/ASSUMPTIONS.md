# Assumptions (to be validated with the TBI)

## Business rules

1. Registration has no approval step. Everyone who registers is registered.
2. When all slots are taken, registration closes. There is no waiting list.
3. Walk-ins are not allowed. Only registered people can be marked present or evaluate. *(Replaced 2026-11-24 by item 434: walk-ins register at the QR check-in and are checked in at once.)*
4. Evaluation is tied to the person (not anonymous). It opens after the activity ends and only for people marked present.
5. Certificates are emailed automatically after the certificate is approved and the evaluation is submitted. *(Built in Step 9: items 174–194. The name printed and the address come from the evaluation, item 175.)*
6. Participants get a Certificate of Participation by email. Resource persons get a Certificate of Appreciation in hard copy. *(Step 9: items 180, 186.)*
7. One certificate design is used for all activities. Admin manages it.
8. Documents that are uploaded must be reviewed and approved before they count as approved (For Review, then Approved).
9. Required documents for every activity: invitation letter, activity design, request letter, attendance sheet, accomplishment report. Due dates are placeholders until the TBI confirms them. *(2026-11-16, item 386: the invitation letter and the accomplishment report are optional now; activity design, request letter and attendance sheet stay required.)* *(2026-11-21, item 416: NAVIGATU's 5-day rule: the activity design is due 5 days before the start date (was 10), like the request letter; the attendance sheet's due date is still a placeholder.)*
10. Missing-document reminders go to the person who created the activity, inside the app.
11. Navigatu startup data is read-only and reached through one mock provider for now. Startups are identified by email. *(Refined in Step 7, item 135: matched by Navigatu ID first, then by email.)*
12. Any TBI staff member can see every startup.
13. The admins are the researchers.
14. Certificate numbering format is decided by the researchers. *(Decided in Step 9: `TBI-<Manila year>-<5-digit sequence>`, item 176.)*

## Technical (Step 0)

15. The tech-stack document is `docs/Tech Stack for TBI Activity Tracker.docx` (added after Step 0; read in the Step 4 review). It matches `CLAUDE.md`: PostgreSQL + pgvector (documents, form definitions **as JSON**, and embeddings in one database), Django, RAG in Python, Gemini Flash and the Gemini embedding model, Django Templates + HTMX + Alpine.js (React optional, only for the AI Assistant), local file storage, WeasyPrint and openpyxl, Django email with Django-Q2 on the database, and local login now with Navigatu SSO later. *(Updated in the Step 4 review; it changes nothing in the plan.)*
16. Python 3.12 is used (3.14 is also installed, but some libraries may not have wheels for it yet).
17. Django 6.1.2 (latest stable at setup time) and the `pgvector/pgvector:pg17` Docker image (PostgreSQL 17).
18. Time zone is `Asia/Manila`. All datetimes are stored in UTC and shown in Manila time.
19. During development, emails are printed to the console. Real SMTP is configured in Step 9. *(Done: item 187.)*
20. Default Gemini model names are `gemini-3.5-flash-lite` (answers) and `gemini-embedding-001` (embeddings, requested at 768 dimensions). Both can be changed in `.env` (`GEMINI_CHAT_MODEL`, `GEMINI_EMBEDDING_MODEL`) without code changes. *(Changed on 2026-10-20: the original `gemini-2.5-flash` returns HTTP 404 "no longer available to new users" for this project's key, although it is still listed. `python manage.py check_gemini` showed `gemini-3.5-flash-lite` answering in seconds, with and without the tool declarations; Google's other recommendation, `gemini-3.8-flash`, also worked but took minutes per reply and hit the 60-second deadline (HTTP 504) on a second run. `gemini-embedding-001` is kept: every stored embedding was made with it, and Google lists its earliest shutdown as May 2028. Changing the embedding model would require re-indexing every document.)*
21. The pgvector extension is enabled by `assistant/migrations/0001_enable_pgvector.py` (moved there from `accounts` in Step 1, see 29).
22. The Docker database listens on host port **5433** (not 5432), because another PostgreSQL already uses 5432 on the development machine. *(Backend review, 2026-10-23: it is published on `127.0.0.1` only, so other computers on the network cannot connect to it. Django must then use `POSTGRES_HOST=127.0.0.1`, not `localhost`: on Windows "localhost" tries IPv6 `::1` first, and every connection waited about 30 seconds.)*

## Models (Step 1) — decisions confirmed by the owner

23. TBI_MEMBER and STARTUP both use a hashed `password` field (Django's hashing). This **replaces the ERD's `password_hash` name**. On STARTUP it is nullable.
24. TBI_MEMBER also has Django's `last_login` and an `is_superuser` column. `is_staff` is **not** a column: it is a read-only property that is True only when the member's role has `can_manage_users`, so only Admin can open the Django admin site. Django's groups/permissions tables are not used (no PermissionsMixin); admin access is decided by the role.
25. ROLE, ACTIVITY_STATUS, PARTICIPANT_CATEGORY and DOCUMENT_STATUS have **no** `is_active` (no new columns). ROLE, ACTIVITY_STATUS and DOCUMENT_STATUS are never deleted (PROTECT) and the app depends on their fixed names. PARTICIPANT_CATEGORY is master data the Admin manages (see 84): the three seeded categories are never renamed or deleted; an Admin-added category can be renamed, and deleted only while no activity uses it. ACTIVITY_TYPE and DOCUMENT_TYPE keep `is_active`. *(Updated in the Step 4 review.)*
26. DOCUMENT.related_certificate is a nullable FK to CERTIFICATE (PROTECT). Documents with it set belong to the Certificates tab and are excluded from the document checklist and the Document Repository: use `Document.objects.activity_documents()` there, and `Document.objects.certificate_documents()` in the Certificates tab. *(Step 9, owner decision: it holds optional evidence such as a signed hard copy or a receipt, item 189.)*

## Models (Step 1) — decisions made while building (reviewed by the owner)

27. Primary keys use the ERD names (`member_id`, `activity_id`, ...), and every table uses the ERD name in lower case (`tbi_member`, `activity`, ...). Keys are `bigint` (Django's default) instead of the ERD's `int`.
28. ACTIVITY_RESOURCE_PERSON has `activity_resource_person_id` as its key (the current ERD shows it; the first version had no key), plus the unique (activity, resource_person) constraint.
29. The pgvector migration lives in `assistant`, not `accounts`. Django's admin/auth migrations depend on the **first** migration of the user model's app, so `accounts/0001` must create TBI_MEMBER.
30. App placement: NOTIFICATION lives in its own `notifications` app (see 42). DOCUMENT_EMBEDDING lives in `assistant`.
31. ERD `string` columns that can hold long text (`options` on form fields, `answer_value`, `chunk_text`) are stored as unlimited text. Other strings get set lengths (names/titles 255, contact numbers 30, emails 254). Optional strings are stored as an empty string, not NULL, **except where an empty value has its own meaning or must stay out of a unique rule:** ACTIVITY.description (item 71), REGISTRATION.confirmation_method (empty until attendance is confirmed, item 36), DOCUMENT_TYPE.due_reference and due_offset_days (only required types have them, item 34), STARTUP.navigatu_id (unique, so rows without one must be NULL) and STARTUP.maturity_stage (Navigatu may not send it), and the nullable date-time and file columns of the ERD. *(Corrected in the backend review; this item used to say "never NULL".)*
32. `registration.email` is unique per activity **ignoring upper/lower case**.
33. CERTIFICATE nullability: see 41.
34. Extra safety constraints not in the brief:
    - a NOTIFICATION has exactly one recipient (member or startup);
    - a required DOCUMENT_TYPE must have `due_reference` and `due_offset_days`;
    - a CERTIFICATE's filled recipient column matches `recipient_type`;
    - ACTIVITY `end_date >= start_date` and `budget_amount >= 0`;
    - one DOCUMENT_EMBEDDING per (document, chunk_index);
    - counts and positions (`max_slots`, `sort_order`, `file_size_kb`, `chunk_index`) cannot be negative.
35. Delete rules: **PROTECT** for lookups, members, signatories, templates and official records (activities, registrations with certificates, documents, certificates; a document cited by the assistant). **CASCADE** only for rows that belong to one parent (resource-person links, form templates and fields, a registration's evaluation and answers, notifications, embeddings, chat messages). REGISTRATION.startup is SET_NULL.
36. `registration.confirmation_method` is empty until attendance is confirmed.
37. `manage.py createsuperuser` gives the new account the Admin role *(since 2026-10-31: "System Administrator", item 339)*, so the roles must be loaded first (Step 2 seed command).
38. In the Django admin, lookups, signatories and templates have no Delete button, and the activity audit log is read-only.
39. Lookup tables (ROLE, ACTIVITY_STATUS, ACTIVITY_TYPE, PARTICIPANT_CATEGORY, DOCUMENT_STATUS, DOCUMENT_TYPE) have **unique names**, so the seed command and the code can look rows up by name. *(Confirmed by the owner.)*

## Step 1 review — decisions confirmed by the owner

40. **One evaluation per registration.** EVALUATION_SUBMISSION.registration is a one-to-one field (the ERD will be updated from one-to-many to one-to-one). DOCUMENT.reviewed_by is a nullable FK to TBI_MEMBER (the ERD type will be corrected from `string` to `int`).
41. **CERTIFICATE columns.** A certificate row is only created when the certificate is generated, so `certificate_template` and `certificate_number` (unique) are **required**. `file_path` is nullable because the PDF is rendered later by a background job. `reviewed_at`, `director_signed_at`, `vp_signed_at`, `issued_at` and `sent_at` are nullable. `status` is required. `vp_signatory` and `director_signatory` are required. *(2026-11-13, item 363: `vp_signatory` / `director_signatory` are replaced by `left_signatory` / `right_signatory`; new `reissued_at`, item 364.)*
42. **Certificate status** has four stored values: `generated` (Generated, the default), `pending_signoff` (Pending Sign-off), `approved` (Approved), `issued` (Issued). *(Replaced on 2026-11-11 by item 357: Generated → Approved by the Director → Issued; no Pending Sign-off.)* The rules are enforced in `certificates.services.set_certificate_status()`, not in the database:
    - Approved requires `reviewed_at`, `director_signed_at` and `vp_signed_at`.
    - Issued requires the certificate to be Approved and, for participants (`recipient_type = registration`), a submitted evaluation. Resource persons (hard copy) do not need an evaluation.
    - Issuing sets `issued_at`, and links the participant's evaluation to the certificate if it was not linked yet.
    - *(Step 9)* Moving to Pending Sign-off sets `reviewed_by` and `reviewed_at` (item 181).
    - Which moves are allowed at all: see 46.
43. **File path columns are 255 characters** (`max_length=255` on every FileField/ImageField). Django's default of 100 was too short for long upload names.
44. **NOTIFICATION is in its own `notifications` app** (moved out of `documents`).
45. **Activities are never deleted.** ACTIVITY_AUDIT_LOG.activity is PROTECT, so an activity with history cannot be deleted. Cancelled or wrong activities should be handled through status changes, not deletion. (Other links to ACTIVITY that are still CASCADE — resource-person links, form templates, notifications — never fire because activities are not deleted.)

## Certificate status moves — pending validation by the TBI

46. **Allowed certificate status moves** (enforced in `set_certificate_status()`, *to be validated with the TBI*): *(Replaced on 2026-11-11 by item 357: Generated → Approved by the Director → Issued; no Pending Sign-off.)*
    - Forward, one step at a time: Generated → Pending Sign-off → Approved → Issued. Skipping a step (for example Generated → Approved) is refused.
    - One backward move: Pending Sign-off → Generated, used when the reviewer **rejects** the certificate. It clears `reviewed_at`, `director_signed_at` and `vp_signed_at` (`reviewed_by` is kept).
    - **Issued is final.** Nothing can leave it. A wrong certificate is replaced by a new certificate with a new number.
    - Moving to the same status is refused.
47. Activities cannot be deleted from the Django admin either (no Delete button, no bulk delete action). The other CASCADE links to ACTIVITY stay as they are.

## Seed data (Step 2)

48. `python manage.py seed_data` loads the lookups listed in the brief. Document type names are stored in Title Case: Invitation Letter, Activity Design, Request Letter, Attendance Sheet, Accomplishment Report, Pre-registration Form, Evaluation Form, Certificate. The code relies on these exact names. *(2026-11-16, item 386: also "Other"; whether a type is required and its due rule are set only when seed_data creates the type.)*
49. Running `seed_data` again never duplicates rows. It **resets only the fields it defines**:
    - roles: `can_create_activity`, `can_manage_users`;
    - document types: `is_required`, `due_reference`, `due_offset_days`.
    Descriptions and `is_active` are only set when a row is first created, so an admin's edits survive. Statuses, categories and activity types are only created, never changed.
50. The first Admin account *(System Administrator role since 2026-10-31, item 339)* comes from `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD` and `SEED_ADMIN_NAME` (optional, default "TBI Admin") in `.env`.
    - If email or password is missing, the account is skipped with a warning (lookups are still loaded).
    - The password must pass Django's password validators, otherwise the command stops.
    - The account is created with the Admin role, `is_superuser = True` and `must_change_password = False`.
    - On later runs the password and `must_change_password` are **never** reset; only the Admin role is re-applied if it was changed.
51. Both seed commands live in the `accounts` app (`accounts/management/commands/`); the lookup lists are in `accounts/seed.py`.
52. `python manage.py seed_demo` is **demo-only**:
    - It refuses to run when `DEBUG` is False.
    - All people and companies are invented; every email ends in `@demo.example.com`. Demo staff accounts (one per non-Admin role) have unusable passwords and cannot log in. *(2026-10-31: one per role, System Administrator included, item 339.)*
    - Activity dates are relative to the day it is run (three Conducted in the past, one Ongoing today, three Scheduled, one Draft). "Startup Pitch Competition" uses the Forum type because there is no Competition type.
    - "Investor Networking" is deliberately full (10/10) to show a closed registration. Some required documents are deliberately missing to show checklist gaps.
    - Documents are small placeholder `.txt` files saved under `media/demo/`.
    - Safe to run twice: activities that already exist (by title) are skipped.

## Staff login and permissions (Step 3)

53. **Login messages.** A wrong password, an unknown email and a deactivated account all get the same message: "Invalid email or password." Email is matched ignoring upper/lower case. A member who is deactivated while logged in loses access on their next request.
54. **Brute-force lockout.**
    - 5 failed logins for one email within 15 minutes lock that email for 15 minutes. 20 failed logins from one IP address lock that IP for 15 minutes (this catches someone guessing many emails).
    - While locked, even the correct password is refused. A successful login resets that email's counter.
    - Each lockout is logged as a WARNING on the `accounts.security` logger, with the email and IP (never the password).
    - The lockout message is the same whether or not the email exists.
    - Counters are kept in Django's database cache (table `django_cache`, created once with `python manage.py createcachetable`), so all server processes share them.
    - The IP is `REMOTE_ADDR`, or the header named in `TRUSTED_PROXY_HEADER` behind a reverse proxy (item 215). One function, `config.network.client_ip()`, is used by the lockout and by the public registration limit.
    - Limits are settings: `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_MAX_FAILED_ATTEMPTS_PER_IP`, `LOGIN_LOCKOUT_MINUTES`.
55. The Django admin's own login page (`/admin/login/`) redirects to the staff login, so the lockout also protects the admin site.
56. Logout only accepts POST (a link on another site cannot log a member out).
57. **First-sign-in password change.** While `must_change_password` is true, every URL — including the Django admin — redirects to the change-password page; the JSON API answers 403 `{"error": "Change your password first (first sign-in)."}` instead (item 225). Only the change-password page, logout and static files (CSS/JS) are reachable; uploaded files are not. The new password must pass Django's password validators and must differ from the current one.
58. **Permissions** are written once in `accounts/permissions.py`: `has_permission()`, `require_permission()` (raises a 403), and the view decorators `staff_login_required` and `permission_required(Permission.X)`. Someone not logged in is sent to the login page (the JSON API answers 401 `{"error": "Login required."}`, item 225); a logged-in member without the permission gets a 403 with the reason (JSON `{"error": ...}` for `/api/` URLs, plain text otherwise).
59. **Admin guardrails.** "Admin" means an active member whose role has `can_manage_users` (not the role's name; the seeded role is "System Administrator" since 2026-10-31, item 339).
    - An Admin cannot deactivate their own account.
    - The last active Admin can be neither deactivated nor moved to a role without `can_manage_users` (the role-change check is how "at least one active Admin" is kept when only one Admin exists).
    - The check locks the Admin rows, so two Admins cannot remove each other at the same moment.
    - An Admin may change their own role if another active Admin exists.
    - Members are never deleted (no delete in the API or the Django admin).
60. **Temporary passwords** are 16 random characters (Python `secrets`). They are hashed before saving, appear only in the invitation email, are never logged, and are never returned by the API. During development the console email backend prints the email to the terminal (standard output, not the log). Since Step 9 every email goes through the project's mailer (`settings.MAILERS`, item 187); the Admin password reset (item 226) sends a new temporary password the same way.
61. **Django admin member screen** uses the same services: adding a member asks only for email, name and role and sends the invitation; `is_active` and `must_change_password` are read-only; members are (de)activated with the "Deactivate/Reactivate selected members" actions; demoting the last Admin is refused. *(Backend review:)* an Admin can reset another member's password with the "Reset password" action (item 226).
62. **Startups are never staff.** The staff login only checks TBI_MEMBER. A startup can never be `request.user`; the startup login (Step 7) will keep its id under the separate session key `startups.services.STARTUP_SESSION_KEY`. Staff views only admit an active TBI_MEMBER. *(Built in Step 7: items 140–141.)*
63. The login, change-password and landing (`/`) pages are temporary plain forms until the frontend phase. The Admin Console is a small JSON API under `/accounts/api/`.
64. Tests use a fast password hasher (MD5) so the suite runs in seconds. The real server always uses Django's default PBKDF2 hasher.
65. **Closed in Step 6 (see 120).** Was an open item: in development (`DEBUG=True`) uploaded files under `/media/` are served without a login check. Step 6 must serve documents through a view that checks permissions.

## Activities (Step 4)

66. **Activity status: manual moves plus two automatic ones.** *(Rewritten 2026-11-06, owner decision; pending validation by the TBI.)* Allowed moves: Draft → Scheduled, Scheduled → Ongoing, Ongoing → Conducted, and Scheduled → Draft (refused if anyone has registered). Conducted is final. Staff still make every move by hand, including Ongoing → Conducted without any attendance. The system also makes two moves by itself (item 346): **Scheduled → Ongoing when the start arrives**, and **Ongoing → Conducted when the end has passed and at least one attendance is recorded**. Draft is never touched. Because Scheduled → Ongoing happens at the start time, **registration (which needs Scheduled) now also closes when the activity starts**, at the latest within the 5-minute job or on the next page load.
67. Any member whose role has `can_create_activity` may edit **any** activity, not only the ones they created. *(Pending validation by the TBI.)*
68. **Date rules** compare the deadline's date in Manila time: a registration deadline *on* the start date (for example 7:00 AM) is allowed, and an evaluation deadline *on* the end date is allowed.
69. The "activity type must be active" rule is checked only when the type is set or changed. An activity whose type is deactivated later can still be edited.
70. Only Conducted activities are read-only. Draft, Scheduled and Ongoing activities can be edited, and every validation rule still applies.
71. An empty description is stored as no description (NULL).
72. **Audit log rows.**
    - `field_changed` uses readable labels: Title, Description, Activity Type, Participant Categories, Start Date, End Date, Start Time, End Time *(since 2026-11-06, item 345; older rows keep "Time Schedule")*, Venue, Max Slots, Registration Deadline, Evaluation Deadline, Budget Amount, Status, Resource Person Added, Resource Person Removed.
    - Values are formatted by one function, `activities.formatting.format_audit_value()`: dates "Oct 5, 2026"; date-times "Oct 15, 2026 5:00 PM" (Manila time); money "₱30,000.00"; lookups and resource persons by name; empty values "". The description is stored in full.
    - Creating an activity writes one row (Status: "" → "Draft"). A resource person row stores the name (in `new_value` when added, in `old_value` when removed).
73. **The audit log is append-only**, enforced in the model: saving an existing row, deleting a row, and bulk update/delete all raise `AuditLogIsAppendOnly`. There is no service, endpoint or admin screen that edits or deletes it. Its default order is newest first (migration `activities/0002`, options only; no data change).
74. An edit and its audit rows are saved in one database transaction. The activity row is locked while it is edited, so two people editing at once cannot overwrite each other's "old value".
75. **Resource persons.** A link's topic cannot be edited: remove the person and add them again (each step is audited). `create_resource_person` refuses an email that already exists (ignoring upper/lower case). People without an email are not checked for duplicates. Removing a link never deletes the person.
76. **Registration open/closed.** Registration is still open at the exact deadline time. `registered_count` counts every registration (there is no cancellation yet).
77. **Activity list cards.** "Conducted this month" means status Conducted with an end date in the current month (Manila time). "Open for registration" means open on the public page: Scheduled, deadline not passed, slots left **and a registration form exists** (the `registration_closed_reason()` / `open_activities()` rule). One query, `activities.services.open_for_registration()`, is used by the Activities card and its preset, the dashboard KPI, `/register/` and the startup portal, so they always agree; a Scheduled activity without a registration form is counted nowhere (changed 2026-11-01, owner decision; before, the Activities card ignored the form).
78. **List and calendar.** List filters use IDs (activity type, status, category; an activity matches the category filter if the category is one of its categories). The date range keeps activities that overlap it. Sort options: title, start_date, registered, created_at (prefix `-` for descending; the default is newest start date first). Pages hold 20 rows. The calendar shows every status, including Draft (staff only). *(2026-11-08: the page defaults to "Grouped (recommended)"; the API keeps `-start_date`; ties follow the sort's direction, item 353.)*
79. **JSON API** (`/activities/api/`): dates are `YYYY-MM-DD`; date-times are ISO 8601. In requests, a date-time without an offset (`2026-11-13T17:00`) is read as Manila time. In responses, every API (activities, registrations, public pages, accounts) shows date-times in Manila time with the offset, e.g. `2026-10-26T18:00:00+08:00`, never UTC "Z" (all views use `config.json.JsonResponse`; fixed in the Step 5 review); money is a string (`"30000.00"`). An update sends only the fields to change. Errors: 400 with `{"errors": {field: [...]}}` or `{"error": "..."}`; 403 with a clear message when `can_create_activity` is missing. Someone not logged in gets 401 `{"error": "Login required."}` (item 225).
80. **Django admin for activities** goes through the same services:
    - Adding and editing an activity both validate and audit.
    - Status is read-only on the form and is changed with the list actions ("Change status to …"), which follow the allowed moves.
    - A Conducted activity is fully read-only.
    - Resource persons can be added or removed on the activity's page (not on the add page); existing links are read-only.
    - The separate resource-person-link screen is view-only.
    - Activities cannot be deleted.
81. The demo command (`seed_demo`) now creates activities, resource persons and status changes through these services, so demo activities have a real audit log.

## Step 4 fixes (after owner testing)

82. **No past dates.** When creating an activity, `start_date` must be today or later and `registration_deadline` must not be in the past. "Today" is the date in Manila (`TIME_ZONE = "Asia/Manila"`, `USE_TZ = True`). The start date is compared by date; the registration deadline is compared to the current time, so a deadline earlier today (for example 8:00 AM when it is now 3:00 PM) counts as past. **Final (confirmed by the owner):** the deadline is compared to the current time, not just the date. When editing, these two checks apply only to a field whose value is being changed, so an activity that already started can still be edited. The existing date rules are unchanged.
83. The services accept an optional `now` argument. Only tests (fixed calendar dates) and the demo seed use it; real calls never pass it. The demo seed creates each activity "as of" two weeks before its start date, so past (Conducted) demo activities pass the rule.
84. **Lookup tables in the Django admin.**
    - ACTIVITY_STATUS and DOCUMENT_STATUS are view only (no add, change or delete): the app's rules rely on their fixed names.
    - PARTICIPANT_CATEGORY is managed by the Admin:
      - The Admin can add categories and edit any category's description.
      - The seeded categories (Faculty, Incubatees, MSMEs) cannot be renamed or deleted. "Seeded" is decided by name, using the list in `accounts/seed.py` (`PARTICIPANT_CATEGORIES`), never by row ID.
      - An Admin-added category can be renamed, and deleted only while no activity uses it. Otherwise the admin shows "Cannot delete "X": it is used by N activities…" instead of an error page. There is no bulk delete; categories are deleted one at a time.
      - Names are trimmed before saving and must be unique ignoring upper/lower case and outer spaces. A unique index on `LOWER(TRIM(name))` enforces this in the database too (migration `activities/0003`; an index, not a new column).
      - The rules live in `activities/services.py` (`save_participant_category`, `delete_participant_category`, `is_seeded_category`), which need `can_manage_users`.
      - `seed_data` matches category names ignoring case and spaces, and only creates missing ones, so it never duplicates or overwrites a category.
      - Nothing assumes there are only three categories: forms, list filters, the API, counts and the demo all work with any number.
    - ACTIVITY_TYPE and DOCUMENT_TYPE can be added and edited (and deactivated), never deleted.
    - ROLE: only `can_create_activity` and `can_manage_users` can be edited. Roles cannot be added, renamed or deleted, and the description is read-only. Taking `can_manage_users` away from a role is refused if no active member of another role could still manage users (`accounts.services.update_role_permissions`).
85. **Decided (owner, 2026-10-31):** the system administrator role (renamed "System Administrator") keeps `can_manage_users` and **no longer has `can_create_activity`** (separation of duties). Details: item 339.
86. **Changing status in the Django admin.**
    - The activity edit page shows the status as plain text (no link to the status lookup row).
    - A "Change status (now: …)" button opens a small form that offers only the allowed next statuses (`activities.services.allowed_next_statuses()`; Draft is not offered once anyone has registered).
    - The "Change status to …" list actions remain.
    - Every status change shows a success or error message.
    - The Action dropdown appears only on the Activities **list** page, and only when the list has at least one activity (Django hides it on an empty list).
87. The API detail field `allowed_status_moves` now uses the same `allowed_next_statuses()`, so it also leaves out Draft once someone has registered.
88. **Data check (2026-10-19, development database).** Row counts: activity_status 4, participant_category 3, activity_type 6, document_status 2, document_type 8, role 5. No duplicate names, including ignoring upper/lower case and spaces. The reported "Activity status with this Name already exists." came from the old read-only Status link on the activity page: it opened the lookup row of the activity's *current* status (Draft), and typing "Conducted" there tried to rename Draft to Conducted. The unique name rule blocked it, so no data changed. That page is now view only.

## Many participant categories per activity (Step 4 review, ERD change)

89. **ERD change.** ACTIVITY no longer has `participant_category_id`. The new table ACTIVITY_PARTICIPANT_CATEGORY (`activity_participant_category_id` PK, `activity_id` FK, `participant_category_id` FK, unique (activity, participant category)) links an activity to each category it is for. Delete rules: activity → CASCADE (a pure child row; activities are never deleted anyway), category → PROTECT. In Django this is `Activity.participant_categories`, a many-to-many field through `ActivityParticipantCategory` (no column on ACTIVITY).
90. **Migration `activities/0004`** creates the table, copies each activity's old category into it, then drops the old column. It prints the counts and stops (rolling everything back) if the number of links does not equal the number of activities. Going back only works on an empty database. Development database before the migration: 2 activities, both "Incubatees"; expected after: 2 links.
91. **At least one category per activity**, checked on create and update ("Choose at least one participant category."), in the API and in the Django admin. This replaces the Step 4 rule "exactly one participant category".
92. **Audit log.** One row per edit, field "Participant Categories", old and new values are the category names sorted and joined with ", " (for example "Faculty, MSMEs"). No row when only the order changes or a category is repeated: categories are compared as a set.
93. **API.** Create and update take `participant_categories` as a list of IDs (`[1, 3]`). Responses (list rows and detail) return `participant_category_ids` (sorted) and `participant_categories` (names, sorted). The list filter still takes one `participant_category` ID.
94. **Counting by category.** One activity can count under several categories, so per-category numbers can add up to more than the total. Totals must count distinct activities (the list and the stats already do: the category filter uses an EXISTS subquery, so an activity is never counted twice).
95. **Django admin.** Categories are a checkbox multi-select on the activity form, read-only (shown as text) when the activity is Conducted. The activity list has a "Participant categories" column and a category filter. Category changes are saved only through the service (with the audit row).
96. **Category delete rule.** An Admin-added category can be deleted only if no activity links to it (item 84). The "no registration uses it" check is added in Step 5, when REGISTRATION.participant_category_id exists.
97. **Demo data.** "Startup Pitch Competition" is for Incubatees and MSMEs, and "Technology Forum" for Faculty and Incubatees; the other demo activities have one category.

## Registration, attendance and evaluation (Step 5)

98. **ERD change: REGISTRATION.participant_category_id** (FK to PARTICIPANT_CATEGORY, PROTECT), **required (NOT NULL)** as in the ERD. Migration `registrations/0002` added it (nullable for the moment), filled it when the registration's activity has exactly one category, and printed how many stayed empty. Migration `registrations/0003` then makes it NOT NULL; it first checks that no registration is still empty and, if any is, stops with a clear message and changes nothing. The development database had 0 registrations, so nothing needed filling. `registrations.services.register()` also always requires it. *(Updated in the Step 5 review.)*
99. **Choosing a category.** The registrant chooses one of the activity's categories (ACTIVITY_PARTICIPANT_CATEGORY); anything else is refused with the list of allowed names. If the activity has only one category, it is used automatically. **A person who fits several groups picks the one they attend as.**
100. **Category rules that involve registrations.** `update_activity` refuses to remove a category from an activity while any registration of that activity uses it. An Admin-added category can be deleted only if no activity links to it **and** no registration uses it (completes item 96).
101. **Forms.** One registration form and one evaluation form per activity (database constraint). Creating a form copies the DEFAULT_FORM_FIELD rows for the activity's type and form type; `generated_from_default` is true when at least one default was copied. The default title is "<activity title> - Registration" / "- Evaluation". Forms can be created in any activity status.
102. **Form lock.** A form is locked once anyone has submitted it (a registration or an evaluation). Then fields cannot be added, edited (label, type, options, required) or removed, because earlier answers would no longer match. Reordering stays allowed (it does not change any answer).
103. Name, email, contact number and participant category are REGISTRATION columns. A registration form refuses a field labelled Name, Full name, Email, Email address, Contact number, Participant category or Category.
104. **Field types and stored answers** (one function: `registrations.services.validate_answers`).
    - `short_text`: up to 255 characters; `long_text`: up to 5,000. Text is trimmed.
    - `rating`: a whole number 1 to 5, stored as "1".."5".
    - `multiple_choice`: exactly one of the options.
    - `checkbox`: a JSON list of the chosen options (stored in the form's order); a required checkbox needs at least one tick (this is how the Data Privacy consent is enforced).
    - Required fields must be answered; an unanswered optional field has no FORM_ANSWER row.
    - FORM_FIELD.options and DEFAULT_FORM_FIELD.options hold a JSON list (e.g. `["Yes", "No"]`) in the same string column (no ERD change; matches "form definitions as JSON" in the tech-stack document). Migration `registrations/0002` converted any old "one option per line" values.
105. **Default form fields are PLACEHOLDERS to validate with the TBI.** The same set is created for every activity type by `seed_data`:
    - registration: "Affiliation / Organization" (short text, required) and "Data Privacy consent" (checkbox, required, one option: "I agree to the processing of my personal data in accordance with the Data Privacy Act of 2012 (RA 10173).");
    - evaluation: "Overall rating", "Resource person rating", "Organization / venue rating" (ratings, required), "Key learnings" (long text, required), "Suggestions" (long text, optional).

    They are matched by (activity type, form type, label) and only created when missing, so `seed_data` never duplicates them or overwrites the Admin's edits. An activity type added later gets defaults when `seed_data` is run again. The Admin can edit them in the Django admin.
106. **Public registration (no login).** Only Scheduled activities are visible; Draft, Ongoing and Conducted return 404. The public response shows the activity details, its categories, slots left, whether registration is open (and why not), and the form fields — never who registered. *(Backend review: the JSON endpoint is `/api/register/<id>/` and the response also names the hidden honeypot field, item 214.)* Registration is open when `activities.services.is_registration_open()` is true (Scheduled, at or before the deadline, slots left) **and** a registration form exists. The closed reasons are "not open", "deadline has passed", "all slots are taken" and "form is not ready". *(Phase F3, owner decision, item 279: the JSON endpoint is unchanged, but the HTML page `/register/<id>/` of an **Ongoing or Conducted** activity shows its title, dates and venue with "Registration for this activity has closed." (200); Draft and unknown IDs stay "not available" (404).)*
107. The activity row is locked while registering, so two people cannot take the last slot (tested with two threads at once).
108. Emails are stored trimmed and in lower case, and are unique per activity ignoring case and surrounding spaces (database constraint on `LOWER(TRIM(email))`). A second registration gets "This email is already registered for this activity."
109. The REGISTRATION and its FORM_ANSWER rows are saved in one transaction. The confirmation email is sent only after the save succeeds, **as a Django-Q2 task** (`registrations.tasks.run_send_registration_confirmation`, queued on commit with `robust=True`; backend review, item 216), so a mail-server failure never turns a saved registration into an error; it names the activity, dates, venue and the chosen category, and nothing about other registrants. `startup` stays empty until Step 7.
110. The public endpoints are JSON under `/api/`: `/api/register/<id>/`, `/api/evaluate/<token>/` and `/api/startups/set-password/<token>/` (owner decision, backend review). The user-facing URLs `/register/<id>/`, `/evaluate/<token>/` and `/startups/set-password/<token>/` are **reserved for the HTML pages of the frontend phase**; emails always link to these page URLs. Until the frontend exists they show a plain placeholder page that reads neither the activity nor the token. *(Phase F3: the placeholder is gone; these URLs are now the real pages, item 278.)* The POSTs keep Django's CSRF protection; the frontend pages will include the token.
111. **Attendance.** *(Replaced 2026-11-24 by item 434: attendance per day, QR self check-in, walk-ins; staff marking stays, one day at a time, method "staff".)*
    - Marked or unmarked by a member with `can_create_activity`, one or many at a time (all or nothing), only while the activity is Ongoing or Conducted. **Conducted locks the activity's own fields, not its registrations.**
    - Marking needs a method: `email` or `wet_signature`. Unmarking ("Undo") clears the method and is refused for anyone who has already submitted the evaluation. *(2026-11-06:)* marking also records who marked it and when; undo clears them (item 349).
    - Attendance changes are not written to ACTIVITY_AUDIT_LOG (they are not activity fields).
    - *(Pending validation by the TBI.)* "Email" confirmation means staff record attendance after the participant confirms by email; there is no self check-in (confirmed again 2026-11-06: no public check-in page is built; answered later by item 434: the QR check-in).
112. Any signed-in staff member can list an activity's registrations (with category, contact details and answers), filtered by attended / not attended and by category.
113. **Evaluation window.** The evaluation opens at 00:00 Manila time on the activity's `end_date` (so people can answer at the closing of the event) and closes at `evaluation_deadline` (still open at that exact time). The brief's "after end_date" was read this way because item 68 allows the deadline on the end date. **Confirmed by the owner (2026-10-23).** Only registrations with `attendance_confirmed` can evaluate; one evaluation per registration (item 40); never anonymous.
114. **Personal evaluation links.** Participants have no login. Each link carries the registration ID signed with Django's signing module (salted, using `SECRET_KEY`); no new column and no ERD change. A tampered, forged or unknown link is refused with "This evaluation link is not valid." Nobody can identify themselves by typing an email. A person always gets the same link, and it has no expiry of its own: the evaluation window decides when it works. Changing `SECRET_KEY` makes all old links invalid. Links use the `SITE_URL` setting (`.env`) and open the page URL `/evaluate/<token>/` (item 110). *(Backend review:)* once the evaluation is submitted or the window has closed, the link shows **only** `is_open: false` and the closed reason, never the registrant's name or email (a forwarded or old email should not reveal them). Before the window opens it still shows the form and the suggested name and email. *(2026-10-31: the activity's general evaluation page, item 334, only emails this personal link to the registered address; it never shows it.)*
115. **Sending links.** *(Rewritten 2026-11-06, item 348.)* The first link goes out **automatically**: the 5-minute Django-Q2 job emails each attendee who has not evaluated, has not been sent a link and whose link has not failed, once the activity is Conducted, an evaluation form exists and the window is open. Each send is recorded on the registration (`evaluation_link_sent_at` or `evaluation_link_failed_at`, one clears the other). **A failed email is not retried automatically.** Staff resend on the Evaluation tab ("Resend link" for one person, "Resend to all who haven't answered"): `send_evaluation_links(member, activity, registration_ids=None)` queues one task (`registrations.tasks.run_send_evaluation_link`) per person and answers how many were queued; it needs `can_create_activity`, an evaluation form and the open window, and refuses a person who already evaluated. A task skips someone who submitted in the meantime. The mail-server failure is logged (registration ID and error type only).
116. **Django admin.** Registrations, answers, evaluation submissions and activity forms are view only. Attendance is changed with the registration list actions ("Mark attended (confirmed by email)", "Mark attended (wet signature)", "Unmark attendance"). The activity list has "Create registration and evaluation forms (from defaults)" and "Send evaluation links …" actions. Default form fields are editable (options must be a JSON list).
117. **Demo data.** `seed_demo` creates both forms from the defaults (through `create_form`), gives every registration one of its activity's categories, and checks every answer with `validate_answers`. Demo registrations are written directly because most demo activities are already closed for registration.


## Documents, checklist and reminders (Step 6)

118. **Who may do what with a document.** *(Pending validation by the TBI.)*
     - **Upload:** any active staff member (any role, also Admin Staff), for any activity, in every activity status, including Conducted (the accomplishment report is due after the end date). **Conducted locks the activity's own fields, not its documents.** Only active document types can be chosen. The title is optional and defaults to the type name.
     - **Approve / return:** a member whose role has `can_approve_documents` (seeded: the Director only). They may **approve their own upload**; they may not return it (they replace or delete it instead). *(Owner change 2026-11-05, item 344; it replaces the old rule "can_create_activity, never the uploader".)*
     - **Replace the file / delete:** only the uploader, and only while the document is For Review. Delete also removes the file.
     - **Approved is final:** no replacement, no type change, no deletion. A wrong approved document is handled by uploading a new one. There is no "change type" action at all: a For Review document of the wrong type is deleted and uploaded again.
119. **Returned documents (no Rejected status).** DOCUMENT_STATUS stays For Review / Approved. Returning sets `reviewed_by` and `reviewed_at` (the reviewer and the time) and keeps the status For Review. The review state is calculated, not stored:
     - For Review, `reviewed_at` empty: **waiting for a reviewer**;
     - For Review, `reviewed_at` set: **returned**, waiting for the uploader;
     - Approved: **approved**.

     A returned document cannot be approved (or returned again) until the uploader replaces the file; replacing clears `reviewed_by` and `reviewed_at`. The reason (required, at most 300 characters) is **not stored on DOCUMENT** (the ERD has no column for it). It is only in the uploader's in-app NOTIFICATION (type `document_returned`), which names the document title, type, activity and reviewer. NOTIFICATION has no document column, so it links to the activity and document type. The API (detail and repository) shows `review_state` (`waiting_for_review`, `returned`, `approved`).
120. **Serving files (closes item 65).** `/media/` is no longer served, not even in DEBUG (the `static()` line was removed from `config/urls.py`). Documents are downloaded only through `/documents/<id>/download/`, which requires an active staff login (anyone else is sent to the login page) and always sends the file as an attachment under its original name, with `X-Content-Type-Options: nosniff`. **Any active TBI member may download any activity document** *(pending TBI)*. Certificate files (`related_certificate` set) give 404 there; since Step 9 they are downloaded through the certificate views (item 189). Nothing else used `/media/` URLs: `seed_demo` only writes files to disk, and certificate templates/signature images will need their own permission-checked view in Step 9.
121. **File checks.** Allowed: pdf, docx, xlsx, jpg (`.jpeg` is accepted and stored as `jpg`), png. `.doc`, `.xls`, `.txt` and everything else are refused. The real content is checked, not only the name: PDF starts with `%PDF-`, PNG and JPEG with their signatures, and DOCX/XLSX must be a ZIP containing `[Content_Types].xml` plus `word/document.xml` or `xl/workbook.xml` (so an XLSX renamed to .docx is refused). No extra library is needed (python-magic needs a separate DLL on Windows). Maximum size: `DOCUMENT_MAX_UPLOAD_MB` (default 10). Empty files are refused. Stored columns: `file_name` = the original name without any folder part (cut to 255 characters, keeping the extension), `file_extension` lowercase without the dot, `file_size_kb` rounded up (at least 1).
122. **Stored file path.** Built from DOCUMENT_TYPE.`naming_pattern` as `documents/<pattern>.<ext>`. Placeholders: `{activity_id}`, `{activity_slug}` (title, at most 40 characters), `{type}` (type name as a slug), `{date}` (upload date in Manila, `YYYYMMDD`), `{year}`. The uploaded name is never used in the path. Patterns may only contain letters, digits, `- _ /` and these placeholders (checked when an Admin saves a document type). If a name is taken Django adds a random suffix; the path always fits in 255 characters. When a type has no pattern, the fallback `activity-{activity_id}/{type}/{date}-{type}` is used. Migration `documents/0002` only records the new `upload_to` function (no database change).
123. **Open item for the TBI:** ask for their official file-naming convention. An Admin can enter it per document type in the Django admin (`naming_pattern`); until then all seeded types use the fallback pattern. `seed_data` does not set patterns.
124. **Checklist** (`documents.services.document_checklist`, database checks only; the Step 8 assistant will reuse it for "what is missing"):
     - every **active required** type with its due date, state and overdue flag; **optional** active types are listed separately with their documents *(the Invitation Letter and the Accomplishment Report are optional since item 386)*;
     - due date = `start_date` or `end_date` + `due_offset_days` (both are already Manila dates), **computed, never stored**;
     - state: **Approved** if at least one Approved document of that type exists (that completes it), else **For Review** if any document exists (returned ones included), else **Missing**;
     - **overdue** = today (Manila date) is after the due date and the state is not Approved. On the due date itself it is not yet overdue. A For Review document past its due date is overdue;
     - certificate files never count (item 26).
125. **Reminders** (`documents.services.send_document_reminders`), to the activity's creator (`created_by`) as in-app notifications:
     - **due soon** (`document_due_soon`): a required type with **no document uploaded at all**, due today or within `DOCUMENT_REMINDER_DAYS_BEFORE` days (default 3, both ends included);
     - **overdue** (`document_overdue`): a required type after its due date and not Approved;
     - Draft activities are skipped; Scheduled, Ongoing and Conducted get reminders. Inactive types are skipped;
     - **a deactivated creator is skipped and logged** (WARNING on the `documents.reminders` logger). *Open item for the TBI: who should get these reminders instead?*
     - "due soon" and "overdue" are separate, so one type can get each once. Approving later does not delete old reminders.
126. **Never the same reminder twice.** Migration `notifications/0002` adds a partial unique constraint on (member, activity, document_type, type) for the two reminder types only (returned-document notifications can repeat). Each reminder is inserted in its own savepoint; a duplicate is refused by the database and counted as "already sent", so two runs at the same moment cannot duplicate it, and running again creates nothing new. The development database had 0 notifications when this was written.
127. **Daily schedule (Django-Q2, database broker).** `python manage.py setup_schedules` creates the Schedule "Daily document reminders" (`documents.tasks.run_document_reminders`, type Daily, repeats forever, next run at 8:00 AM Manila time, `DOCUMENT_REMINDER_HOUR`). Running it again keeps one schedule (it updates the same row, removes extra rows with that name, and only resets the next run if it is not at 8:00). A Daily schedule is used instead of a cron expression so no extra library (croniter) is needed; Manila has no daylight saving time, so it stays at 8:00. `Q_CLUSTER["catch_up"] = False`: if the cluster was off at 8:00, the missed run happens once when it starts, not once per missed day. `python manage.py send_document_reminders` runs it once by hand.
128. **qcluster on Windows works** (checked on the development machine on 2026-10-20 with Django-Q2 1.11.1 and Python 3.12, in a throwaway database that was dropped afterwards): the cluster started (guard, pusher, monitor, 2 workers), ran the scheduled task once (Success, 0 failures) and kept running cleanly for 90 seconds. Windows has no SIGALRM, so a task's time limit is enforced by the cluster's guard process instead (Django-Q2 handles this). Stop it with Ctrl+C; killing the process abruptly prints harmless "reincarnated ... after sudden death" / "Access is denied" lines. **Fallback** if it ever cannot run: Windows Task Scheduler running `python manage.py send_document_reminders` daily at 8:00.
129. **Repository and review queue (JSON API, `/documents/api/`).** Any active staff member can list and search activity documents across activities, filtered by activity, document type, status, uploader and review state, searching the title and the file name, 20 per page, newest first. The "waiting for my review" list shows only documents waiting for a reviewer that are not my own uploads (returned ones are left out); a member without `can_create_activity` gets 403 from the API (and an empty list from the service). *(Since 2026-11-05, item 344: the list needs `can_approve_documents` and includes my own uploads.)*
130. **Notifications API (`/notifications/api/`).** A member sees only their own notifications (newest first, optional `unread=1`), the unread count, and can mark one or all as read; another member's notification gives 404.
131. **Django admin.** Documents and notifications are view only. The document screens show a "Download" link through the permission-checked view (never a `/media/` link). Document types validate `naming_pattern` and show the placeholders.
132. **Demo data.** `seed_demo` still writes small `.txt` placeholder files directly (demo only); they can be downloaded through the new view but could not be uploaded through the service.
133. *(Done in Step 8, item 156.)* `approve_document` queues the indexing task after the approval is committed.


## Startups and the Navigatu provider (Step 7)

134. **One access point to Navigatu.** `startups/providers.py` is the only module that knows where startup data comes from. `StartupProvider` has two methods, `list_startups()` and `get_startup(navigatu_id)`, which return plain `StartupRecord` objects (not models). The class is chosen by the `STARTUP_PROVIDER` setting (dotted path, also readable from `.env`):
     - `MockStartupProvider` (default) reads 10 invented startups from `startups/mock_data/navigatu_startups.json` (all emails end in `@demo.example.com`; one is inactive). The first six are the startups `seed_demo` already created in Step 2, with the same emails and `DEMO-NAV-00x` IDs.
     - `NavigatuStartupProvider` is a stub that raises "The Navigatu connection is not available yet." The staff sync API answers 503 with that message; the command stops with it.
135. **Sync** (`startups.services.sync_startups`). Navigatu data is read-only in this system.
     - Matching: by `navigatu_id` first, then by email ignoring case (this refines item 11). An email match links a row that had no `navigatu_id` yet.
     - Copied fields: `name`, `contact_number`, `email` (stored lowercase), `maturity_stage`, `last_assessment_at`, `is_active`. A row is saved only when one of these changed. `password` is never touched.
     - **`last_synced_at`** is refreshed for every startup the provider returned, even if nothing else changed, with one bulk update that does not touch `updated_at`. So a second run saves no row and reports created 0, updated 0, deactivated 0. *(Owner decision S1.)*
     - **Email conflicts are skipped and logged (WARNING on `startups.sync`), never merged:** two Navigatu records with the same email; a record whose email already belongs to a different row; an email match whose row has a different `navigatu_id`. The rows involved are left untouched (not updated, not deactivated). Unusable records (no `navigatu_id`, no name, invalid email, values too long) are skipped too.
     - Only one sync runs at a time (a PostgreSQL advisory lock inside the sync transaction).
     - Result: `created`, `updated`, `unchanged`, `deactivated`, `skipped`, `deactivation_skipped`, `deactivation_skipped_reason`.
136. **Startups missing from Navigatu are deactivated, never deleted** (registrations may point to them), and each is logged. This includes rows that never had a `navigatu_id`. A record that comes back active reactivates the row.
137. **Deactivation safety guard.** If the provider returns **zero records**, or the run would deactivate **more than** `STARTUP_SYNC_MAX_DEACTIVATE_RATIO` (0.5, a setting) of the currently active startups, nobody is deactivated in that run. Creates and updates still happen; a WARNING is logged; the result has `"deactivation_skipped": true` and the reason. Exactly half is allowed. `python manage.py sync_startups --force` overrides the guard on purpose; the scheduled task and the staff "sync now" API never force.
138. **Emails are unique ignoring case** (migration `startups/0002`: a unique index on `LOWER(email)`, no new column). The development database had 0 startups when this was added.
139. **Daily sync schedule.** `python manage.py setup_schedules` now creates two schedules from the list in `config/schedules.py`: "Daily document reminders" (8:00 AM) and "Daily Navigatu startup sync" (7:00 AM Manila, `STARTUP_SYNC_HOUR`). Same idempotent rules as item 127 (one row per name). The command moved from `documents/` to `accounts/management/commands/` because it now covers several apps; it is typed the same way. A provider error fails only that run (Django-Q2 records it as a failed task). `python manage.py sync_startups` runs it once by hand.
140. **Startup login** (separate from staff, item 62).
     - JSON endpoint `/startups/portal/api/login/` (GET sets the CSRF cookie; POST logs in). Email ignores case and outer spaces.
     - Unknown email, wrong password, inactive startup and a startup without a password all get "Invalid email or password." (for an unknown email the password is still hashed once, so the timing is the same).
     - **Same lockout rules and messages as staff** (item 54: 5 per email, 20 per IP, 15 minutes), but **separate counters**: failed startup logins never lock a staff login and vice versa.
     - Logging in starts a fresh session holding only `STARTUP_SESSION_KEY` and a fingerprint of the password hash. Every portal request checks that the startup is still active and the fingerprint still matches, so a deactivated startup, or a startup whose password changed, is logged out on its next request.
     - Logout is POST only.
141. **Staff and startups never cross.** A startup session has no Django user, so every staff page and the Django admin send it to the staff login, and the staff JSON API answers 401 (item 225). Every portal URL refuses a session with a logged-in staff member (403 "Staff accounts cannot use the startup portal.") and answers 401 "Startup login required." when nobody is logged in. A staff member cannot use the portal login either (403); they must log out first.
142. **Set-password link** (STARTUP has no `must_change_password`, so the staff invitation is not copied).
     - Only a member with `can_manage_users` sends it (staff API or the Django admin action), and only to an active startup. It is emailed (console backend in development) and never returned by the API or logged. The emailed link opens the page `/startups/set-password/<token>/`; the page talks to `/api/startups/set-password/<token>/` (item 110).
     - The link carries the startup ID and a fingerprint of the current password hash and email, signed with Django's `TimestampSigner` (salted, `SECRET_KEY`). It expires after `STARTUP_PASSWORD_LINK_HOURS` (72).
     - **Single use without a new column:** setting the password changes the hash, so the fingerprint no longer matches and the link (and every other link sent earlier) stops working. A sync that changes the email also invalidates it.
     - Refused links: tampered ("This link is not valid."), expired ("This link has expired. …"), used or replaced ("This link has already been used or is no longer valid. …"), or for an inactive startup.
     - The new password must pass Django's password validators (including similarity to the startup's email). A refused password does not use up the link.
     - Setting the password does not log the startup in.
143. **The Admin-sent link is also the "forgot password" route** for now. There is no self-service reset and no change-password page in the portal. *(Question for the TBI.)*
144. **Startup portal** (`/startups/portal/api/`, startup login required):
     - **My profile:** the Navigatu data (read-only) and `last_synced_at`; never the password.
     - **My registrations:** registrations made while logged in (`startup` = me) plus older public registrations with my email (ignoring case) that are not linked to any startup, with activity, dates, category, attendance and confirmation method. Reading never changes a row (old rows are not linked automatically).
     - **Register while logged in:** the same service and rules as public registration (`registrations.services.register`, which gained one optional `startup` argument): only Scheduled activities, deadline, slots, row lock, required answers, one registration per email. Name, email and contact number come from the profile; `registration.startup` is set; the startup still chooses one of the activity's categories (chosen automatically when there is only one); any category may be chosen.
     - **My notifications:** list (newest first), unread count, mark one or all as read, through the same `notifications.services` functions as staff (they now accept a member or a startup, and always filter by that recipient). Nothing creates startup notifications yet.
145. **Staff side** (`/startups/api/`): any active staff member can list and search startups (name or email, maturity stage, active), 20 per page, by name, and open a startup's detail with its registration and attendance history (the same "my registrations" rule). Running a sync now and sending set-password links need `can_manage_users`.
146. **Django admin:** startups are view only (Navigatu fields cannot be edited here; the password hash is never shown). The only action is "Send set-password link to selected startups".
147. **Maturity stages are placeholders** until the TBI confirms them: Ideation, Validation, Early Traction, Scaling (stored as plain text from Navigatu). *(Question for the TBI.)*
148. **Demo data.** `seed_demo` now creates its startups through a mock sync (`sync_startups` with the mock provider), so demo startups go through the same path as real data: 10 startups, 9 active. Its sync never deactivates anything (`deactivate_missing=False`), so it cannot touch non-demo startups. Safe to run twice.
149. **Development emails show long links on one line.** Django's console backend wrapped long lines ("quoted-printable", a `=` at each line end), so a set-password link printed in the terminal could not be copied (Step 5 evaluation links would wrap too once IDs grow). The development backend is now `config.mail.ConsoleEmailBackend`: the same console output, but lines may be up to 998 characters (the RFC 5322 limit). Real sending (SMTP, Step 9) is unchanged; mail programs decode wrapped lines themselves.


## AI assistant: RAG pipeline (Step 8)

150. **One gateway to Gemini.** `assistant/gemini.py` is the only code that talks to Gemini (`google-genai`). The key is `GEMINI_API_KEY` in `.env`; the models are `GEMINI_CHAT_MODEL` (default `gemini-3.5-flash-lite`, item 20; it was `gemini-2.5-flash` before 2026-10-20) and `GEMINI_EMBEDDING_MODEL` (default `gemini-embedding-001`), item 20. `get_gateway()` is the single injection point: tests inject a fake there, and creating a real Google client inside a test fails the test. Logs contain only error codes, counts and IDs: never the key, a question, an answer or document text.
151. **Indexing rules** (`assistant/indexing.py`, one function `why_not_indexable`):
     - only **Approved activity documents**: never certificate files (`related_certificate` set), never For Review;
     - never the document types in `ASSISTANT_EXCLUDED_DOCUMENT_TYPES` (default `["Attendance Sheet"]`, names compared ignoring case and outer spaces), because they hold personal data. If a type is added to the list later, its old chunks are removed the next time the document is indexed, and they are never retrieved anyway;
     - only pdf (pypdf 6.19.0), docx (python-docx 1.2.0: paragraphs and tables in reading order) and xlsx (openpyxl: each sheet's non-empty rows, formula results);
     - **not indexed, with a logged reason:** images (jpg, png), PDFs with no extractable text (scans), damaged or password-protected files, missing files, other types (the demo `.txt` files). OCR is not available (TBI question).
152. **Chunking:** about 1,200 characters per chunk with about 200 characters of overlap (`ASSISTANT_CHUNK_SIZE`, `ASSISTANT_CHUNK_OVERLAP`), cut at a paragraph, line, sentence or word boundary. `chunk_index` is 0, 1, 2… in reading order. *(Starting values, to tune with real documents.)*
153. **Embeddings:** `gemini-embedding-001` at `EMBEDDING_DIMENSIONS` (768), task type `RETRIEVAL_DOCUMENT` (with the document title) for chunks and `RETRIEVAL_QUERY` for questions. Vectors are normalized to unit length, because the model only normalizes its full 3072-dimension output. Requests are batched (`ASSISTANT_EMBED_BATCH_SIZE` = 20 texts).
154. **Rate limits and failures:** a rate limit (HTTP 429) or server error is retried with backoff (2, 4, 8 s; `ASSISTANT_MAX_RETRIES` = 4 attempts). A rejected key (401/403) is not retried. When it still fails, indexing returns "failed", is logged without any text, and **keeps the document's old chunks**. The task never raises. A failed document is retried by `index_documents`, by the daily schedule (item 157), or with `--document <id>`.
155. **Replacing, never duplicating:** parsing and embedding happen first; then, in one transaction, the document row is locked, the rules are checked again, its old chunks are deleted and the new ones are inserted. A document is never half-indexed and never has duplicates (also enforced by the unique (document, chunk_index) constraint).
156. **When indexing runs:** `approve_document` queues a Django-Q2 task after the approval is committed (`transaction.on_commit(..., robust=True)`: a problem queueing the task never undoes the approval). The approver does not wait; `qcluster` must be running. `python manage.py index_documents` indexes every approved document without chunks (backfill, safe to run twice; it skipped excluded types and images), and `--document <id>` re-indexes one.
157. **Daily retry:** `setup_schedules` now also creates "Index pending documents" at **7:30 AM** Manila (`ASSISTANT_INDEX_HOUR`, `ASSISTANT_INDEX_MINUTE`), which runs the same backfill. Scanned PDFs and damaged files stay "not indexable" and are re-checked daily without calling Gemini. `Q_CLUSTER["ack_failures"] = True`: a task that raised unexpectedly is recorded as failed and not re-queued every 6 minutes forever.
158. **"Searchable"** (document detail and repository, plus a `searchable=true|false` repository filter) means the document has at least one chunk. It is calculated, never stored.
159. **Vector index:** the HNSW index with cosine distance created in Step 1 (`assistant/0002`, m=16, ef_construction=64) is used; no new migration. HNSW was kept rather than IVFFlat because it needs no training step (IVFFlat must be built after data exists and loses recall as data grows) and gives good recall on a small, growing collection. Searches raise `hnsw.ef_search` to 100 and use pgvector 0.8's iterative scan, so filtering to one activity still finds enough rows.
160. **Answering** (`assistant/services.py`), with Gemini function calling run step by step (at most 4 tool rounds, temperature 0.2):
     - **Record tools** (read-only, existing services): `find_activities` (title and/or date), `get_document_checklist` (Step 6 checklist), `list_activity_documents`, `documents_needing_attention` (overdue required documents and documents waiting for review or returned, across non-Draft activities). Status, missing, overdue and pending questions are answered from these, never from documents.
     - **Document search:** the question is embedded and the 6 most similar chunks (`ASSISTANT_TOP_K`) of approved activity documents are retrieved by cosine similarity; chunks below `ASSISTANT_MIN_SIMILARITY` (0.55) count as not relevant. An `activity_id` limits the search to one activity. *(Starting values, to tune.)*
     - The last 6 chat messages (`ASSISTANT_HISTORY_MESSAGES`, 3 questions and answers, each cut to 2,000 characters) are sent as context; sources are not resent.
161. **Citations and sources.** Each excerpt is numbered [1]…[n]. Only citations to real excerpt numbers are kept; they are renumbered 1, 2… in order of first appearance (the same chunk keeps one number), and one AI_MESSAGE_SOURCE row (document, chunk_index) is saved per cited chunk, in that order, so row order = citation number. Excerpts that were retrieved but not cited are not saved.
162. **Where an answer comes from** (`answered_from`, calculated from the saved message, no column):
     - `documents`: it cites excerpts;
     - `records`: it used record tools and cites nothing; the answer ends with "(Source: the system's records.)" and saves no sources;
     - `both`: it used record tools **and** cites excerpts; the cited excerpts are saved and the answer ends with the records note *(owner decision)*;
     - `not_found`: no record tool and no valid citation (nothing relevant found, an uncited answer, or no tool at all). The reply is then always "I couldn't find the answer in the approved documents." instead of a guess.
163. **Document text is data, not instructions.** Excerpts are sent inside `<excerpt …>` tags in the tool result, never in the system instruction. Any `<excerpt` or `</excerpt` inside the text is escaped (`&lt;`), so a document can never close its tag or open a fake one; attribute values are HTML-escaped. The system instruction tells the model to ignore instructions inside excerpts, and an answer that "obeys" a document without citing it is replaced by the not-found reply.
164. **Versions.** Each excerpt carries its document's approval date (`approved_on`, Manila date of `reviewed_at`). The system instruction says: "When excerpts disagree, prefer the most recently approved document and mention that an older version says otherwise." Approved documents are never deleted or replaced (Step 6), so an older and a newer approved version of the same document both stay searchable. *(Replaces plan item R5.)*
165. **Personal data sent to Gemini.** The record tools send only activity and document information (titles, types, dates, states), never participant names, emails or contact numbers. However, **excerpts from other approved documents may contain personal data** (names in a letter or report), and the question itself is sent. Attendance Sheets are excluded from indexing (item 151). *(TBI question: may real documents be sent on the free tier?)*
166. **Chats** are private: every query filters by the member, so another member's chat gives 404 (and the service refuses). A new chat is titled "New chat"; the first question (cut to 80 characters) becomes its title. A question and its answer are saved together and only when answering succeeded; when Gemini is unavailable nothing is saved. *(2026-11-15: the first question is only the title until a short title generated in the background is ready, and a chat can be renamed or deleted by its member: items 377–378.)*
167. **Errors are clear messages, never stack traces:** 503 with `{"error", "kind"}` for a missing key ("The AI assistant is not set up: GEMINI_API_KEY is empty in .env."), a rejected key, a rate limit after retries ("The AI service is busy …"), or an unavailable service; 400 for an empty question, a question over 2,000 characters, or an unknown activity.
168. **Who can use it:** any active staff member, about every activity and every approved document (consistent with item 120). The answer follows the language of the question.
169. **Django admin:** embeddings, chats, messages and sources are view only; embeddings show the vector's length (768) and the first 80 characters of the chunk, never the numbers.
170. **Checking Gemini (`python manage.py check_gemini`).** Uses the same settings and gateway as the app. It prints the chat and embedding model names in use and where each comes from (`.env`, the terminal's environment, or the default in settings), lists the models the key can use for `generateContent` and `embedContent`, then makes one tiny embed call and one tiny generate call without and with the assistant's tool declarations, one attempt each, printing the model, the HTTP status and Google's own error message. `--chat-model <name>` tries another model without changing settings. The key is never printed. Being *listed* does not prove a model works (gemini-2.5-flash is listed but answers 404); only the calls do.
171. **A terminal can hide `.env` changes.** Settings read `.env` with `load_dotenv()`, which never overrides a variable already set in the environment (on purpose: real environment variables win). The project's `.vscode/settings.json` has `"python.terminal.useEnvFile": true`, so VS Code copies `.env` into a terminal's environment **when the terminal opens**. A `runserver` or `qcluster` restarted in an old terminal therefore keeps the old values. Fix: open a new terminal (or remove the variable with `Remove-Item Env:GEMINI_CHAT_MODEL`). `check_gemini` warns when the terminal's value differs from `.env`.
172. **Gemini errors in the log** now include the model, the HTTP status and Google's message (cut to 300 characters, with the key masked as `***` if it ever appears); never the key, a question, an answer or document text. A 404 tells the member which model is unavailable and to run `check_gemini`.
173. **Request timeout:** one Gemini request may take at most `ASSISTANT_REQUEST_TIMEOUT_SECONDS` (60). The timeout is also sent to Google as the request deadline (a slow model answers HTTP 504 DEADLINE_EXCEEDED). A timeout counts as "unavailable": it is retried like a server error, then reported as a clear 503. The google-genai library's own retries stay off (no `retry_options`), so only our retry loop runs.

## Certificates, sign-off and emails (Step 9)

174. **ERD change: EVALUATION_SUBMISSION.certificate_name and certificate_email** (owner decision). Both are required strings (`certificate_name` at most 150 characters, `certificate_email` at most 254). Migration `registrations/0004` adds them as nullable and fills every existing evaluation from its registration's name and email (trimmed; email in lower case), printing the counts; if a row cannot be filled (empty or too-long name, invalid email) it stops with the submission IDs and changes nothing. `registrations/0005` checks again and makes both NOT NULL. **Development database (2026-10-21): 0 evaluation submissions, so 0 filled and 0 problems.**
175. **The name printed and the address used come from the evaluation, not the registration.**
     - `submit_evaluation` requires both. Name: outer spaces trimmed, required, at most 150 characters, capitalization kept exactly as typed. Email: trimmed, checked with Django's email validator, at most 254 characters, stored in lower case. Their errors are reported together with the answer errors.
     - The evaluation page (GET) returns `suggested_certificate_name` and `suggested_certificate_email` (the registration's name and email) as defaults for the frontend.
     - **The participant is responsible for the name and email they type.** There is no correction service. The frontend must show a review step ("this is how your name will appear") before the evaluation is submitted (to build in the frontend phase). Issued stays final (item 46).
     - The startup portal's "My certificates" still follows the startup's registrations (item 188), never `certificate_email`.
176. **Certificate numbers** (owner decision Q3, closes item 14): `TBI-<year>-<5-digit sequence>`, for example `TBI-2026-00001`. The year is the Manila year when the certificate is generated; the sequence restarts at 00001 each year and is shared by both certificate kinds. Numbers are allocated inside the generation transaction under a PostgreSQL advisory lock (held until commit): the next number is the highest of that year + 1, so two generations at the same moment never collide and there are no gaps. The unique constraint on `certificate_number` is the final guard. Five digits allow 99,999 certificates a year.
177. **Certificate templates** (Admin, `can_manage_users`). The design is a background image: PNG or JPG, checked by its real content like Step 6 (signature bytes, then fully decoded with Pillow; a renamed or damaged file is refused), A4 landscape (width/height 297/210 within 3%), at least 1754 x 1240 pixels (A4 at 150 dpi), at most `CERTIFICATE_IMAGE_MAX_MB` (5). It is stored as `certificate_templates/template-<random>.<ext>`; the uploaded name is never used. Activating one deactivates the previous one in the same transaction (at most one active, existing constraint). **Files never change after upload:** a new design is a new row, so existing certificates keep exactly the design they were generated with. Nothing is deleted (PROTECT); templates are deactivated.
178. **Signatories** (Admin): name, position (VP or Director) and a signature image, **PNG only** (a transparent background prints cleanly), same content checks and size limit, stored as `signatures/<position>-<random>.png`. Adding a signatory makes them the active one for that position and deactivates the previous one in the same transaction (one active per position, existing constraint). Name, position and image are read-only afterwards; a change means a new signatory. Never deleted. *(2026-11-13: replaced by item 363: a list of people with free-text positions, managed by `can_approve_certificates`; no active-per-position rule.)*
179. **Template and signature images are served only through permission-checked views, to the Admin only** (`/certificates/admin-files/...`, always as attachments). Anyone else gets the login page or 403; a startup session is never staff. Nothing is served under `/media/` (items 65, 120). Signature images are never public; they appear only inside final certificate PDFs.
180. **Generating** (`can_create_activity`): only for Conducted activities; refused, naming what is missing, without an active template, VP or Director. It locks the activity and creates only the missing certificates: a Certificate of Participation (delivery: email) for each registration with `attendance_confirmed`, and a Certificate of Appreciation (delivery: hard copy) for each linked resource person. Safe to run twice. Each certificate records the template and the VP and Director **active at generation time** and keeps them, even if the Admin changes the active ones later. New constraint (migration `certificates/0002`, which checks for duplicates first; the development database had 0 certificates): one Certificate of Appreciation per resource person per activity. **Change to Step 5:** unmarking attendance is refused once the registration has a certificate. *(Replaced on 2026-11-12 by item 362: the System creates the certificates when the activity is Conducted; no Generate button.)* *(2026-11-13, item 363: the activity's own left and right signatories instead of the active VP and Director.)*
181. **Sign-off** (owner decision Q2). *(Replaced on 2026-11-11 by item 357: Generated → Approved by the Director → Issued; no Pending Sign-off.)* Every action needs `can_create_activity` and works on one certificate, a list, or all certificates of an activity in the starting status. It is **all or nothing**: if one certificate breaks a rule, nothing changes and the error names each one. The rows are locked while they change.
     - **Review:** Generated to Pending Sign-off sets `reviewed_by` and `reviewed_at`.
     - **Record a sign-off:** the Director and the VP are not members and have no login. A member records "Director signed" or "VP signed" after the signatory approves on paper or by email. Only while Pending Sign-off, once per position. The time saved is when it is recorded. **Who recorded it is logged** (INFO on the `certificates` logger: member ID and certificate IDs); the ERD has no column for it.
     - **Approve:** Pending Sign-off to Approved needs `reviewed_at` and both sign-offs. Approval means the activity's certificate (design, wording, activity details, signatories) is approved; a participant's name and email are not known until the evaluation.
     - **Reject:** Pending Sign-off to Generated clears `reviewed_at` and both sign-offs and keeps `reviewed_by` (item 46). No reason is stored (no column).
182. **Preview** (any active staff member): a PDF rendered on request and never stored, with a "PREVIEW - NOT VALID" watermark and no signature images. *(Replaced on 2026-11-11 by item 358: both signatures, a small corner mark until Approved.)* A participant's shows the registration name marked "(name from evaluation)". Reviewers use it to check the layout and the activity details before sign-off.
183. **The final PDF** (`certificates/pdf.py`, the only module that calls WeasyPrint; tests use a fake renderer). *(2026-11-13, item 363: the signature blocks print the certificate's left and right signatory, each with its own name and position.)*
     - Layout: A4 landscape; the template image fills the page; the text sits in the central safe area (about 60% of the width, starting at 28% of the height); two signature blocks (Director left, VP right); the certificate number centred at the bottom. Long names get a smaller font.
     - Text: "Certificate of Participation / is presented to / <name> / for participating in "<title>" held on <dates> at <venue>." For resource persons: "Certificate of Appreciation ... in grateful recognition of their valuable contribution as resource person on "<topic>" during "<title>" ...". Then "Given on <activity end date> at <CERTIFICATE_ORGANIZATION_NAME>."
     - **The wording, the organization name, the "Given on" date and the signatory order are placeholders for the TBI.**
     - Images are embedded as `data:` URIs and WeasyPrint may fetch only `data:` URIs, so text on a certificate can never make it load a file or a web address.
     - The file is written to a temporary file in the same folder and then renamed into place (`certificates/<year>/<number>.pdf`), so it is either complete or absent, never half-written. If saving the row then fails, the file is removed.
184. **When the final PDF is rendered** (a Django-Q2 task queued after the commit; a problem queueing the task never undoes the approval or the evaluation):
     - **Participant:** when the certificate is Approved **and** the evaluation is submitted, whichever happens last. The task renders the PDF with `certificate_name`, then in one transaction saves the file, sets Issued and `issued_at`, links the evaluation (item 42) and queues the email. Approval alone does not render a participant's PDF.
     - **Resource person:** on approval. The certificate stays Approved until the hand-over.
     - A failed render is logged (certificate ID and error type only), leaves no file, and the certificate stays Approved for the daily retry. Two tasks for the same certificate render it once (the second finds the work done).
185. **Emailing.** A participant certificate is emailed only when it is Issued, its delivery method is email and its evaluation is submitted. It goes to the evaluation's `certificate_email` with the PDF attached (`<number>.pdf`). The task locks the certificate row while sending, so a second task waits and then sees `sent_at`: **never sent twice**. `sent_at` is set only after the mailer accepted the email. A failed send is retried after 5, 30 and 120 minutes (`CERTIFICATE_EMAIL_RETRY_MINUTES`, as one-off Django-Q2 schedules that are deleted after they run), then by the daily retry. *Remaining risk:* if the mail server accepts the email but the database commit then fails, a later retry could send it again. This is very unlikely and is preferred over losing a certificate.
186. **Resource persons:** staff mark the certificate Issued when the printed copy is handed over ("hand-over": Approved to Issued, hard copy only, needs the rendered PDF because staff print it). It is never emailed. Participant certificates cannot be issued by hand.
187. **Email settings.** `.env`: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` (True), `EMAIL_REAL_SENDING` (False).
     - While `DJANGO_DEBUG=True`, every email is printed in the terminal unless `EMAIL_REAL_SENDING=True`. With DEBUG off, SMTP is always used.
     - **Even with real sending, addresses in `EMAIL_BLOCKED_DOMAINS` (`demo.example.com`) are never sent:** they are printed instead (`config.mail.GuardedSMTPBackend`). This also covers a `certificate_email` typed with a demo address. *(Backend review:)* they are printed only while `DEBUG` is on; with DEBUG off only "N email(s) to a blocked demo domain were not sent." is logged, never the body (it may hold a link or a temporary password) or the address.
     - The console backend now summarizes attachments ("[attachment TBI-2026-00001.pdf, application/pdf, 52,113 bytes - not printed]") instead of printing the base64 text.
     - `python manage.py send_test_email <address> [--smtp]` sends one test email (`--smtp` really sends it, even in DEBUG). The password is never printed. Gmail needs an App Password.
     - Every existing email (invitations, registration confirmations, evaluation links, set-password links) uses the same mailer, so turning real sending on sends those too.
188. **Who sees certificates.** Any active staff member lists them (by activity, status and recipient type, 20 per page), opens the detail (including the name and email from the evaluation) and downloads the PDF through `/certificates/<id>/download/` (always an attachment; 404 until it is rendered). **Startup portal "My certificates":** Issued certificates of the startup's registrations (linked ones, or older public registrations with its email, as in item 144), each with a download link. Any other certificate gives 404, the same answer as "does not exist". Participants without a login get the PDF only by email.
189. **Evidence documents** (owner decision Q1, uses DOCUMENT.related_certificate): an optional scan for a certificate, such as a wet-signed hard copy or an acknowledgment receipt, uploaded with the document type "Certificate". **The Step 6 rules apply unchanged:** any active staff member uploads; file checks, naming pattern and For Review as usual; approve or return by a member with `can_create_activity` who is not the uploader *(since 2026-11-05: `can_approve_documents`, item 344)*; replace or delete by the uploader while For Review. Evidence appears only with its certificate (`/certificates/api/<id>/evidence/`, downloaded through `/certificates/evidence/<id>/download/`), never in the checklist, the repository, the document download or the assistant (it is never queued for indexing).
190. **Django admin:** certificates are view only, with a download link. Templates and signatories are added through the services (same image checks; a new signatory replaces the active one; "make this the active design" is a checkbox for templates), are read-only after creation, are (de)activated with list actions and are never deleted. *(2026-11-13, item 363: signatories are view only in the Django admin; they are managed on the Signatories page.)*
191. **Daily retry schedule.** `setup_schedules` now also creates "Retry certificate PDFs and emails" at **8:30 AM** Manila (`CERTIFICATE_RETRY_HOUR`, `CERTIFICATE_RETRY_MINUTE`). It renders and issues Approved certificates that are ready (resource persons, and participants with an evaluation), then sends Issued participant certificates that were not sent. One failure never stops the others. `python manage.py retry_certificates` runs it once by hand.
192. **Demo helpers** (DEBUG only, not needed in real use): `make_sample_certificate_images <folder>` draws an invented A4 background and two invented signatures; `certificate_walkthrough <step>` runs each step of the flow through the real services as the first active Admin, so the owner can try it on the development database. The walkthrough activity is real data and stays (activities are never deleted); its title starts with "[Walkthrough]".
193. **WeasyPrint on Windows** needs the Pango/GObject libraries from MSYS2 and `WEASYPRINT_DLL_DIRECTORIES=C:\msys64\mingw64\bin` (see CLAUDE.md, "Setup: WeasyPrint on Windows"). Checked on the development machine on 2026-10-21 with WeasyPrint 70.0: a test PDF with ñ, é and ₱ rendered correctly, and so did a full certificate with the sample design. `python manage.py check_pdf` renders a one-line PDF in memory and prints OK or the exact error. WeasyPrint's own INFO logging (one line per render step) is reduced to warnings.
194. **Known limit, kept as is (item 46):** an Approved certificate can only move to Issued. A participant who never submits the evaluation keeps an Approved certificate that is never issued or emailed, and a wrong Approved certificate cannot be withdrawn. Changing this needs an owner decision. *(2026-11-12, item 362: approval now needs the evaluation, so new certificates no longer stay Approved without one; older ones are left as they are.)*

## Reports and dashboard (Step 10)

195. **Reports count only Conducted activities, by `end_date` in the period** (owner decision D2). The range is inclusive and uses Manila calendar dates (`end_date` is already a Manila date, so no time-zone conversion is needed). An activity from Dec 30 to Jan 2 counts in the year it ends, so it is never in two yearly reports. Draft, Scheduled and Ongoing activities are never counted in "period" numbers. Optional filter: one activity type (GENERATED_REPORT.activity_type_id).
196. **Number definitions** (all in `reports/metrics.py`; "period" = the activities of item 195):

     | # | Number | Exactly what it counts |
     |---|---|---|
     | N1 | Activities conducted | distinct period activities |
     | N2 | by type | period activities per activity type (adds up to N1) |
     | N3 | by category | period activities linked to each category (ACTIVITY_PARTICIPANT_CATEGORY, subquery); can add up to more than N1 (item 94) |
     | N4 | per month | period activities (and their registered/attended) per month of `end_date`; every month of the range is listed |
     | N5 | Registered | REGISTRATION rows of period activities |
     | N6 | Attended | N5 rows with `attendance_confirmed` |
     | N7 | Attendance rate | N6 ÷ N5 (none when N5 = 0) |
     | N8 | Unique attendees | distinct `LOWER(email)` among N6 (a person at 3 activities counts 3 in N6, 1 here) |
     | N9 | Registered / attended by category | by `REGISTRATION.participant_category`; adds up to N5 / N6 |
     | N10 | Evaluations | EVALUATION_SUBMISSION rows of period activities |
     | N11 | Evaluation response rate | N10 ÷ N6 |
     | N12 | Average per rating question | see item 199 |
     | N13 | Overall average rating | mean of every rating answer in the period, with the answer count |
     | N14 | Document compliance | per period activity and active required type: due date, Approved / For Review / Missing, overdue (item 200) |
     | N15 | Fully compliant activities | period activities where every active required type has an Approved document |
     | N16 | Certificates issued | status Issued and the **Manila date of `issued_at`** in the range (not the activity's period); split participation / appreciation; type filter on the certificate's activity |
     | N17 | Certificates emailed | Manila date of `sent_at` in the range |
     | N18 | Startups engaged | distinct startups with an **attended** registration in a period activity: linked (`startup`) or an unlinked registration with the startup's email ignoring case (the item 144 rule); inactive startups count too (history) |
     | S1 | Activities by status | every activity, **Draft included**, with `start_date` in the range, by current status |
     | S2 | Upcoming | Scheduled, `start_date` ≥ today (Manila); count + the next 5 (title and dates) |
     | S3 | Documents overdue | (activity, active required type) pairs in **non-Draft** activities past due and not Approved (the reminder rule, item 125) |
     | S4 | Waiting for review / returned | activity documents (never certificate evidence) For Review with `reviewed_at` empty / set |
     | S5 | Ready for approval | **activities** with certificates **ready** for approval (Generated + both signature images + the evaluation for participants: `certificates.services.ready_for_approval_q`), plus the number of certificates *(since 2026-11-12, item 362; 2026-11-11, item 357: every Generated certificate; before: Generated + Pending Sign-off)* |
     | S6 | Approved, not issued | certificates Approved |
197. **Two scopes** (owner change to the plan). Every dashboard card has `"scope": "period"` or `"scope": "now"`:
     - **period**: depends on the date range. S1 is a period card but uses `start_date` and includes Draft (it shows planning, not results); every other period card uses item 195.
     - **now**: S2–S6 are a snapshot of today. They **ignore the date range** but **respect the activity type filter**.

     The response also carries a `notes` list saying this in words.
198. **Counting by category** (completes item 94): N3 can add up to more than N1 and every output says so (dashboard note, report table note); N1 itself never counts an activity twice. N9 always adds up to the totals because each registration has exactly one category.
199. **Evaluation results across activities.** Forms are edited per activity, so rating questions are grouped by their **label, ignoring upper/lower case and outer spaces** ("Overall rating" and " overall RATING " are one row). The row shows the label used most often, the average (2 decimals) and the number of answers; rows follow the questions' form order. Only `rating` fields are averaged (answers are "1".."5", cast to a number by the database). **Limitation:** a question renamed in one form shows as a separate row. Text answers and comments are **never** shown in reports *(TBI question: should suggestions and comments appear, without names?)*. *(2026-11-17, item 401: answered by NAVIGATU at the UAT: the per-activity evaluation summary now lists the text answers and comments without names; the period reports are unchanged.)*
200. **Document compliance in SQL** (owner decision D1). N14, N15 and S3 are computed by the database with the **same rules** as `documents.services.document_checklist()` (item 124): only active required types; a type is complete when an Approved activity document exists (certificate evidence never counts); overdue means today (Manila) is after `start_date`/`end_date` + `due_offset_days` and nothing is Approved (on the due date itself it is not overdue). `document_checklist()` stays the only per-activity check used by the checklist, reminders and assistant. A parity test (`reports/tests/test_compliance_parity.py`) compares the two for every activity of a varied fixture, at four moments including Manila midnight, so they cannot drift apart.
201. **One source of truth.** The dashboard API, the PDF and the Excel file all call `reports/metrics.py`; the report content (`reports/content.py`) only reshapes its results into tables. Everything is counted by the database (aggregates, EXISTS and per-row subqueries, so counts never multiply; the period is a plain join condition on the activity, `metrics.in_period()`, not "activity IN (subquery)"); Python only loops over the small results (months, rating questions, the rows of the report). Tests: the dashboard and a report give identical numbers for the same filters, and the dashboard always runs the same number of queries whatever the amount of data (18 on 2026-10-23; the test allows 20).
202. **Dashboard API:** `GET /reports/api/dashboard/?start=YYYY-MM-DD&end=YYYY-MM-DD&activity_type=<id>`, any active staff member (`config.json.JsonResponse`). On the dashboard (only) a missing start or end takes January 1 / December 31 of the current Manila year. Dates follow item 213; an unknown activity type also gives 400. The response has `filters` (with `range_text`, the range in words), `today`, `cards` (key, label, scope, value and details such as `attended`, `attendance_rate`, `by_status`, `next`), `charts` (`per_month`, `by_type`, `by_category`, `average_rating_by_question`) and `notes`. Rates are fractions (0.85 = 85%). `python manage.py show_dashboard [--start --end --activity-type]` prints the same JSON in the terminal.
203. **Report types and sections** (owner decision D3: all five are built). `included_sections` is stored as a **JSON list** in the ERD's existing string column (no new column). Sections, in report order: `summary`, `ai_summary`, `activities`, `by_type`, `by_category`, `monthly`, `evaluation`, `documents`, `certificates`, `startups`. Each report type is a preset of default sections; the member may choose any sections (unknown keys are refused; duplicates removed):
     - **accomplishment** (main): summary, activities, by_type, by_category, monthly, evaluation;
     - **participation** (main): summary, by_category, monthly, startups;
     - **evaluation**: summary, evaluation, activities;
     - **document_compliance**: documents;
     - **certificates**: certificates (issued/emailed in the period, plus a "right now" table).

     The AI summary is never a default; it is added by choosing `ai_summary`.
204. **Who may do what** (owner decision D4, *pending TBI*): generating a report needs `can_create_activity`; listing, reading and downloading reports, and the dashboard, are open to any active staff member (reports hold totals, activity titles and startup names, never participant names or emails; consistent with item 120).
205. **Generating** (`reports/services.py`):
     - `request_report` checks the input, saves the GENERATED_REPORT row at once with an empty `file_path`, and queues the Django-Q2 task `reports.tasks.run_build_report` named `report-<id>` after the commit (a problem queueing never undoes the request). `generated_at` is the request time.
     - The task computes the content, adds the optional AI narrative, renders the file and saves it as `reports/<Manila year>/report-<id>-<type>.pdf|xlsx` (written to a temporary file and renamed into place, `config/files.py`, now shared with certificate PDFs). Two tasks for one report build it once. A failure is logged (report ID and error type only), leaves no file, and is raised so Django-Q2 records the task as failed.
     - **State is calculated, never stored:** `ready` (file saved), `failed` (the `report-<id>` task failed), otherwise `pending`.
     - **Reports are history:** never deleted (no delete anywhere; the Django admin is view only). Generating again creates a new row. "Figures as of" in the file is the day the task ran.
     - `python manage.py generate_report --type <type> --format pdf|excel --start YYYY-MM-DD --end YYYY-MM-DD [--activity-type --sections --member]` builds one right away through the same services (without qcluster); `--report <id>` builds a report that is still pending or failed.
206. **PDF** (WeasyPrint through `certificates.pdf.get_renderer()`, the Step 9 injection point; tests use FakeRenderer; only `data:` URIs may be fetched). *(Page and table layout replaced on 2026-11-16, item 385: landscape A4, fixed column widths, one Date column, never below 8 pt. The content, charts and escaping below are unchanged.)* A4 portrait, page numbers, a header with the period (in words and as dates, item 213), activity type, who requested it and when, then each section's tables. Tables with more than 6 columns use a smaller font. Charts (by type, by category, per month, rating per question) are single-series horizontal bars drawn as inline SVG, with the value written at each bar and the same numbers in the table below. All typed text is escaped (Django template autoescape; chart labels escaped in Python). Checked with real WeasyPrint on the development machine on 2026-10-23 (4-page accomplishment report, read-only from the development database).
207. **Excel** (openpyxl). *(Layout replaced on 2026-10-31, item 338: the "Filters" sheet is now "Report info", dates are `mmm d, yyyy`, the header is on row 7 under a title block. The numbers and the formula-injection rule below are unchanged.)* A "Filters" sheet (type, the period in words, period start and end as dates, activity type, who, when, counting rule, sections, and "Result: No conducted activities in this period." when that is the case), then one sheet per section. Numbers are real numbers (`#,##0`, `0.0%` for rates stored as fractions, `0.00` for averages), dates are real dates (`yyyy-mm-dd`), the first header row of every sheet is frozen (`A2`). **Formula injection:** every text value that starts with `=`, `+`, `-`, `@`, tab or carriage return is written with the text data type and Excel's quote prefix, so it is shown exactly as typed and never calculated (tested with an activity titled `=HYPERLINK(...)`: no `<f>` element anywhere in the file).
208. **AI narrative** (`reports/narrative.py`, section `ai_summary`), through the Step 8 gateway (`get_gateway().generate`, no tools):
     - Gemini receives **only** the report's computed figures, activity titles and dates, and lookup names (activity types, categories, document types, rating question labels), as JSON. Never participant names, emails, contact numbers, answers, comments, startup names or document text (columns marked `for_ai=False` are removed). The system instruction says the titles are data, not instructions, and to use only the given figures (one to three short paragraphs, at most 200 words, plain text; cut at 2,000 characters).
     - The exact text sent (system instruction and prompt) is saved in `ai_prompt`. When nothing is sent (no key) `ai_prompt` stays empty.
     - The section is labeled **"AI-generated summary based on the figures in this report; please review."** The tables always come from the database, never from the AI.
     - If Gemini fails or is not set up, the report is still generated; the section says "AI summary not included: <reason>" and a WARNING is logged with the error kind only. **The ERD has no column for the reason**, so it is recorded in the file and the log.
209. **Downloads** only through `/reports/<id>/download/` (any active staff member; always an attachment named `<type>-report-<start>-<end>-<id>.pdf|xlsx`, `X-Content-Type-Options: nosniff`; 404 while pending). Never through `/media/`. Someone not logged in, a startup session or a deactivated member is sent to the staff login.
210. **API** (`/reports/api/`): `GET options/` (report types with default sections and which are main, sections, formats); `GET /` list (newest first, 20 per page, `report_type` and `format` filters, with `state` and `download_url`); `POST /` request (`report_type`, `included_sections`, `date_range_start`, `date_range_end`, `activity_type`, `format`) → 202; `GET <id>/` detail (includes `ai_prompt`). Every report in the API shows `date_range_start`, `date_range_end` and `date_range_text` (item 213).
211. **Demo data** (`seed_demo`): seven more Conducted activities between about 235 and 45 days before the day it is run (several months, every activity type, one to three categories), with registrations, attendance, evaluations, approved/missing/returned documents, rotating linked startups, and certificates moved through the real services to Generated, Pending Sign-off or Approved (`retry_certificates` or qcluster then renders, issues and "emails" the approved participant certificates; demo addresses are only printed). If no certificate design is active, an invented "[DEMO]" design and signatures are added as the first active Admin (none: certificates are skipped with a message). One demo activity is titled `=HYPERLINK("https://example.com","Formula test")` on purpose, to show item 207. Run early in a year, some demo activities fall in the previous year (dates are relative to today). At the end it runs `ANALYZE` on the tables it filled: a table PostgreSQL last measured while empty is otherwise planned as "1 row", and the report queries then nested full scans of every table (seen twice in the test suite: one query ran for over 20 minutes; in a real database autovacuum corrects the statistics within minutes).
212. **Known limits:** a report whose task never runs (qcluster off) stays `pending` until qcluster runs or `generate_report --report <id>` is used. A report is not regenerated when data changes later (it is a record of what was reported); generate a new one instead.
213. **Date range rules** (owner request after Step 10):
     - **Reports need both dates** (`date_range_start`, `date_range_end`; also `--start` and `--end` in `generate_report`). A missing or empty one gives 400 "Required. Use the format YYYY-MM-DD, for example 2026-06-30." There is no default for reports (the dashboard keeps its current-year default, item 202).
     - **Format exactly YYYY-MM-DD** (checked by shape before parsing: Python alone would also accept `20260105` or the week date `2026-W02-1`). A date that does not exist (`2026-02-30`) gives "… is not a real date." Both apply to the dashboard too.
     - **End on or after start**, otherwise 400 naming both dates: "The end date (2026-06-01) is before the start date (2026-06-30). The end date must be on or after the start date." A one-day range is allowed. Both days are included.
     - **A range in the future (or any range without Conducted activities) is allowed.** The report then says **"No conducted activities in this period."** in its header and in every section built from the period's activities (summary, activities, by type, by category, per month, evaluation, documents, startups) instead of tables of zeros and empty charts; the Excel "Filters" sheet adds "Result: No conducted activities in this period." and those sheets hold only that sentence. The certificates section still shows its tables, because certificates count by the date they were issued or emailed (item 196, N16/N17). The AI summary is not requested (nothing is sent to Gemini; `ai_prompt` stays empty) and the section says why.
     - **The range in words** is in the report title, the PDF header (with the two dates in YYYY-MM-DD) and the Excel "Report info" sheet (item 338): "January 1 – June 30, 2026", "March 1 – 31, 2026", "December 1, 2025 – January 31, 2026", "March 5, 2026" (`reports.content.range_in_words`).
     - **The API lists the range used:** each report has `date_range_start`, `date_range_end` and `date_range_text`; the dashboard's `filters` has `start`, `end` and `range_text`.
     - **Maximum range: 10 years** (unchanged, item 202): it keeps the per-month table and the queries bounded and catches typing mistakes such as 2206 instead of 2026. *Owner decision pending: keep 10 years or lower it to 5.*

## Backend review, batch 1 (2026-10-23)

214. **Public registration rate limit and honeypot** (`registrations.services.guard_public_registration`, called only by the public JSON view, never by the service itself, so the startup portal, the Django admin and the demo seed are not limited).
     - At most `REGISTRATION_MAX_ATTEMPTS_PER_IP` (300) attempts per IP address and `REGISTRATION_MAX_ATTEMPTS_PER_EMAIL` (10) per email (lowercased, any activity) in `REGISTRATION_RATE_WINDOW_MINUTES` (60). **Every POST counts, refused ones too** (wrong answers, already registered, closed). The window starts at the first attempt. Over the limit: HTTP 429 "Too many registration attempts. Please try again in 60 minutes." *(Owner decision, 2026-10-23: the per-IP limit is high because many participants register from the same campus network, which has one public IP address; the per-email limit stops repeated attempts for one person. Still to validate with the TBI: open question, CLAUDE.md.)*
     - The counters live in the same database cache as the login lockout (item 54), so every server process shares them. *(Batch 2: the counting code is the shared limiter `config/ratelimit.py`, also used by the forgot-password form, item 227.)*
     - **Honeypot:** the form has an extra field `website` (named in the GET response as `honeypot_field`). The frontend hides it from people with CSS (not `type="hidden"`), so people leave it empty while simple bots fill it in. A filled-in honeypot gets 400 "Your registration could not be accepted." and nothing is saved.
     - Both are logged as WARNING on `accounts.security` with the IP address only, never the email.
215. **The client's IP behind a reverse proxy.** `TRUSTED_PROXY_HEADER` (`.env`, empty by default) names the header the proxy fills in, for example `X-Forwarded-For` or `X-Real-IP`. When it is set, `config.network.client_ip()` uses the **last** address in that header (the proxy appends the address it saw; earlier ones come from the client and can be made up; one proxy is assumed), and falls back to `REMOTE_ADDR` when the header is missing or not an IP address. It must stay empty without a proxy, because any client can send the header. Without it, everyone behind a proxy would share the proxy's address and one person's failed logins would lock everyone out.
216. **Registration emails are background tasks** (Django-Q2, `registrations/tasks.py`): the confirmation (item 109) and the evaluation links (item 115). The tasks never raise; a failure is logged as ERROR on `registrations` with the registration ID and the error type only (never the address or the link). `qcluster` must be running for these emails to go out. Tests run the tasks straight away (root conftest `registration_emails_run_now`). *(2026-11-06:)* evaluation links are sent by `services.deliver_evaluation_link`, which locks the registration row while sending and records sent/failed on it; the automatic first sending runs inside the 5-minute job (already in qcluster), with SKIP LOCKED so two runs never email the same person (item 348).
217. **Fields for the frontend.** Activity list rows and the detail have `activity_type_id` next to the name. The detail has `registration_form_id` and `evaluation_form_id` (null until the form exists).
218. **Lookups endpoint** `GET /api/lookups/` (any active staff member): `activity_types`, `participant_categories`, `document_types`, `activity_statuses`, `document_statuses`, `roles`, each a list of `{"id", "name"}`. Activity and document types are **active only** (an inactive one cannot be chosen); the other tables have no `is_active`, so every row is listed. Statuses are in workflow order, everything else by name.
219. **Evaluation results of one activity** `GET /reports/api/activities/<id>/evaluation/` (any active staff member, like the dashboard): `registered`, `attended`, `evaluations`, `evaluation_response_rate` (a fraction) and, with the rules of item 199 (`reports.metrics`, the same grouping function as the reports), each rating question's average and answer count plus the overall average. Any activity status (evaluations open on the end date, item 113). Only rating questions: **never comments, text answers or names.** *(Unchanged: this API and the Evaluation tab. Text answers without names are in the evaluation summary file, item 401; one participant's answers with the name only for can_create_activity, item 402.)*
220. **Strict true/false values** (`config.parsing.parse_bool`): request values that mean true or false accept only JSON `true`/`false` or the text "true"/"false" (any case, outer spaces ignored); anything else (`1`, `"yes"`, `"on"`, a list) is a 400 naming the field. This covers attendance `attended` (now required), the registrations list `attended`, form field `is_required`, the repository `searchable`, the startups list `is_active`, the template upload's `activate`, and the notifications `unread` filter (**changed from `unread=1` to `unread=true`**, staff and startup portal). Before, the text "false" counted as true for attendance.
221. **Members API input.** `name` and `email` must be text and `role_id` a whole number (not `true`, `"3"` or `1.5`); anything else is a 400 with a clear message instead of a server error.
222. **TBI_MEMBER email unique ignoring case** (migration `accounts/0002`): a unique index on `LOWER(email)`, like STARTUP (item 138). The migration first looks for emails that differ only in case and, if any exist, stops with their member IDs and changes nothing. Development database (2026-10-23): 7 members, no case duplicates; migrated.
223. **NOTIFICATION.activity stays nullable** (owner decision; the ERD does not mark it): every notification created today names its activity, but future startup notifications (Step 7 portal) may have no activity.
224. **Master data and role flags stay in the Django admin** (owner decision): participant categories, activity types, document types, default form fields and the two role flags are managed there only. The Admin Console (frontend) covers members, the certificate design and signatories, and the startup sync/set-password actions. *(Exception since 2026-11-05, item 344: the Admin Console roles screen switches `can_approve_documents` on or off; it is also editable in the Django admin.)*

## Backend review, batch 2 (2026-10-23)

225. **JSON errors for the API.** For `/api/` URLs (`/api/...` and `/<app>/api/...`, `config.json.is_api_path`): not logged in (also a deactivated member, or a startup session on a staff API) gets **401 `{"error": "Login required."}`** instead of a redirect; a member who must still change the first-sign-in password gets 403 `{"error": "Change your password first (first sign-in)."}`; 404 is `{"error": ...}` (our own messages such as "Activity not found.", otherwise "Not found.", never Django's list of URLs); 500 is `{"error": "Something went wrong on the server. Please try again later."}` (the error itself is only in the log). HTML URLs (pages, file downloads, the Django admin) keep redirecting to the login page and keep Django's error pages. The 404 and 500 handlers apply only with DEBUG off; with DEBUG on Django shows its debug pages.
226. **Admin password reset** (`accounts.services.reset_member_password`, `can_manage_users`): a new 16-character temporary password, emailed like the invitation ("Your CSU-TBI Track password was reset"), hashed, never logged or returned by the API; `must_change_password` is set, and the member's open sessions end (the password hash changed). Refused for your own account (use Change password) and for a deactivated member (reactivate first). Admin Console API `POST /accounts/api/members/<id>/reset-password/` and a Django admin action. Closes the open item of item 61.
227. **Forgot password (staff, self-service):** Django's own password-reset views at `/accounts/password/forgot/` (linked from the login page). The answer is always the same ("check your email"), so it never reveals whether an email belongs to a member; only an active member with a usable password gets an email (startups never: they use the set-password link, item 143). The link (`SITE_URL` + `/accounts/password/reset/<uid>/<token>/`) works **once** and expires after `PASSWORD_RESET_TIMEOUT` (1 hour). The new password must pass the same `AUTH_PASSWORD_VALIDATORS`; a password chosen this way clears `must_change_password`. The pages are temporary plain forms until the frontend phase. **Rate limited** with the shared limiter (`config/ratelimit.py`, the same one as public registration, item 214) and `client_ip()` (item 215): at most `PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL` (**5**) requests per email (lowercased) and `PASSWORD_RESET_MAX_REQUESTS_PER_IP` (**20**) per IP address in `PASSWORD_RESET_RATE_WINDOW_MINUTES` (**60**). Every request counts, also for unknown emails. Over a limit the answer is **the same "check your email" page**, so it reveals neither the limit nor whether the email exists; no email is sent, and a WARNING is logged on `accounts.security` with the IP address only, never the email (`accounts.services.password_reset_allowed`). This stops anyone from flooding a member's inbox with reset emails.
228. **Open activities list** (`registrations.services.open_activities`): the same rule as the registration page (Scheduled, deadline not passed, slots left, a registration form), soonest first. Public: `GET /api/activities/open/` with only the public fields of `/api/register/<id>/` (no form, never who registered). Startup portal: `GET /startups/portal/api/activities/` with the same rows plus `already_registered` (the startup's registrations, item 144).
229. **Sessions** (staff and startup portal) last at most **8 hours** after logging in (`SESSION_COOKIE_AGE`) and end when the browser is closed (`SESSION_EXPIRE_AT_BROWSER_CLOSE`), for shared office computers. With DEBUG off, the session and CSRF cookies are **HTTPS only** (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` follow DEBUG): the real server must use HTTPS or logging in will not work. The CSRF cookie stays readable by JavaScript (the frontend sends it with HTMX requests).
230. **Assistant time limit.** The assistant still answers inside the web request (synchronous), but one question has at most `ASSISTANT_QUESTION_TIMEOUT_SECONDS` (**90**) in total: each Gemini request gets at most the time left (and at most `ASSISTANT_REQUEST_TIMEOUT_SECONDS`), a retry wait that would end past the limit is not started, and a round that ends late stops the question. The API then answers 503 `{"error": "The AI assistant took too long to answer (more than 90 seconds). Please try again in a moment.", "kind": "timeout"}` and nothing is saved. The web server's own timeout must be longer (for example gunicorn `--timeout 120`). Indexing (background) has no such limit. **Future improvement:** answer in a Django-Q2 background task and let the page poll for the answer, so no web request waits on Gemini.
231. **Real WeasyPrint in one test** (`certificates/tests/test_real_weasyprint.py`, marker `real_weasyprint`): a `data:` image is embedded, while a local `file://` image and an `http://` address are refused (the PDF is still made, without them); a control shows that plain WeasyPrint would load the local file. Skipped automatically when Pango/GObject are missing. Every other test still uses the fake renderer.
232. **Static rule tests** (`accounts/tests/test_project_rules.py`): only `config/json.py` imports Django's `JsonResponse`, and only `assistant/gemini.py` imports `google.genai` (like the Navigatu test). The unused `reports.services.ReportError` was removed.

## Frontend F1: design foundation, layout, login pages, dashboard (2026-10-24)

233. **Stack and self-hosting.** Django templates + HTMX 2.0.11 + Alpine.js 3.17.4, Tailwind CSS v4.3.3 (standalone CLI, no Node.js), Inter 4.1 (variable font) and Lucide 1.52.0 icons (inline SVG through `templates/components/icon.html`). Everything is served from `static/` (versions, sources and licences: `static/vendor/VERSIONS.md`); a test fails if a template loads a script or stylesheet from another site. Pinned versions were at least two weeks old when chosen. The favicon (32 px), apple-touch icon (180 px) and sidebar image (96 px) were resized from `docs/design/logo.png`.
234. **Tailwind.** v4 has no `tailwind.config.js`: the design tokens are the `@theme` block of `frontend/tailwind/app.css` (sidebar #22378F, primary #1D4ED8, KPI teal #0D9488, accent #14B8A6, page #F4F6FB, card border #E6EAF2, text #0F172A, muted #475569, danger #DC2626, status colours). `python manage.py tailwind install` downloads the pinned CLI into `tools/` (gitignored) and refuses it unless its SHA-256 matches the one written in the command; `tailwind build` writes the minified `static/css/app.css`; `tailwind watch` rebuilds on every change. Tailwind only scans the folders listed with `@source` (every app's `templates/`, plus the two Python modules that choose classes): a new app's templates folder must be added there.
235. **The built CSS is committed** (owner question). The app then runs on any machine (a teammate's laptop, the defense PC, the server) without downloading the Tailwind binary, the file is small (about 34 KB), and the same input always gives the same output. Cost: noisy diffs and the risk of a stale file, so run `tailwind build` before committing template changes; a test fails if `app.css` is missing or lacks the tokens.
236. **Contrast** (WCAG, checked 2026-10-24): body text 17.9:1, muted text 7.6:1 on white and 7.0:1 on the page background, links and primary buttons 6.7:1, white on danger 4.8:1, sidebar text 10.4:1, sidebar labels 7.0:1, every badge 5.5:1 or more, input placeholders 4.6:1. **The KPI teal (#0D9488) is 3.7:1 on white**, so it is used only for the large KPI numbers (36 px bold; large text needs 3:1). The accent teal (#14B8A6, 2.5:1) is never used for text (donut, active marker only).
237. **The dashboard page uses the dashboard API's numbers.** `reports.views.dashboard_page` calls `reports.services.dashboard()` (the same function as `/reports/api/dashboard/`, so the same metric queries and scopes) and only reshapes the result (`reports/dashboard_page.py`). A test compares the page with the API for several filters.
238. **"Upcoming activities (next 30 days)"** (owner decision): S2 now also returns `within_30_days`: Scheduled activities starting from today to today + 30 days (both included, Manila), respecting the type filter. It is a new detail of the existing S2 card (also in the API); S2's value is unchanged. The "Upcoming activities" list is S2's next three (Scheduled only, no Drafts), with `registered_count()` against `max_slots`.
239. **Documents card** (owner decision): a member with `can_create_activity` sees "Documents to review" = their own review queue (`documents.services.review_queue`: waiting for a reviewer, never their own uploads), with the age of the oldest; this count ignores the activity type filter, like the sidebar badge. A member without it sees "Documents waiting for review" = S4 (everyone's, respects the type filter) and no "Review" action. *(Since 2026-11-05, item 344: the flag is `can_approve_documents` and the queue includes my own uploads.)*
240. **Open for registration** = `activities.services.open_for_registration()` (the one rule, also behind `open_activities()`, item 77), narrowed to the type filter like the other "now" cards.
241. **Needs attention**, in this order: (1) overdue required documents (exactly the S3 pairs, red; "Upload" when nothing is uploaded, otherwise "Open"); (2) required documents **due soon** (owner request): nothing uploaded and due within `DOCUMENT_REMINDER_DAYS_BEFORE` days, the same rule as the "due soon" reminder (`documents.services._reminder_kind`), shown in amber; (3) documents waiting for my review (reviewers only); (4) certificates waiting for sign-off (S5) with the activities that have the most, "Review" only with `can_create_activity`. Documents are grouped by type ("Request Letter is overdue for 10 activities", two titles and "and N more"); a single activity gives a sentence ("X is missing the Invitation Letter · Overdue since Oct 8"). The first five items are shown, the rest behind "Show N more". Read-only helpers: `metrics.document_attention()` (tests: overdue rows = S3; due-soon and overdue rows = what `send_document_reminders` creates) and `metrics.certificates_waiting_by_activity()`.
242. **Recent changes** = the latest 5 ACTIVITY_AUDIT_LOG rows across all activities (`activities.services.recent_audit_entries`), ignoring the filters (it is a feed). `activities.formatting.audit_sentence` writes them from the stored readable values: "X created T as Draft", "X changed the status of T from Draft to Scheduled", other fields "X changed the venue of T to TBI Lounge" (like the design), "X changed the description of T" (no value), resource persons added/removed. Values longer than 60 characters are cut with "…" (the audit log keeps them whole). The time is relative ("just now", "10 min ago", "3 hours ago", "yesterday", then the date).
243. **Charts** are plain SVG/CSS (no chart library), each with a visually hidden table of the same numbers. The donut shows attendees per registration category (N9, adds up to N6); categories are master data, so the colours repeat after eight. "Activities by status" is S1; "Conducted by type" is N2 without the types that have none. "No conducted activities in this period." replaces empty charts (item 213).
244. **Dashboard filters**: quick choices This month / This quarter / This year (default) / Last year (Manila calendar) and a custom range (two dates, item 213 rules); a quick choice wins over typed dates. An invalid custom range shows the error and this year's numbers. Every change sends one HTMX request; only the part below the filters is replaced and the URL keeps the filters (`?range=...&activity_type=...`), so reloading or sharing the link shows the same view.
245. **Pages of later phases** (Activities, New activity, Documents, Certificates, Startups, Reports, AI Assistant, Admin Console) already have their final URL names and show one "coming soon (F2–F6)" page (`config.views.COMING_SOON_PAGES`; New activity needs `can_create_activity`, Admin Console `can_manage_users`). The search box sends `q` to the activities list. All "Needs attention" actions point to the Documents / Certificates pages for now.
246. **Errors and HTMX.** HTML URLs get `templates/errors/403.html` (with the reason), `404.html` and `500.html` (rendered without the request); `/api/` JSON errors are unchanged. An HTMX request without a login gets **401 with `HX-Redirect`** to the login page (`next` = the page the member was on, from `HX-Current-URL`, only if it is this site); during the first-sign-in change an HTMX request gets `HX-Redirect` to the change-password page. A failed HTMX request shows a toast.
247. **CSRF for HTMX**: the staff layout sets `hx-headers` with the token, so every HTMX request inside it sends `X-CSRFToken`. It is **not** on `base.html`: the token changes on every request, and the forgot-password "check your email" page must look exactly the same whether or not the rate limit was reached (item 227; the existing test caught this).
248. **Sidebar** items come from one list (`config.frontend`), each with an optional permission; today only Admin Console needs one (`can_manage_users`), because every other module is open to any active staff member. The Documents badge is the member's review queue. The member card at the bottom links to Change password (the design has no profile menu); Log out is a POST form. Below 1024 px the sidebar is hidden behind a menu button.
249. **Not a bug after all:** the F1 plan said `MustChangePasswordMiddleware` compared paths with the relative `STATIC_URL` ("static/"). Django turns a relative `STATIC_URL` into "/static/" when the setting is read, so the check was already right; only a comment was added.
250. **Development tools** are in `requirements-dev.txt` (pytest, pytest-django, Playwright 1.63.0 for screenshots); `requirements.txt` holds only what the app needs to run.
251. **Sign-in pages redesigned** (owner request, `docs/design/login.png`): login, forgot password ("check your email" included), reset password (with "saved") and the first-sign-in change use `templates/layouts/auth_split.html`: the blue panel (logo, headline, "Sign in to continue with the Technology Business Incubator at Caraga State University.", the illustration) and a card on the right; only the card's heading, helper text and form change. Below 1024 px the illustration is hidden and the panel is a short header above the card. The illustration is drawn inline from `docs/design/login-illustration.svg` (see item 253). The footer year comes from the server (`{% now "Y" %}`, Manila). The password fields have a show/hide button (Alpine; `aria-label` "Show password" / "Hide password", `aria-pressed`). `components/form_field.html` gained optional `icon`, `placeholder`, `reveal` and `aside_url`/`aside_label`. Behaviour is unchanged (same views, messages, lockout, rate limit, autocomplete, CSRF). Contrast on the panel: white 10.4:1, the light sentence 8.6:1, the teal line (#5EEAD4) 7.1:1, and at least 5.6:1 where text crosses the lighter circle. The error pages keep the centered `layouts/auth.html`.
252. **University name:** Caraga State University. The only wrong occurrence ("Cagayan State University") was the footer of `templates/layouts/auth.html`, written in F1; nothing else in templates, docs, settings, emails, seed data or tests mentioned it.
253. **Sign-in panel, final version** (owner request): (a) the info note "For TBI staff. Startups sign in through the startup portal." is removed from every sign-in and password page; **in phase F6 a small link goes under the card: "Are you a startup? Sign in to the startup portal"**. (b) The illustration is inline (`templates/partials/login_illustration.html`, with the design's `role="img"` and `aria-label`) so its parts can be animated; `docs/design/login-illustration.svg` stays the unchanged reference and there is no copy in `static/img/`. Its two faint background circles are left out: the bottom-right one was cut off by the viewBox and looked like a leftover block behind the documents; only the panel's own two circles remain, as in `docs/design/login.png`. (c) **Animations, CSS only** (`frontend/tailwind/app.css`, "Sign-in panel animations"; no library, no JavaScript): on load the headline lines fade up 80 ms apart, then the dashboard, progress ring, calendar and documents cards rise in 120 ms apart; the pulse line draws itself, the ring fills to 75%, the two check marks and then the "Approved" badge pop in; the card fades up once. Entrance ends at about 1.45 s. Afterwards only two things loop, both in the left panel and never near the form: the cards float 4 px (6–7.5 s, out of step) and the dashed connector flows slowly (the owner asked for both). Only transform, opacity and stroke-dashoffset change, so nothing shifts the layout. Every element's natural state is its final state, so `prefers-reduced-motion: reduce` simply turns every animation off (`.anim`, `.rise`) and the finished picture shows at once. The same panel is used on all sign-in and password pages (below 1024 px the illustration is hidden).
254. **Hover effects** (owner request), CSS only, short (150–250 ms), using only transform/translate, shadow and colour: the illustration's cards lift 6 px with a deeper shadow (to allow it, the cards' entrance animation now uses fill-mode `backwards`, so after the entrance they are back in their natural state); buttons rise 1 px with a shadow (pressed: back down); a button's arrow slides 4 px forward (`.btn-arrow`); KPI cards lift 2 px with a shadow (`.hover-lift`); the "Needs attention", upcoming and recent-change rows get a stronger border and a light shadow (`.hover-row`); sidebar icons grow to 110%. Under `prefers-reduced-motion: reduce` nothing moves (Tailwind `motion-safe:` and the reduced-motion block); colour and shadow changes stay, because they are not motion. The sign-in card (owner request) lifts 4 px with a deeper, slightly blue shadow and a light-blue border (`.auth-card`); it moves only when the mouse enters or leaves it, never while typing, and stays still with reduced motion. For this the entrance (`.rise`) also uses fill-mode `backwards`.
255. **Sign-in panel gradient** (owner request: #22378F and #60CDEE). Desktop (1024 px and wider): `linear-gradient(160deg, #22378F 0–36%, #60CDEE 100%)`, dark behind the logo and text at the top left, light cyan behind the illustration at the bottom right; white text on #60CDEE would be only 1.9:1, so no text sits on the light end. Below 1024 px the panel is a short header full of text, so its gradient stays darker (#22378F to #2F5BAE). The two decorative circles became translucent (white 7%, dark navy 25%) so they follow the gradient. Contrast measured on the rendered page (worst pixel behind each text, 2026-10-24): at 1440 px the logo name 10.4:1, the headline 7.2:1 or more, the teal line 4.4:1 (large text, needs 3:1), the sentence 6.3:1; at 390 px the sentence 5.0:1 and the teal line 4.5:1. The dashboard sidebar keeps the solid #22378F.
256. **New logo** (owner request, 2026-10-25): `docs/design/logo.png` is now a white and teal mark on a transparent background. White would vanish on light backgrounds (browser tab, error pages), so the three images in `static/img/` (favicon 32 px, apple-touch icon 180 px, sidebar/sign-in/error image 96 px) are the mark cropped to its content, centred at about 72% of the size on a solid brand-blue (#22378F) square, resized with Pillow (LANCZOS). Templates are unchanged (same file names; CSS rounds the corners).
257. **Sign-in pages: full-screen gradient and glass card** *(Calmer colours since 2026-11-20, item 411: the gradient is #1F2F6E → #41598C instead of #22378F → #60CDEE and the glass is white 80%; layout, card and hover unchanged.)* (owner request, 2026-10-25; replaces the blue panel and grey right side of items 251 and 255; `docs/design/login.png` is the new reference, a 1440×900 screenshot). `layouts/auth_split.html`: the whole page is one gradient (`.auth-bg`): from 1024 px `linear-gradient(135deg, #22378F 0–22%, #60CDEE 100%)`; below 1024 px the page is tall and narrow, so it runs at 170° with the dark part to 34% (the sentence stays on the dark part). Two columns from 1024 px: logo, headline, sentence and illustration on the left; the card on the right, vertically centred. Four soft white shapes (5–7% white) spread over the screen; checked with Playwright that none overlaps a field, link or button of the card at 1024–1440 px and at 390 px (two are hidden below 1024 px for that reason). **Card** (`.auth-card`): white 70% with `backdrop-filter: blur(20px)` (and `-webkit-`), 1px white 50% border, radius 24px, soft shadow; where backdrop-filter is not supported (`@supports`), white 92%. Inputs: white 85%, the same border plus a 1px shadow, focus border #1D4ED8 and a 3px #1D4ED8 ring at 35%; error state keeps the red border. Text in the card: #0F172A, helper text #334155 (`text-slate-700`); on the glass, links use #1E40AF and placeholders #475569 (one shade darker than elsewhere) because the glass is darker than white. **Footer** "© year CSU Technology Business Incubator": #0F172A on a small white 60% pill (`.auth-footnote`), because the gradient behind it is mid-blue at some widths, where neither white nor dark text passes on its own. **Fit**: the illustration's height is `clamp(200px, 100svh − 412px, 440px)`, so the login page fits without scrolling at 1024×768, 1280×720, 1366×768 (illustration 356 px) and 1440×900 (440 px), also with the "Invalid email or password." alert; the forgot / check-email / reset pages fit too. The first-sign-in **change-password** page still scrolls at 768 px: its card alone is about 800 px (Django's list of password rules under the new-password field). Below 1024 px the illustration is hidden and the page may scroll. **Contrast** (worst pixel behind each text on the rendered page with the text hidden, Playwright, 2026-10-25), at 1366×768 / 1440×900 / 1024×768 / 390×844: logo name 10.4 / 10.4 / 10.4 / 8.7; headline white lines at least the logo name's value, teal line (#5EEAD4, large text, needs 3:1) 5.8 / 6.2 / 5.0 / 5.8; the Caraga State University sentence (#DBEAFE) 5.3 / 5.8 / 5.4 / 7.1; card heading 11.9 / 11.9 / 11.6 / 10.5; card helper text 7.0 / 7.0 / 6.8 / 6.2; labels 12.1 / 12.2 / 11.8 / 11.2; links 6.4 / 6.4 / 6.2 / 5.9; placeholders 7.2 / 7.2 / 7.2 / 7.1; footer 10.1 / 10.1 / 9.9 / 10.6 (lowest on any sign-in page at 390 px: 9.2). The input border (white 50% on the glass) is decorative: the field is recognised by its lighter fill, label and icon. The animations, reduced-motion support, card hover lift, show/hide password button and all behaviour are unchanged.
258. **Staff app shell and dashboard restyle** *(Still valid, with calmer colours since 2026-11-20, item 411: the frame gradient is #1F2F6E → #41598C instead of #22378F → #60CDEE, the welcome banner and the spotlight are white cards, the page title moved from the top bar into a page header.)* (owner request, 2026-10-25; style reference `docs/design/inspo-dashboard.jpg`, only its frame, rounded panel, hero banner, soft cards and right column; `docs/design/dashboard.png` is the new reference, a 1440×900 screenshot). Only appearance and layout changed: the same services, numbers, permissions, filters and HTMX behaviour. **Shell** (`layouts/app.html`): the whole screen is the login gradient (`.app-bg`: `linear-gradient(135deg, #22378F 0–30%, #60CDEE 100%)`). 160° was tried on paper: on a 1366×768 screen the bottom of the sidebar would sit at about 60% of the gradient, where white text is only about 3.4:1; at 135° with the dark part held to 30% the whole sidebar stays dark and the light end is at the bottom right, behind the panel. The sidebar sits directly on the gradient (no colour of its own on desktop; below 1024 px the slide-in sidebar keeps the solid #22378F because it covers the page): white text and icons, active item a white 18% pill with a 3px white indicator on the left, hover white 10%; the WORKSPACE label (#C7D2FE), Documents badge and ADMIN section are kept. The member card is frosted glass (`.sidebar-glass`: white 12%, a dark navy 18% tint, 1px white 25% border, 12px blur) with Log out below it. The content is a light panel (#F7F9FC, `bg-panel`) with 28px corners, 16px from the top, right and bottom edges, a soft shadow; from 1024 px only the panel scrolls (the frame and sidebar stay put; the panel is `position: relative` so screen-reader-only text stays inside it) and the top bar (title, date, search, Ask AI, bell) stays at the panel's top. Buttons are pill-shaped in the shell (`.app-shell .btn`; the sign-in pages keep their own). Cards: 24px corners, KPI cards 20px, shadow 0 10px 30px rgba(34,55,143,0.08), lift 2px with a deeper shadow on hover (`.hover-lift`, movement only under `motion-safe`). **Dashboard** (≥1280 px): main column about 2/3 (welcome banner, Overview with the filters as pill selects, KPI cards in one row of five, Needs attention) and right column about 1/3 (Next activity, Upcoming activities, Recent changes), then the three chart cards in one row across both; below 1280 px everything stacks. `#dashboard-body` is `display: contents` so the filtered parts sit in the page grid; the loading dim moved to its children. **Banner**: "Good morning/afternoon/evening" by Manila time (before 12:00 / before 18:00 / later; `dashboard_page.greeting()`), the member's first word of the name, a summary from the page's own numbers (Needs attention items and the Upcoming card's "within 30 days"), refreshed after a filter change with an out-of-band HTMX swap (`partial` in the view's context), "Review items" (to `#needs-attention`) and "New activity" (only with can_create_activity). The illustration is the inline sign-in illustration (`partials/login_illustration.html`; there is no `static/img/login-illustration.svg`, item 253), still (no animations) and hidden from screen readers. **KPI cards**: a Lucide icon on a light tint (blue calendar, amber award, teal file-check, violet users, green user-plus), the label, the number and the existing badge. **Needs attention**: a severity icon tile (red triangle = overdue, amber clock = due soon, blue file = review/sign-off), same items, texts and links. **Next activity**: the first upcoming Scheduled activity (`upcoming[0]`): dates, title, venue, type and category tags, a registration ring (registered / max slots, `role="img"` with the numbers in its label) and "Open activity", which goes to the Activities list searched by the title until the activity page exists (phase F2). The list under it shows the next ones (UPCOMING_SHOWN is now 4: the spotlight plus three) with dark blue gradient date tiles and a thin progress bar. **Charts**: same data and text tables; the donut is thicker (7 units) with rounded ends, each segment drawn shorter by half the stroke plus half a 1.5-unit gap at each end (only when there are two or more segments); bars and legend dots are rounded. **Recent changes**: a timeline with the member's initials (`config.frontend.initials`, shared with the sidebar), the sentence and the relative time; long unbroken words wrap. **Contrast** (worst pixel behind each text, Playwright, 2026-10-25, at 1366×768 / 1440×900): sidebar — active item "Dashboard" 6.35 / 6.35, other items and logo name 10.4, WORKSPACE 7.0 / 7.0, Documents badge 8.7, member name 7.1 / 6.8, role 7.0 / 6.8, Log out 8.0 / 7.6; banner (gradient held at #1D4ED8 to 60%) — greeting 6.7 (large), summary 5.7 / 5.7, "Review items" 6.7, "New activity" 5.0 / 5.0; spotlight texts 6.3 or more; date tiles 7.5 or more. Fit: at 1366×768 the sidebar, the banner and the KPI cards are visible (cards end at 645 px of 768) with no horizontal scroll. The error pages keep `layouts/auth.html` (they cannot use the staff shell: the 500 page is rendered without the request); the coming-soon pages use the new shell.
259. **Dashboard polish** (owner request, 2026-10-27; `docs/design/dashboard.png` replaced by a new 1440×900 screenshot). Changes item 258's dashboard layout from 1280 px: row 1 is the welcome banner (2/3) beside the **Next activity** card (1/3); Overview + filters and the **KPI cards** (one row of five) span the full width; then Needs attention (2/3) beside Upcoming activities + Recent changes (1/3); then the charts row. When there is no upcoming Scheduled activity (also after an activity-type filter), the place beside the banner shows a "Nothing scheduled yet" card (with "New activity" for can_create_activity) so the row never has a hole; the Upcoming card keeps its "No upcoming activities" empty state. Checked with Playwright at 1024, 1280, 1366 and 1440 px: every KPI label is on at most 2 lines and every badge on 1 line (badges never wrap, `whitespace-nowrap`); "sign‑off" in the label uses a non-breaking hyphen (U+2011) so it never breaks as "sign-" / "off". Shorter badges: "30 days" (was "next 30 days"), "oldest 4 d" / "oldest today" (was "oldest: 4 days"). **Dates**: the Next activity card and the upcoming rows use compact dates from the `date_span` filter (`config/ui_tags.py`): "Nov 6, 2026", "Oct 30–31, 2026", "Oct 30 – Nov 2, 2026", "Dec 30, 2026 – Jan 2, 2027"; the rows now show the date as visible text (before: screen-reader only) with the status badge on the same line. **Upcoming titles** wrap to at most 2 lines (`line-clamp-2`, long words break) with the full title in a `title` tooltip. At 1366×768 the KPI cards end at 686 px of 768.
260. **Faster tests** (owner request, 2026-10-27). pytest-xdist 3.8.0 runs the suite with **2 workers** (`pytest.ini`: `-n 2 --reuse-db`), each with its own test database (`test_<name>_gw0`, `_gw1`), kept between runs (`pytest --create-db` after a migration change). Measured on the development laptop (4 CPUs, 7.9 GB RAM, Docker sharing both): serial 23 min 53 s; 4 workers 24 min 33 s (the laptop ran out of memory and swapped); 2 workers **8 min 12 s**. Every test now gets an empty in-memory cache (root `conftest.py`, `fresh_cache`): the database cache table is not a Django model, so a transactional test's flush never emptied it and lockout counters leaked between tests (the slowest lockout test went from 47 s to about 1 s). Test DOCX/XLSX files are zipped with a fixed date, because their bytes are part of the test IDs and every worker must collect the same IDs. The `seed_demo` tests (about 20 s each, about 4,700 queries) were left as they are.

## Phase F2a: Activities pages (2026-10-27)

261. **Pages and URLs** (`activities/page_views.py`, thin; `activities/pages.py`, display only): `/activities/` (list + calendar), `/activities/new/`, `/activities/<id>/` (tabs), `/activities/<id>/edit/`, POST `/activities/<id>/status/`, POST `/activities/<id>/resource-persons/add/` and `.../<person>/remove/`, GET `/activities/resource-persons/options/` (HTMX pick list). Viewing needs an active staff login; every change needs `can_create_activity` (`permission_required`, 403 otherwise). The coming-soon entries for Activities and New activity are removed. The dashboard's "Open activity" and its upcoming rows now open the activity page (before: the list searched by the title, item 258).
262. **List.** KPI cards = `activity_stats()` (total, open for registration, conducted this month, conducted in total). Filters (search by title `q`, type, status, category, From / To dates, sort) go to `list_activities()` unchanged; 20 rows a page; the URL keeps them and HTMX replaces only the results. A filter value that cannot be read (not a number, a bad date, "To" before "From", an unknown sort) is ignored with an amber note, not a 400 page. Sort labels: Start date newest / oldest first, Title A to Z / Z to A, Most / Fewest registered, Recently / First created (exactly `SORT_OPTIONS`; a test checks it). The type filter also lists inactive types, marked "(inactive)", so old activities can still be found. *(2026-11-08: "Grouped (recommended)" added as the first, default option, item 353.)*
263. **Calendar** = `activities_between()` for the weeks shown (Monday first): every status, including Draft, and **no filters** (the filters belong to the list view and are hidden in the calendar). A multi-day activity appears on each of its days; up to 3 chips a day, then "+N more" (the list filtered to that day). Chip colours = the status badges (Draft also gets a thin outline, otherwise it is hard to see on white). Previous / next month and "This month" swap only the calendar.
264. **Create and edit form.** Sections Details / Schedule / Registration / Budget, every field `create_activity` / `update_activity` accept. The type list offers active types plus the activity's current one. Dates are `<input type="date">`; deadlines are `datetime-local`, read and shown in Manila time (`TIME_ZONE`). A datetime-local input has no seconds, so an untouched deadline that has seconds in the database keeps its stored value (`pages.keep_stored_minutes`); otherwise it would count as changed and be re-checked against "not in the past". Every error comes from the form's type conversion or from the service and is shown under its field (`aria-invalid`, `aria-describedby`), with a summary on top; the first field with an error gets the focus. The service checks required fields and categories first and stops there, so date errors appear once those are fixed (same as the admin and the API). After saving: the activity page with a toast ("… was created as Draft." / "Changes saved.").
265. **Conducted = read-only page.** Its edit URL shows the details with "This activity is Conducted, so it is read-only." and no form; a POST anyway gets the service's refusal. The activity page hides Edit, Change status and the resource-person controls for Conducted.
266. **Activity page.** Header: title, status, type and category tags, compact dates, time, venue, and the actions the member may take. Tabs: Overview and History (built now); Forms, Registrations, Evaluation (phase F2b) and Documents, Certificates (phase F4) show a placeholder that names the phase. Tabs are links (`?tab=`), swapped by HTMX with the URL kept; an unknown tab shows the Overview. History = `get_audit_log()` (newest first, 20 a page) as `audit_sentence()` sentences.
267. **Registration state on the activity page** = `registration_closed_reason()` (the one rule), with `registered_count()` / `slots_remaining()`. "Copy registration link" (the public `/register/<id>/` page URL) and the link field are shown **only while registration is open** (reason is None), which also means only while Scheduled. A Scheduled activity whose registration is closed shows the reason as an amber notice in the header and in the Overview; when the reason is the missing registration form (`registrations.services.REGISTRATION_FORM_NOT_READY`, now a named constant) staff see "Registration form not created yet. Create it in the Forms tab (phase F2b)." instead (owner request). Other statuses show the reason as plain text, no notice.
268. **Status change**: a modal (native `<dialog>` with `showModal()`: focus stays inside, Escape closes it, focus returns to the button) listing only `allowed_next_statuses()`; choosing one and pressing "Change status" is the confirmation. Messages are the admin's: `Status of "T" changed from A to B.` / `Status not changed: <reason>` (as toasts).
269. **Resource persons** (Overview): add someone from the list (HTMX search by name, `services.search_resource_persons`, 20 shown; the API uses the same function, 100) or a new person (name, optional email, contact number), with an optional topic; remove with an inline confirmation. A new person and the link are saved in one transaction, so a refused link (duplicate, Conducted) leaves no unlinked new person. The services' messages ("… is already a resource person for this activity.", "A resource person with this email already exists. Pick them from the list instead.") come back as toasts.

## Phase F2b: Forms, Registrations and Evaluation tabs (2026-10-27)

270. **Pages and URLs** (`registrations/page_views.py`, thin; `registrations/pages.py`, display only; templates in `templates/registrations/`). The three tabs are rendered by the activity page (`?tab=forms|registrations|evaluation`, no longer placeholders); their actions live under `/registrations/`: POST `activities/<id>/forms/<registration|evaluation>/create/`, GET `forms/<id>/card/`, GET `forms/<id>/fields/new/` and `.../fields/<field>/edit/` (the card with the inline editor), POST `forms/<id>/fields/add/`, `fields/<field>/save/`, `fields/<field>/remove/`, `fields/<field>/move/` (`direction=up|down`), GET `activities/<id>/registrations/<registration>/` (details for the modal), POST `activities/<id>/attendance/`, POST `activities/<id>/evaluation-links/`, GET `activities/<id>/attendance-sheet/`. Every POST and the field editor need `can_create_activity` (403 otherwise); viewing the tabs, the details and the attendance sheet needs an active staff login. Field actions answer an HTMX request with the updated card and a toast (`HX-Trigger: {"toast": ...}`); without HTMX they redirect to the Forms tab with a message. "Create from defaults", attendance and evaluation links are normal POSTs that come back to their tab with a toast (a full page, so the header's registration notice and link update after the registration form is created).
271. **Locked forms show no edit controls at all** (owner decision): once a form has answers (`form_is_locked`), the card shows "Locked. People have already answered this form, so its fields can no longer be added, edited, removed or moved." and hides Add field, Edit, Remove **and Move up / down**. `services.reorder_fields` itself still allows reordering after answers (order does not change meaning, item 101), but the page refuses a move on a locked form with the service's `FORM_LOCKED` message, like the other field actions.
272. **Form fields on the Forms tab.** Fields in their order with a type badge (Short text, Long text, Rating 1–5, Multiple choice, Checkbox), Required / Optional and their options. Move up / down are buttons (keyboard-usable; disabled at the ends) that call `reorder_fields` with the order from `pages.moved_order`. The inline editor (question, answer type, required, options) shows the service's messages under its inputs and focuses the first one; Escape or Cancel closes it. Options are edited as a list (add, rename, remove) and posted as repeated `option` inputs; empty rows are dropped, then `clean_options` checks them (at least one, no repeats) and stores the JSON list. Options are sent only for the choice types (`services.CHOICE_TYPES`, given to the page by the server). "Create from defaults" says how many default fields the activity type has (an empty form when it has none).
273. **Preview** (a modal) shows the form as participants will see it, read-only, using `components/answer_field.html` (the field look the public pages of phase F3 will reuse; rating = five numbered choices, 1 = poor, 5 = excellent). The fixed fields the public pages always ask are shown around the form's own fields: registration: Full name, Email address, Contact number, Participant category (the activity's categories; with one category it is shown as text, since it is picked automatically); evaluation: the name and email for the certificate first, Comments (optional) last.
274. **Registrations tab.** Counts: registered / max slots (`registered_count`), attended, evaluations submitted and response rate (`reports.services.activity_evaluation`, the same metrics as the Evaluation tab). Table (by name, 20 a page): name, email, contact number, category, registered at (Manila), attendance (Attended · method, or Not yet), evaluation (Submitted or –), startup; a "View" button opens the details and registration answers in a modal (checkbox answers as a comma list). Search matches the name or the email (`list_registrations(search=...)`, ignoring case); filters: attended (yes / no) and category (shown only when the activity has more than one). The filters replace only the table (HTMX `hx-select`); the URL keeps them. The table scrolls sideways inside its card on narrow screens (the scroll box is `relative`, so the screen-reader-only texts of its right-hand cells stay inside it).
275. **Attendance on the page** *(rewritten 2026-11-06, item 349)*: one "Confirmed by: Wet signature / Email" choice above the table (wet signature by default) used by every mark button, so there is no dialog any more; each row has "Mark attended" or "Undo" (Undo is hidden and replaced by "Already evaluated: attendance can no longer be undone." once the person has evaluated, `services.undo_refused_reason`); checkboxes and "select all on this page" with "Mark selected as attended" and "Undo selected" (one `set_attendance` call, all or nothing). A row button sends only its own registration, even when other rows are checked. Rows show "Marked by Ana · Nov 6, 10:52 AM". Before the activity is Ongoing there are no buttons; the tab shows `services.attendance_closed_reason` instead ("Attendance opens when the activity starts on Nov 6, 2026, 8:00 AM."), the same text the service refuses with. Refusals come back as error toasts with the service's message. The member returns to the same filters (only known filters are kept from the posted copy of the query string).
276. **Evaluation links** *(rewritten 2026-11-06, item 348)*: the Registrations tab's old "Send evaluation links" button is now an "Evaluation links" link to the Evaluation tab. The Evaluation tab has an "Evaluation links" card: an explanation built from the rules (who gets the link; sent automatically once per person after the activity is Conducted, every `ACTIVITY_STATUS_INTERVAL_MINUTES` while qcluster runs; failed emails are not retried automatically; the window's opening and closing time; "no evaluation form yet" when missing; in development mode the amber "emails are printed in the terminal" warning, `config.mail.prints_emails()`), the counts sent / failed / not sent yet / answered, and the attendees who have not answered (20 a page, name and link state only) with "Resend link" per person and "Resend to all who haven't answered". The resend buttons need `can_create_activity` and `services.evaluation_links_closed_reason` None (otherwise the reason is shown); the success message goes through `email_sent_message()`. Only people who have **not** answered are named; the rating part of the tab still never shows names, comments or text answers.
277. **Attendance sheet** (print page, any active staff member, opens in a new tab): A4 (`@page`), browser print; the Back link and Print button are hidden when printing. Title, compact dates, time and venue, then No., Name, Participant category and an empty Signature column, sorted by name (`list_registrations` order). Never emails or contact numbers (`pages.attendance_sheet_rows` passes only name and category; a test checks the HTML). **Evaluation tab** = `reports.services.activity_evaluation` only: registered, attended, evaluations, response rate, the overall average with its number of rating answers, and each rating question with its average (bar out of 5) and answer count in a real table. Never comments, text answers or names (a test checks that a comment, a text answer, the participant's name and the certificate name/email are absent). Empty state before any evaluation.

## Phase F3: Public pages (2026-10-27)

278. **Pages and URLs** (no login; `registrations/public_views.py` + `registrations/public_pages.py` (display and form parsing only), `startups/page_views.py`; templates in `templates/public/`, layout `layouts/public.html`): `/register/` (open activities), `/register/<id>/` (GET + POST), `/register/<id>/registered/` (success), `/register/<id>/calendar.ics`, `/evaluate/<token>/` (GET + POST, with the review step), `/evaluate/<token>/thanks/`, `/startups/set-password/<token>/` (GET + POST) and `/startups/set-password/done/` (listed first, so "done" is never read as a token). The placeholder view and `templates/pages/placeholder.html` are removed; emails keep linking to the same page URLs. Every page calls the services the JSON endpoints use, and **all JSON endpoints are unchanged**. Plain HTML forms with CSRF and `novalidate` (every check is the server's, so all messages appear together under their fields); everything works without JavaScript. Alpine only disables the submit button while sending (after the form data is built, so the "step" button value is still sent) and shows/hides passwords. No HTMX on these pages: none needs a partial update.
279. **Registration page states.** Draft or unknown ID: a "This activity is not available" card (404), never the title. **Ongoing / Conducted** (owner decision): title, type, dates, time and venue (no description, slots or categories) with `services.REGISTRATION_ENDED` "Registration for this activity has closed." and a link to the open activities (200); a POST registers nobody. Scheduled but closed (`registration_closed_reason`: deadline passed, all slots taken, form not ready): the full summary and the reason in a card. Scheduled and open: the summary (title, type, dates, time, venue, slots left, deadline in Philippine time, categories, description) and the form. The lookup is the new read-only `services.registration_page_activity()` (anything but Draft); the JSON endpoint keeps `get_public_activity()` (Scheduled only).
280. **Registration form.** Full name, email, contact number (optional, as in the service), participant category (one category: a preselected radio, item 331; several: a required radio choice), then the activity's fields through `components/answer_field.html`, including the Data Privacy consent. Errors appear under their fields (`aria-invalid`, `aria-describedby`) with "Please check the N fields marked below." on top; the **first field with an error gets `autofocus`** (works without JavaScript); typed values are kept. Service messages (already registered, closed, honeypot, rate limit) appear in a red alert. Status codes: 400 for refused input, 429 for the rate limit. The footer's contact address is `TBI_CONTACT_EMAIL` (placeholder `tbi.contact@example.com`, question for the TBI).
281. **One shared check for public registration POSTs** (`registrations/public.py`, `guard_registration_request(request, email, honeypot)`): the client IP from `config.network.client_ip()`, then `services.guard_public_registration` (item 214). Both the JSON endpoint and the HTML page call it before `services.register`, so they share the same counters (attempts on one count on the other; tested in both directions, and that both pass the trusted-proxy address). The honeypot `website` is a real text input inside `.hp-field` (moved off-screen with CSS, `aria-hidden`, `tabindex="-1"`, `autocomplete="off"`), never `type="hidden"`.
282. **Success page** = a redirect after the POST (a refresh never posts twice). Only the browser that registered sees it (owner decision): a session flag lists the activity IDs it registered for (at most 20), so an anonymous visitor gets a session cookie. Anyone else is sent to the registration page. It shows "You're registered!", "A confirmation email is on its way" (the Step 5 background task), the activity's public details and **Add to calendar**.
283. **Calendar file** (`public_pages.activity_ics`, RFC 5545): *(2026-11-06, item 345)* one timed event from the start date at the start time to the end date at the end time, written in UTC (`…Z`, no time-zone block needed); an all-day event from the start date to the end date (DTEND is the day after: all-day events end exclusively) only when the activity has no times. The description holds the time text and the page URL. UID `activity-<id>@<SITE_URL host>`; text escaped (backslash, semicolon, comma, line breaks); lines folded at 75 octets; CRLF. Public facts only, the same file for everyone: never who registered or who downloads it. Available for any non-Draft activity (404 for Draft).
284. **Evaluation page** (`/evaluate/<token>/`). The new read-only `services.evaluation_state()` is the one rule (in this order: attended, not submitted, form exists, window), with `kind` (open, not_attended, submitted, no_form, not_yet_open, closed), the reason and `shows_person`; `evaluation_closed_reason()` and `evaluation_page()` now use it (API output unchanged, tested). Every refusal is a card with its own title and icon and the **service's reason as the text** (the not-yet-open reason includes the opening date and time); an invalid or tampered link answers 404. **The cards never show the name or the email**, not even before the window opens (the HTML page has no form to show then). The form: certificate name and email first (prefilled with the registration's), then the evaluation fields, then optional comments.
285. **Review step** (item 175; always a second server page, owner decision). "Review my answers" posts `step=review`: the new read-only `services.check_evaluation()` (the only check of an evaluation's contents; `submit_evaluation` now calls it) cleans the values without saving anything. The review page shows "Your certificate will be issued to:" and "It will be sent to:" in large text **as they will be stored** (name trimmed with its capitals kept; email in lower case), a warning that the certificate cannot be changed after it is issued, and every answer ("No answer" for skipped optional ones). The values travel in hidden inputs: "Edit" (`step=edit`) goes back to the form with them; "Confirm and submit" (`step=confirm`) calls `submit_evaluation`, which **checks everything again** (a changed hidden value is refused and the form comes back with its errors). Then a redirect to `/evaluate/<token>/thanks/` ("Thank you!", the certificate is emailed once the TBI has approved it; only the activity title), shown only when that link's evaluation is submitted.
286. **Set-password page.** Valid link: the startup's name and email, new password + confirmation (show/hide buttons, `autocomplete="new-password"`, a hidden username for password managers), Django's password rules listed under the field (`password_validators_help_texts`), the service's errors under their fields; a refused password does not use up the link. Tampered link: 404; expired, used or replaced: 200; both a card with the service's message (ending "Ask the TBI to send you a new one."), never the name or email. Success: a redirect to `/startups/set-password/done/` ("Your password is set." and that the portal pages are coming; no link yet: the portal sign-in page arrives in phase F6).
287. **Private token pages** (`config.headers.private_token_page`: the evaluation pages and the set-password pages, including thanks/done and the refusal cards): `Referrer-Policy: same-origin` (also `<meta name="referrer" content="same-origin">`): the URL with its token is never sent to another site. **Not `no-referrer`** (the brief asked for it): checked in Chromium, a page with `no-referrer` sends `Origin: null` when it posts its own form, so Django's CSRF check refused every evaluation and new password with 403 (and on HTTPS Django also needs the Referer). `same-origin` sends a referrer only back to this site, which already has the URL, `X-Robots-Tag: noindex, nofollow` and `Cache-Control: no-store` (a shared computer's browser does not keep the page). Every page already has `<meta name="robots" content="noindex">` (base.html). Pages without a token keep Django's `same-origin` referrer policy. Everything is self-hosted (tested: no external `src`/`href`).
288. **Design** (phones first, 360–430 px, then centred): the sign-in look in `layouts/public.html`: the `.auth-bg` gradient, the logo and "Technology Business Incubator" in white on its dark top-left part, content in `.public-card` (the `.auth-card` glass **without the hover lift**: long forms should not move under the pointer), pill buttons and inputs at least 44 px high (`.public-shell`), 16 px input text (phones do not zoom in), and the footer "© year CSU Technology Business Incubator · Caraga State University" plus the contact line on small glass pills. Column widths: 640 px; the open list 1040 px with two columns of cards from 768 px; set password 480 px. `components/answer_field.html` gained prefilled values, errors and autofocus, and 44 px rating buttons and option rows (also in the Forms tab preview). New icon `calendar-plus`; new display filter `date_time` (Manila time, as the services' messages). **Contrast** (worst pixel behind each text line with the text hidden, Playwright, 2026-10-27, every public page and card at 390x844 / 1440x900): logo name 8.6 / 9.0, logo subtitle 7.1 / 6.7, the open list heading 8.6 / 9.8 and sentence 7.1 / 6.6, card headings 10.2, labels 10.2 / 10.4, helper text 5.9, type label 5.0, tags 10.4, field errors 4.8 / 5.0, the review name 17.2 and its amber labels 8.7, footer 11.8 / 10.0, footer link 6.4 / 5.4. **Field errors on the glass** were 3.7 to 4.1 with red-700, so they are now red-800 (#991B1B) on both glass cards (also the sign-in pages). No page scrolls sideways at 390 px.

## Tests and the time of day (2026-10-28)

289. **No test may depend on the real time of day.** `certificate_walkthrough activity` failed between 23:00 and midnight Manila time: it set the registration deadline to "now + 1 hour" on an activity starting today, which is tomorrow after 23:00 (the deadline must be on or before the start date). The step now reads the clock once (`today` = that moment's Manila date, so the two can no longer straddle midnight), keeps registration open until the end of today (23:59:59.999999) and passes the same `now` to `create_activity` and `register`. `activities/tests/test_past_dates.py::test_create_allows_start_today` skipped itself within 5 minutes of midnight; it now runs on a fixed clock (09:00 and 23:58). **Tools** (root `conftest.py`): the `clock` fixture (`clock(datetime(2026, 11, 20, 23, 30))`, Manila time) and the environment variable `TEST_FAKE_NOW` (for example `2026-10-28T23:30`), which runs every test as if it started at that moment. Both replace `django.utils.timezone.now` (the only clock the app reads; `localdate`/`localtime` use it), and the clock keeps ticking from there. Signed-link timestamps (`time.time`) are not moved. Checked: the whole suite passes at 23:30 and at 00:30 Manila; the regression test runs the walkthrough step at 23:30, 23:59:59 (it passes midnight during the step) and 00:00:30.

## Phase F4: Documents and certificates pages (2026-10-28)

290. **Pages and URLs** (`documents/page_views.py` + `documents/pages.py`, `certificates/page_views.py` + `certificates/pages.py`, thin views and display-only helpers; templates in `templates/documents/` and `templates/certificates/`). Documents: `/documents/` (repository) and `/documents/review/` ("Waiting for my review"), two tabs of one page; `/documents/<id>/` (document page, also for certificate evidence); POST `/activities/<id>/documents/upload/`; POST `/documents/<id>/approve|return|replace|delete/`; GET `/documents/<id>/<return|replace|delete>/form/` (the dialog's content for HTMX, a page of its own without JavaScript). Certificates: `/certificates/` (all certificates); POST `/activities/<id>/certificates/generate/` and `/activities/<id>/certificates/bulk/` (the activity-page actions live under `/activities/`, so a page that comes back from them keeps Activities active in the sidebar); GET `/certificates/<id>/evidence/` (dialog or page) and POST `.../evidence/upload/`. The activity page's Documents and Certificates tabs are built (no placeholder tab, no phase badges any more); the "coming soon" entries for Documents and Certificates are removed (the Admin Console stays for F6). Every JSON endpoint is unchanged.
291. **Permissions on the pages** (the services decide; the pages only hide what the member may not do): viewing, downloading and uploading need an active staff login (any member uploads, as in the service); Approve and Return appear only when `documents.services.allowed_actions()` says so (can_create_activity, never on your own upload, never on a returned document), Replace and Delete only for the uploader while For Review. Generate and the bulk certificate actions need can_create_activity (403 otherwise); members without it see no checkboxes or action buttons. Every refusal of a service comes back as an error toast with the service's own message (e.g. "You cannot review a document you uploaded. Another member must review it."). *(Since 2026-11-05: replaced by item 344.)*
292. **Four document states on the pages** (display only, `documents.pages`): For Review is shown as "Waiting for review" (reviewed_at empty) or "Returned" (reviewed_at set: waiting for the uploader); "Approved"; a required type with nothing uploaded is "Missing". A checklist item that is For Review shows "Returned" only when every uploaded document of that type was returned. The overdue badge is the checklist's `is_overdue`. The return reason is not stored (no ERD column): it reaches the uploader only as the notification, and the document page says "Returned by X on <date>" (owner decision 4).
293. **Documents tab**: the checklist (`document_checklist`: required types in due-date order with their due date in Manila time, state, overdue badge and documents; then the optional types that have documents) and the upload form (type, optional title, file). `?tab=documents&type=<id>` preselects the type; the Certificate evidence type is not offered here (evidence has its own upload). The file field is a real `<input type="file">` with a drag-and-drop zone that fills the same input (`components/file_drop.html`); it shows "PDF, DOCX, XLSX, JPG, PNG · up to N MB" (`DOCUMENT_MAX_UPLOAD_MB`), but only the server's file checks decide. A refused upload re-renders the activity page (400) with the error under its field, the chosen type kept and the focus on the first field with an error (`activities.page_views.render_activity_page`, new: the activity page with extra context).
294. **Return / replace / delete ask first** in one shared dialog per page (`documents/_dialog.html`): the button loads the form with HTMX and opens it; Escape closes it and the focus goes back to the button. Return asks for a reason (`maxlength` = `RETURN_REASON_MAX_LENGTH`, 300); a refused reason or file comes back inside the dialog under its field with the focus on it; success answers `HX-Redirect`. Without JavaScript the buttons are links to the form's own page. Delete's dialog puts the focus on Cancel. After an action the member returns to the page they came from (`next`, only a page of this site; after deleting, the activity's tab instead of the deleted document's page).
295. **Repository and review queue**: filters (search in title and file name, activity, type, state, uploader, searchable) swap only the table (HTMX) and stay in the URL; a filter that cannot be read is ignored with a note (as item 262). Certificate evidence is never listed. The review tab shows `review_queue()` (oldest first, never my uploads, never returned ones) and its count, the same number as the sidebar badge (owner change: the sidebar's Documents item always opens `/documents/`). Without can_create_activity the review tab explains that only members who can create activities review documents. *(Since 2026-11-05, item 344: without `can_approve_documents` the review tab is hidden and its URL answers 403; the queue includes my own uploads. Since 2026-11-06, item 352: this table is the "All documents" view; the default is "By activity".)*
296. **Certificates tab**: counts per status, "Generate certificates" (disabled with the reason from the new read-only `certificates.services.generation_closed_reason()`: not Conducted, or the missing template / VP / Director named by `active_design()`; `generate_certificates` refuses with the same message), the table (number, recipient, Participation / Appreciation, Email / Hard copy, status, issued, sent), Preview, PDF (once rendered) and Evidence. Rows are selected with checkboxes ("select all", a live "N selected"); the actions are Review, Record Director sign-off, Record VP sign-off, Approve, Reject and Mark handed over, each one service call with the selected IDs (only IDs of this activity are accepted). All or nothing: a refusal re-renders the tab (400) with a red list "number: reason" for every refused certificate (the new `CertificateError.problems`, also kept in the message) and a summary toast. Mark handed over is offered for any selection; the service refuses participant certificates ("only hard-copy certificates are handed over"). *(Replaced on 2026-11-12 by item 362: no Generate, no checkboxes; Approve on the preview, "Approve all ready certificates", Apply the current design; row actions Mark handed over and Resend.)*
297. **Preview opens inline in a new tab** (owner decision 1): the watermarked preview is rendered by us and never an uploaded file, so it is sent `Content-Disposition: inline` with `nosniff`. Final PDFs, documents and evidence stay attachments.
298. **Evidence** (DOCUMENT with `related_certificate`): a dialog per certificate lists its evidence with the document actions (the same Step 6 rules and the same action views) and an upload form (title, file). It is never listed in the repository or used by the assistant.
299. **Certificates page** (`/certificates/`): every certificate with filters (activity, status, recipient type, delivery) and counts per status for the other filters (new read-only `certificates.services.certificate_status_counts()`, one query; `list_certificates` gained an optional `delivery_method` filter). A count is a link that filters by its status. Actions stay on each activity's tab. *(2026-11-14, item 372: organised by activity by default, newest first, with an "All certificates" view and a search box.)*
300. **Links** (owner decisions 2 and the F4 change): the dashboard's overdue and due-soon document items open the activity's Documents tab with the type preselected (a group of several activities: the one with the oldest due date; the item still names them all); "documents waiting for your review" opens `/documents/review/`; certificates waiting for sign-off open the activity's Certificates tab, or `/certificates/` (narrowed to Generated or Pending Sign-off when only one of them has certificates) when several activities have some. A notification in the bell's dropdown is a small POST form: it marks the notification as read and opens its activity's Documents tab with the type preselected (document reminders and returned documents), the activity, or the dashboard when it has no activity (`notifications/pages.py`, new view `notifications:open`).

## Phase F5: Reports, AI Assistant, Startups, Notifications and search (2026-10-28)

301. **Pages and URLs** (thin views plus display-only helpers; the services decide): `/reports/` (`reports/page_views.py` + `reports/pages.py`) with `/reports/<id>/row/` for polling; `/assistant/` (a new chat), `/assistant/<id>/` and `POST /assistant/ask/` (`assistant/page_views.py` + `assistant/pages.py`); `/startups/`, `/startups/<id>/`, `POST /startups/sync/` and `POST /startups/<id>/send-password-link/` (`startups/staff_views.py` + `startups/pages.py`; the public set-password page stays in `startups/page_views.py`); `/notifications/` with `POST /notifications/<id>/read/` and `/notifications/read-all/`; `/search/` (`config/search.py`). The Reports, AI Assistant and Startups placeholders are gone; only the Admin Console stays "coming soon" (F6).
302. **New report form** (only with `can_create_activity`; everyone else sees the history and a note, and a POST is refused with 403 by `request_report`). The five types, the two main ones first and marked "Main"; the sections as pills, preset by the type (Alpine copies the type's default sections when the type changes) and editable; the AI summary is a separate "Include AI summary" switch that adds `ai_summary`. Quick dates: This month, This quarter (the default), January – June, This year, Last year, Custom (Manila calendar, `reports.pages.date_presets`). A quick choice wins over the typed dates on the server too, so the form works without JavaScript; typing a date switches to Custom. Every value is checked by `request_report`; its messages appear under their fields (400) and the form stays open. Posting without any section pill and without the switch uses the type's default sections (the service's rule).
303. **History and polling.** The history is `list_reports()` with `report_state()` (one query for the failed tasks per page): type, sections (an "AI" badge when the AI summary is included), the period in words, format, who requested it and when, state (Pending / Ready / Failed) and Download when ready. A pending row asks for itself every 4 seconds (`hx-trigger="every 4s"`, outerHTML); a ready or failed row comes back without the trigger, so polling stops, and its new state is announced in a polite live region (response header `HX-Trigger: report-state`). **Polling stops after 15 minutes on the page** (owner decision F5-3); the row then says "Still pending. Is qcluster running? Refresh the page to check again." The page says reports are made while qcluster is running.
304. **Chats** (owner decision F5-1): "New chat" opens `/assistant/` without creating a row; the first question creates the chat. If that first question fails (any refusal or the AI not available), the empty chat is deleted in the same request (new `assistant.services.discard_if_empty()`: only the member's own chat and only without messages), so empty "New chat" rows never pile up. A question is saved only with its answer (unchanged, item 166).
305. **Answers are untrusted text.** `assistant.pages.answer_html()` escapes the whole answer, then adds only our own links for `[n]` markers that match a saved source (to that document's page, the same link as its source chip; other markers stay plain text) and `<br>` for line breaks. No HTML or Markdown from the model is ever rendered (a test sends `<script>` and `<img onerror>`). The question is escaped too. Each answer shows its "answered from" badge (`services.answered_from`) and its sources as numbered chips (title, type, activity). *(**Replaced on 2026-11-15 by item 376:** answers are still escaped first, but bold, italics, `#` headings as a bold line, bullet and numbered lists are now shown. Raw HTML, links, images and tables still stay text.)*
306. **While waiting** (up to `ASSISTANT_QUESTION_TIMEOUT_SECONDS`): the question appears at once with a typing indicator, the send button is disabled, double sends are dropped (`hx-sync`), and "Stop waiting" aborts only the browser's request (owner decision F5-4): the server may still finish and save the answer, which then appears when the chat is opened again; the page says so. A refusal appears above the message box (with HTMX: status 200 and `HX-Retarget`; without JavaScript: the page again with 400, or 503 when the AI is not available) with the service's message and a hint per kind (not set up, key rejected, model unavailable, busy, too slow, unavailable). Each new answer is read once by screen readers (a polite live region). Ctrl + Enter sends. *(2026-11-15: "Ctrl + Enter sends" is replaced by Enter to send and Shift + Enter for a new line, and Send stays disabled while the box is empty: item 374.)*
307. **Suggested questions** on an empty chat: "Which documents are missing for <the next Scheduled activity>?" (`reports.metrics.upcoming_activities`; without one, a general question), "Which documents are overdue right now?" and "Which documents are waiting for review?". Clicking one fills the box; it is not sent automatically. The activity filter lists every activity, newest first.
308. **Startups pages.** The list (search in name and email, maturity stage, active; HTMX like the documents list; a filter that cannot be read is ignored with a note) and a startup's page (the Navigatu fields, last synced, whether a portal password is set, and its registrations from `registrations_for()` with attendance). The maturity stage choices are the stages the synced startups have (new read-only `startups.services.maturity_stages()`, distinct ignoring case and outer spaces), since they are placeholders until the TBI confirms them (item 147). "Sync now" and "Send set-password link" appear only with `can_manage_users` (the views check it again: 403); the link button only for active startups. Sync results become a toast with the counts, a warning when the deactivation guard held back, and an error with the provider's message when Navigatu is not available. The link itself is never shown on a page.
309. **Notifications page.** My notifications, newest first, with an All / Unread filter (owner decision F5-2: no "unread first" order), mark one or all as read (back to the same list; `next` only within this site), and the same "Open documents" / "Open activity" target as the dropdown. The dropdown has a "View all notifications" link.
310. **Global search** (the top bar's box now opens `/search/`): Activities (title), Documents (title or file name; never certificate evidence) and Startups (name or email), the first 5 of each from the lists' own services (`list_activities`, `list_documents`, `list_startups`) with the total and a "See all" link to that list filtered by the same `q`. At most 100 characters. Empty states for an empty search and for no matches.
311. **Accessibility in F5:** live regions for new chat answers and report state changes, labelled filters and form fields, errors linked with `aria-describedby`, text next to every state badge, real buttons and links, and a loading indicator for every HTMX swap.

## Phase F6: Startup portal and Admin Console (2026-10-28)

312. **Portal pages and URLs** (`startups/portal_views.py` thin + `startups/portal_pages.py` display only; templates in `templates/portal/` on the new `layouts/portal.html`): `/startups/portal/login/` and `logout/`, `/startups/portal/` (Overview), `activities/`, `activities/<id>/register/` and `registered/`, `registrations/`, `certificates/`, `notifications/` (with `read-all/` and `<id>/read/`), `profile/`. The JSON API under `/startups/portal/api/` is unchanged; the certificate download stays `startups:portal_certificate_download`.
313. **Portal access** (`startups.decorators.portal_page_required`, the HTML twin of `startup_login_required`): a staff session gets the 403 page "Staff accounts cannot use the startup portal. Log out of the staff account first." (also on the portal login); nobody logged in goes to the portal login with `next` (HTMX: 401 + `HX-Redirect`). The login follows `next` only to a portal path (`/startups/portal/...`), never another site or a staff page. A startup session on any staff page or the Django admin goes to the staff login (it is never `request.user`). Tests cover every page in both directions.
314. **Startup login page** (owner request): the staff sign-in design (`layouts/auth_split.html` gained the blocks `headline`, `panel_text`, `home_url` and `below_card`), titled "Startup portal", with `authenticate_startup` (startup lockout counters and the same messages). Under the staff card: "Are you a startup? Sign in to the startup portal" (item 253); under the startup card: "TBI staff? Sign in here". No self-service reset (item 143): "Forgot your password? Ask the TBI to send you a new link" with `TBI_CONTACT_EMAIL`. A logged-in startup that opens the login goes to the Overview (owner decision F6-5). The set-password "done" page now links to the portal login.
315. **Portal layout**: the staff shell (gradient frame, rounded panel) with its own sidebar (`config.frontend.PORTAL`: Overview, Open activities, My registrations, My certificates, Notifications with the unread count, Profile), the startup's name and email at the bottom and Log out (POST). The context processor `config.frontend.portal_layout` gives `portal` only when `request.startup` is set. Below 1024 px the sidebar is behind the menu button; lists become stacked cards on phones (checked at 390 px).
316. **Overview**: a welcome banner, my next registered activity (Scheduled or Ongoing, not ended, soonest start), three counts (my registrations, attended, Issued certificates: `registrations_for` and `certificates_for_startup`) and up to three open activities I have not registered for (`open_activities`, "See all" when there are more).
317. **Registering in the portal**: the open list marks "Already registered" (a registration from `registrations_for`, linked or an older public one with my email) and then offers no Register button; opening the form of such an activity shows "You're already registered". The form shows name, email and contact number read-only from the Navigatu profile, the category (text when there is only one) and the activity's fields (`components/answer_field.html`); it posts to `register_as_startup` (no rate limit or honeypot: the portal is not public, item 214). Errors appear under their fields; a refusal about the profile fields (e.g. the email already registered) is shown as one message with "ask the TBI to correct it". The success page is the F3 style, shown only right after registering (session flag).
318. **My registrations**: upcoming (the activity ends today or later, Manila) and past (most recent first). Attendance: Attended / Not recorded yet (upcoming) / Not marked as attended (past). Evaluation from `evaluation_state()`: open shows "Evaluate this activity" (the personal signed link, `/evaluate/<token>/`); submitted shows a badge; anything else shows the service's reason. **Upcoming registrations show "Evaluation opens after the activity."** instead of the service's "not attended" reason (owner decision F6-4).
319. **My certificates, notifications, profile**: certificates are `certificates_for_startup` (Issued only, mine only) with Download through the existing permission-checked view (someone else's: 404). Notifications: All / Unread, mark one or all as read (`next` only within the portal). Profile: the Navigatu fields and last synced, read-only, with "Changes are made in Navigatu"; never the password.
320. **Admin Console pages** (`accounts/console_views.py` + `accounts/console_pages.py`, templates in `templates/console/`, all `permission_required(MANAGE_USERS)`), a tab row on every page plus a link to the Startups page (sync and set-password links stay there): **Members** `/accounts/console/` (new read-only `accounts.services.list_members`: search in name and email, role, active; 20 a page; HTMX like the other lists), Invite (a `<details>` form; `create_member` now also refuses an empty name and an invalid email address: "Enter the member's name." / "Enter a valid email address."; errors under their fields with the form open), change role (a select per row), Reset password and Deactivate (confirmation dialogs), Reactivate. The buttons are shown for every member: the services refuse what is not allowed and their messages appear as toasts ("At least one active Admin must always exist.", "You cannot deactivate your own account.", "Use Change password for your own account."). Members are never deleted. **Roles** `/accounts/console/roles/`: read-only table (both flags and the number of active members) with "Edit in the administration site" (item 224).
321. **Certificate design** `/accounts/console/certificates/`: designs (upload with name, image and "make it the active design", checked by `upload_template`; thumbnails through the Admin-only `certificates:template_image`; Make active / Deactivate) and signatories per position (the active one marked, add VP or Director with a PNG through `add_signatory`, which replaces the active one; Deactivate). Warnings when no design, VP or Director is active. **There is no delete URL** for designs or signatories (tests post to made-up delete URLs: 404, rows kept). *(2026-11-13, item 363: the signatories card now shows the default signatories read only, with a link to the Signatories page.)*
322. **System status** `/accounts/console/system/` (new read-only `accounts/system_status.py`; each item green, amber with a one-line fix, or blue): qcluster (Django-Q's cluster status `Stat.get_all()`, read from the shared database cache), the four daily schedules (next run, last result from the schedule's last task; missing ones: run `setup_schedules`), email (**blue "Development mode: emails are printed in the terminal" when DEBUG is on** (owner decision F6-2); amber with the fix `EMAIL_REAL_SENDING` when console mode runs without DEBUG; green with the SMTP server and the from address for real SMTP; never the SMTP user or password), Gemini (only whether `GEMINI_API_KEY` is set, plus the model names; **never calls Gemini**, the tip is `python manage.py check_gemini`), PDF rendering (one line rendered in memory through `certificates.pdf.get_renderer()`, loaded by HTMX after the page so the rest appears at once; on every visit, not cached: owner decision F6-3), approved documents not indexed yet (`indexing.pending_documents`) and Issued certificates not emailed yet (`pending_emails`).
323. **The placeholder is gone**: `config.views.coming_soon`, `COMING_SOON_PAGES` and `templates/pages/coming_soon.html` are removed (nothing uses them). New icons (Lucide 1.52.0): clipboard-list, external-link, image, pen-line, server. `components/modal.html` gained `trigger_sr` (screen-reader words for a button repeated per row). `public/_activity_summary.html` gained `heading="h2"` for use inside the portal layout.

## UI polish: selects, KPI cards, chat panel (2026-10-29)

324. **One select style** (`.field-select` in `frontend/tailwind/app.css`, used as `class="field-input field-select"`): no native arrow (`appearance: none`), one chevron (Lucide chevron-down as an inline SVG, slate-500; a lighter one when disabled) 14 px from the right edge and vertically centred, and the same right padding (`pr-10`) on every select, so long text never runs under the arrow. Selects and date inputs have one fixed height (42 px; 40 px in the pill filter bars, 36 px for the per-row role select of the Admin Console, `.field-select-sm`), so they line up in a row; templates no longer set their own `pr-*`/`py-*` on selects. A test scans all our templates (not the Django admin overrides in `templates/admin/`): every select uses it, except list boxes (`multiple` / `size`), which have no arrow (there are none today). The glass inputs (login and public pages) now set `background-color` instead of the `background` shorthand (which would remove the chevron); text inputs look the same, as none has a background image.
325. **Compact KPI cards** (`components/kpi_card.html`, every KPI card: Dashboard, Activities list, an activity's Registrations and Evaluation tabs, the portal overview): icon (32 px chip) and label on one row, the number (`text-2xl`, still large text for the KPI teal) with its pill below, 14 px padding, no minimum height: about 88 px instead of 144 px. In a narrow card (the portal overview keeps three cards side by side on a phone) the label wraps under the icon instead of overflowing.
326. **My chats can be hidden** on the AI Assistant page: the panel is 15rem (240 px); a close button in its header and, when closed, "Chats" and a "New chat" icon button at the left of the chat header (real buttons with `aria-controls="my-chats-panel"` and `aria-expanded`; focus moves to the other toggle). The chat card then spans both columns. Open or closed is remembered per browser only (`localStorage` key `tbi.assistant.chats`, inside try/catch, default open); a person who closed it may see it open for a moment while the page loads. Without JavaScript the toggles are hidden and the panel stays open. New icons (Lucide 1.52.0): messages-square, panel-left-close. *(2026-11-15: the panel's header now holds only "My chats" and the hide button; "New chat" is a full-width button below it, so the heading is never cut off: item 379.)*

## Optional documents on activity creation + in-page document preview (2026-10-30)

327. **Documents (optional) on the New activity form** (not on Edit: the Documents tab handles later uploads). Rows of document type + file + optional title, added and removed with Alpine (without JavaScript one empty row; at most `MAX_DOCUMENTS_AT_CREATION` = 10 rows). A row left completely empty is ignored, so an untouched section creates the activity exactly as before. **Offered types** come only from `documents.services.types_allowed_at_creation()`: active types that are not due after the end date (`due_reference = end_date`: Attendance Sheet, Accomplishment Report) and not named in `settings.DOCUMENT_TYPES_AFTER_ACTIVITY` (`["Certificate", "Evaluation Form"]`: no due rule, but they only make sense afterwards; the system builds the evaluation form itself). *(Since item 386 also "Accomplishment Report": optional, so without a due rule, still only afterwards.)* With the seeded types that leaves Invitation Letter, Activity Design, Request Letter and Pre-registration Form. Types added later in the Django admin are offered unless they are due after the end date or listed in the setting. **Offering a blank Evaluation Form later needs only that setting** (remove it from the list); TBI question added. The service checks the type again, so a posted Attendance Sheet is refused ("This document type cannot be uploaded when creating an activity. Upload it from the activity's Documents tab later.").
328. **One service call, all or nothing**: `documents.services.create_activity_with_documents(member, data, documents)` (in `documents`, because `documents` already imports `activities`). It first checks every row (`check_new_documents`: type, `validate_upload` (real content, size), title length), then in one `transaction.atomic()` runs `activities.services.create_activity` (Draft + its first audit row, as before) and the existing `upload_document` for each row (For Review with `reviewed_at` empty = "Waiting for review", uploader recorded, path from the naming pattern). Any problem rolls the database back, deletes the files already stored, and raises `NewActivityError` with the activity's `ValidationError` and the errors per row **together**, so the page shows both at once (the form view also runs `check_new_documents` when the activity fields are invalid). `upload_document` creates no notification, email or task: indexing is queued only on approval (`transaction.on_commit`, unchanged), so a refused create never tells anyone anything (tested). Documents have no audit table in the ERD; the activity's audit row is written as before. Approval still needs can_create_activity and a member other than the uploader; Attendance Sheets are still never sent to the AI (and cannot be chosen here). After an error each row keeps its type and title with its error underneath, and an amber note says the browser cleared the chosen files and asks to choose them again. After saving: the activity page opens on the Documents tab (when documents were added), where the files appear under their required-document rows.
329. **In-page preview** of activity documents (not certificate evidence, which keeps its own download): a **Preview** button in `documents/_actions.html` (activity Documents tab, Documents page and review queue, document page) loads `documents.page_views.preview` with HTMX into one shared large `<dialog id="document-preview">` (in `documents/_dialog.html`, `max-w-5xl`): title, type, state badge, Download, Close (gets the focus); Escape or Close closes it, the body is emptied (the PDF stops loading) and the focus goes back to the Preview button. Never a new tab; without JavaScript the same panel is its own page. Body by `documents.pages.preview_kind()`: **pdf** = `<iframe>` (75vh), **image** (PNG/JPG) = `<img>`, anything else = "Preview isn't available for this file type" + Download (no LibreOffice or converter). The kind comes from `services.preview_content_type()`, i.e. the stored `file_extension`, which `validate_upload` confirmed from the real content at upload, never the file name.
330. **The inline file view** `documents:view` (`/documents/<id>/view/`, `documents.views.view_document`): the same rule as Download (`staff_login_required`, activity documents only), `Content-Disposition: inline`, the content type from `PREVIEW_CONTENT_TYPES` (application/pdf, image/png, image/jpeg), `X-Content-Type-Options: nosniff`; `services.open_for_preview` checks the **stored bytes** against the confirmed type again (a mismatch, a Word/Excel file or a missing file: 404). It is the **only** view with `@xframe_options_sameorigin`; every other response keeps Django's `X-Frame-Options: DENY` (tested). The project sets **no Content-Security-Policy** (no `SECURE_CSP`, no CSP middleware or header), so no `frame-ancestors` / `frame-src` change was needed; if a CSP is added later it must allow `frame-src 'self'` on the pages with the preview dialog and `frame-ancestors 'self'` on `documents:view` only. Files are still never served from `/media/`.

## Participant category, shareable links and QR codes (2026-10-31)

331. **Participant category on the registration form** (public page and startup portal, one shared component `components/category_choice.html`). The options are only the activity's own categories (ACTIVITY_PARTICIPANT_CATEGORY, through `services.public_activity_summary`). **One category:** a single radio button, already checked, so its ID is sent (and still saved in REGISTRATION.participant_category_id; `services.register` would pick it anyway). **Several:** a required radio group with no default. `registrations.services._choose_category` (called by `register`) stays the only check: a missing choice gives "Choose the category you are attending as.", a category of another activity "Choose one of this activity's categories: …", shown under the group (400). This replaces "shown as text" in item 280. **One registration per email per activity** is enforced by the database (`unique_registration_per_activity_email` on `LOWER(TRIM(email))` + activity, item 108; tested again here).
332. **Share card** on the activity page's Overview tab (`activities/share.py`, display only; `templates/activities/_share_card.html`), visible to every active staff member (owner decision):
     - **Draft:** no card (Draft is never public).
     - **Scheduled:** the registration link with an **Open / Closed** badge and the closed reason (full or past the deadline, or the form missing), from `pages.registration_summary()` (= `registration_closed_reason()`). The link box moved here from the Registration card; the header's "Copy registration link" button stays (only while open).
     - **Ongoing / Conducted:** the registration section is hidden; only the evaluation section: the general evaluation link while it is open (item 334), otherwise when it opens or that it has closed.
     - Each link: the full URL to select, **Copy link**, its **QR code** and **Download QR (PNG)**.
     - **Every shared link starts with `SITE_URL`** (`.env`, already used by the email links; placeholder in `.env.example`), never the Host of the request: `registrations.services.registration_page_url()` and `evaluation_request_url()`. The header button and the calendar file now use the same helper (before, the page used `request.build_absolute_uri`).
333. **QR codes**: `segno` 1.6.6 (pure Python, writes PNG itself; added to `requirements.txt`), error correction M, 12 px per module, the standard 4-module quiet zone (about 500 px for a typical link, sharp on an A4 poster). The view `activities:qr_code` (`/activities/<id>/qr/<registration|evaluation>.png`) needs an active staff login (`staff_login_required`; anyone else goes to the login page) and answers only for a link the Share card shows (`share.qr_url`): Draft, the registration QR of an Ongoing/Conducted activity, the evaluation QR before it opens or after it closes, and unknown kinds are 404. `?download=1` sends the same PNG as an attachment `activity-<id>-<kind>-qr.png`. `Cache-Control: private, no-cache` (the link shown can change with the status).
334. **General evaluation link** (one per activity): `/evaluation/<id>/` (and `/evaluation/<id>/sent/`), on `layouts/public.html`, phones first, no login, plain form with CSRF, `novalidate` and the honeypot `website`. **The decisions of items 4, 40, 113–114, 175 and 285 are unchanged:** only attendees evaluate, one evaluation per registration, never anonymous, through the personal link with its review step; certificate name and email still come from the evaluation. The page only asks for the email used to register; `registrations.services.request_evaluation_link()` then queues the existing personal-link email (`queue_evaluation_link`, the Django-Q2 task of the staff "Send evaluation links" action, sent by qcluster, after the commit) for every registration of **this** activity with that email (ignoring case and spaces) that **attended and has not evaluated** (`evaluation_link_recipients`). The database allows only one such registration, but the code handles every match (tested). The link goes only to the registered address, so typing someone's email never shows their link (item 114 holds).
335. **It never reveals who attended.** Attendee, absent, not registered, already evaluated or unknown: the same redirect to the same page, "If you attended this activity, we've sent your evaluation link to that email." (`services.EVALUATION_REQUEST_SENT`), never the email or a name. Only an invalid address gets a field error ("Enter a valid email address.", 400), which says nothing about anyone. **Rate limit** (`services.guard_evaluation_request`, shared limiter `config/ratelimit.py`, IP from `config.network.client_ip()`): `EVALUATION_REQUEST_MAX_PER_EMAIL` (**3**) per email (lowercased, any activity) and `EVALUATION_REQUEST_MAX_PER_IP` (**300**, high because a whole campus shares one IP) in `EVALUATION_REQUEST_RATE_WINDOW_MINUTES` (**60**); every request counts. Over a limit, or with the honeypot filled in: **the same neutral page**, nothing sent (like the forgot-password form, item 227), and a WARNING on `accounts.security` with the **IP address only, never the email** (tested for this guard and for the registration guard of item 214). *Accepted limit:* queueing a task makes an eligible request a few milliseconds slower; with the rate limit this cannot be used to find attendees in practice. *(2026-11-08: the request is recorded on matching registrations for staff, item 354; the key holds an HMAC, not the email, item 355.)*
336. **When the general link works:** the evaluation window of the personal links (item 113): from **00:00 Manila on the end date** until `evaluation_deadline`, and only when an evaluation form exists (`services.evaluation_request_state`: open / not_yet_open / closed). Before: "The evaluation is not open yet" with the opening date and time (or "The evaluation is not open yet." without a form); after: "The evaluation is closed" with the date. A POST then sends nothing. The Share card shows the link and its QR only while it is open.
337. **Draft activities are 404 on every public page** (checked in one test): `/register/<id>/`, its success page and calendar file, `/api/register/<id>/`, `/evaluation/<id>/` and `/evaluation/<id>/sent/` (all through `registration_page_activity` / `get_public_activity`), and Draft never appears in the open lists. `/evaluate/<token>/` cannot reach a Draft activity: registering needs Scheduled, and Scheduled → Draft is refused once anyone has registered (item 66).

## Excel layout of the reports (2026-10-31)

338. **Every report's Excel file is a printable report** (`reports/excel.py`, layout only: the same content and numbers as before; one layout for all five report types, owner decision).
     - **Sheets:** "Report info", then the sections in report order with short names (Summary, AI summary, Activities, By type, By category, Per month, Evaluation, Documents, Certificates, Startups); the section's full name is the sheet's subtitle.
     - **Title block** on every sheet, kept compact (owner request): 5 rows, then 1 blank row: "CSU-TBI Track — <report name>", "Technology Business Incubator, Caraga State University", the subtitle, "Period: <range in words>" (+ " · Activity type: <name>" when filtered) and "Generated <Nov 6, 2026 3:05 PM> (Manila time) by <name>". Merged across the table width, one line each (the first column, or else the last, is widened so no line is cut off).
     - **Data tables are real Excel tables** (header `#1D4ED8`, white bold; style TableStyleMedium2 banded rows; filter buttons), starting on row 7 (row 8 when a sheet has several tables and each gets a bold title row: Documents, Certificates). The panes are frozen below the first table's header (the title block stays visible) and that header row repeats on every printed page. Excel allows only one frozen area and one repeated row per sheet, so a second table on the same sheet has neither.
     - **Opening without Excel's "repair" warning:** table names unique in the workbook, no spaces (`Table<n>_<section>`); every header cell a non-empty string, never repeated within a table (`header_labels`: an empty label becomes "Column n", a repeat gets " (2)"); totals and notes are **below** the table range, never inside it; no merged cell inside a table (merges only in the title block and the notes, owner decision); an empty table is never written. Tested for all five report types. Excel itself is not installed on the development machine, so the file was checked against these rules, not opened in Excel.
     - **Formats:** text left (wrapped), numbers right (`#,##0`), percentages `0.0%`, ratings `0.00`, dates centered `mmm d, yyyy` ("Nov 6, 2026"), date-times `mmm d, yyyy h:mm AM/PM`, money `"₱"#,##0.00` (the symbol quoted so Excel and WPS read it; no report column holds money yet). **Date-times are converted to Manila time and written without tzinfo** (`excel_datetime`; openpyxl refuses aware values). Column widths from the table cells: at least 10, at most 50 (longer text wraps; long headers wrap at about 24 characters).
     - **Totals rows** under By activity type, By participant category and Per month: bold with a thin top border, outside the Excel table (sorting or filtering never moves them). Their values come from `metrics.period_summary()` through the new optional `Table.total` in `reports/content.py`, never from adding up the rows: the by-category total is labelled "Total (distinct activities)" because one activity can count under several categories (item 94). The attendance rate in a total is the period's rate. The PDF does not show the totals (unchanged).
     - **Notes** (e.g. the category counting note, "As of …") in italics below their table, merged across the table width and wrapped (owner decision).
     - **Report info:** two columns, bold labels, values left-aligned, period start/end as dates in the report format and "Generated at" as a Manila date-time; the same items as the old "Filters" sheet.
     - **Empty cases:** a period without conducted activities: each period section is one row "No conducted activities in this period." (item 213); an empty table in a period that has activities (e.g. no startups engaged): one row "Nothing to show for this period." instead of a table.
     - **Print setup** on every sheet: A4 landscape, fit to 1 page wide (as many pages tall as needed), narrow margins, footer "Page x of y".

## System Administrator: separation of duties (2026-10-31)

339. **The role "Admin" is now "System Administrator"** and it no longer creates activities (decides item 85).
     - **Migration `accounts/0003_system_administrator`** renames the row (role names are unique: it stops and changes nothing if a "System Administrator" role already exists), sets `can_create_activity = False` and a new description. It can be reversed. `seed_data` creates and re-applies the same flags (`Role.SYSTEM_ADMINISTRATOR`, `accounts/seed.py`); `createsuperuser` gives that role. "Admin Staff" is unchanged.
     - **Permissions still come only from the flags**, never from the name. Code that used the role name now uses a flag: `generate_report` without `--member` acts as the first active member who can create activities; `seed_demo` adds the invented certificate design as the first active member who can manage users.
     - **What the System Administrator can do:** the Admin Console and the Django admin (members, role flags, master data, certificate designs and signatories, startup set-password links), view every staff page for support (dashboard, activities, documents, certificates, reports, startups, notifications, AI assistant: read only), and upload documents like any staff member. **What it cannot do** (everything that needs `can_create_activity`): create or edit activities, change status, manage forms or attendance, send evaluation links, approve or return documents, act on certificates (generate, review, sign-off, approve, reject, hand over), generate reports. The buttons are hidden as for every read-only member, and every service refuses (403).
     - **No role may have both flags** (`can_manage_users` and `can_create_activity`): `accounts.services.check_role_flags`, called by `update_role_permissions` and by the Django admin role form ("A role cannot both manage users and create activities (separation of duties). Turn one of the two flags off."). The last-System-Administrator guard (item 59) is unchanged. Role flags stay editable by the System Administrator in the Django admin (owner decision).
     - **Django admin:** activities, resource persons and registrations are **view only, with no actions**, for a member without `can_create_activity` (`accounts.admin.ActivityDataAdminMixin`; the "Change status" page answers 403). Since only members who can manage users open the Django admin and no role may have both flags, activity data is in practice changed only in the app pages. The code of the admin activity screens is kept (their tests use a test-only role written straight to the database).
     - **Sidebar:** the section label "ADMIN" is now "SYSTEM" (the item stays "Admin Console"). Tighter spacing (64 px logo row, 37 px nav rows, smaller section gaps and profile card): the whole sidebar needs about 575 px, so it fits the browser window of a 1366×768 screen (about 620–660 px tall) and a 768 px viewport without a scrollbar; only smaller heights scroll the nav (measured with Playwright at 1366×768, 1366×657 and 1366×600). The startup portal sidebar shares the nav row and is tighter too.
     - **Demo accounts:** `seed_demo` creates one account per role, `<role-slug>@demo.example.com` (system-administrator@, director@, incubation-project-head@, community-development-head@, admin-staff@), with unusable passwords and no first-sign-in password change. With no other System Administrator, the demo one adds the invented certificate design. To log in with a demo account, set its password on that machine with Django's `python manage.py changepassword <email>` (typed twice in the terminal; never in code, `.env` or the repository).
     - **ERD:** in the ROLE table, the `role_name` note becomes "Director / Incubation Project Head / Comm. Dev Head / Admin Staff / System Administrator (unique)" and the `can_manage_users` note "true only for System Administrator" (optionally `can_create_activity`: "false for System Administrator").
340. **Account audit log.** Every member creation, role change, activation and deactivation, password reset by an administrator and role flag change is logged at INFO on the logger `accounts.audit`: who did it (member ID and email), to whom or which role, and the old and new values (e.g. `Member role changed: member_id=7 <ana@…> role 'Director' -> 'Admin Staff' by member_id=1 <admin@…>`; `Role flag changed: role='Admin Staff' can_create_activity False -> True by …`). Passwords are never logged (the temporary password is only in the email). Role changes made in the Django admin go through `change_member_role` too, so they are logged and guarded the same way. **Known limit:** a System Administrator could still move their own account to another role (while another System Administrator remains) or create a second account with another role; nothing in the system prevents that. This log is how such changes are made visible. *(TBI question: should account and role changes also be shown as a history in the Admin Console?)*
341. **Activities list cards** (2026-11-01, revised the same day, owner decision). "Activities in total" is a plain card (no link, no hover). "Open for registration", "Conducted this month" and "Conducted in total" open a dialog (`components/modal.html` with `remote=True`, loaded with HTMX from `/activities/cards/<preset>/`); **the list below never changes**. The dialog: title = the card's label with the count ("Open for registration (1)"); compact rows (date tile, title, status badge, venue, registered/max), each a link to the activity page; open = soonest start first, conducted = newest first; at most 50 rows (`PRESET_LIST_LIMIT`), scrolling inside the dialog (at most 70 % of the screen height), then "Showing the first 50 of N · Show all in the list" (`/activities/?preset=…`); an empty state when the count is 0. Escape, a click on the backdrop or Close closes it, the body is emptied and the focus goes back to the card. Without JavaScript the cards are links to the list with the preset (the dialog URL also redirects there). The card's number, the dialog and the list preset come from the same query (`activities.services.preset_activities()`, `LIST_PRESETS`): open = item 77's rule, conducted_month = Conducted with `end_date` in the current Manila month (item 77, not the From / To overlap filter), conducted = Conducted. **List presets** exist only for "Show all in the list": the URL keeps the preset; search, type, category and sort combine with it (From / To with open and conducted); a status filter, or From / To with conducted_month, drops it; the chip "Showing: <name> · Clear" appears only then (Clear removes only the preset); an unknown preset is ignored with the amber note. Cards have no active, clicked or highlighted style; keyboard focus only (`:focus-visible`, `.stat-card-link`) shows a soft ring (#1D4ED8 at 35 %) rounded like the card.
342. **Email success messages in development mode** (2026-11-01). While the default mailer only prints emails (the console mailer: `config.mail.prints_emails()`, the same check as the Admin Console's System status), every staff success message that says an email was sent says instead "Email printed in the terminal (development mode). Set EMAIL_REAL_SENDING=True to send real emails." (`config.mail.email_sent_message()`): member invitation and administrator password reset (Admin Console, Django admin, API), set-password link (staff page, Django admin, API), "Send evaluation links" (keeps "Queued N evaluation link emails.") and `send_test_email` (with `--smtp` it checks the SMTP mailer). Public pages keep their neutral wording (registration confirmation, forgot password, general evaluation link): they must look the same in every case.
343. **Every modal is centred** (2026-11-01). Cause of the top-aligned dialogs: not Tailwind's preflight but the parent's `space-y-5` utility, which sets `margin-top: 0` / `margin-bottom` on every child, the `<dialog>` included, and utilities win over the `.modal` component rule. `.modal` (frontend/tailwind/app.css) is now `position: fixed; inset: 0; margin: auto !important` (the one `!important`: a parent's layout utility must never move a dialog), at least 16 px from the edges (width 100% - 2rem, height at most 85% of the screen, `85dvh`) and taller content scrolls inside the dialog. Applies to every `<dialog class="modal">`: components/modal.html (normal and remote), documents/_dialog.html (return / replace / delete, preview), the registration details and evidence dialogs. Checked with `config/tests/test_modals_browser.py` (opt-in, Playwright: `BROWSER_TESTS=1`), which opens nine modals at 1366×768 and 390×844 on the demo data in the test database and checks centring, the 16 px margins, the 85% height, the dimmed backdrop, Escape and the focus going back to the trigger. "Invite member" is not a modal (an expandable section), so it is not in the list.

344. **Only the Director approves or returns documents** (owner change request, 2026-11-05; replaces the reviewer rule of items 118, 129, 189, 239, 291 and 295). *(Open question, CLAUDE.md.)*
     - **New role flag `ROLE.can_approve_documents`** (boolean, default False), next to `can_create_activity` and `can_manage_users`; `Permission.APPROVE_DOCUMENTS`. Migration `accounts/0004_role_can_approve_documents` adds the column and sets it to True for the role named "Director" and False for every other role (looking the role up by name happens only in this migration; the code checks the flag). Without a "Director" row nobody gets it. `seed_data` gives it to the Director **only when it creates the role**: unlike the two older flags it is never re-applied, so running `seed_data` again keeps the System Administrator's choice.
     - **Who changes it:** the System Administrator, on the Admin Console roles screen (a Turn on / Turn off button per role, `accounts.services.set_role_document_approval`, POST `enabled=true|false`) or in the Django admin role form. Both go through `update_role_permissions`, which writes `Role flag changed: … can_approve_documents False -> True by …` on `accounts.audit`. This is the one exception to item 224.
     - **Separation of duties:** a role with `can_manage_users` may not also have `can_approve_documents` ("A role cannot both manage users and approve documents (separation of duties). Turn one of the two flags off."; `check_role_flags`). `can_approve_documents` does not need `can_create_activity`.
     - **No last-approver guard:** turning the flag off for the last role with active approvers is allowed; the roles screen then shows an amber note "No active member can approve documents." and uploads wait for review until a role can approve again.
     - **Approve and return need `can_approve_documents`**, checked in `documents.services` (`_ensure_can_review`) for activity documents and certificate evidence alike. Members without it get 403 from `documents:approve`, `documents:return`, the JSON APIs (`/documents/api/<id>/approve|return/`, `/certificates/api/evidence/<id>/approve|return/`), the review tab `/documents/review/` and `/documents/api/review-queue/`. Certificate status approval (`set_certificate_status`, sign-off with `can_create_activity`) is unchanged.
     - **Self-approval is allowed:** an approver may approve their own upload; `reviewed_by` is then the uploader (the document's usual review record: documents have no ACTIVITY_AUDIT_LOG rows). Every approval and return is also logged at INFO on the logger `documents.audit` (document ID, member ID, "(own upload)" when it applies; never file contents). **Returning your own upload is refused** ("You cannot return a document you uploaded. Replace or delete it instead.") and its Return button is hidden.
     - **What members without the flag no longer see:** Approve and Return buttons (`allowed_actions`), the "Waiting for my review" tab, the sidebar's Documents badge (the review queue is empty for them) and the dashboard's "Documents to review" card and Needs attention item (they see the read-only S4 count, as before). The review queue of an approver includes their own uploads (they may approve them).
     - **New notification "Document waiting for your review"** (`NOTIFICATION.type = "document_waiting_for_review"`, in-app, with the activity and document type): created by `documents.services.notify_approvers` for every **active** member with the flag **except the member who uploaded or replaced the file**, on every upload and every replacement. It is saved in the same transaction as the document, so a refused upload or a refused "new activity with documents" notifies nobody. No uniqueness rule (each upload/replacement is a new request for review).
     - **Unchanged:** any active staff member uploads; only the uploader replaces or deletes, while For Review; Approved is final; only Approved activity documents are indexed for the assistant (queued on approval). Missing / overdue **reminders still go to the activity's creator** (they ask for an upload, item 125), not to the approvers.
     - **ERD:** ROLE gets one row after `can_manage_users`: `boolean | can_approve_documents | true only for Director (approves or returns documents)`.

## Start and end time, automatic status, needs attention, evaluation links, attendance (2026-11-06)

345. **Start and end time replace `time_schedule`** (ERD change). ACTIVITY gets `start_time` and `end_time` (Manila time of day, nullable) and loses the free-text `time_schedule`.
     - **Migration `activities/0005`** adds both, fills them by reading `time_schedule` with `activities.time_parsing.parse_time_schedule` ("-", "–", "—" or "to" between the two times; each side AM/PM or 24-hour; a side without AM/PM takes the other side's, or the opposite one when only that puts the end after the start), and removes the old column. An unreadable text (one time only, "9-5", "TBA", end not after start) leaves **both empty**; the migration prints how many were read and left empty. The reverse migration rebuilds the text ("8:00 AM - 5:00 PM", or empty).
     - **Database rules:** both times set or both empty; on a one-day activity the end time is after the start time. A several-day activity may end earlier in the day than it starts (8:00 AM on day one to 12:00 PM on the last day).
     - **Form and services:** two required time pickers (`<input type="time">`, the same 42 px height as the date inputs). `validate_activity_values` requires both on create and on every edit (so an old activity without times must get them on its next edit) and refuses "End time must be after the start time." on a one-day activity. History labels "Start Time" / "End Time", values like "8:00 AM"; old "Time Schedule" rows stay as they are.
     - **One display:** `activities.formatting.format_time_range()` → "8:00 AM – 5:00 PM" or "Time not set" (template filter `time_range`): activity page, list, overview, public and portal pages, confirmation and evaluation emails ("Time: …" line), attendance sheet, the reports' new "Time" column (activities table, PDF and Excel) and the calendar file (item 283). The AI record tools return `start_time` / `end_time` ("08:00", Manila). Public JSON: `start_time`, `end_time` and `time_text` instead of `time_schedule`.
     - `activities.services.activity_start_end(activity)`: start and end as Manila date-times; without times, 00:00 on the start date and the end of the end date (00:00 the next day).
346. **Automatic status moves** (item 66). `automatic_next_status()` is the rule: Scheduled → Ongoing when `now >= start`; Ongoing → Conducted when `now >= end` **and** at least one registration is marked attended (`has_attendance`). Without attendance the activity stays Ongoing (the Needs attention box says so); once staff mark someone, the next run or page load conducts it. Draft and Conducted are never touched. *(2026-11-12, item 362: the move to Conducted also creates the certificates.)*
     - `apply_automatic_statuses(now=None)` (every candidate) and `refresh_status(activity)` (one) lock each row with `SELECT … FOR UPDATE SKIP LOCKED` (a row being edited is left for the next run) and use the same transition code as `change_status` (`_move_status`); one run can make both moves (two audit rows). The audit row has `changed_by` empty = **System** (ERD change, migration `activities/0006`); History, the dashboard's recent changes, the audit API and the Django admin show "System" (`formatting.changed_by_name`).
     - Runs: the Django-Q2 schedule "Activity statuses and evaluation links" every `ACTIVITY_STATUS_INTERVAL_MINUTES` (5; `config/schedules.py` now has a "minutes" kind, `MinutesSchedule`; `setup_schedules` creates it and System status lists it as "Every 5 min"), on every activity page load (`refresh_status`) and Activities list load (`apply_automatic_statuses`). **Page loads only change statuses and never send emails.**
347. **Needs attention** (`activities/attention.py`). `activity_attention(activity, now)` (the amber box below the activity title, each item linking to its tab or the edit page; a small green "All set" when empty) and `attention_counts(activities, now)` (the warning icon with the count on the Activities list, `role="img"` with an aria-label) share one function that turns loaded facts into items, so they always agree (parity test); the facts for any number of activities take five queries (test). Items reuse existing rules only: set the start and end time (not Conducted); Draft whose start date is within `ATTENTION_DRAFT_DAYS_BEFORE` (7) days or past; Ongoing with no attendance ("Activity is ongoing: mark attendance on the Registrations tab." / after the end: "Activity ended but no attendance recorded. Mark attendance or change the status."); registration form missing (Draft/Scheduled); evaluation form missing (until the evaluation deadline); required documents overdue or due soon and not uploaded (the daily reminders' rule, `documents.services.reminder_kind`, on `document_checklist`'s items via `required_items`), with their names; documents waiting for approval and returned to the uploader (the review states); Conducted with attendees but no certificates; certificates waiting for review (Generated), for sign-off (Pending Sign-off, not both signed), for approval (both signed); resource-person certificates Approved but not handed over; Issued email certificates not sent; evaluation link emails failed or not sent yet (Conducted, a form, the window open). *(2026-11-08: two more items, item 356.)* *(2026-11-12, item 362: the certificate line counts only ready certificates.)*
348. **Evaluation links are sent automatically** (items 115, 216, 276). REGISTRATION gets `evaluation_link_sent_at` and `evaluation_link_failed_at` (ERD change, migration `registrations/0006`). The 5-minute job (`activities.tasks.run_activity_automation`: first the statuses, then `registrations.services.send_automatic_evaluation_links`) sends the link once to each attendee who has not evaluated and has neither date set, only for Conducted activities with an evaluation form and an open window (`automatic_link_activities`). It runs inside qcluster and locks each row (SKIP LOCKED), so nobody gets two emails. Sent clears failed and vice versa; failed sends are not retried automatically; staff resend on the Evaluation tab. *(2026-11-21, item 419: also while the activity is still Ongoing, from `EVALUATION_LINK_HOURS_BEFORE_END` (3) hours before the end inside the evaluation window; one History row by System per run that sent links, shown on the Evaluation tab.)*
349. **Who marked attendance and when.** REGISTRATION gets `attendance_marked_at` and `attendance_marked_by` (FK to TBI_MEMBER, PROTECT, nullable; ERD change, migration `registrations/0006`), set by `set_attendance` (also from the Django admin actions, which call it with the signed-in member) and cleared with the method on undo. Every mark and undo is logged at INFO on `registrations.audit` with the activity, registration and member IDs only (never names or emails). Not written to ACTIVITY_AUDIT_LOG (item 111). Old attended rows have no marker ("Marked by" is not shown for them).
350. **Upload box text:** "It starts as “Waiting for review”. The Director (a member with approval rights) approves it." (item 344: only members whose role has `can_approve_documents` approve).

## Dashboard "Happening now" and the Documents page by activity (2026-11-06)

351. **Dashboard spotlight: "Happening now" before "Next activity".** The card beside the banner is chosen by `reports.services.dashboard_spotlight()` (never the template): an activity happening now wins over the next upcoming one. *(2026-11-08: an activity whose end time has passed today is no longer "happening now", item 353.)*
     - **Happening now** = `activities.services.happening_now()`: status Ongoing and today (Manila) between its start and end date, the one that started first first (`activity_start_end()`'s order: start date, then start time). Several: the first is shown with "+N more ongoing", a link to the Activities list filtered to Ongoing (`?status=<id>`).
     - The dashboard load first runs `apply_automatic_statuses()` (as the Activities list; statuses only, never emails), so an activity whose start time has passed is already Ongoing: the same rules as the 5-minute job (item 346).
     - An Ongoing activity **past its end date** (attendance not marked yet) is not "happening now" (owner decision): the Needs attention box covers it. Draft is never shown; the activity-type filter is respected.
     - Otherwise the first Scheduled activity of S2 (unchanged). The card for an ongoing activity: label "Happening now", the time, ring **attended / registered** (`attended_count()`, the `has_attendance()` rule counted), button "Mark attendance" → the Registrations tab. The "Upcoming activities" list then starts with the first upcoming activity.
352. **Documents page organised by activity** (`/documents/`, `documents.pages.repository_page`). No new rules: everything comes from `documents.services`.
     - **Summary cards:** Waiting for approval (members with `can_approve_documents`; others see the same number as "Waiting for the Director"), Returned, Missing, Approved (`document_state_counts()`): documents by their review state (S4's rules) and required types with nothing uploaded in **non-Draft activities** (owner decision; the reminders' and S3's scope). A card is a link that filters the page by its state; clicking it again clears it. The selected card (the URL's `state`) has `aria-pressed="true"` (links with `role="button"`; Space works) and `.stat-card-link`'s selected look: 2px primary border following the corners, blue tint, filled icon circle; the others fade to 80%. Never a browser outline on click; keyboard focus = a rounded blue ring. The cards are re-rendered with every swap (filters, cards, view switch), and `accounts.permissions.is_htmx()` is False for HTMX's history restore, so back/forward without the cache gets the whole page.
     - **"By activity"** (default): one card per activity of `overview_activities()` (every non-Draft activity, plus Draft activities that already have documents; Ongoing first, then the newest start date; 10 a page). `activity_document_overview()` gives `document_checklist()`'s required items for many activities in a fixed number of queries (same `_required_item` rule; parity test). Card: title, dates, status, "N of M approved · K missing" with a progress bar; a native `<details>`, open when something is missing, waiting or returned (or a filter is in use), closed when complete. Lines: required types in due-date order (each upload, newest first, later ones indented; or "Missing" with the due date and an Upload link to the activity's Documents tab with the type preselected), then the other documents. Actions from `allowed_actions()` (Approve / Return only with `can_approve_documents`). No table: below 1280 px the actions move under the text.
     - **"All documents"** (`?view=all`): the flat table of item 295, unchanged.
     - **Filters on one row:** search (activity title, document title, file name, type), activity, type, state (Missing only "By activity"; choosing it switches there), "AI search: included / not included" (was "Searchable or not"). The "Uploaded by" field is removed from the row (owner decision); `?uploaded_by=` and the old `?review_state=` still work. Everything is in the URL (`view`, `q`, `activity`, `document_type`, `state`, `searchable`, `page`); HTMX swaps the part below the title. The first tab is now called "Repository".
     - Screenshots: `docs/design/screens/documents-by-activity.png` (1366 px) and `-1024.png`, `dashboard-spotlight.png` (taken as a member without approval rights).

## Grouped Activities list, follow-up, evaluation link requests (2026-11-08)

353. **Activities list: "Grouped (recommended)" is the page's default sort** (owner request). `activities.services.grouped_activities()` applies the list filters unchanged (search, type, status, category, From / To, preset; `_filtered_activities`, shared with `list_activities`) and puts each activity in one section with `activity_group()` (`activity_start_end()` and the automatic job's rules, item 346; the page runs `apply_automatic_statuses()` first):
     - **Happening now:** Ongoing, today within its dates and its end not passed (`is_happening_now()` = `happening_now()`, parity test), earliest start first.
     - **Needs follow-up:** Ongoing whose end has passed (exactly what `automatic_next_status` cannot conduct: no attendance yet) and Draft whose start has arrived. Each card shows its Needs attention text (attention items now have a `kind`: "draft", "attendance"). Ongoing cards: "Mark attendance" (the Registrations tab) and "Mark as Conducted" (a confirm `<dialog class="modal">`, POST to `activities:change_status` with `next=list`, so `change_status()` and its audit row; offered when `allowed_next_statuses()` has Conducted), both only with `can_create_activity` (the view answers 403 without it). Draft cards (owner decision): the "Still a Draft but it started on …" text and "Open activity" (a Draft can go neither to Conducted nor take attendance).
     - **Upcoming:** Scheduled, plus Ongoing that starts on a later day (moved early by hand), soonest first. **Drafts:** Draft whose start is still ahead, soonest first.
     - **Past:** Conducted, newest first, the only paginated section (20 a page), with month headings ("October 2026", by start date). The other sections show every activity.
     - Each section has a header with its count; empty sections are hidden. The other eight sort options give the plain list as before. `?sort=` is left out of the URL for the default; the JSON API's default stays `-start_date` (`DEFAULT_SORT`); `GROUPED_SORT` is a page option only.
     - **`happening_now()` (dashboard spotlight) now also leaves out an Ongoing activity whose end time has passed today** (owner decision; refines item 351), so the dashboard and the list agree.
     - **The "newest first" bug** (owner report: October activities shown oldest first under "Start date: newest first"). The service was right; the page was not. HTMX saves a copy of the page's HTML before each pushed URL and puts it back on Back / Forward, and a `<select>`'s current choice is not in the HTML (only its `selected` attribute). After "sort oldest first → another filter → Back", the list was the oldest-first one while the dropdown showed its first option. Reproduced and checked in Chromium (`config/tests/test_history_browser.py`, opt-in `BROWSER_TESTS=1`). Fix in `templates/base.html`: after every HTMX swap (`htmx:afterSettle`) the current values of every filter form (`form[hx-push-url]`: selects, checkboxes, radios, text and date inputs; never passwords or files) are written into their attributes, so the copy saved before the next swap matches the list it shows (on `htmx:beforeHistorySave` it would be too late: the control that triggered the request already holds the next value). Also, **ties are now broken in the sort's own direction**: start date, then start time, then ID (before, always ID ascending).
354. **Requests on the general evaluation link are recorded per registration** (owner request; the public answer is unchanged, item 335). ERD change, migration `registrations/0007`, three nullable REGISTRATION columns:
     - `evaluation_link_requested_at` (datetime) and `evaluation_link_request_result` (string, enum `queued` / `not_attended` / `already_evaluated` / `window_closed`): **the latest request only**, written by `request_evaluation_link()` on every registration of the activity with the typed email, in this order: the page took no requests (`window_closed`: not open yet, closed, or no evaluation form), not marked as attended, already evaluated, otherwise queued. A POST on a page that is no longer open (left open past the deadline) still goes through the guard and the service to record `window_closed`, and gets the same reason card as a GET. **An email that matches no registration is stored nowhere.**
     - `evaluation_link_delivery` (string, enum `sent` / `printed_dev` / `printed_demo`): how the last link email went out, set by `deliver_evaluation_link()` with `evaluation_link_sent_at` (cleared on failure): `printed_dev` while the mailer is the console one (`config.mail.prints_emails()`), `printed_demo` for a blocked demo address (`config.mail.is_blocked_address()`, printed by `GuardedSMTPBackend`), otherwise `sent`. The owner asked for the existing columns where possible: `evaluation_link_sent_at` only says the mailer accepted the email, and the email mode can change later, so this one extra column was needed. `evaluation_link_sent_at` keeps its meaning (the automatic job still sends once). Rows sent before this change have no delivery value and show "Sent".
     - **Evaluation tab:** "Link requests from the general evaluation link" (`registrations.pages.link_requests`, the 20 latest, newest first): "Name · requested Nov 7, 8:51 PM · Sent" or "Not sent: not marked as attended / already evaluated / the evaluation was not open / only printed in the qcluster terminal (development mode) / demo address, only printed in the terminal / the email failed (resend it below)", or "Queued: waiting for qcluster to send it" (`link_request_outcome`: a delivery counts for a request only when it is at or after `requested_at`). It may name someone who has already evaluated: staff see that on the Registrations tab anyway; still never ratings, comments or answers. The attendees table shows "Printed only" instead of "Sent" for a printed email.
     - **Development mode note** (when `prints_emails()`): "Emails are only printed in the qcluster terminal, not sent. Set EMAIL_REAL_SENDING=True in .env (and restart runserver and qcluster) to send real emails."
     - **ERD:** REGISTRATION, after `evaluation_link_failed_at`: `datetime | evaluation_link_requested_at | nullable`, `string | evaluation_link_request_result | nullable, enum: queued / not_attended / already_evaluated / window_closed`, `string | evaluation_link_delivery | nullable, enum: sent / printed_dev / printed_demo`.
     - **Why the owner's email did not arrive (2026-11-07, development database, checked read-only):** the rate-limit window of their address and 127.0.0.1 started at 20:51; the only general evaluation page open then was "Market Research Training" (#13, until Nov 13), whose registrations are all demo addresses, so nothing matched and nothing was queued (no Django-Q row, no task result; a request that matches nobody logged nothing). Their address is registered for "Ideation Bootcamp" (#23: attended, not evaluated, but its evaluation closed Nov 6 12:00 AM and attendance was marked Nov 6 9:54 PM, after it) and "Product Development Workshop" (#12: not attended; closed Nov 7 5:00 PM). With `EMAIL_REAL_SENDING=False` a queued link would only have been printed in the qcluster terminal anyway.
355. **Rate-limit keys hold no email or IP address in plain text** (`config.ratelimit._key`): the value is stored as an HMAC-SHA256 keyed with `SECRET_KEY` (registration, forgot password, general evaluation link). Before, the typed email was part of the `django_cache` key for up to an hour. The login lockout keys (`accounts.services`) are unchanged.
356. **Needs attention, two new items** (item 347): an Ongoing activity whose end has passed **with nobody registered** shows "No one registered. Mark as Conducted or change the status." (to the Overview) instead of "mark attendance" (while it is still running the "Activity is ongoing…" text stays); and "Attendance was marked after the evaluation closed. These attendees can't evaluate." (to the Registrations tab) when an attendee who has not evaluated was marked after `evaluation_deadline` (`attendance_marked_at > evaluation_deadline`; owner's Ideation Bootcamp case). It stays as long as such an attendee exists: a Conducted activity is read-only, so its deadline cannot be extended. The facts still take five queries (the registered and late counts are in the existing annotate query); the automatic rule is unchanged (Conducted only with attendance).

## Certificates approved by the Director, preview, failed emails, document Back link (2026-11-11)

357. **Only the TBI Director approves certificates, once per activity** (owner change request; replaces the sign-off of items 42, 46, 181 and the S5 definition of item 196). *(Open question, CLAUDE.md.)* *(2026-11-12: who approves, the signatures, the separation-of-duties rule for this flag, the wording and the tab layout changed: item 361. The statuses, approve all, regenerate, the columns and the counts by activity stay.)* *(2026-11-12, item 362: replaced in part: certificates are created automatically, approved one by one on the preview or "Approve all ready certificates" once ready; S5 counts ready ones only.)* *(2026-11-13, item 363: the VP / Director signature reasons became "Add the signature of <name> (<position>) first.")* **(2026-11-14: the approval part is REPLACED by item 369: one approval per activity, stored on the activity; S5 counts activities that need approval.)** *(2026-11-21, item 418: adding signatories and signature images, the Signatories page and the defaults moved to the new flag `can_manage_signatories`, the Director only; approving stays with this flag.)* *(2026-11-21, item 421: can_approve_certificates is now the Director's only by default; Retry and signature images also for can_create_activity.)*
     - **New role flag `ROLE.can_approve_certificates`** (boolean, default False) and `Permission.APPROVE_CERTIFICATES`. Migration `accounts/0005_role_can_approve_certificates` adds it and sets it to True for the role named "Director" only (same pattern as `can_approve_documents`, item 344; development database: "Director" found). `seed_data` sets it only when it creates a role (never re-applied). The System Administrator switches it on the Admin Console roles screen (new column "Approve certificates", `accounts.services.set_role_certificate_approval`, POST `/accounts/console/roles/<id>/certificate-approval/`) or in the Django admin role form; both go through `update_role_permissions` and are logged on `accounts.audit`. **Separation of duties:** `check_role_flags` refuses it together with `can_manage_users`. No last-approver guard; the roles screen warns "No active member can approve certificates."
     - **Who does what:** generate, regenerate, mark handed over and resend need `can_create_activity` (the Director and the two Project Heads); approve needs `can_approve_certificates` (the Director); Admin Staff and the System Administrator only view, preview and download (and still upload evidence: the Step 6 rule, owner decision). *(Since item 388 Admin Staff create activities and approve certificates.)*
     - **Statuses: Generated (shown as "Waiting for approval") → Approved → Issued.** Allowed moves: Generated → Approved; Approved → Issued; Approved → Generated (only when regenerated). Issued is final (item 46). Pending Sign-off, Review, Reject and the two "Record sign-off" actions are removed. The VP does not approve in the system: their signature is an image on the certificate.
     - **ERD change on CERTIFICATE** (migrations `certificates/0003_director_approval` (two columns + data) and `0004_director_approval_columns` (rename/remove), two migrations because PostgreSQL refuses to ALTER a table in the same transaction as row updates with pending deferred foreign-key checks): `reviewed_by` renamed **`approved_by`** (FK to TBI_MEMBER, nullable: who approved); new **`approved_at`** (datetime, nullable); **`reviewed_at`, `director_signed_at`, `vp_signed_at` removed**; new **`send_failed_at`** (datetime, nullable, item 360); `status` = Generated / Approved / Issued. **Data (development database, 2026-11-11):** 31 Pending Sign-off certificates moved back to Generated (they wait for the Director); Generated rows lost `reviewed_by`; the 157 Approved (54) and Issued (103) certificates were kept as approved under the old rule, `approved_by` = the old `reviewed_by`, `approved_at` = the latest sign-off, else the review time, else the issue time. Result: 100 waiting, 54 Approved, 103 Issued.
     - **Approval** (`certificates.services.approve_certificates(member, certificate_ids=… | activity=…)`): one certificate, a selection, or **"Approve all for this activity"** (every certificate waiting for approval), always all or nothing, rows locked. It sets `approved_by` and `approved_at`, logs "Member X approved certificates …" on the `certificates` logger, and queues the PDF of each ready certificate after the commit (resource persons at once; participants once evaluated, item 184).
     - **Signature images:** approval is refused while the VP's or the Director's signature image **of the signatories stored on the certificate** is missing or not a PNG (the file is read: a deleted or damaged file counts as missing): "The VP's signature image is missing. Upload it in the Admin Console, then regenerate the certificates." (same for the Director; `services.missing_signature_reason`, also `approval_closed_reason(activity)` for the tab). `signature_image_path` is already required by the database, so in practice "missing" means a file that disappeared from the disk; a new signatory does not change existing certificates (item 180), so the fix is upload + regenerate.
     - **Regenerate** (`regenerate_certificates`, `can_create_activity`, not Issued): applies the template, VP and Director that are active now, removes the rendered PDF (after the commit) and sets the certificate back to Generated with `approved_by` / `approved_at` cleared: **the Director approves it again**. The number stays. There is no other way to edit a certificate (a Conducted activity is read-only). A render that started before a regeneration is never saved (`prepare_certificate` compares the design under the row lock: "design changed").
     - **Certificates tab:** an approval panel when certificates wait: "Waiting for your approval" (Director) or "Waiting for the Director's approval" (others), the summary "26 certificates · 24 participants by email · 2 resource persons by hard copy" (`pages.approval_summary`), a preview of the first waiting certificate with Previous / Next (an `<iframe>`, the list in a `json_script`), and for the Director "Approve all for this activity" behind a confirm `<dialog>` (POST `activities:certificates_approve_all`), disabled with the reason when a signature image is missing. Bulk actions per permission: Approve selected (Director), Regenerate, Mark handed over, Resend email (`can_create_activity`); the bulk view checks the action's permission (403) and the service checks it again. API: `/certificates/api/<approve|regenerate|hand-over|resend>/` (the old review / sign / reject answer 404).
     - **Dashboard and Needs attention count activities:** S5 (`metrics.certificates_now`: `waiting_activities` = distinct activities with Generated certificates, `waiting_certificates`, `approved_not_issued`; one query); card key `certificates_waiting_for_approval` (value = activities, `certificates` = the count; the API changed accordingly). KPI "Certificates waiting for approval": the activities, a pill "100 certificates" and the note "activities · waiting for your approval" / "… waiting for the Director". Needs attention: "Certificates waiting for approval: 3 activities (100 certificates)", detail "Waiting for your approval · <titles>" with "Approve" for members with the flag, "Waiting for the Director · <titles>" with "Open" for others; one activity → its Certificates tab, several → `/certificates/?status=generated`. The activity page's box (item 347) has one certificate line "N certificates waiting for your approval / for the Director's approval" (`activity_attention(..., can_approve_certificates=)`: wording only, the items and counts are the same for everyone) instead of the review / sign / approve lines. The Certificates report's "Right now" table shows the certificates and the activities waiting for approval.
     - **Demo data:** `seed_demo` approves its "approved" demo activities as the demo Director; the former Pending Sign-off demo activities stay waiting. `certificate_walkthrough` steps: approve (as the Director by default) and regenerate; review / sign / reject removed.
358. **Certificate preview inside the system** (owner decision: a small corner mark until Approved). *(2026-11-12: the preview is now a large panel on the tab with Previous / Next through every certificate; the row's Preview shows that certificate there; item 361.)* *(2026-11-12, item 362: an Issued certificate's preview is its stored final PDF; ?v= and no-store; the preview starts at the first row of the list.)* *(2026-11-13, item 364: an Issued certificate's copy changes only when a member reissues it.)*
     - `services.preview_pdf()` renders the certificate **exactly as printed**: the same template as the final PDF (`pdf.certificate_html`), the name, activity, dates, certificate number and **both signature images**. While the certificate waits for approval a small "PREVIEW" mark sits in the top-right corner (`services.preview_marked`); once Approved there is no mark. The big "PREVIEW – NOT VALID" watermark and the "(name from evaluation)" line are gone. A signature file that cannot be read is left blank in a preview (approval is refused anyway, item 357); a final render still fails.
     - **Name:** a participant's printed name comes from the evaluation (item 175). Until it is submitted the preview uses the registration name and the dialog says so outside the certificate ("The printed name is the one the participant types in the evaluation."; `services.preview_name`).
     - **Where:** "Preview" on each row opens the shared `#certificate-preview` dialog (HTMX, `certificates:preview_dialog`, its own page without JavaScript) with the PDF in an `<iframe>` of `certificates:preview` and "Download PDF". `certificates:preview` is now the **second** `xframe_options_sameorigin` view (with `documents:view`, item 330): rendered by us, inline, `nosniff`; every other page keeps `X-Frame-Options: DENY`. Any active staff member may preview (signature images are now visible to staff in previews; item 179's rule for the image files themselves is unchanged).
     - **Download PDF** (`certificates:download`): the final PDF once it is rendered; before that, the preview rendered now as an attachment `<number>-preview.pdf` (before: 404).
359. **Document page Back link** (owner request). Links to a document's page from the Documents page (both views and the review tab) and from the activity's Documents tab (and certificate evidence) carry `?next=<the page in the address bar>` (`{{ request|here|urlencode:"" }}`; `config.links.current_page` uses `HX-Current-URL` for HTMX partials, so filters and the view are kept). `documents.pages.back_link` uses it only when `config.links.is_internal_path` accepts it (starts with one "/", not "//" or "/\", same host): "Back to Documents", "Back to the documents of <activity>", or "Back"; otherwise the old link to the activity's tab. The Activity link in the details stays. Fixed outdated text on the document page: "Not reviewed yet. The Director (a member with approval rights) approves or returns it." (was "A member who can create activities (not the uploader) …"); also the Certificates page subtitle and the dashboard's empty Needs attention text no longer mention sign-off.
360. **Failed certificate emails and Resend.** `send_certificate` records `send_failed_at` when sending fails (cleared, with `sent_at` set, when it succeeds); the automatic retries (item 185) are unchanged. The Certificates tab's Sent column shows the date, **"Send failed"** with a **Resend** button (its own small form per row), "Waiting for qcluster" (Issued, not sent yet), "After the evaluation" (Approved) or "After approval". **Resend** (`resend_certificates`, `can_create_activity`, Issued email certificates not sent) queues the email again; `send_certificate` still sends each certificate at most once. In development mode (`config.mail.prints_emails()`) the tab shows the Evaluation tab's note "Emails are only printed in the qcluster terminal, not sent. …" and the Resend message ends with the console-mode notice (item 342). Needs attention: "N certificates could not be emailed. Resend on the Certificates tab." (separate from "issued but their email not sent yet").

## Certificate approvers, signatures on the Certificates tab, new-activity notification (2026-11-12)

361. **Certificates are approved by the Director, both Project Heads and the System Administrator, who also add the signatures** (owner change request; changes item 357, keeps its statuses Generated ("Waiting for approval") → Approved → Issued, "Approve all for this activity" with a confirm dialog plus "Approve selected", regenerate, `approved_by` / `approved_at`, `send_failed_at` and the counts by activity). *(Answered: items 418, 421.)* *(2026-11-12, item 362: "Approve all for this activity" and "Approve selected" are replaced by Approve on the preview and "Approve all ready certificates"; only ready certificates are approved and counted.)* *(2026-11-13, item 363: the VP and Director slots are replaced by each activity's left and right signatory; replacing an image is a new version for every activity that uses the person.)* **(2026-11-14: "Approve all ready certificates", Approve on the preview and approving one certificate are REPLACED by item 369; who approves (the flag) stays.)** *(2026-11-21, item 418: adding signatories and signature images, the Signatories page and the defaults moved to the new flag `can_manage_signatories`, the Director only; approving stays with this flag.)* *(2026-11-21, item 421: can_approve_certificates is now the Director's only by default; Retry and signature images also for can_create_activity.)*
     - **Who:** `can_approve_certificates` (approve; add or replace signatures) is True for Director, Incubation Project Head, Community Development Head ("Community Development Project Head" in the owner's words: the same role, not renamed) and System Administrator, False for Admin Staff *(seed_data gives it to Admin Staff too since item 388; migration 0006 is unchanged)*. Migration `accounts/0006_certificate_approvers` sets it (by name, only in the migration; going back = Director only again); `seed_data` uses the same defaults **only when it creates a role**; the Admin Console roles screen still switches it. Generate, regenerate, mark handed over and resend stay with `can_create_activity` (Director, both Project Heads): **the System Administrator approves and adds signatures but never generates**. Admin Staff view, preview, download and upload evidence only.
     - **Separation of duties:** `check_role_flags` no longer refuses `can_approve_certificates` with `can_manage_users` (only this flag). A role with `can_manage_users` still may not create activities or approve documents.
     - **Signatures section on the Certificates tab** (`certificates.pages.signatures_panel`): a VP and a Director slot with the signatory this activity's not-Issued certificates use (the newest if they differ), otherwise the active one (`services.activity_signatories`); "Added" / "Missing"; a thumbnail and the upload form only for members with the flag (signature images are served by `certificates:signature_image` to `can_manage_users` or `can_approve_certificates`, never from `/media/`; 404 while there is no image). Without an active signatory for a position: "There is no active VP signatory yet. The System Administrator adds one in the Admin Console first." (no upload).
     - **Adding or replacing** (`services.upload_signature`, POST `/activities/<id>/certificates/signatures/<vp|director>/`): PNG or JPG, at most `CERTIFICATE_IMAGE_MAX_MB` (5), checked by its real content (`validate_signature_image`, now PNG or JPG everywhere; the help text recommends a transparent PNG because a JPG prints its white background). **Add** (the signatory has no image yet) stores the image on that row, so every certificate that uses it, of any activity, gets it. **Replace** creates a new signatory row with the same name and position (an image never changes after upload, item 180), active if the old one was (the old one is deactivated), so other activities keep the signature they were generated with. Either way the activity's certificates that are not Issued are switched to it (`_refresh_certificates`, also used by regenerate): the rendered PDF is removed after the commit and Approved ones go back to "Waiting for approval" (`approved_by` / `approved_at` cleared). Issued certificates never change. One line on the logger `certificates.audit`: `Signature replaced: position=vp signatory_id=1 -> 3 activity_id=12 certificates_updated=26 by member_id=4` (member and signatory IDs only; the Admin Console upload logs `Signature added: … (Admin Console)`).
     - **ERD change:** `CERTIFICATE_SIGNATORY.signature_image_path` is **nullable** (migration `certificates/0005_signature_image_optional`): the Admin Console (and the Django admin) can add a signatory by name and the image is added later. Certificates can be generated without the images (the preview shows a blank signature line); **approval is refused** while one of the certificate's signatories has no readable PNG/JPG image: "Add the VP's signature first." / "Add the Director's signature first." (`missing_signature_reason`, `approval_closed_reason`; the tab shows the reason to everyone).
     - **Large preview on the tab** (`pages.preview_items`): as soon as certificates exist, the first certificate (number order) in a large `<iframe>` of `certificates:preview` with Previous / Next through all of them, its number, name, type, status and "Download PDF" (the final file once rendered, otherwise the preview). Rendered from the same template as the PDF; the "PREVIEW" corner mark until approved (item 358). The row's **Preview** button (beside the name) shows that certificate in the panel and scrolls to it (an Alpine event); without JavaScript it opens the preview page. The preview dialog is removed from the tab. A participant who has not evaluated: the registration name with the note outside the certificate "The final name comes from the evaluation." (resource persons: always final).
     - **Wording:** "Waiting for your approval" (members with the flag) or "Waiting for approval" (others) on the tab and the dashboard; "Members with approval rights approve certificates."; the Regenerate message "… waiting for approval again."; the metric label "Activities with certificates waiting for approval"; the report row "Certificates waiting for approval".
     - **Needs attention** (item 347): one certificate line, the same for everyone: "Add the VP's signature first." when a waiting certificate's signature is missing, else "26 certificates waiting for approval." (the `can_approve_certificates` wording parameter is gone). The facts take six queries (one more: the waiting certificates' signatory pairs, DISTINCT ON).
     - **Email:** unchanged (item 360): emailed once when Approved and evaluated (whichever comes last) to the evaluation's `certificate_email` with the PDF attached; "Send failed" + Resend; automatic retries; the development-mode note.
     - **New-activity notification:** when an activity is created (`save_new_activity`, so the New activity page, the API, the Django admin and the demo seed alike) by a member who is not the Director, every **active** member whose role is named "Director" (`Role.DIRECTOR`: who is told, never a permission; owner decision) gets an in-app NOTIFICATION `type = "activity_created"`, `activity` set: "<Name> created a new activity: <Title> (<Nov 20, 2026>)"; the bell and the list open the activity. Nobody is notified when a Director creates it. Saved in the same transaction as the activity (a refused activity notifies nobody). **No email** (owner decision; a daily summary email could come later).
     - **ERD:** ROLE, after `can_approve_documents`: `boolean | can_approve_certificates | Director, both Project Heads, System Administrator (approve certificates, add signatures)`. CERTIFICATE (item 357's change, not drawn yet): `reviewed_by` → `approved_by` (FK to TBI_MEMBER, nullable); remove `reviewed_at`, `director_signed_at`, `vp_signed_at`; add `datetime | approved_at | nullable` and `datetime | send_failed_at | nullable`; `status` note "Generated (Waiting for approval) / Approved / Issued". CERTIFICATE_SIGNATORY: `signature_image_path` note "nullable, max 255". NOTIFICATION: no column change (`type` gains the value `activity_created`).

## Automatic certificates, ready for approval, approve on the preview, tab fixes (2026-11-12)

362. **Certificates are created automatically and approved one by one on the preview once they are ready** (owner change request; replaces the manual Generate of items 180 and 296, the "approve all, all or nothing" and approve-before-evaluation of items 357 and 361, and narrows S5 (item 196); keeps the statuses Generated ("Waiting for approval") → Approved → Issued, `approved_by` / `approved_at`, `send_failed_at`, the signatures section, hand-over and the email rules of item 360). *(Open question, CLAUDE.md.)* *(2026-11-13, items 363, 364: creation needs the activity's two signatories; "Issued certificates are never changed" now means never automatically: a member may reissue them.)* **(2026-11-14: approving one by one on the preview and "Approve all ready certificates" are REPLACED by item 369: one approval per activity, then the System approves each certificate when it is ready; S5 / Needs attention count activities that need approval. Automatic creation, "ready" (readiness) and the statuses stay.)**
     - **Created by the System** (`certificates.services.generate_missing_certificates(activity, registration_ids=None)`, no member): one Certificate of Participation (email) per attendee and one Certificate of Appreciation (hard copy) per resource person, with the active template, VP and Director. Called from `activities.services._move_status` when the target is Conducted (by hand, the automatic move, page loads), from `set_attendance` when someone is marked attended on a Conducted activity (their certificate only), and from the 5-minute job (`run_activity_automation` → `generate_pending_certificates`, a catch-up for `activities_missing_certificates()`). Safe to run any number of times (activity row lock + the unique constraints). `auto_generate_certificates` wraps it in a savepoint: an incomplete design (no active template / VP / Director signatory) never blocks the status change; the tab shows "Certificates are not created yet: <reason>", Needs attention says "Conducted with N attendees but no certificates yet: …", and the next 5-minute run creates them. The Generate button, its page view, URL and the JSON `generate` endpoint are removed.
     - **"Created automatically" without a new column** (owner decision: no `generated_by`, no migration, no ERD change): one ACTIVITY_AUDIT_LOG row by System (`changed_by` empty), field "Certificates", new value "3 created automatically (2 Participation, 1 Appreciation)" (`activities.services.write_certificate_history`); History shows "System: certificates of <title>: 3 created automatically (…)". The tab says "Created automatically when the activity is Conducted".
     - **Undo attendance after Conducted** (owner decision): allowed while the person's certificate is still waiting for approval (not approved, not issued, no evidence): the certificate is deleted (`delete_certificate_for_undone_attendance`), its number is not reused (a gap), and a History row by the member says "<number> (<name>)" → "Deleted: attendance undone". Still refused once the person has evaluated ("already submitted the evaluation") or the certificate was approved ("Certificate approved: attendance can no longer be undone.").
     - **Ready for approval** (computed, never stored; `services.readiness()`): a participant's certificate needs the submitted evaluation (the printed name and email come from it) and both signature images; a resource person's needs both signature images. Labels instead of the Approve button: "Waiting for signatures", "Waiting for evaluation", and "Evaluation closed without an answer" after `evaluation_deadline` (never ready, no certificate is sent, not a staff task: open question, CLAUDE.md). The database version for counts is `ready_for_approval_q()` (image column filled; `readiness()` also reads the files). `readiness_map()` checks many with one query.
     - **Approve on the preview** (`can_approve_certificates`: Director, both Project Heads, System Administrator): the preview card has an Approve button for the certificate shown, enabled only when it is ready (otherwise disabled with "Not ready for approval: <label>."); POST `/activities/<id>/certificates/<certificate_id>/approve/` (`approve_certificate`, refused unless ready); afterwards the page opens on `?tab=certificates&preview=<next>`: the next certificate waiting for approval in the list (a ready one first, then from the top). **"Approve all ready certificates"** (a small secondary button, confirm dialog: "N certificates will be approved", how many are emailed and printed, how many keep waiting): `approve_certificates(activity=...)` approves only the ready ones; refused with `approval_closed_reason` (nothing waiting, "Add the VP's signature first.", or "No certificates of this activity are ready for approval yet."). The checkboxes, "Approve selected" and the per-row approve are removed; rows keep Preview, Resend (send failed) and Mark handed over (Approved hard copy with its PDF). **Members without the flag see no approve button at all** (no disabled button either); Admin Staff stays view only.
     - **Apply the current design** (owner decision; replaces the Regenerate bulk action): one secondary button for `can_create_activity`, confirm dialog; `regenerate_certificates(activity=...)`: the active template and signatories on every certificate that is not Issued; Approved ones go back to waiting; Issued are never changed.
     - **Sending:** unchanged. Because approval now needs the evaluation, a participant's certificate is rendered, issued and emailed right after approval (task). Certificates approved before this change without an evaluation stay Approved and are issued when the evaluation arrives (owner decision: old rows are not changed). Resource persons: rendered on approval, Mark handed over → Issued.
     - **Counts** (S5 of item 196, the dashboard, Needs attention): only certificates **ready** for approval. Dashboard: "2 activities (45 certificates) ready for your approval" (approvers, "Approve") or "… ready for approval" (others, "Open"); KPI "Certificates ready for approval". Needs attention: "Add the VP's signature first." when a waiting certificate's signature is missing, else "N certificates ready for approval."; certificates waiting for an evaluation are not a staff task. No certificate notification (owner decision).
     - **Tab bugs fixed:** (A1) every certificate action redirects to `/activities/<id>/?tab=certificates` (a refused row action keeps its list in the session for that one view, `pages.keep_problems`); every tab link, the Needs attention links, the Registrations "Evaluation" button, the Documents checklist "Upload" and the History pager start with the activity's own address (`activity_url`), never `?tab=` alone; the POST-only actions under `/activities/<id>/…` use `config.links.post_only_or_tab(tab)` (a GET goes to that tab, HTMX gets `HX-Redirect`) instead of `require_POST` (certificates, status, resource persons, document upload, form create, attendance, evaluation links); the documents upload error page (still re-rendered for its form errors) fixes the address bar with `history.replaceState`. (A2) The cause in the owner's data was not the Replace: the preview started on an Issued certificate, which keeps the signature it was sent with, and the list showed the registration name while the PDF printed the evaluation's name. Now an Issued certificate's preview is its stored final PDF with "Issued — this copy was already sent and is not changed." (hard copy: "…already handed over…"); preview and download URLs carry `?v=` (`services.preview_version`: status, design, signatories, file, evaluation) and are sent with `Cache-Control: private, no-store`; a Replace needs no background worker (the preview is drawn on request); when approved certificates wait for their PDF or email and qcluster is not running, the tab says so ("Certificates are waiting for the background worker."). (A3) The preview and the list are the same list in the same order (certificate number), the preview starts at the first row, and both show the printed name with "registered as <registration name>" when it differs.
     - **ERD:** no change in this step.

## Different signatories per activity, reissue with the current signatures (2026-11-13)

363. **Each activity chooses its own two signatories from a list of people** (owner change request; changes items 41, 178, 180, 183, 190, 321, 357, 361 and 362: the fixed VP / Director slots are gone). *(Open question, CLAUDE.md.)* *(2026-11-21, item 418: "Who manages it" below is now `can_manage_signatories` (the Director only) instead of `can_approve_certificates`; choosing an activity's two signatories from the list is unchanged.)* *(2026-11-24, item 431: choosing an activity's two signatories and their titles is now the Director's only (`can_approve_certificates`).)*
     - **The list (CERTIFICATE_SIGNATORY):** name, position as printed (**free text**, e.g. "University President", "Vice President for Research", "Director, NAVIGATU"), signature image (PNG or JPG, nullable until added, item 361), `is_active`. The "one active per position" rule is removed. Images are stored as `signatures/signature-<random>.<png|jpg>` (never the position or the uploaded name).
     - **Who manages it:** members with `can_approve_certificates` (Director, both Project Heads, System Administrator), on the new **Signatories page** `/certificates/signatories/` (`certificates.page_views.signatories`; linked from the Certificates page, the tab and the Admin Console): add a person (image optional), edit name / position, add or replace the image, deactivate, set the defaults. Also **"Add a person"** in each slot of the activity's Signatures section (the new person then signs on that side). JSON API `/certificates/api/admin/signatories/` now needs `can_approve_certificates`. The Django admin shows signatories and ACTIVITY_SIGNATORY **view only**. Nobody is ever deleted (no delete URL).
     - **A row that a certificate uses never changes.** Replacing the image, or editing the name or position of a person already printed on a certificate, creates a **new version** (a new row: `services._new_version`): it gets the old row's `is_active` and `default_slot`, the old row is deactivated, every ACTIVITY_SIGNATORY that pointed to the old row moves to the new one (one History row per activity: "Certificate signatory (left)": "<name> (<position>)" → "<name> (<position>), new version"), and every certificate that is not Issued and used the old row, **in every activity**, is refreshed (`_refresh_certificates`: PDF removed after the commit, Approved → waiting for approval again). Issued certificates keep the old row. A person not used by any certificate is edited in place; adding the first image is stored on the row itself (owner decisions 2, A).
     - **Impact before saving (owner addition A):** the Replace signature dialog (tab and Signatories page) and the Edit dialog of a used person say "This updates N activities. X certificates not issued yet will use the new version; Y issued certificates will be marked 'Issued with an old signature'." (`services.version_impact` / `version_impact_text`; the service returns the same counts afterwards).
     - **Deactivating** only removes the person from the dropdowns; activities that use them keep them and the tab shows "(inactive)" (owner decision 6). Refused for an active default: "<name> is the default left signatory. Choose another default first." There is no reactivation (add the person again).
     - **Defaults:** `CERTIFICATE_SIGNATORY.default_slot` (left / right, nullable, at most one row per slot; no settings table). Set on the Signatories page by `can_approve_certificates` (`set_default_signatories`: two different, active people); the Admin Console's Certificate design screen shows them **read only** with a link (owner decision 3). A new activity starts with them (`activities.services.save_new_activity` → `assign_default_signatories`, same transaction). Changing the defaults never changes existing activities.
     - **Each activity (new table ACTIVITY_SIGNATORY):** activity, signatory, slot left / right; unique (activity, slot) and (activity, signatory): the same person cannot fill both slots (also checked by the service). Changed only through `services.set_activity_signatory` by `can_create_activity` **or** `can_approve_certificates` (`accounts.permissions.require_any_permission`), in any activity status (certificates exist only from Conducted on): the person must be active; one History row; the activity's certificates that are not Issued use the new person (refreshed). Admin Staff: view only. A slot is never emptied. An activity created before the defaults existed uses the current defaults until certificates are created, then they are saved (`activity_signatories` / `_ensure_slots`).
     - **Certificates:** `vp_signatory` / `director_signatory` are replaced by **`left_signatory` / `right_signatory`** (required). The PDF prints left then right, each with its own name, position and image (`pdf.certificate_html`; no fixed "Vice President" / "Director" text). Creating certificates needs the active design and both slots ("Choose the two signatories on the Certificates tab first."). "Apply the current design" applies the active design and the activity's current signatories.
     - **Readiness:** "Waiting for signatures" = the activity's two signatories (which the certificates that are not Issued always use) do not both have an image. Approval refusal: "Add the signature of <name> (<position>) first." (left first). Needs attention uses the same text.
     - **Migration** `certificates/0006_signatory_list` (schema), `0007_signatory_data` (data, prints counts), `0008_signatory_columns` (left/right required, vp/director removed; separate for PostgreSQL's pending-trigger rule). Data: positions "vp" → "Vice President", "director" → "Director" (the labels printed until now); the active Director → default left, the active VP → default right; every certificate: left = Director, right = VP (Issued included, no PDF touched); every activity: the signatories its own certificates use (newest not-Issued, else newest Issued), activities without certificates the active Director and VP. An Issued certificate printed before a signature of its activity was replaced (old "Replace") is correctly shown as outdated. Reversible while every position is still one of the two original labels.
     - **ERD:** CERTIFICATE_SIGNATORY `position` note "free text, max 255", new `string | default_slot | nullable, enum: left / right (unique when set)`; new table ACTIVITY_SIGNATORY; CERTIFICATE `vp_signatory_id` / `director_signatory_id` → `right_signatory_id` / `left_signatory_id`. 30 tables.
364. **Issued certificates with an old signature can be reissued, only when a member decides** (owner change request; changes items 358 and 362: "an Issued certificate is never changed" now means "never changed automatically"). *(Open question, CLAUDE.md.)*
     - **Computed, no column** (owner decision 4): an Issued certificate whose left or right signatory row is not the activity's current one on that side is **"Issued with an old signature"** (`services.outdated_q`, `outdated_ids`). A replaced image or an edited name is a new row, so comparing the rows covers both. Only signatories count, not a changed design. Shown as a badge in the tab's list and on the Certificates page, and as a note on the preview.
     - **Buttons, only for `can_approve_certificates`** (members without the flag see neither; views 403, service PermissionDenied; Admin Staff have it since item 388): **"Reissue with current signatures"** on the preview of an outdated certificate (POST `/activities/<id>/certificates/<certificate_id>/reissue/`) and **"Reissue all outdated certificates (N)"** on the tab with a confirm dialog: "N issued certificates will be reissued… X will be emailed again to the participants (“Updated copy”, it replaces the earlier copy). Y hard copies need reprinting and handing over again." (POST `/activities/<id>/certificates/reissue-outdated/`; JSON `/certificates/api/reissue/`). Refused, with the reason on the tab, without an active design or while a current signatory has no image.
     - **Reissue** (`services.reissue_certificates`, all or nothing, rows locked): the activity's current signatories and the active design go on the certificate; the **number and the status Issued stay**; new column **`CERTIFICATE.reissued_at`** (datetime, nullable; owner decision 1) is set; `file_path`, `sent_at` and `send_failed_at` are cleared; one History row by the member ("Certificates": "Reissued with the current signatures: TBI-2026-00001, …"); `certificates.audit` logs the IDs; `prepare_certificate` is queued on commit. The background task renders the new PDF to the same file name (`final_pdf_name`), **replacing the old file only once the new one is written** (owner decision 5), then: **participants** are emailed again to the evaluation's `certificate_email` with the subject "Updated copy: Your Certificate of Participation: …" and the line "This is an updated copy with the current signatures. It replaces the copy we sent you earlier."; `sent_at` is set when it is sent; a failure sets `send_failed_at` ("Send failed" + Resend, the 5/30/120-minute and 8:30 retries, item 360). **Resource persons:** "Not handed over (updated copy to print)" (`reissued_at > issued_at`); "Mark handed over" sets a new `issued_at` (no status change).
     - **While the updated copy is being prepared** (`updated_copy_pending`: Issued, reissued, no file): the preview is drawn live ("Issued — the updated copy is being prepared"), the list says "Updated copy being prepared", Resend refuses, `send_certificate` does nothing, the startup portal says "An updated copy is being prepared.", the daily retry renders it (`pending_preparation` includes it) and the tab shows "Certificates are waiting for the background worker." when qcluster is not running.
     - **Notices:** after a slot change, a new image or a new name/position: "N issued certificates still have the old signature (in M activities). Reissue them from the Certificates tab." (`services.outdated_notice`).
     - **Needs attention (owner addition B):** "N issued certificates have an old signature." in the activity's box, **informational** (`AttentionItem.informational`, marked "(for information)"): it is never counted in the Activities list's warning count (`attention.warning_count`) and is not on the dashboard, so one image replacement does not light up every past activity. When it is the only line the box says "For your information". Also "N certificates for resource persons reissued: updated copy to print and hand over." (counted: a staff task).
     - **ERD:** CERTIFICATE `datetime | reissued_at | nullable`.

## Who signs and what they sign as, white backgrounds, inactive signatories, PDFs that never came (2026-11-14)

365. **Staff choose who signs and the title printed for them, starting from the defaults** (owner change request; extends item 363). *(Answered: item 431.)* *(2026-11-21, item 420: on the NEW activity form the fields are inside the optional "Prepare the certificates now"; not ticked = the defaults. The Edit form and the tab are unchanged.)* *(2026-11-24, item 431: only `can_approve_certificates` (the Director) chooses the people and the "Title on this certificate"; everyone else sees them read only.)*
     - **Where:** a "Who signs the certificates" section on the activity create / edit form (two dropdowns, "Name · Position" for active people, pre-filled with the defaults on a new activity and with the activity's people on edit; plus "Add a person…" for `can_approve_certificates`, which adds the person over HTMX (`certificates:signatory_add_inline`) and selects them without leaving the form), and the Signatures section on the Certificates tab (as before, now with the title field and "Add a person…" in the dropdown). Who: `can_create_activity` or `can_approve_certificates`; Admin Staff view only. The form needs `can_create_activity` (the System Administrator, who approves but never creates, uses the tab). Conducted activities are read-only, so for them only the tab works.
     - **Title on this certificate:** new column **`ACTIVITY_SIGNATORY.position_on_certificate`** (text, nullable, max 255; spaces only = empty). When filled it prints instead of the person's position, for this activity only (`services.printed_title`). Choosing another person clears it unless the same form sends one. A change follows the refresh rules of a slot change: one History row ("Certificate signatory (left)": "Name (old title)" → "Name (new title)"), not-issued certificates re-rendered, Approved ones back to waiting, Issued ones "Issued with an old signature" (reissue possible).
     - **Issued certificates keep their printed title (owner-approved option):** new columns **`CERTIFICATE.left_signatory_title` / `right_signatory_title`** (text, required, max 255): the titles printed. Filled from the slots whenever a certificate is created, refreshed, regenerated or reissued (`services.design_fields`); the PDF prints them. `services.outdated_q()` now compares the signatory row **and** the stored title with the activity's current ones. Migrations `certificates/0009` (columns), `0010` (data: every certificate gets the position of the signatory it uses = what was printed; no PDF changes, nothing becomes outdated), `0011` (required). `Certificate.save()` fills an empty title from the signatory's position (a safety net for rows made outside the services, e.g. test fixtures).
     - **Never the same person on both sides; never emptied:** the form and the tab refuse it (`SlotError` with the form field, shown under it). A chosen slot has no empty option; an empty value sent anyway means "no change". Swapping left and right in one form save works.
     - **Services:** `set_activity_signatory(member, activity, slot, person, title=...)` (tab), `set_activity_signatories(member, activity, {slot: (person, title)})` (edit form, inside `activities.services.update_activity`'s transaction), `assign_default_signatories(activity, chosen)` (new activity, inside `save_new_activity`; `create_activity` / `create_activity_with_documents` take `signatories=`).
366. **The system sticks to the defaults; white backgrounds are removed** (owner decisions). *(Open question, CLAUDE.md.)*
     - **Empty slots:** a new activity gets the defaults; a slot without a default stays empty ("Choose who signs the certificates (left)" / "(right)" / "(left and right)" in Needs attention, a normal warning, only while the activity has no certificates or not all of them are Issued). Certificates are not created until both slots are filled. The old fallback ("an empty slot takes the default when certificates are created") is removed. **When a default is set or changed** (`set_default_signatories` → `fill_empty_slots_from_defaults`), every EMPTY slot of an activity **without certificates** gets the new default, with one History row by System ("Certificate signatory (left)": "" → "Name (Position), default"). A slot someone already chose is never replaced; activities with certificates are never touched.
     - **White background (`certificates/imaging.py`, Pillow only):** on every upload (add a person, add / replace an image; on the Signatories page, the tab and the activity form) white and near-white pixels become transparent: the darkest colour channel ≥ 245 → transparent, ≤ 200 → kept, a soft ramp in between; edge pixels are un-blended from white so no light halo is left; the result is stored as a PNG. An image that already has transparent pixels is stored unchanged. The size limit (`CERTIFICATE_IMAGE_MAX_MB`) is checked first, on the uploaded file. **"Keep the background"** (checkbox) stores the image as uploaded.
     - **Preview before saving:** choosing a file (or switching the checkbox) posts it with HTMX to `certificates:signature_preview` (`services.signature_preview`, `can_approve_certificates`); the dialog shows the image as it will be stored on a checkered background and what was done. Nothing is saved. Without JavaScript the form still works.
     - **"Remove white background"** on the Signatories page for an existing image without transparency (`services.white_background_removable`): a confirm dialog with the impact (`version_impact_text`), then `remove_signature_background` creates a new version like Replace (Issued certificates keep the old image). Refused for an image that is already transparent.
367. **Inactive signatories on activities** (owner request after finding activity 20 on an old Director row). *(Open question, CLAUDE.md.)*
     - **Cause (development database, 2026-11-14):** row 3 "Engr. Sample Director" was created on 2026-11-12 by the item-361 "Replace" on one activity's tab, which then switched only that activity's not-issued certificates (activities 6 and 13). Migration 0007 gave every activity the signatory its own certificates used, so 12 activities kept the replaced row 2 (5, 7, 8, 9, 10, 16, 17, 18, 19, 20, 21, 23); nothing linked row 2 to row 3. **Owner decision: these 12 activities are left as they are**; staff use "Use the current version" per activity (it sends their not-issued certificates through the refresh rules, incl. Approved ones back to waiting, and makes their Issued ones outdated).
     - **Versions are linked:** new column **`CERTIFICATE_SIGNATORY.previous_version`** (FK to the same table, nullable, PROTECT): the version a row replaced; `_new_version` sets it. Migration 0010 links existing rows with the same name and position when the older one is inactive (development database: row 3 → row 2); **it moves no activity**. Rows replaced by an earlier name edit cannot be linked (the name differs).
     - **A new version moves every activity on ANY older version** of the person (`older_versions`, back to the first), not only the replaced row's; `version_impact` counts them too.
     - **Signatures section:** a slot with an inactive person shows an amber note; when a newer active version exists (`current_version_of`) a **"Use the current version"** button (`use_current_version`, POST `/activities/<id>/certificates/signatories/<slot>/current-version/`; `can_create_activity` or `can_approve_certificates`; keeps the title; same refresh rules), otherwise "… is inactive. Choose someone else." Needs attention: **"A signatory on this activity is inactive."** (normal warning), only while the activity has no certificates or not all of them are Issued (owner decision).
368. **Approved certificates without a PDF say why** (owner request). *(Open question, CLAUDE.md.)*
     - **Cause (development database, 2026-11-14):** 46 participant certificates (activities 8, 16, 17, 18, 19; TBI-2026-00099 Lorenzo Abad and TBI-2026-00100 Lorenzo Flores belong to activity 16 "Design Thinking Workshop", not 20) were approved on 2026-10-23 by `seed_demo`, before item 362 made approval wait for the evaluation; none has an evaluation and every evaluation deadline has passed. No render failed and no task was lost. They stay Approved (owner decision, item 362).
     - **The last column** (`services.pdf_state`, labels in `pages.PDF_STATE_LABELS`) for an Approved (or reissued) certificate without its PDF: "Approved before the evaluation rule — waiting for the evaluation" / "… — evaluation closed without an answer" (an Approved participant certificate without an evaluation can only come from before item 362, so no date column is needed: `approved_before_evaluation_rule`), "PDF failed — Retry" (with the error), "Waiting for the background worker" (qcluster not running, asked once per page: `pages.WorkerCheck`) or "PDF being prepared". "Waiting for the evaluation" appears only inside the "approved before the evaluation rule" label (owner decision). Hard copies use the same states.
     - **Render errors:** new columns **`CERTIFICATE.render_failed_at`** (datetime, nullable) and **`render_error`** (text, nullable, max 255), set by the background task and the daily retry (`record_render_failure`), in plain words (`render_error_text`: the unreadable signature by name, the design image, WeasyPrint not set up, otherwise "The PDF could not be created (<error type>)…"; never the raw message, which can contain paths). Cleared when the PDF is written, on Retry, and when the certificate is refreshed or reissued.
     - **Retry** (`retry_certificate_pdfs`, `can_approve_certificates`, row action `retry_pdf`): clears the failure and queues the render. The daily retry (8:30) already renders every Approved certificate that can be rendered, failed ones included (`pending_preparation`). **New 5-minute catch-up** (`queue_stalled_preparations` in `run_activity_automation`, result key `pdfs_queued`): a renderable certificate without a PDF and without a failure, approved or reissued more than 10 minutes ago, is queued again (a task never queued or lost; rendering twice is safe). Pre-rule certificates without an evaluation cannot be rendered (no printed name) and are never picked up.
     - **ERD (this step):** ACTIVITY_SIGNATORY `string | position_on_certificate | nullable, max 255`; CERTIFICATE_SIGNATORY `int | previous_version_id | FK to CERTIFICATE_SIGNATORY, nullable` (self-relationship); CERTIFICATE `string | left_signatory_title | max 255`, `string | right_signatory_title | max 255`, `datetime | render_failed_at | nullable`, `string | render_error | nullable, max 255`. Still 30 tables.
369. **Certificates are approved once per ACTIVITY** (owner change request, 2026-11-14; replaces the per-certificate approval of items 357, 361 and 362 and the S5 definition of items 196 / 362). *(Open question, CLAUDE.md.)*
     - **Stored on the activity** (ERD change, migration `activities/0007`): `ACTIVITY.certificates_approved_by` (FK to TBI_MEMBER, PROTECT, nullable) and `certificates_approved_at` (datetime, nullable). Empty = "Needs approval". Set and cleared only by `certificates.services` (`approve_activity_certificates`, `cancel_certificate_approval`); never by the activity form, `update_activity` or the Django admin. One History row "Certificate approval" each time ("Needs approval" → "Approved by <name>"; "Approved by <name>" → "Needs approval (cancelled: <what changed>)", by the member who made the change).
     - **Approving** (`approve_activity_certificates`, `can_approve_certificates`): the Certificates tab's primary button "Approve certificates for this activity" with a confirm dialog ("Certificates of this activity will be emailed to each participant as soon as they submit their evaluation; resource persons' certificates become ready to print."). The approver checks the preview (Previous / Next) first. Refused (`approval_closed_reason`): already approved; no certificates yet (they exist from Conducted on, so the preview can be checked); every certificate Issued; no active design or an empty slot; a signature image missing ("Add the signature of … first."). Approving before anyone evaluated is allowed.
     - **After the approval, by the System** (`approve_ready_certificates`): every certificate of the activity that is ready (`readiness`: both images; a participant's evaluation) becomes Approved in the approver's name (`approved_by` = the activity's approver, `approved_at` = the moment it became Approved) and is rendered, issued and emailed as before (resource persons: rendered, "Mark handed over"). It runs at the approval, after each evaluation (`evaluation_submitted`, after the commit), when certificates are created later (`generate_missing_certificates`: attendance marked after Conducted) and in the 5-minute job (`approve_pending_certificates`, result key `certificates_approved`, a catch-up). Nothing is approved automatically while the activity is not approved. The approver keeps being the name on later certificates even if their role later loses the flag or the account is deactivated (the approval was theirs); There is no "unapprove" button: only a change to what is printed cancels the approval.
     - **Cancelled automatically** (`cancel_certificate_approval`) when, after approving, someone changes a signatory or a title (`_apply_slots`, also "Use the current version"), saves a new version of a person (new image, removed background, name or position: every approved activity using any version of the person, owner decision), uses "Apply the current design" (`regenerate_certificates`) or changes the certificate text (item 370; also the default text for the activities using it). The activity "Needs approval" again; its Approved certificates go back to "Waiting for approval" (PDF removed); Issued certificates never change (reissue stays as it is, item 364). A save that changes nothing cancels nothing.
     - **Removed:** the preview's Approve button, "Approve all ready certificates", the URLs `activities:certificates_approve` / `certificates_approve_ready`, `services.approve_certificate` / `approve_certificates`, `ready_certificates`, `pages.next_to_approve`. The API action `approve` takes `{"activity_id": n}` only (`certificate_ids` → 400). Kept: the preview with Previous / Next, Download PDF, Resend, Retry, Mark handed over, Reissue.
     - **The tab:** a "Certificate approval" card at the top for everyone: "Approved by Ana Cruz on November 14, 2026, 3:05 PM" (`services.approved_text`, Manila) or "Needs approval"; the button only for `can_approve_certificates` (disabled with the reason when refused). Admin Staff: the state only. Rows waiting for an evaluation in an approved activity say "Sent automatically after the evaluation".
     - *(2026-11-15, item 373: the state shown is now three-way; "Needs approval" only while certificates wait.)*
     - *(2026-11-21, item 420: the approval may also be given BEFORE Conducted, once the certificates are prepared
       ("Prepare the certificates now" on the New activity form or the tab); the certificates are then created in an
       approved activity. Before Conducted it is also cancelled by a change to the activity's title, dates, venue,
       resource persons or the active design. "No certificates yet" is no longer a refusal for a prepared activity.)*
     - **Counts** (`services.needs_approval_q`: Generated and the activity not approved; one rule): the dashboard's S5 card ("Certificate approval", activities, with the number of certificates in the pill; "activity · need your approval"), its Needs attention line ("2 activities need certificate approval"), the activity's Needs attention ("Certificates need approval.", after a missing-signature line, which wins), the Activities list count and the certificates report's "now" rows. The old migration data: **no activity is marked approved** (owner decision); certificates already Approved or Issued stay as they are; activities with waiting certificates show "Needs approval".
370. **Editable certificate text** (owner change request, 2026-11-14; replaces the fixed wording of item 183, which is still the default). *(Open question, CLAUDE.md.)*
     - **New table CERTIFICATE_TEXT** (ERD change, migrations `certificates/0012`–`0014`): `certificate_text_id`, `certificate_type` (participation / appreciation), `activity_id` (FK to ACTIVITY, nullable: empty = the default wording), `title` (max 60, required), `line_before_name` (max 100), `main_sentence` (max 400, required), `closing_line` (max 200), `is_current`, `created_by_id` (FK to TBI_MEMBER, nullable: empty = System), `created_at`. One current default per type, one current custom text per (activity, type) (partial unique constraints). **CERTIFICATE.certificate_text_id** (FK, PROTECT, required): the wording the certificate is printed with. Versioned like the signatories: saving new text creates a NEW row (the old one is no longer current) and only certificates that are not Issued move to it; an Issued certificate keeps its row (and the text it was sent with). Migration 0013 creates the two defaults with exactly the text printed before (by the System) and gives every existing certificate the default of its type; no PDF changes.
     - **Placeholders** (`certificates/wording.py`, filled by `pdf.placeholder_values`): `{name}`, `{activity}`, `{date}` (the activity dates), `{venue}`, `{given_date}` (the activity's last day, item 183), `{organization}` (`CERTIFICATE_ORGANIZATION_NAME`) and `{topic}` (resource person, Certificate of Appreciation only). Text in `[ … ]` prints only when every placeholder inside it has a value (owner decision; replaces the template's "at <venue>" / "on "<topic>"" conditions). Refused with a clear message: an unknown placeholder (with the list), `{topic}` on Participation, a stray `{` or `}`, nested or unclosed brackets, a line break, a field over its limit, an empty title or main sentence. Plain text only: the PDF template escapes it.
     - **Default wording** on the "Certificate text" page (`/certificates/text/`, next to Signatories, `can_approve_certificates`; `save_default_text`): each type saved on its own after a confirm dialog with the impact (`default_text_impact_text`). Owner decision: it applies to the not-Issued certificates of every activity that uses the default and cancels those activities' approval (item 369); one History row "Certificate text" per activity. Activities with custom text keep theirs.
     - **Per activity** on the Certificates tab ("Certificate text": "Use the default text" / "Custom text for this activity"; `save_activity_text`, `use_default_text`; `can_create_activity` or `can_approve_certificates`, Admin Staff read only): the fields of both types, the placeholder list, a live preview while typing (HTMX, `wording_preview`, the activity's first certificate's values or a sample name; nothing saved). A changed type gets a new row for the activity; the same words as now save nothing. Then the approval is cancelled and the certificates of the changed types that are not Issued are rendered again; History "Default text" → "Custom text (Participation)". A refused save shows the page again with the typed text and the error under each field.
     - **Reissue** (item 364) prints the current text too; a text change alone never makes an Issued certificate "Issued with an old signature" (owner decision).
371. **Staff invitations** (owner request, 2026-11-14). The invitation email now starts with "Sign in here: <SITE_URL>/accounts/login/". If the invitation cannot be emailed (SMTP or email API down), the member is NOT created (one transaction) and the Admin Console shows "The invitation could not be emailed; the member was not added. Try again." under the email field instead of a server error; `accounts.audit` logs the error type (never the email typed). Unchanged: a random 16-character temporary password only in the email, `must_change_password = True`, every page (also the API and the Django admin) goes to "Change your password" until the member chooses their own (`MustChangePasswordMiddleware`).

## Certificates page organised by activity, with a search box (2026-11-14)

372. **Certificates page: "By activity (recommended)" is the default; a search box** (owner request; changes the flat, oldest-first table of item 299). View only, as before: approving, previewing with actions and reissuing stay on each activity's Certificates tab; Admin Staff sees the same page. No database or ERD change.
     - **"By activity (recommended)"** (default, `certificates.services.certificates_by_activity`): one card per activity that has at least one certificate matching the filters and the search (groups with nothing left are hidden; choosing one activity shows only its group). **Newest activity first by its END:** `end_date` newest first, then `end_time` newest first; an activity without times counts as the whole day (it ends at midnight), so it comes **before** the timed activities ending that same day; then the newest activity (`NEWEST_ACTIVITY_FIRST`). A multi-day activity counts by its last day. **10 activities a page; an activity is never split across pages** (the activities are paginated, then all their matching certificates are loaded). Inside a group: certificate number newest first.
     - **Group header** (`certificates.pages.activity_groups`): the activity title (link to `/activities/<id>/?tab=certificates`), its dates and times (Manila; `date_span`, `time_range`), the approval state exactly as on the tab (`services.approval_state`, item 373: "Needs approval", the grey "Not approved — new certificates will wait for approval" or "Approved by … on …"), and counts Waiting for approval / Approved / Issued with every filter except the status (as the page's status counts). The rows have no Activity column.
     - **"All certificates (newest first)"** (`?view=all`, `services.list_certificates_newest`): the flat table, certificate number newest first, 20 a page, with the Activity column. The JSON API keeps `list_certificates` and its order.
     - **Search box** (`?q=`, `services.search_q`, both views): every word (up to 10, any case) must appear in the certificate number, the registered name, the name or email typed in the evaluation, the resource person's name or the activity title. At most 100 characters; spaces are collapsed. It works with the filters (activity, status, type, delivery) and the status counts.
     - **Everything is in the URL** (`view`, `q`, `activity`, `status`, `recipient_type`, `delivery_method`, `page`; never the session); an unknown `view` is the default. The filters, the status counts, the view switch, Clear and the pager swap the part below the title (HTMX; the search box keeps the focus).
     - **Table:** Type with the delivery under it, Issued with the sent date under it, and the status cell wraps (the readiness / PDF label under the status badge), so nothing is cut off and no laptop screen (1280 px and wider) scrolls sideways; narrower screens scroll the table inside its card. Links on the page have no outline on click and a rounded blue ring for the keyboard (`.cert-link`).
     - **Queries:** a fixed number per page in both views, whatever the number of activities (tested): activities, their certificates, their counts, plus one query each for evidence counts, readiness, printed names and "old signature".
     - Screenshots from the demo data only (seed_demo, invented people, @demo.example.com), never the development database: `docs/design/screens/certificates-by-activity.png` and `certificates-all.png` (opt-in browser test `certificates/tests/test_list_page_browser.py`, `SAVE_SCREENS=1`).

## Approval state by what the user needs to do; formula-like titles confirmed (2026-11-15)

373. **The certificate approval state says what the user needs to do** (owner request after activity "Test", Nov 14, 2026, 5:44–6:00 PM, showed an orange "Needs approval" with its 2 certificates Issued and 0 waiting). No database or ERD change; no data changed.
     - **Why "Test" is not approved (checked in History and the migration log):** the approval was never cancelled. Its two certificates were approved one by one on the preview by the Incubation Project Head at 6:08 PM and 6:22 PM (the old per-certificate rule, item 362), then issued and emailed. Migration `activities/0007` (8:33 PM, item 369) added the approval stored on the activity and, by the owner's decision, marked no activity approved; there is no "Certificate approval" History row. The behaviour is correct: it is not counted anywhere and "Approve certificates for this activity" is refused ("Every certificate of this activity is already issued."). Only the label was misleading. (Same state: the old walkthrough activity.)
     - **One rule** (`certificates.services.approval_state(activity, waiting)`, `waiting` = ALL the activity's certificates waiting for approval, never only the ones a filter shows; `waiting_counts(ids)`, one query): approved → "Approved by … on …" (green, unchanged); not approved AND certificates waiting → "Needs approval" (orange, as before); not approved AND nothing waiting (also no certificates yet) → the neutral grey note **"Not approved — new certificates will wait for approval"** (slate text, info icon; not a task, never counted). The tab's card adds "No certificate is waiting now. A certificate created later (for example attendance marked after the activity was Conducted) waits until the activity's certificates are approved." The approver's disabled button and its reason are unchanged.
     - **Used by** the Certificates page group header (`pages.activity_groups`, one extra query per page, not per activity) and the tab's "Certificate approval" card (`pages.approval_panel`, counted from the tab's own list). **Counts unchanged (confirmed and tested):** S5 on the dashboard and its Needs attention line, the activity's Needs attention ("Certificates need approval."), the Activities list count and the certificates report keep `needs_approval_q` (Generated and not approved), which already left out activities with nothing waiting; "Needs approval" is shown exactly when that rule counts the activity (test). History texts are unchanged.
     - **(2026-11-15, owner request) Five states; no button when there is nothing to approve.** `approval_state(activity, counts)` now takes the activity's counts per status (all its certificates, whatever the filters; `approval_counts(ids)`, one query, replaces `waiting_counts`) and returns, in this order: **approved** → "Approved by … on …"; **needs_approval** (certificates waiting) → "Needs approval" with the button, as before; **no_certificates** → only "Certificates are created automatically when the activity is Conducted." (grey); **all_issued** (not approved, every certificate Issued, e.g. "Test" and the old walkthrough activity) → **"All certificates issued (N)"** with a green check in the Issued badge's green (green-100 / green-800) and the short grey line "Nothing to approve. A certificate added later (attendance marked after Conducted) will show here for approval."; **not_approved** (anything else: old data with Approved but not Issued certificates and nothing waiting; approving is still allowed) → the grey "Not approved — new certificates will wait for approval". For **all_issued** and **no_certificates** the state has `offers_approval = False`: the tab shows NO approve button (not even disabled), no reason line and no long explanation, for approvers and Admin Staff alike; the reasons for missing certificates (design, signatories) stay where they were (the tab's "why certificates are not created yet" notice and Needs attention). The service still refuses a hand-made request (`approval_closed_reason`: "Every certificate of this activity is already issued." / no certificates yet). A certificate created later (late attendance) makes it "Needs approval" with the button again. The Certificates page header shows the same states (the note under "All certificates issued (N)"); an activity without certificates never appears there. Counts unchanged: only "needs_approval" agrees with `needs_approval_q`; all_issued and no_certificates are counted nowhere. No database or ERD change.
     - *(2026-11-21, item 420: before Conducted (no certificates yet) the state is `approved` + "Certificates will be
       created when the activity is Conducted.", `prepared` ("Prepared — needs approval", with the approve button) or
       `not_prepared` (no button; "Prepare the certificates now"). After Conducted the five states above are unchanged;
       `no_certificates` now only appears for a Conducted activity whose certificates could not be created.)*
     - **Formula-like titles (confirmed, no code change):** the demo title `=HYPERLINK("https://example.com","Formula test")` (item 211, kept as it is) is plain text on every page (Django escapes it; no title is placed in JavaScript: Alpine/onclick values are IDs, the preview list uses `json_script`) and in the report PDF. Excel reports write every text value starting with `=`, `+`, `-`, `@`, tab or CR as text with Excel's quote prefix (`reports.excel.safe_cell`, item 207); real negative numbers stay numbers. Tests: `reports/tests/test_formula_safety.py` (each starting character, saved and reopened, no `<f>` in the file; the pages and the PDF HTML), `certificates/tests/test_approval_state.py`, two header tests in `certificates/tests/test_list_page.py`.

## AI Assistant: Enter to send, auto-scroll, safe formatting, delete and rename, generated titles (2026-11-15)

374. **Enter sends** (owner request; replaces "Ctrl + Enter sends" in item 306). In the message box Enter sends and Shift + Enter makes a new line; while an input method editor is composing a word, Enter does nothing (`isComposing`). The help text says "Enter to send, Shift + Enter for a new line. At most 2000 characters." Send is disabled while the box is empty (or only spaces) and while an answer is pending, and Enter is ignored then too; `hx-sync="this:drop"` stays as the backup against double sends. The rule that a question cannot be empty is still `services.ask` (the page without JavaScript gets the same "Type a question." refusal). Only this keyboard behaviour is Alpine.
375. **Auto-scroll with "Jump to latest".** The conversation opens at the latest message. Sending always scrolls to the bottom (the question and the typing indicator appear there). When the answer arrives, the chat scrolls down only if the reader is still near the bottom (within 80 px); a reader who scrolled up on purpose to read older messages stays where they are and sees a small "Jump to latest" button, which scrolls down (smoothly only when the system allows motion) and disappears. Scrolling back to the bottom by hand also hides it. Alpine only; checked in Chromium by the opt-in `assistant/tests/test_chat_browser.py` (`BROWSER_TESTS=1`).
376. **Answers show a small, safe Markdown subset** (owner decision; **replaces item 305**). `assistant.pages.answer_html()` still escapes the WHOLE answer first, then turns only these patterns of the escaped text into our own tags: `**bold**` → `<strong>`; `*italic*` and `_italic_` → `<em>`, only around words (so "2 * 3 * 4", `a*b*c`, `certificate_text_id` and `file_name_v2.pdf` stay as they are); `***both***`; lines starting with `* `, `- ` or `• ` → a bullet list; `1. ` / `1) ` → a numbered list (a list that starts at 3 keeps its number); `#`, `##`, `###` + space → a bold line without the marks (no heading tags; `####` and `#hashtag` stay text); line breaks → `<br>` (a blank line keeps a paragraph gap; blank lines next to a list are dropped); `[n]` → our citation link, as before. Everything else stays escaped text: raw HTML, Markdown links (also `javascript:`), images and tables. No sanitizer library is needed because nothing from the model ever becomes a tag, an attribute or a URL: the patterns only wrap escaped text in `strong`, `em`, `ul`, `ol`, `li`, `br`, and the only attributes are our citation link's (`href` from the saved source, never from the answer) and an ordered list's `start` (digits). Tests send `<script>`, `<img onerror>`, `<iframe>`, `<svg onload>`, `[x](javascript:…)` (also inside bold and lists, and as `[1](javascript:…)`), images and a table, and parse the result to check that only these tags and attributes appear. The screen-reader live region gets the answer without the marks (`pages.answer_text`). The answer instruction now asks Gemini for short paragraphs and simple bullet or numbered lists, a few bold words at most, and no tables or headings; the citation rule is unchanged. Questions are still escaped text with line breaks only.
377. **A member can delete their own chats.** Each row of My chats has a small "⋯" menu (Rename, Delete; a real button with `aria-label`, `aria-haspopup="menu"`, `aria-expanded`, closed by Escape or a click outside). Delete opens a confirmation dialog ("Delete this chat? This can't be undone.", Cancel focused first) and posts to `POST /assistant/<id>/delete/` → `services.delete_chat(member, chat_id)`: someone else's chat is 404 and untouched; the chat is deleted with its messages and their sources (CASCADE, item 35); the cited documents are never affected. The page goes back to the chat that was open (the form's `current`), or to a new chat when the open chat was the one deleted, with the toast "Chat deleted.". GET is not allowed (405). The rename and delete dialogs are shared by all rows (one of each on the page; the row gives its URL and title through data attributes, never inside JavaScript code). Without JavaScript the menu is hidden, so chats cannot be renamed or deleted (the chat itself still works).
378. **Chat titles are generated in the background** (owner request; refines item 166). After a chat's FIRST answer, `services.ask` still saves the first question (shortened to 80 characters) as the title, and queues the Django-Q2 task `assistant.tasks.run_generate_chat_title(chat_id)` after the commit (`services.queue_chat_title`). The task calls `services.generate_chat_title()`: Gemini (through `get_gateway()`) receives only the first question and the beginning of the answer (1,000 characters each), marked as data, with `TITLE_INSTRUCTION` (at most 6 words, the question's language, no quotes or end punctuation) and no tools; `services.clean_title()` then keeps the first line, removes Markdown marks, quotes, a "Title:" prefix and punctuation at either end, and cuts to 6 words and 80 characters. The title is saved only while the chat still has its first-question title (compare-and-set in one UPDATE, which also keeps `updated_at`, so the chat does not jump in My chats). **The first question stays the title** when the qcluster is not running, Gemini has no key, fails or times out (90 s limit), the reply is empty, the member renamed the chat (before or while Gemini answered), or the chat was deleted. The task never raises and logs only the chat ID and the kind of error, never the question, the answer or the title. Tests use the FakeGemini only. **No database change:** no "title generated" flag is needed, because "the title still equals the first question" is the signal; the ERD is unchanged. The page fetches My chats once more 6 seconds after a new chat's first answer (`GET /assistant/chats/?chat=<id>`, `page_views.chat_list`, only my chats; it also updates the chat's header title), so the generated title appears by itself; if the worker is slower, it appears on the next page load. **Renaming:** "Rename" in the row's menu opens a dialog → `POST /assistant/<id>/rename/` → `services.rename_chat()`: own chats only (404 otherwise), spaces cleaned up, 1–80 characters ("Type a title." / "The title can be at most 80 characters." as a toast), the chat keeps its place in the list. A renamed chat is never retitled by the task.
379. **"My chats" is never cut off.** The panel is still 15rem (item 326), but its header row holds only the heading and the hide button; "New chat" is a full-width button below it. The chat titles in the list are still shortened with "…" (the full title is the link's text for screen readers and in the Rename dialog).
380. **The AI Assistant page fits the window** (owner bug report: the whole panel scrolled, the chat card slid under the page header and left an empty area below the cards). Cause: the chat grid's height was a guessed `calc(100vh - 9.5rem)` with a 30rem minimum inside a scrolling panel, and the hidden screen-reader labels of the messages ("You asked:", "The assistant answered:", `sr-only` = absolutely positioned) escaped the messages box because it was not `relative`, stretching the panel by the whole conversation's height. Now, from 1024 px wide AND 600 px high (Tailwind variant `fit-screen:` in `frontend/tailwind/app.css`), the panel does not scroll: the top bar keeps its height, `main` fills the rest (20 px bottom padding), the grid fills `main`, and both cards reach the bottom; only the messages and the chat list scroll inside their cards (both `relative`), and the message box, the activity filter, the help text and Send stay at the bottom of the chat card. Auto-scroll and "Jump to latest" (item 375) use the same inner messages box. Below 600 px high (`short-screen:`) the panel scrolls as before and the grid is 34rem high (the inner areas still scroll); below 1024 px wide nothing changed. Only this page changes: `layouts/app.html` has two optional blocks, `panel_scroll` (default `lg:overflow-y-auto`) and `main_class` (default padding), which only `assistant/chat.html` overrides. Checked in Chromium at 1280×720, 1366×768, 1440×900, 1920×1080 and 1366×560, and that the dashboard keeps its panel scrolling (opt-in `assistant/tests/test_chat_browser.py`).

## AI Assistant: activities by period, source file cards and finding files (2026-11-16)

381. **Activity questions are answered by our code, by period** (owner bug report: "What are the activities for the month of November?" answered "I couldn't find any activities recorded for November" although Nov 3–14, 2026 had five). *(Week from Monday to Sunday and drafts left out: open question, CLAUDE.md.)* No database or ERD change.
     - **Cause (checked in git and in the dev database, read only):** no routing or tool code had changed since Step 8 (Oct 21); the Nov 15 commit only changed the answer format line and added generated titles. Routing was left entirely to Gemini, and its only activity tool, `find_activities`, takes a title fragment and ONE date, so a month question made Gemini improvise. Chat 8 (a fresh chat) happened to call it with no filter, which returns the 10 activities with the latest dates (the November ones were among them). Chat 11 asked the November question as a follow-up after "can you give me the files for november's activity" (answered "not found"), and Gemini then most likely searched a title "November" or the single day Nov 1: 0 rows, so "none" with the records badge. The title "November Activity Files Missing Search" came from the chat's FIRST question and answer (item 378), not from how the second question was routed. Tool calls were not logged (they are now, see below).
     - **Routing by our code** (`assistant/routing.py`, before Gemini, in this order): (1) missing / overdue / late / waiting / pending / returned / due / checklist / outstanding / not uploaded … → Gemini with the existing record tools ONLY (`find_activities`, `get_document_checklist`, `list_activity_documents`, `documents_needing_attention`); (2) says / mention / summary / according to / budget / objectives … together with a file word or a document type, or without any activity word → Gemini with `search_approved_documents` + `find_activities` (item 382 cards); (3) find files (item 383); (4) an activity word (activity, event, schedule, calendar, happening, upcoming, conducted, held, program, session, or an ACTIVITY_TYPE name, singular or plural, read from the table) together with a period, a status, "how many" or a list word → **activities path**; (5) anything else → Gemini with every tool, including two new tools that take the user's OWN words (`list_activities_by_period(period_text, status, activity_type)`, `find_files(activity_name, period_text, document_type, uploaded)`); our code works out the dates, and when only these tools ran the reply is our text, not Gemini's. "Scheduled" is not read as a status ("activities scheduled for December" lists every December activity).
     - **Periods** (`assistant/periods.py`, Manila date, `now` passed in): a month ("November", "Nov", "november's", "in November 2026", "last/next November"), a day ("November 14", "14th of November", "2026-11-14"), today / tomorrow / yesterday, this / next / last week (**Monday to Sunday**; on Sunday Nov 15 "this week" = Nov 9–15), this / next / last month and year, "in 2026", "upcoming" and "happening now". A month or day **without a year is in the current year**, except: past wording (conducted, held, was, were, did, done, happened, finished, completed, took place, had) and a month later than this month → last year; future wording (upcoming, will, planned, scheduled, coming, next, going to) and a month earlier than this month → next year. "may" is a month only next to a date word ("in May"). Label examples: "November 1–30, 2026", "November 30 – December 6, 2026", "January 1 – December 31, 2026".
     - **Which activities** (`assistant/listings.activities_answer`): the Activities list's own rules. A period = the activities that touch it (`activities.services.activities_in_range`, the list's date filter `_overlapping`; a multi-day activity crossing two months is in both), earliest first (the list's start-date sort). "Upcoming" = the list's Upcoming group (`activity_group`); "happening now" = `happening_now()`. `apply_automatic_statuses()` runs first, as on the list page (statuses only, never emails). Status words: draft(s), ongoing / in progress, conducted / held / done / completed / finished / took place (not with future wording). **Drafts are left out** unless the question asks for drafts ("draft activities" = drafts only; "including drafts" = all plus drafts); when drafts were left out and the period has some, the heading says "(drafts not included: N drafts in this period; ask about drafts to see them)". A chosen status never mentions drafts. Visibility = the Activities list: every active staff member; startups never reach the page.
     - **The answer**: "**Activities in November 1–30, 2026: 5**" (with status / type: "Conducted Training activities in …"; counts use the same heading), then one numbered line per activity: title, its activity-page link, dates (`date_span`), time (`format_time_range`, "Time not set"), status, venue. At most **20**, then "Showing the first 20 of N. M more are in the Activities list" with a link to the list filtered by those dates (and status / type). Nothing found: "There are no activities in November 1–30, 2026." (the exact period; also for "now" and "upcoming").
     - **Without the Gemini key** the activities and find-files paths still answer (they never call Gemini); the page shows "The AI is turned off. You can still ask about activities and find files (for example: activities this month, files for the Bootcamp)." (`gemini.is_configured()`, never shows the key). Other questions keep the current "not set up" message. The generated title then stays the first question (item 378).
     - **Links are worked out when shown, never taken from the model**: the saved text holds markers `[activity:<id>]`, `[activities-list:<from>:<to>:<status id>:<type id>]`, `[documents-list:<activity id>:<type id>]` (and `[file:<id>]`, item 383). `assistant.pages.answer_html` (still escape first, item 376) reads only digits and dates from them, builds the URL with `reverse()`, and links only activities that exist (one query per page); anything else is dropped. In titles typed by staff, a "[" that could start a marker or a citation is written as "［" (`listings.safe_text`), so a title can never create a link, a card or a list line.
     - **Log** (one line per answer): chat id, message id, path (`activities`, `find_files`, `document_status`, `document_content`, `gemini_all_tools`) and the names of the tools that ran; never the question, the answer or any document text.
382. **Source file cards under document answers** (owner request: help staff retrieve files, not only read text). Below an answer that cites documents, a "Sources" list shows **one card per cited document** (never one per excerpt: [1] and [3] from the same file share one card that shows both numbers), in citation order: title, document type, activity (link to its page), review-state badge (Waiting for review / Returned / Approved, `documents.pages.state_badge`), upload date (Manila), **Open** (`documents:detail`) and **Download** (`documents:download`: active staff login, always an attachment; never a `/media/` path). Each `[n]` in the text links to its card (`#m<message>-d<document>`, highlighted with `:target`). Cards are built from the saved AI_MESSAGE_SOURCE rows and the database, never from the model's text; uncited excerpts never get a card. Replaces the small source chips of item 305. No database change.
383. **Find files** (owner request; our code, no Gemini). Questions like "give me the files for November's activities", "attendance sheet of the Ideation Bootcamp", "request letters this month", "files of the Pitching Competition", "documents uploaded last week" (route (3) of item 381: a file word (files, documents, docs, copy, soft copy, download, attachment, upload(ed), pdf, scan) or a DOCUMENT_TYPE name, singular or plural, read from the table, together with an activity title, a period or a document type). *(Listing every review state: open question, CLAUDE.md.)*
     - **Activity title**: the question's words minus period words, type names, file words and common words; only words that appear in some activity title are kept (other words are noise), and an activity matches when its title contains all of them (any case). The page's activity filter is used when the question names no activity. An ACTIVITY_TYPE word limits the activities too.
     - **Period**: the item 381 parser. It applies to the **activity dates** (activities touching the period), or to the **upload date** in Manila when the question says uploaded / submitted.
     - **Which files**: the Documents page's rows (`documents_for_listing()`: activity documents, never certificate evidence), **any review state** with its badge, for every active staff member (the Documents page's rule). Attendance sheets are listed and downloadable through the existing download view; their content is still never indexed or sent to Gemini (item 165), and the `find_files` tool gives Gemini only titles, types and review states.
     - **The answer**: "**Files for activities in November 1–30, 2026: 8**" (or "Attendance Sheet files for Pre-incubation / Ideation Bootcamp: 1", "Files uploaded in November 9–15, 2026: 2"), then file cards (item 382's card, without numbers) grouped by activity: newest activity first (start date), newest upload first inside a group; at most **20**, then "Showing the first 20 of N. M more on the Documents page" with a link to the Documents page's "All documents" view filtered by the activity (when exactly one) and the document type. The Documents page has no date filter, so a period cannot be passed and the text says so. **Nothing found**: "No files for activities in November 1–30, 2026." (the exact period or activity checked; "There are no matching activities in …" when no activity is in the period; "No activity title matches “…”" for a name), followed by "Required documents still missing (from the checklist)" per activity (`document_checklist()`, outside Draft as the checklist's count, at most 10 activities).
     - **Stored without a database change**: the found files are NOT saved as AI_MESSAGE_SOURCE rows, because that table's `document` foreign key is PROTECT and find-files lists For Review files that their uploader may still delete (a saved row would block the delete). The answer text keeps one line per file ("- Attendance Sheet: Bootcamp attendance [file:41]") and one heading line per activity; the page turns them into cards from the database (two queries for the whole conversation). A file deleted since then shows "This file is no longer available."
     - Tests (fake Gemini only): `assistant/tests/test_periods.py`, `test_routing.py` (about 45 phrasings over the five paths, the tools offered per path, the chat-11 follow-up, the log line), `test_activity_answers.py`, `test_find_files.py`.
384. **Reports → New report: "Describe the report you want"** (owner request, 2026-11-16). The New report card works like a chat box: **type, check, generate** (revised the same day after the owner's review; see "Page" below). The member types a request ("Participation report for this quarter, trainings only, in Excel"), checks the settings the system filled in and clicks Generate report as usual. No database or ERD change.
     - **Gemini only reads the request** (`reports/request_reader.py`, through `assistant.gemini.get_gateway()`, one tool `fill_report_form`): report type, sections, AI summary on/off, the period in the user's own words (`period_words`), activity type, format, and parts the form cannot do (`unsupported`). It gets the request, the allowed choices and the active activity type names only. It never writes numbers, text or results: the report is still made by `request_report` / `build_report` from the database, unchanged, and `ai_prompt` stays the AI summary's (item 203).
     - **Every value is checked by `reports.services.fill_report_form()`**: report type in `REPORT_TYPES`, sections in `SECTIONS` (report order, no duplicates; `ai_summary` only through the switch), format in `GeneratedReport.Format`, activity type matched by name against ACTIVE ACTIVITY_TYPE rows (ignoring case and a plural "s"; "all" = All activity types). Anything else is dropped. A report type without sections gets the type's usual sections (the form's rule); a setting the request does not mention keeps its current value.
     - **Several things in one request** (owner bug report, 2026-11-16: "list of events this year and all certificates issued this year" became a Certificates Report with one section and "These can't be set in this form: “list of events this year”"). Sections combine freely in one report, so:
       - Gemini's instructions say so, with a plain description of every section (`request_reader.SECTION_DESCRIPTIONS`: activities = the list of conducted activities, certificates = certificates issued, startups = startups engaged, …): choose the report type for the main topic and ADD the sections for the other things; "unsupported" only for what no section or setting covers. Events, programs, trainings, seminars and workshops are activities unless the word is an activity type name.
       - **Our code adds what is missing** (never Gemini): a request that mentions activities (activities, events, programs, programmes, trainings, seminars, workshops, sessions; a word that is the name of an ACTIVE activity type in the table is the type filter instead, e.g. "trainings only" with the seeded Training type; "activity type" is not a mention) gets "Activities conducted" when the chosen sections lack it; a request that mentions certificates gets "Certificates". The note says "Added: Activities conducted." (or "Added: Certificates."). The base is Gemini's sections, else the type's usual sections, else the form's current sections (the view passes them).
       - An "unsupported" quote is dropped when the form covers it: it mentions activities / certificates and that section is in, or it is upcoming wording explained by the note below.
       - **Conducted only**: a request with upcoming / planned / scheduled / future / forthcoming gets the fixed note "Reports include only Conducted activities. For upcoming activities, ask the AI Assistant." (no activity list is added for it, the dates are not changed, and the "not a date range" note is not repeated). Reports count Conducted activities by end date (item 195).
     - **Dates are ours**: `assistant.periods.parse_range()` (Manila; quarters, halves, "January to June", ISO ranges, then everything item 381 reads; a month range without a year is this year, or "last year" / "next year" when said; a year written after the end month belongs to the end). Gemini's `period_words` are used only when they appear word for word in the request; otherwise the request itself is parsed. "upcoming" / "happening now" are not date ranges and are refused with a note. The dates choose the matching quick choice (This month, This quarter, …) or Custom.
     - **Messages are our own sentences** (escaped by the template): what was filled, "Couldn't work out the period “…”", "There is no activity type named “…”", "These can't be set in this form: “…”" (only quotes that appear word for word in the request, at most 3, 80 characters each) and "Not in your request, so kept as it was: …". Gemini's free text is never shown. A reply without the tool: "The request could not be read. Nothing was changed; fill in the form by hand."
     - **Permission**: the same as generating a report, `reports.services.GENERATE_REPORT_PERMISSION` (= can_create_activity, item 204), used by `request_report`, the page and `POST /reports/fill/` (403 without it). Nothing is saved; the request text is never stored or logged (one log line: member ID, number of settings filled and notes).
     - **Limits**: at most 500 characters; 30 requests per member per hour (`config.ratelimit`, scope `report_form_fill`; then "You've used this 30 times in the last hour. Try again later, or fill in the form by hand." and Gemini is not called); the whole call inside `gemini.time_limit(30 s)`; Gemini failures show the gateway's safe message and leave the form as it was (settings `REPORT_FORM_FILL_*`).
     - **Without `GEMINI_API_KEY`**: no prompt box and no examples; the full form is shown open, as before, with the grey line "Describing the report is not available: the AI is turned off."
     - **Page** (`templates/reports/_form.html`; the New report card is always open, no longer behind a "New report" button, so the prompt box is visible at once):
       - **"What report do you need?"**: one large box (placeholder "e.g. Participation report for this quarter, trainings only, in Excel") with a round send button inside it. Enter sends, Shift + Enter makes a new line, never while an input method editor is composing (the AI Assistant's rule, item 374); Send is disabled while the box is empty or a request is being read, and "Reading your request…" shows inside the box meanwhile.
       - **Example chips** under the box (`reports.pages.EXAMPLE_REQUESTS`: four requests). A click puts the text in the box and sends it; without JavaScript each chip is a submit button (`name="example"`) that the server uses as the request.
       - **Summary card** after a fill (instead of the long form): Report [select], Period [select: the quick choices + Custom] with the dates in words, Activity type [select], Format [PDF | Excel], "N sections · Change" (opens More settings at the sections), the note lines, a large **Generate report** and a small **Start over** (a clean `/reports/`). Choosing Custom opens More settings at the dates. The chips are hidden while the summary is shown. A refused Generate keeps the summary (`show_summary`) and opens More settings to show the field errors.
       - **More settings** (a `<details>`, closed by default) holds the full existing form (`templates/reports/_form_fields.html`) with its own Generate button.
       - **One form, one set of named fields**: only the full form's fields have names, so both Generate buttons post exactly what `request_report` reads (unchanged). **Explicit exception (owner-approved, 2026-11-16):** besides the keyboard, the chips and opening More settings, Alpine also **mirrors values** between the summary card's controls (no `name`) and the full form (`x-model` on the same values: type, range, start/end, activity type, format, sections, AI summary). It only copies values; every check stays in the services (`fill_report_form`, `request_report`). Without JavaScript the server renders the summary with the right values and the full form under More settings still works.
       - HTMX posts the whole form to `reports:fill` and swaps `#report-form` (Alpine starts again on the filled values); without JavaScript the send button's `formaction` returns the whole page.
       - Fits at 1366×768: the page header, the prompt box and the summary card are visible without scrolling; Report history stays below (checked in Chromium).
     - **Template comments**: Django's `{# #}` works on one line only; a multi-line one was printed on this page. Multi-line comments use `{% comment %}`; `config/tests/test_template_comments.py` scans every template and renders the staff pages (AI on and off) for "{#" / "#}".
     - Tests (fake Gemini only): `reports/tests/test_form_fill.py`, `assistant/tests/test_periods.py` (parse_range), `config/tests/test_template_comments.py`; opt-in Chromium: `reports/tests/test_report_form_browser.py` (`BROWSER_TESTS=1`). *(Sending staff requests to Gemini on the free tier: open question, CLAUDE.md, item 165.)*
385. **Report PDFs never cut anything off** (owner bug report, 2026-11-16: in an Accomplishment Report for January 1 – December 31, 2026 the Activities table's columns after "Evaluations" ran off the page and the Time column wrapped onto 4 lines). Layout only: no number, rule, section, database or ERD change; the Excel file is unchanged and keeps every column (Start and End as real dates, Time as text; checked in `test_reports.py`).
     - **Cause**: A4 portrait (182 mm wide inside the margins) with automatic table layout: long titles and venues widened their columns, and number cells could not wrap, so a 12-column table became wider than the page. The browser default `body { margin: 8px }` also made the usable width 16 px narrower than the page content area.
     - **Landscape A4 for the whole report, every report type** (owner decision; 269 mm wide inside the 14 mm margins). WeasyPrint supports named pages (`page: wide`), but only the wide tables in landscape would force a page break before and after each of them (half-empty pages), mix orientations (awkward to print and read on screen) and add a rule about which sections are "wide". Charts and short tables look the same in landscape.
     - **Fixed column widths** (`reports.pdf.pdf_columns` / `pdf_table`, `table-layout: fixed`, a `<colgroup>` per table): numbers 21 mm (right-aligned, one line), a date 25 mm, the combined Date 29 mm, the time 31 mm; text columns share the rest by weight (Title / Activity 3, Venue 2, others 1.5), between 18 and 110 mm, so a Measure / Value table stays narrow instead of page-wide. Wider than the page (many document types, master data) → every column shrinks by the same factor. `PAGE_CONTENT_WIDTH_MM` must stay in step with the template's `@page` margins.
     - **Text**: tables with more than 6 printed columns use 8 pt (headers too; before: 7–7.5 pt); never below 8 pt. Header cells wrap; long titles, venues and words without spaces wrap inside their cell (`overflow-wrap: anywhere`); header rows repeat on each page; a row is never split across pages; a heading stays with what follows it; the chart SVG is at most the page width.
     - **Combined columns, PDF only**: the content's columns carry an optional `key` (`reports.content.Column.key`: title, start, end, time, venue). Start + End print as one **Date** column with `activities.formatting.format_date_span()` ("Mar 2–3, 2026", "Mar 5, 2026", "Mar 30 – Apr 2, 2026", "Dec 30, 2026 – Jan 2, 2027"; the same function as the pages' `date_span` filter, moved from `config/ui_tags.py`). Time prints on one line with `compact_time_range()` ("8:00 AM–5:00 PM"; "Time not set" unchanged); `format_time_range()` stays the one source of the text.
     - **Checked with real WeasyPrint** (opt-in `reports/tests/test_report_pdf_layout.py`, `PDF_LAYOUT_TESTS=1`): the TEST database filled by `seed_demo` plus one activity with a very long title and venue (a 78-character word in both), every report type with its default sections and one report with all ten sections; it fails if any box on any page ends past the page content area or any text past its table cell. `PDF_LAYOUT_OUT=<folder>` also writes the PDFs and the first two pages as PNG (pypdfium2, `requirements-dev.txt` only, never `requirements.txt`). Samples (demo data): `docs/design/screens/report-pdf-accomplishment-page1.png`, `report-pdf-accomplishment-page2.png`, `report-pdf-document-compliance.png`. The fake-renderer tests are `reports/tests/test_report_pdf_tables.py`.
386. **The Invitation Letter and the Accomplishment Report are optional; "Other" documents are easier to upload** (owner requests, 2026-11-16; the Accomplishment Report the same day, after the Invitation Letter). *(Open question, CLAUDE.md.)*
     - **One flag decides "required"**: `DOCUMENT_TYPE.is_required` (one value per type, not per activity). The checklist (`document_checklist()`: the Documents tab, reminders, the assistant's "what's missing" and the find-files "still missing" note), the Documents page (`activity_document_overview()`), Needs attention (`attention.py`), the compliance report and the dashboard's overdue number (`reports/metrics.py`, item 200) all read it, so turning it off for a type removes it from every count at once, with no other code change. Still required: Activity Design (start − 10), Request Letter (start − 5), Attendance Sheet (end date). No per-activity data exists or is added (no ERD change).
     - **Existing activities** (both types): where nothing was uploaded the type simply leaves the required list; where a file was uploaded it stays, with its status and actions, and is listed under **Other documents** (the tab and the Documents page's "other documents" rows). **No file or document is ever deleted.** Reminders already sent stay in the notification lists as history; no new ones are created. Reports generated before keep their files (the compliance report loses the column only in new reports).
     - **Data migration `documents/0003_invitation_letter_optional`** (no schema change): changes ONE row of DOCUMENT_TYPE, the one named "Invitation Letter": `is_required` → false, `due_reference` and `due_offset_days` → empty (like the other optional types). Nothing happens when the row does not exist (a new database: seed_data creates it optional). The reverse puts back required, start_date, −7. Development database on 2026-11-16 before the migration: Invitation Letter (id 1) required, 12 files in 12 of 25 activities, all kept.
     - **Data migration `documents/0004_accomplishment_report_optional`** (no schema change; 0003 is unchanged): the same for ONE row, the one named "Accomplishment Report": `is_required` → false, `due_reference` (end_date) and `due_offset_days` (7) → empty; nothing when the row does not exist; the reverse puts back required, end_date, +7. Development database before it: Accomplishment Report (id 5) required, 5 files in 5 of 25 activities, all kept (listed under Other documents).
     - **`seed_data`**: the Invitation Letter and the Accomplishment Report are `(False, None, None)` and a new optional type **"Other"** (`DocumentType.OTHER`) is created when missing. `is_required` and the due rule are set **only when seed_data creates a type** (`get_or_create`; before: re-applied on every run), so a rerun never undoes a later change in the Django admin (e.g. the TBI marking the Invitation Letter required again), like the role approval flags (items 344, 357).
     - **The "Other" type needs a title** (`documents.services.title_error`, the one title rule, used by `upload_document` and the New activity form's `check_new_documents`): empty → "Give this document a title, for example: MOA with ABC Corp." (the type's name says nothing about the file). Every other type keeps the optional title (empty = the type's name). Title and file errors are reported together. The AI Assistant never reads "Other" from a question ("the other one", "other files" are plain English: `assistant.routing.route` leaves the type out of the word match); its files are still listed by activity and period.
     - **Documents tab** (`documents.pages.documents_tab`, display only): the "Other documents" card has an **"Upload other document"** button → `?tab=documents&focus=type#upload` (scrolls to the form; the Document type dropdown gets `autofocus`, works without JavaScript); its empty text names the optional types from the table. The dropdown is grouped `<optgroup>` **"Required"** / **"Other documents"** (by name; no "(required)" suffix; Certificate still hidden: evidence has its own upload). The required rows' Upload buttons keep pre-selecting their type (`?type=<id>`, confirmed and tested, also for an optional type). The title help says it is required for "Other".
     - Tests: `documents/tests/test_other_documents.py` (migration and its reverse, with and without a file, every count, the title rule, the tab), `accounts/tests/test_seed.py` (rerun keeps changes, "Other" only if missing), `assistant/tests/test_routing.py` ("other" phrasings), `activities/tests/test_create_with_documents.py`; tests that used the Invitation Letter as their example of a required type now use the Request Letter (start − 5).
387. **Document rows never squeeze the title** (owner bug report, 2026-11-16: an "Other" document titled "Accomplishment report" on the Documents tab showed its title and meta line one or two letters per line while the badge and the Download / Preview / Replace / Delete buttons took the width).
     - **Cause**: `templates/documents/_document_item.html` (both cards of the tab and certificate evidence) gave the title block `min-w-0 flex-1` in a `flex-wrap` row. `flex-1` starts it at width 0, so the buttons never wrapped to a second line, and `min-w-0` + `break-words` let the title shrink below its longest word and break it letter by letter. Short titles hid it.
     - **Fix** (one template, the Required and Other documents rows look the same): the title block is `min-w-0 grow basis-64` (starts at 16rem, takes the free width; words wrap by words; a single word longer than the line still breaks instead of overflowing); the state badges and the buttons are ONE group `ml-auto flex flex-wrap justify-end` on the right that moves under the title when there is no room. The Documents page (grid, by activity and all documents) and the document page already had their own widths and were checked too (unchanged).
     - Tests: `documents/tests/test_other_documents.py` (the classes); opt-in Chromium `documents/tests/test_document_rows_browser.py` (`BROWSER_TESTS=1`): long titles on the tab (both cards), the Documents page (both views) and the document page at 1366 and 1024 px: never more lines than words in a title or meta line, no sideways scroll.
388. **Admin Staff create and edit activities and approve certificates** (owner decision change, 2026-11-16; changes the Admin Staff parts of items 85, 204, 344, 357, 361 and 363, where Admin Staff were view only). *(Open question, CLAUDE.md.)* *(2026-11-21, item 418: Admin Staff still approve certificates but no longer add signatures or signatories.)* *(2026-11-21, item 421: can_approve_certificates is now the Director's only by default; Retry and signature images also for can_create_activity.)*
     - **Defaults** (`accounts/seed.py`, only when `seed_data` creates the role): Admin Staff get `can_create_activity = True` and `can_approve_certificates = True`; `can_approve_documents` and `can_manage_users` stay False. `can_create_activity` is now set only on create too (before: re-applied on every run), like the approval flags, so a rerun never undoes a switch in the Admin Console; `can_manage_users` is still re-applied on every run (a System Administrator always exists). **No database or ERD change**; no data migration (no hosted copy exists yet): an existing database gets it with the new switch. ERD note to update: ROLE.`can_approve_certificates` "Director, both Project Heads, Admin Staff, System Administrator (approve certificates, add signatures)".
     - **Roles screen**: "Create and edit activities" now has a Turn on / Turn off switch like the two approval columns (`accounts.services.set_role_activity_creation` through `update_role_permissions`: the separation-of-duties check and the `accounts.audit` line "Role flag changed: … can_create_activity …"; toasts "<role> can now create and edit activities" / "… can no longer create or edit activities"; `POST /accounts/console/roles/<id>/activity-creation/`, can_manage_users only, POST only, enabled=true|false). Turning it off is allowed with no last-member guard; an amber note says when no active member can create or edit activities (`services.active_activity_creators`). The footnote now says a role that manages users may approve certificates.
     - **Separation of duties unchanged** (item 339, 344): `can_manage_users` never goes with `can_create_activity` or `can_approve_documents` (the switch refuses with the existing messages); `can_approve_certificates` may (item 361).
     - **Everything follows the flags**, never the role name, so nothing else changed in the code: Admin Staff now see Change status / Edit and the New activity page (no "View only: your role cannot change activities"), the certificate approval button, the Signatories page and signature uploads, the certificate text editors, Needs attention's actions, report generation (`can_create_activity`, item 204); every Director is notified when Admin Staff create an activity (any non-Director, item 361). Only code comments that said "Admin Staff: view only" were reworded ("without the flag").
     - **Tests**: the checks that used Admin Staff as "a member without permissions" now use a test-only role **"View Only (test)"** with every flag off (`conftest.VIEW_ONLY_ROLE`, fixture `view_only`; created by the `roles` fixture, never in a real database), so every "cannot …" check stays. New: `accounts/tests/test_activity_creation_flag.py` (switch, toasts, audit, separation, warning, seed create-only, 403/405), `activities/tests/test_admin_staff_rights.py` (the seeded Admin Staff create, edit, see the page actions, open the Signatories page, Directors notified; still no document approval or Admin Console).
     - **Roles after this change** (seed defaults): Director: create ✓, manage users ✗, approve documents ✓, approve certificates ✓; Incubation Project Head and Community Development Head: ✓ ✗ ✗ ✓; Admin Staff: ✓ ✗ ✗ ✓; System Administrator: ✗ ✓ ✗ ✓.
389. **Deleting an activity (System Administrator)** (owner request, 2026-11-16; an exception to "activities are never deleted", items 49, 59, 345). *(Open question, CLAUDE.md.)*
     *(2026-11-24, item 432, owner: Approved activity documents (approved copies) no longer block a delete; they are deleted with the activity and their files removed after the commit. Still refused: an Issued certificate, Approved certificate evidence, a document an AI answer cites. The confirm dialog says "N documents (approved) and their N files"; ACTIVITY_DELETION.counts also stores `documents_approved` and `document_files`.)*
     - **Flag** `ROLE.can_delete_activities` (`Permission.DELETE_ACTIVITIES`), never a role-name check. On for System Administrator only (migration `accounts/0008` sets it on the row named "System Administrator"; `seed_data` sets it only when it creates a role). Switched on the Admin Console roles screen like the other flags (`set_role_activity_deletion`: toast, `accounts.audit` line, amber note when no active member has it) and in the Django admin. **Allowed together with `can_manage_users`** (cleaning up mistakes and test activities is the System Administrator's job) and with every other flag; the separation rules of items 339 / 344 are unchanged.
     - **Refused** (`activities.deletion.deletion_refused_reason`, the one rule; page, dialog and service) while the activity holds real records: any **Issued** certificate, any **Approved** document (activity documents and certificate evidence), or a document an AI answer cites (AI_MESSAGE_SOURCE is PROTECT; only Approved documents are cited today). Message: "This activity can't be deleted: it has 1 issued certificate and 2 approved documents. Real records are kept."
     - **Otherwise** (`activities.deletion.delete_activity`, one transaction, the activity row locked; the member types the activity's title exactly, outer spaces ignored): deleted are the certificates waiting for approval **and approved ones not yet issued** (listed separately in the dialog: "… approved certificates not yet issued (resource persons' copies may already be printed)"), their evidence, the activity's documents (For Review / returned; their AI index rows cascade), evaluations, registrations and answers, forms, the activity's own certificate text, signatory slots, category and resource person links, notifications, the History rows (`ActivityAuditLogQuerySet.delete_for_deleted_activity`: the ONE exception to the append-only log) and the activity. **Kept**: resource persons, signatories, startups, members. The stored files (documents, evidence, certificate PDFs) are removed from `media/` **after the commit** (`transaction.on_commit`; a failed delete loses no file; a file already missing is logged and skipped). Queued certificate tasks for a deleted certificate end quietly (`certificates.services.CERTIFICATE_GONE`, no retry).
     - **The record that survives**: new table **ACTIVITY_DELETION** (no foreign key to the activity): `deleted_activity_id`, title, dates, activity type name, `counts` (JSON: registrations, attended, evaluations, certificates waiting / approved not issued, documents, evidence, files, forms, history, notifications), `certificate_numbers`, `deleted_by` (TBI_MEMBER, PROTECT, required), `deleted_at`; plus one `activities.audit` log line (IDs and counts). Shown read only, newest first, 25 a page, in the Admin Console ("Deleted activities") and the Django admin.
     - **Certificate numbers are never given again**: new table **CERTIFICATE_NUMBER_SEQUENCE** (`year` PK, `last_number`). `_certificate_numbers()` locks the year's row and continues from max(stored number, highest existing number); migration `certificates/0016` fills it from the existing numbers. This also fixes item 362's undo-attendance case (the old "highest existing + 1" rule gave a deleted NEWEST number again).
     - **Page**: a small "⋯" menu beside the activity's title (only with the flag) → "Delete activity…" → a dialog (HTMX into `components/modal.html` remote) with the refusal, or the list of what goes with it and the title box; without JavaScript the same as a page (`/activities/<id>/delete/`). After the delete: the Activities list with a toast.
     - **AI answers**: an `[activity:n]` marker for a deleted activity shows "(activity no longer available)" in plain text (never a link); a find-files group heading says "Activity no longer available"; deleted files already showed "no longer available" (item 382).
     - Tests: `certificates/tests/test_activity_deletion.py`, `accounts/tests/test_activity_deletion_flag.py`.
390. **My profile and positions** (owner request, 2026-11-16). *(Positions: the TBI's list is an open question, CLAUDE.md.)*
     - **No new sidebar item**: the name card at the bottom of the sidebar (avatar, name, role) opens **My profile** (`/accounts/profile/`, before: the password page); hover (white 18%), a white `:focus-visible` ring, no outline on mouse focus, `aria-current` on the page. The card shows no position (not crowded).
     - **Always your own account** (no member ID in the URL; posted IDs, email or role are ignored): `accounts.services.update_own_profile` (full name, trimmed, 1–255 characters, like TBI_MEMBER.name; position) and `change_own_password` (current password, the new one twice, Django's validators, different from the current one; wrong current passwords limited to 10 per hour per member with `config.ratelimit`; `must_change_password` cleared; the view keeps the session with `update_session_auth_hash`; a "Your password was changed" email without the password, printed while `EMAIL_REAL_SENDING` is off, message through `email_sent_message`; a failed email never undoes the change). Logged on `accounts.audit` (IDs and names, never a password). Email and role are shown read only ("Only the System Administrator changes it", "Your role is set by the System Administrator").
     - **Position = a displayed job title only**, never a permission (no code reads it for one; the role decides). New table **POSITION** (`position_id`, `name` unique ignoring case and spaces, `is_active`, `created_at`) and **TBI_MEMBER.position_id** (nullable FK, PROTECT). Managed in the Admin Console → **Positions** (can_manage_users: add, rename, deactivate / reactivate; never deleted: a deactivated position stays on its members and is no longer offered; the member's own inactive one stays selectable). Starts empty (no seeded placeholders). Shown under the name on My profile and in the Members list. Django admin: view only.
     - A small note on My profile when an active certificate signatory has the member's name: certificates use the signatory list, so changing the name here changes nothing printed.
     - Tests: `accounts/tests/test_profile.py`.
     - **Tables: 31 → 34** (POSITION, ACTIVITY_DELETION, CERTIFICATE_NUMBER_SEQUENCE); new columns ROLE.can_delete_activities, TBI_MEMBER.position_id.

## Hosted client review version: Docker Compose setup, written for one Azure server (2026-11-16; the UAT ran on the laptop)

A hosted copy for NAVIGATU staff to check the system with real accounts (hosted UAT). **Full live deployment is out of
scope of the study**; this is not the final production deployment. The setup was written for ONE Azure virtual machine
(Azure for Students, B2ats v2, about 1 GB of memory, Ubuntu 24.04 LTS) with Docker Compose. **That server was never
used**: the review version and the November 17, 2026 UAT run on the researchers' laptop with the same production setup
(Docker Compose project `csu_tbi_uat`, `deploy/docker-compose.laptop.yml`, a Cloudflare quick tunnel; DEPLOY.md "Laptop
review version", item 414). The Azure items below describe the optional server guide. **Since 2026-11-21 neither is
used** (item 415): all further testing and the defense demo run only on the researchers' laptop. No Railway (not free); no earlier
deployment work existed, so nothing host-specific had to be removed. Step-by-step guide: `docs/DEPLOY.md`.
**No database or ERD change** (no migration, no new column; `docs/design/erd.png` unchanged).

391. **Server setup and security** (owner request, 2026-11-16).
     - `deploy/docker-compose.prod.yml` (project `csu-tbi`, always through `deploy/compose.sh` with the server's
       `/opt/csu-tbi/.env`, root only, mode 600): **db** `pgvector/pgvector:pg17` (named volume, **never published**),
       **release** (one-off `deploy/release.sh`: `check --deploy --fail-level ERROR`, `migrate`, `createcachetable`,
       `seed_data`, `setup_schedules`, `collectstatic`), **web** (gunicorn, `deploy/start-web.sh`: gthread 2×2, timeout
       120 s > the assistant's 90 s, `--max-requests`), **worker** (`qcluster`, same image and media volume), **caddy**
       (the only published ports 80/443, automatic free HTTPS for `<name>.<region>.cloudapp.azure.com`, `/static/` from
       the static volume, uploads ≤ 12 MB, no access log). Web and worker start only after the release step succeeded.
     - The image (`Dockerfile`): `python:3.12-slim-bookworm`, Pango/HarfBuzz, fontconfig, DejaVu, tzdata; only
       `requirements.txt` (pytest, Playwright and pypdfium2 stay in `requirements-dev.txt`); non-root user `app`. New
       dependency: **gunicorn** only (Caddy serves the static files, no WhiteNoise).
     - Settings from the environment: `BEHIND_HTTPS_PROXY=True` → `SECURE_PROXY_SSL_HEADER` (X-Forwarded-Proto) and HSTS
       for 1 hour **without includeSubDomains** (cloudapp.azure.com is a shared domain); `CSRF_TRUSTED_ORIGINS` = SITE_URL
       when https; `SECURE_CONTENT_TYPE_NOSNIFF`; `TRUSTED_PROXY_HEADER=X-Forwarded-For` (Caddy sets it to the visitor's
       address, `client_ip()` reads the last value). `config/checks.py` (`check --deploy`, DEBUG off only): SITE_URL must
       be https (E001) and in ALLOWED_HOSTS (E002), the secret key must not be a placeholder (E003); warnings for a proxy
       without the trusted header (W001) and SMTP without a user/password (W002).
     - **Tokens never reach the logs**: Django logs the path of refused requests ("Not Found: /evaluate/<token>/");
       `config.logfilters.HideTokensFilter` on the console handler replaces evaluation, set-password and password-reset
       tokens with `<hidden>`. Docker logs rotate (10 MB × 3).
     - Server: SSH with a key only, no root login, firewall (Azure NSG + ufw) open for 22 (NSG: your IP only), 80, 443;
       automatic security updates with a restart at 03:30 if needed; 2 GB swap; Asia/Manila time zone
       (`deploy/server-setup.sh`). Code from the private GitHub repository with a read-only deploy key;
       `deploy/update.sh` = git pull + build + up (the release step migrates). Daily backup at 02:00 (`deploy/backup.sh`:
       `pg_dump -Fc` + the media volume, newest 14 kept), `deploy/restore.sh` (asks for the database name).
     - `/healthz/` (`config.views.healthz`): "ok" when the database answers, 503 "database unavailable" otherwise; no
       login, no cookie (Docker's health check). Tests: `config/tests/test_review_deploy.py`, `test_deploy_files.py`.
392. **"Review version" label, noindex, robots.txt** (owner request). `REVIEW_VERSION_LABEL` (empty = none; server:
     "Review version") is shown as a small amber pill on every staff page (top bar) and on the sign-in pages
     (`config.frontend.review_label`); public pages do not show it. Every response gets `X-Robots-Tag: noindex, nofollow`
     (`config.headers.NoIndexMiddleware`, always, also in development: nothing of the system belongs in a search
     engine; base.html already had the meta tag) and `/robots.txt` disallows everything.
393. **Startup sync can be turned off** (owner request). `STARTUP_SYNC_ENABLED` (default True; **False on the review
     server**: the mock provider would load invented startups into a clean database). Off: `setup_schedules` removes /
     never creates the "Daily Navigatu startup sync" schedule (`DailySchedule.enabled_setting`), the task returns
     `{"skipped": …}`, `sync_startups` refuses (also with `--force`), System status shows "Turned off
     (STARTUP_SYNC_ENABLED=False)" (not counted as missing).
394. **Privacy notice and consent on the public pages** (owner request; *pending approval by NAVIGATU and the TBI*).
     - `/privacy/` (`templates/public/privacy.html`) and a short summary above a **required checkbox** ("I have read the
       privacy notice and agree that my details are used as described.") on the public registration page, the startup
       portal's registration page and the evaluation's review step (before "Confirm and submit"); the public footer
       links to the notice. One rule: `registrations.services.check_privacy_consent` (field `privacy_consent`: a ticked
       box or JSON `true`), called before `register()` on `/register/<id>/`, `/api/register/<id>/`, the portal page and
       `/startups/portal/api/activities/<id>/register/`, and before `submit_evaluation()` on `/evaluate/<token>/` and
       `/api/evaluate/<token>/`. The service functions themselves are unchanged (staff tools, demo seed).
     - **Nothing is stored** (no column, no ERD change): a registration or an evaluation can only exist when the box was
       ticked. If per-person proof is wanted later, that needs `REGISTRATION.privacy_consent_at` and
       `EVALUATION_SUBMISSION.privacy_consent_at` (an ERD change).
     - **Notice text and date (review version, approved by the owners on 2026-11-17; was 2026-11-16 with
       placeholders)**: what is collected (name, email, optional contact number, participant category, form answers,
       attendance, evaluation answers, certificate name and email); why: "This is a review version of CSU-TBI Track: what
       you enter here is used for testing and evaluating CSU-TBI Track during the researchers' capstone study (User Acceptance Testing)", and within that test to run the activity, attendance, evaluation link and
       certificate, activity reports with totals; who sees it (authorized TBI staff; never other participants; not shared
       outside CSU and NAVIGATU unless the law requires it); retention (*replaced by item 414 on 2026-11-21*): "In this review version, data entered in this review version is kept only until the end of the study and deleted no later than November 30, 2026"; rights
       under the Data Privacy Act of 2012 (RA 10173), contact: "Regine S. Paclibar and Chrizza Patricia E. Mandap, BSIT, CCIS, Caraga State University, through the project email shown in the invitation emails"; "Version November 17, 2026".
       The checkbox's summary (its retention sentence replaced by item 414): "The CSU Technology Business Incubator collects the details on this form, in this review
       version for testing and evaluating CSU-TBI Track during the researchers' capstone study (User Acceptance Testing), to run this activity, record attendance, send your evaluation link and certificate, and
       report on its programs. In this review version, data entered in this review version is kept only until the end of the study and deleted no later than November 30, 2026; they are not shared outside CSU and NAVIGATU.
       Questions or requests: Regine S. Paclibar and Chrizza Patricia E. Mandap, BSIT, CCIS, Caraga State University, through the project email shown in the invitation emails."
       This text is for the UAT only; the production text (real retention period, Data Protection Officer) still needs
       NAVIGATU and the TBI's approval (open question, CLAUDE.md).
     - **The default "Data Privacy consent" question is removed** (owner decision, 2026-11-16; changes the default
       fields of item 280 and `seed_data`'s list): `accounts.seed.DEFAULT_FORM_FIELDS` now has only "Affiliation /
       Organization" for registration, so new forms show only the system's privacy checkbox. Only new installs (and the
       hosted review version) are affected: `seed_data` never deletes DEFAULT_FORM_FIELD rows that already exist, and
       existing activity forms keep their own fields (no data migration). On an older database, the System Administrator
       can delete the old "Data Privacy consent" rows in Django admin → Default form fields if new forms should stop
       copying it.
     - Tests: `registrations/tests/test_privacy_consent.py`.
395. **Clean database and the first System Administrator** (owner request). The review server runs `seed_data` only
     (never `seed_demo`), with `SEED_ADMIN_*` empty (seed_data then skips the account). `python manage.py
     create_first_admin` (`accounts.services.create_first_admin`) asks for email, name and password (typed twice,
     hidden, validated, never printed or logged), creates the System Administrator (no forced change: they typed it), is
     refused once an active member can manage users, and logs "First System Administrator created" on `accounts.audit`.
     Everybody else is invited from Admin Console → Members (temporary password, forced change); positions are added by
     the System Administrator. A fresh seed gives the role defaults of items 388–389 and the document types of 386–387
     (tested in `accounts/tests/test_review_clean_seed.py`). Tests: `accounts/tests/test_first_admin.py`.
396. **One TEST startup** (owner request). `python manage.py create_review_startup --email … [--name …]`
     (`startups.services.create_review_startup`): an active startup without a Navigatu ID or password, the name always
     starts with "TEST", its "set your password" link emailed at once (never printed); nothing saved if the email fails;
     refused for an email already used. Tests: `startups/tests/test_review_version.py`.
397. **Fonts and PDFs on Linux; small memory** (owner request). Linux has no Georgia: the certificate template now says
     `Georgia, Gelasio, "Times New Roman", serif`; **Gelasio** (SIL Open Font License, the same letter widths as Georgia;
     `deploy/fonts/gelasio/` with `OFL.txt`) is installed in the image, so certificates look the same on Windows
     (Georgia) and on the server. Report PDFs use DejaVu Sans (`fonts-dejavu-core`). Memory (about 1 GB): 2 GB swap,
     gunicorn 2 workers × 2 threads, `Q_CLUSTER_WORKERS=1` (setting, default 2) with `recycle: 50`, Postgres
     shared_buffers 96 MB / max_connections 30, container limits (db 320 MB, web 400 MB, worker 400 MB, caddy 64 MB, swap
     allowed above each). If it is too small: resize to B2als v2 (4 GB) from the credit (docs/DEPLOY.md).
398. **Email on the server** (owner request). Gmail SMTP on port 587 with an App Password, `EMAIL_REAL_SENDING=True`
     (with DEBUG off SMTP is used anyway; the flag makes it explicit); `DEFAULT_FROM_EMAIL` must be the Gmail address. The
     demo-domain guard stays. No Brevo backend existed, so there is no `EMAIL_PROVIDER` switch. Every email people get
     carries the https SITE_URL: invitation (already), evaluation link (already), forgot password (already), **certificate
     email** (new line "CSU-TBI Track: <SITE_URL>/") and **password changed** (new "Sign in here: <SITE_URL>/accounts/login/").
     `send_test_email` also prints SITE_URL. Gmail's daily sending limit (about 500 recipients) is enough for the review.

## Corrections after UAT, review round 1: reports (2026-11-17)

UAT with NAVIGATU on November 17, 2026; the corrections are shown to them on November 19 (the system freezes on
December 3). **No database or ERD change, no migration** (safe for the laptop review version's UAT data): every new
value is worked out from existing columns, and the new files are made on request and never stored.

399. **Quarterly, semi-annual and annual reports** (NAVIGATU comment).
     - **Period choices** on Reports → New report (More settings and the summary card): This month · Q1 (Jan–Mar) · Q2
       (Apr–Jun) · Q3 (Jul–Sep) · Q4 (Oct–Dec) · First half (Jan–Jun) · Second half (Jul–Dec) · Whole year (Jan–Dec) ·
       Custom dates, and a **Year** choice for the quarters, halves and the whole year (this year − 5 to this year + 1,
       plus a filled year outside that). They replace "This quarter / January – June / This year / Last year" (Q + Year
       covers them). The default is this quarter of this year. Custom dates are unchanged.
     - **The dates are ours**: `reports.services.preset_range(key, year)` (calendar quarters and halves from
       `assistant.periods`, Manila calendar). Without JavaScript the server works out the dates from `range` +
       `period_year`; with JavaScript Alpine only LOOKS UP a table the server made (`pages.date_presets`, `json_script`),
       never computes a date. Quarters follow the calendar year *(open question, CLAUDE.md)*.
     - **"What report do you need?"** (`assistant.periods.parse_range`; `parse_period` and the AI Assistant unchanged):
       "quarterly report for Q3" → Q3 of this year; "annual report 2026" / "yearly report" → the whole year (no year:
       this year); "semi-annual … first half 2025" → H1 2025; a year written anywhere in the request counts ("Q3 report
       for 2025": before, this year). Gemini's `period_words` are read with the whole request as context, so "Q3" still
       finds the 2025. "quarterly" / "semi-annual" without saying which quarter or half → kind `ASK` and the note
       "Which quarter? Choose the quarter and the year under Period; the dates were not changed." (never guessed). The
       fill selects the matching preset and year (`pages.matching_range_key`; a preset wins over This month). Gemini's
       instruction says frequency words belong to the period. One example chip: "Quarterly accomplishment report for Q3
       (PDF)".
     - **Title and file name** say the period, worked out from the stored dates (`content.period_name`, no column, so
       old reports get it too): "Accomplishment Report - Q3 2026", "Semi-annual Accomplishment Report - January to June
       2026", "Annual Accomplishment Report - 2026"; any other range "Accomplishment Report - March 5 – April 2, 2026"
       (before: "…: …"). Download names: `accomplishment-report-q3-2026-<id>.pdf`,
       `semi-annual-participation-report-jan-jun-2026-<id>.pdf`, `annual-accomplishment-report-2026-<id>.xlsx`; other
       ranges keep `<type>-report-<start>-<end>-<id>`. The stored path is unchanged. The PDF header, the Excel title
       block and Report info say "Period: Q3 2026 (July 1 – September 30, 2026)"; the history shows "Q3 2026" above the
       dates.
     - Tests: `reports/tests/test_period_presets.py`, `assistant/tests/test_periods.py`; opt-in Chromium
       `reports/tests/test_report_form_browser.py` (the Year in the summary card; still fits 1366×768).
400. **Participants by category per activity** (NAVIGATU comment).
     - `reports.metrics.participants_by_category(activity_ids)` (two grouped queries + the names, whatever the number
       of activities): per activity, registered and attended per participant category (REGISTRATION.participant_category)
       and each category's share of the activity's registered / attended. An activity lists its own categories (also
       with 0) and any other category it has registrations in (a category removed from the activity later), by name.
     - **Rounding** (`metrics.shares`): to 0.1 % with the largest-remainder method (every share rounded down to 0.1 %,
       the missing 0.1 %s go to the largest remainders, ties to the earlier row), so each activity's percentages add up
       to exactly 100.0 % (1/3, 1/3, 1/3 = 33.4 + 33.3 + 33.3). Integer arithmetic only. **Blank-safe**: nobody
       registered / attended → every share is None (PDF "—", Excel an empty cell, page "–"), never 0 % or a division by
       zero.
     - **Activity page → Overview**: a full-width "Participants by category" card (Category | Registered | % of
       registered | Attended | % of attended, total row 100.0 %), any active staff member
       (`reports.services.activity_participants_by_category`, counts only; filter `share` in `config/ui_tags.py`).
       *(2026-11-18: replaced by a donut with whole percentages, item 403; the `share` filter was removed; the report
       section is unchanged.)*
     - **Reports**: new section `category_by_activity` "Participants by category per activity", in the default sections
       of the Accomplishment and the Participation Report (any type may add it; stored reports keep their sections). One
       row per activity and category (Activity, Date, Participant category, Registered, % of registered, Attended, % of
       attended), so the Excel table can be filtered; the note explains the rounding. Excel sheet "Category per
       activity", % as `0.0%`, formula-like titles stay text.
     - Tests: `reports/tests/test_category_by_activity.py`; the real-WeasyPrint layout check includes it.
401. **Evaluation summary per activity** (NAVIGATU comment; answers item 199's TBI question for one activity).
     - Evaluation tab → "Evaluation summary (PDF)" / "(Excel)" → `GET /reports/activities/<id>/evaluation-summary/<pdf|excel>/`
       (`reports.page_views.evaluation_summary`), **any active staff member** (like the tab; no names in it). Made on
       request and never stored (no GENERATED_REPORT row, no new column): an attachment, `Cache-Control: private,
       no-store`, `nosniff`; file `evaluation-summary-activity-<id>.pdf|xlsx`.
     - Numbers: `reports.metrics.activity_evaluation_summary(activity)`: registered, attended, evaluations, response rate
       (evaluations / attended, blank when nobody attended); per rating question of the activity's evaluation form (form
       order) the frequency of 5, 4, 3, 2, 1, the percentage of that question's answers (`shares`, adds up to 100.0 %)
       and the mean (2 decimals; none without answers). **No interpretation scale** (owner decision 2026-11-17: the mean
       only until the TBI gives its scale: open question, CLAUDE.md).
     - **Text answers** (short and long text questions, and the Comments box): listed **without names** (never the
       registration's name, the certificate name or the email), empty ones left out, sorted A–Z ignoring case so they
       cannot be matched to the order of submission. A participant may still type a name inside an answer; it is shown
       as typed. Excel keeps formula-like answers as text.
     - Built as a `ReportContent` (`content.build_evaluation_summary`) with `meta_lines` (Activity, Dates, Activity type)
       instead of the period lines, so the landscape PDF template, `pdf_columns` and the Excel writer are reused.
     - Tests: `reports/tests/test_evaluation_summary.py`; the real-WeasyPrint layout check.
402. **One participant's evaluation** (NAVIGATU comment; **owner decision 2026-11-17: only can_create_activity**).
     - **Who**: `registrations.services.INDIVIDUAL_EVALUATION_PERMISSION` = can_create_activity (seed: Director, both
       Project Heads, Admin Staff). Everyone else, **the System Administrator included**, sees only the evaluation
       summary without names (item 401). Checked in `evaluation_answers()` (PermissionDenied) and on the PDF view
       (`permission_required` → 403); never public, never `/media/`.
     - **Registrations tab**: the "Submitted" badge opens the registration dialog (like "View"). The dialog gets an
       "Evaluation answers" part (submitted when, name on the certificate, every answer in form order with ratings as
       "4 / 5", the comments) and **Download evaluation (PDF)**; without the permission only the note "Only staff who
       can create activities see a participant's evaluation answers. The evaluation summary without names is on the
       Evaluation tab." (`registrations.pages.evaluation_detail`).
     - **PDF**: `GET /registrations/activities/<a>/registrations/<r>/evaluation.pdf` (`registrations.page_views.evaluation_pdf`
       → `reports.services.individual_evaluation_file`): the landscape report layout with the name, category, name on
       the certificate, submitted date and the answers; **never the email or the contact number**. File name with IDs
       only (`evaluation-activity-<a>-registration-<r>.pdf`), attachment, `private, no-store`. 404 when the registration
       is not this activity's or has not evaluated.
     - Tests: `reports/tests/test_individual_evaluation.py` (403 on the PDF and no answers in the dialog for "View Only
       (test)" and the System Administrator).
     - *(2026-11-18: the dialog and the PDF were redesigned, portrait PDF, contact details collapsed: item 404.)*

## Corrections after UAT, review round 1: display redesign (2026-11-18)

403. **Participants by category as a donut** (owner request 2026-11-18; replaces the Overview table of item 400).
     - **Activity page → Overview**: the card "Participants by category" now sits in the right column under Registration
       and Share. It shows a donut in the dashboard's style. The SVG ring, colours (`reports.dashboard_page.CATEGORY_COLORS`) and
       rounded-end gaps come from one shared function, `reports.dashboard_page.donut_segments`, and one template,
       `components/donut.html`, used by the dashboard's "Attendees by category" too (its numbers unchanged).
     - **What it counts**: attended, once anybody is marked as attended; before that registered, with the centre word
       "registered" and the note "Nobody is marked as attended yet; the chart shows registrations." Counts come from
       `reports.services.activity_participants_by_category` (item 400, unchanged); `activities.pages.category_donut` only
       lays them out.
     - **Legend**: coloured dot, category, whole percentage in brackets, count on the right ("Incubatees (36%) 92"), and,
       on the attended chart, a small second line "110 registered" (no toggle, no JavaScript). Sorted by count, then name.
       A category with 0 stays in the legend "(0%)" but has no slice; nobody registered = an empty grey ring, "0
       registered" and "(–)" for every category.
     - **Rounding**: whole percentages with the same largest-remainder rule (`metrics.shares(counts, units=100)`), so they
       add up to exactly 100 %. The report section `category_by_activity` (PDF / Excel) keeps its 0.1 % table (item 400).
     - **Accessibility**: the ring has `role="img"` and an `aria-label` with every number ("Participants by category, 255
       attended: Faculty 163 (64%), Incubatees 92 (36%)"); the legend is a text table with a screen-reader caption, so
       nothing is shown by colour only. The dashboard donut got the same aria-label.
     - Tests: `reports/tests/test_category_donut.py`.
404. **One participant's evaluation, redesigned for reading** (owner request 2026-11-18; layout of item 402, same rule).
     - **Data** (`registrations.services.evaluation_answers`, same permission, never the email or contact number):
       `ratings` (question, value 1–5 or None when unanswered, maximum), `average` (the participant's mean of the answered
       ratings, 2 decimals, through `reports.metrics.participant_average` = the summary's rounding; None without ratings),
       `answers` (every other question with a non-empty answer in form order, checkbox answers joined, the Comments last).
     - **Registration dialog**: the top shows only name, participant category, registration date, attendance and
       evaluation status. Then "Evaluation answers": name on the certificate, category, "Submitted Nov 17, 2026, 3:45 PM"
       and **Download evaluation (PDF)** on the right; "Average rating: 4.50 out of 5"; one row per rating question with
       5 dots (hidden from screen readers) + "4/5" (screen readers: "4 out of 5"; unanswered: "Not answered"); the text
       answers in light bordered cards (grey label, normal text), empty ones left out. **Email, contact number and the
       registration answers** moved into a native `<details>` "Show contact details and registration answers" below the
       evaluation (owner decision 2026-11-18): **closed** when the evaluation is shown, **open** when it is not (not
       evaluated yet, or staff without can_create_activity, who still see only the note).
     - **PDF**: its own **portrait A4** template (`reports/templates/reports/individual_evaluation_pdf.html`,
       `reports.pdf.individual_evaluation_html`, rendered through `get_renderer()`): one person's filled-in form has no
       wide tables and reads like the paper form; the period and summary reports stay landscape (item 385). Same parts
       as the dialog: activity title, dates and type; the participant box (name on the certificate, "Registered as …"
       only when different, category, submitted); the average; rating rows with ●●●●○ and "4/5"; the answers in boxes.
       No registration answers, never the email or contact number; file name, headers, 403 and 404 unchanged.
       `content.build_individual_evaluation` (the landscape table version) was removed.
     - Tests: `reports/tests/test_individual_evaluation.py`; the opt-in real-WeasyPrint check renders the new template.
     - No database or ERD change, no migration.


## Corrections after UAT, review round 1: documents (2026-11-18)

NAVIGATU comments from the November 17 UAT, shown on November 19. **One migration** (`activities/0009_activity_creation_documents_required`):
one new NOT NULL boolean column on ACTIVITY with default False. Every existing row (also the laptop review version's UAT data)
gets False and keeps the current rules; no data is changed or deleted; it can be undone (RemoveField). Back up the UAT
database and media before `uat up -d --build` (the release step runs `migrate`; DEPLOY.md "Laptop review version").
Still 34 tables.

405. **The Activity Design and the Request Letter are uploaded when the activity is created** (NAVIGATU: they already exist
     then; owner decisions 2026-11-18). *(Amended by item 417 on 2026-11-21: both are OPTIONAL on the New activity form and
     needed only for "Create as: Scheduled"; Drafts get their reminders. Everything else below still applies.)*
     - **Which types:** `settings.DOCUMENT_TYPES_REQUIRED_AT_CREATION = ["Activity Design", "Request Letter"]`;
       `documents.services.creation_required_types()` = those of `types_allowed_at_creation()` that are active and
       `is_required`. A type the admin deactivates or makes optional drops out of the rule (nothing breaks).
     - **New column `ACTIVITY.creation_documents_required`** (boolean, default False): True = created under this rule. Set
       only at creation, by `documents.services.create_activity_with_documents` (the New activity form) and by the JSON API
       create (`POST /activities/api/`, owner decision: the API gets the flag too) through
       `activities.services.create_activity(..., creation_documents_required=True)`. The Django admin add form, `seed_demo`,
       `certificate_walkthrough` and test fixtures keep False. Not in `EDITABLE_FIELDS`: never changed afterwards (the edit
       form, `update_activity`, the API update and the admin cannot touch it). A "created after date X" setting was
       rejected: the date would differ between the development, UAT and test databases.
     - **New activity form:** the Documents section starts with two fixed, required fields, "Activity Design" and "Request
       Letter" (file + required title, ASSUMPTIONS 410; `documents.pages.required_document_rows`, `activities/_required_document_row.html`,
       posted as `required-<type id>-file|title`), then "Other documents (optional)" (the existing rows; the two types are
       left out of their dropdown). The required rows go first into the one service call, so row errors map back by
       position. Same checks as any upload (`validate_upload`: real content, PDF/DOCX/XLSX/JPG/PNG, `DOCUMENT_MAX_UPLOAD_MB`);
       the error shows under the field and the amber note asks to choose the files again. **All or nothing** (unchanged,
       item 327): a bad file, a missing file or a failed save leaves no activity, no document, no stored file and no
       notification. A caller that sends no row for a type gets `NewActivityError.missing_types` *(removed by item 417: the rows are optional)*.
     - The files are normal documents: For Review ("waiting for approval"), uploaded by the creator, `notify_approvers`.
     - **Drafts (owner decision: option A):** every new activity starts as Draft and the form always requires both files *(2026-11-19: the form now creates it as Scheduled, item 409)*.
       Moving **Draft -> Scheduled** on a flagged activity needs a document of each type **uploaded** (waiting, returned or
       approved; owner decision: not necessarily approved) (`documents.services.scheduling_refused_reason`, called from
       `activities.services._move_status`, so the status form, the list's quick actions and the API all refuse with "Upload
       the Activity Design and the Request Letter before scheduling this activity (Documents tab)."). Certificate evidence
       never counts. This catches a Draft made through the JSON API and a file deleted while Draft.
     - **Never missing or overdue once uploaded:** for these two types on flagged activities, overdue = past the due date AND
       nothing uploaded (`documents.services._required_item`, `is_creation_type`); a waiting or returned file shows "Waiting
       for review" / "Returned", never Overdue (an activity created 3 days before its start would otherwise be overdue at once:
       the Activity Design was due at start - 10; start - 5 since item 416). The same rule in SQL: `reports.metrics._overdue` (S3, the dashboard's Needs
       attention, the compliance report; parity test). Reminders, Needs attention and the Documents page follow the
       checklist. Every other type, and every activity without the flag, keeps "past the due date and not Approved".
     - **Delete:** the only Activity Design or Request Letter of a flagged activity that is not a Draft cannot be deleted
       ("This is the activity's only Activity Design and the activity is Scheduled. Replace the file instead.";
       `_delete_refused_reason`, which also hides Delete through `allowed_actions`). Replace stays allowed; a second copy and
       Drafts can be deleted.
     - Tests: `documents/tests/test_creation_documents.py`, `activities/tests/test_create_with_documents.py`,
       `activities/tests/test_api.py`, `reports/tests/test_compliance_parity.py`; `conftest.creation_document_files()` for
       tests that post the New activity form.
406. **Every upload goes to the Director for approval** (NAVIGATU; confirmed, no code change). Every upload (Documents tab, New
     activity form, Replace, certificate evidence) goes through `upload_document` -> For Review -> `notify_approvers`: an in-app
     "document_waiting_for_review" notification for every active member with `can_approve_documents` (seed: the Director
     only), except the uploader, in the same transaction. Needs attention: the activity's "N documents waiting for approval."
     and the dashboard's "waiting for your review" (`review_queue`, own uploads included). No email (none existed; none added).
     Creating an activity as a non-Director therefore gives the Director three notifications (activity created + two
     documents). **The Director's own upload** (owner decision 2026-11-18, item 344 unchanged): no notification to
     themselves; it is in their review queue and Needs attention, and they may approve it (by default nobody else can;
     `documents.audit` marks "(own upload)"). *(Open question, CLAUDE.md.)* Tests in
     `documents/tests/test_creation_documents.py` and `activities/tests/test_new_activity_notification.py`.
407. **Certificates -> Signatories: names no longer squeezed** (UAT bug). The row used `sm:flex-nowrap` with a `min-w-0 flex-1`
     text block next to the signature image and up to four buttons, so the name and position became a 0 px column, one
     letter per line. Now the row wraps, the text block is `min-w-0 grow basis-48` and the buttons are an `ml-auto flex-wrap`
     group that moves below the text when there is no room (the rule of item 387). The Certificates tab's Signatures section
     stacks the image above the text and was not affected. Opt-in Chromium check
     `certificates/tests/test_signatories_browser.py` (1280, 1024, 390 px: words never broken, text column at least 150 px,
     no sideways scroll; it fails on the old template).
408. **Documents page: buttons no longer cover the State badges** (UAT bug, 2026-11-19). In the "By activity" cards the
     Actions column was a fixed 10rem and the compact button group could not wrap (`flex-nowrap`), so Download + Preview +
     Approve + Return (about 290 px) spilled left over "Waiting for review" / "Overdue". Now, from 1280 px, the header and
     every line share ONE grid through `grid-cols-subgrid` (`documents/_activity_card.html`): Type / Title / Uploaded are
     fractions, State is its own `minmax(9rem, 11rem)` column whose badges wrap, and Actions is `max-content` (as wide as the
     widest line's buttons, so they stay together on one line). Below 1280 px the actions sit on the right in a
     `minmax(0, auto)` column and wrap; below 640 px they go under the text. `documents/_actions.html` always wraps (also
     compact). Checked the other document lists: the activity's Documents tab rows (item 387) and the "All documents" /
     review table (a real table that scrolls inside its card) never overlapped; the Documents tab grid got `grid-cols-1`
     (`minmax(0, 1fr)`), because at 390 px its single implicit column grew 17 px past the window. The opt-in Chromium test
     `documents/tests/test_document_rows_browser.py` now runs at 1366, 1280, 1024, 800 and 390 px with an approver viewing
     a waiting, overdue document uploaded by someone else (Approve + Return), and checks on every document line that no
     badge and button overlap and no button sticks out; it fails on the old templates. No database change.
409. **New activities are created as Scheduled; registration opens only after "Open registration"** (NAVIGATU: with its
     Activity Design and Request Letter an activity is no longer a Draft; owner decisions 2026-11-19). *(Amended by item 417
     on 2026-11-21: the form now asks "Create as: Draft / Scheduled", default Draft; Scheduled works as described here. The
     ready mark, "Open registration" and the Share card rules are unchanged.)* **One more migration**
     (`registrations/0008_form_template_ready_at`): a nullable datetime on FORM_TEMPLATE; existing rows get NULL and nothing
     changes for them (older activities ignore it). Same backup advice as 405.
     - **Status:** `create_activity_with_documents` creates the activity, uploads the two files and then calls
       `activities.services.change_status(..., "Scheduled")` in the same transaction (the one transition rule, including the
       "both uploaded" check of item 405), so History shows "Status: → Draft" and "Status: Draft → Scheduled", both by the
       creator. A failure undoes everything (no activity, no file). After saving, the page opens the **Forms tab** with
       "… was created as Scheduled with 2 documents waiting for review. Registration stays closed until you create the
       registration form, check its fields and click "Open registration"." Scheduled → Draft stays possible while nobody has
       registered. The JSON API create (no files), the admin, `seed_demo` and every older activity still start as Draft.
     - **What Scheduled triggers** (checked before building): registration only with a registration form (below); the public
       page `/register/<id>/` becomes reachable and shows "Registration is closed … the registration form is not ready"
       (Draft: 404); the automatic Scheduled → Ongoing move at the start time (item 346); document reminders and missing
       counts like any Scheduled activity; the dashboard's upcoming list (S2); no notification and no email (no status
       change sends any).
     - **Why a ready mark:** a registration form exists only after "Create from defaults" on the Forms tab
       (`create_form`, which copies the default placeholder fields). Before this item registration opened at that moment
       on a Scheduled activity, with only the defaults. Now, on activities created with their documents
       (`creation_documents_required`), the form counts only once staff click **"Open registration"** on the Forms tab
       (`registrations.services.mark_registration_form_ready`: can_create_activity, sets `FORM_TEMPLATE.ready_at`, one History
       row "Registration form: Marked ready (registration can open)", refused without a form or twice; not undone: fields
       stay editable until the first registration, and Scheduled → Draft closes registration while nobody registered).
     - **The one rule:** `registrations.services.registration_form_ready(activity, form)` = the form exists and (it is marked
       ready or the activity is older). It is used by `registration_closed_reason` (the public page and `/api/register/`,
       the startup portal page, `register()` itself, the activity page notice, the Share card) and, as the same query, by
       `activities.services.open_for_registration` (the open list `/register/`, `/api/activities/open/`, the portal's open
       activities, the Activities card and preset, the dashboard KPI). Parity test. The closed reason stays
       `REGISTRATION_FORM_NOT_READY` ("Registration is not open yet: the registration form is not ready.").
     - **Share card:** on these activities, while the form is missing or not ready, the card shows "Build the registration
       form first: create it on the Forms tab, check its fields and click "Open registration". The registration link and
       QR code appear then." with a link to the Forms tab, and NO link, copy button or QR code (`share.build_form_first`;
       the QR URL answers 404). Older activities keep showing the link with the reason.
     - **Staff hints:** the Forms tab's registration card shows an amber "Registration stays closed." box with the
       **Open registration** button (only with can_create_activity), then "Marked ready on …"; the activity header notice
       says "Registration stays closed until you check the registration form and click "Open registration" on the Forms
       tab."; Needs attention: "Registration form not marked as ready: registration stays closed. …" (Draft/Scheduled).
     - The evaluation form is unchanged (it never opens registration). Tests: `activities/tests/test_created_as_scheduled.py`.

410. **The Activity Design and Request Letter titles are required on the New activity form** (NAVIGATU, 2026-11-19).
     - Label "Title *" (same style as "File *"), no "(optional)"; the input is `required`, `maxlength="255"`, never
       prefilled; the placeholder is only an example ("e.g. Activity Design - Product Development Workshop").
     - Rule in `documents.services.creation_title_error`, called by `check_new_documents` after `title_error` (so the
       255-character limit is the normal document title's): a blank or spaces-only title on a creation-required type
       (`creation_required_types()`) is refused with "Enter a title for the Activity Design." / "... the Request Letter."
       under that field. It is checked before anything is saved, so nothing is half-created (no activity, document,
       stored file or notification). The saved document title is the typed title (stripped).
     - It applies to every row of those two types on the New activity form (also a second Activity Design in an
       optional row). After any error the typed titles are shown again (browsers clear only the file inputs).
     - Unchanged: the Documents tab upload (`upload_document` / `title_error`): the title stays optional there (empty =
       the type's name), required only for "Other". The JSON API create has no documents, so nothing changes there.
     - Tests: `activities/tests/test_create_with_documents.py` (blank / spaces refused per type, nothing created,
       length limit, valid title saved, label and placeholder, typed titles kept, Documents tab still optional).

## Corrections after UAT: UI revision (2026-11-20)

411. *(Colours replaced by item 413 on 2026-11-21; the spacing scale, page header, button levels and fixes stay.)*
     **Calmer colours, one spacing scale and a button hierarchy, same design identity** (NAVIGATU tester at the UAT,
     2026-11-17: "the colors are too bright, the spacing is inconsistent, finding the important buttons is hard, and there
     is no hierarchy on the level of importance on every page"). Visual and layout only: no business rule, URL,
     permission or database change; every button keeps its link / form / behaviour. Audit and screenshots:
     `docs/design/ui-revision/` (README.md). A first version (2026-11-20, "phase 2") replaced the gradient frame with a
     flat page; **the owner rejected it the same day ("changed the design too much")**, so the identity of items 254, 257
     and 258 stays: the gradient frame, the floating rounded panel, the frosted glass sign-in and public cards, pill
     buttons, pill tabs and pill segmented controls. What changed:
     - **Calmer gradient** (owner asked for two colours; first #22378F → #5E86C0, then "darker, less saturated, less
       contrast between the two ends"): **`#1F2F6E` → `#41598C`** (saturation 56% / 37%; contrast between the ends 1.8:1
       instead of 2.8:1), same 135° direction and stops, on the app frame and the sign-in / public pages. White text on
       the dark part 12.4:1. The frosted sign-in / public card is white 80% (was 70%), so its muted text keeps 5.2:1 or more
       wherever it sits on the gradient.
     - **Only a page's primary button is solid primary.** The dashboard welcome card and the "Happening now" / "Next
       activity" spotlight are white cards like the others: the welcome card keeps the brand gradient as a 3 px line along
       its top, the spotlight a green "happening now" dot or a tinted calendar icon; their buttons are secondary; the
       illustration is smaller (w-44). Date tiles, the current pill tab (activity page, Admin Console), count badges next
       to list switches, the calendar's "today", the selected summary card's icon, the AI Assistant's question bubbles and
       source numbers, checked option pills (evaluation ratings, report form) and file buttons became the soft primary
       tint or outlined. Chart bars and rating dots keep their colour (they are data). "New chat" (AI Assistant) and "Add
       resource person" (Overview) are secondary. Still to do per page (Phase 3): the second solid button on the
       Registrations ("Mark selected as attended") and Evaluation ("Resend ...") tabs, the Forms and Certificates tabs.
     - **Calmer tokens** (`@theme` of `frontend/tailwind/app.css`): primary #2B4BA8 (was #1D4ED8; white on it 7.9:1),
       primary-soft #E6ECF8, danger #B42318 (6.6:1), warning #93370D on #FFFAEB, KPI numbers in ink (the teal is gone;
       the colour stays in the icon chip), sidebar count badge white with navy text (was amber).
     - **Softer badges**: tint + 1 px border of the same hue + dark text. The five document states stay apart by hue and
       contrast: Missing grey #344054 on #F2F4F7 (9.5:1), Overdue red #B42318 on #FEF3F2 (6.1:1, with its icon), Waiting
       for review blue #1E40AF on #EFF4FF (7.9:1), Returned amber #B54708 on #FFFAEB (5.2:1), Approved green #067647 on
       #ECFDF3 (5.4:1). Scheduled #175CD3 on #EFF8FF (5.6:1).
     - **Spacing scale** 4 · 8 · 12 · 16 · 24 · 32 px: page padding 16 / 24 / 32 px (phones / 640 px / 1280 px), card padding
       24 px (KPI cards and list rows 16 px), 24 px below the page header and between cards, 16 px card header → content and
       between form fields, 8 px label → input and between buttons.
     - **Buttons**: pill-shaped (as before), one height (40 px, `btn-sm` 32 px; form controls 40 px too, so filter bars line
       up), four levels: `btn-primary` (solid, one per page or card), `btn-secondary` (outlined; a list row's main action),
       `btn-ghost` = `btn-tertiary` (text: Open, View, Download, Preview, Return, Show more; icon-only `btn-icon` in dense
       tables), `btn-danger` (red outline, kept apart; solid only inside a confirm dialog or `btn-danger-solid`).
       `btn-warning` is retired (looks like secondary). Ask AI in the top bar is secondary.
     - **Page header** (`layouts/app.html`): the title (h1) and the page's OWN one-line description (`page_subtitle`) in
       the page; the top bar keeps the review label, search, Ask AI and the bell, so the title never appears twice.
       `{% block page_actions %}` = the page's buttons on the right (primary last); `{% block page_header %}` replaces it
       (the activity page and the document page use their header card, the New / Edit activity form its own header);
       `{% block page_description %}` (the Dashboard shows the Manila date). Every staff and portal page has its own
       description (the Dashboard's "Here's what's happening at CSU-TBI" line had appeared on every page without one);
       cards that repeated the page title lost their visible heading (Certificates, Notifications, Startups).
     - **Hierarchy applied so far**: Dashboard ("New activity" = the page's primary in the header; Needs attention row
       buttons secondary, the red / amber icon chip shows the urgency; banner "Review items" outlined on the banner),
       Activities list ("New activity" in the header), Certificates ("Signatories" secondary in the header), Documents
       (list switch in the header; rows: Approve secondary, Return tertiary, Download / Preview tertiary icons, Delete red
       tertiary 8 px apart).
     - **KPI cards**: one structure (icon + label row at least 36 px high, number + badge on one baseline, optional note
       below, equal heights), so the "Certificate approval" card's extra note no longer moves its number.
     - **No big blank areas**: Dashboard (xl): Needs attention beside Recent changes in one row, stretched to the same
       height (Recent changes: the latest 5, each at most two lines with the full sentence as a tooltip, link "View all" =
       the Activities list, where each activity's History tab has every change; it was labelled "Activity history");
       upcoming activities and the three charts in a 2 x 2 grid with equal heights per row; the spotlight shows time and
       venue on one line and a smaller ring; the welcome card centres its content. Activity Overview: Details and Resource
       persons stacked on the left, Registration / Share / Participants by category on the right (Resource persons was full
       width under both, which left Details stretched with a blank half). Other pages checked at 1366 px: no large blank
       area beside a long column.
     - **Notices** (Needs attention, registration notice): the soft amber box with links in the primary colour.
     - **Fixed while auditing**: input borders #7E8899 (3.6:1 on white; slate-300 was 1.5:1, below 3:1); Admin Console
       Members: the row's buttons wrap ("Deactivate" was hidden past the table's edge at 1366 px); Roles: "Turn on" and
       "Turn off" have one style (secondary) and the headers wrap (the last "Turn on" was hidden at 1366 px); Documents page
       cards: a fixed 21rem Actions column (replaces `max-content` of item 408), so the columns line up across activity
       cards; at 390 px the Activities list (date range, follow-up buttons) and the resource-person switch no longer
       scroll the page sideways.
     - **Label change**: the New activity description says "A new activity is created as Scheduled, with its Activity
       Design and Request Letter." (it said "starts as Draft", wrong since item 409).
     - Tests: CSS / layout tests updated (`accounts/tests/test_staff_layout.py`: calmer gradient, 40 px controls, input
       border, page header, one description per page), the dashboard tone test; new opt-in browser test
       `documents/tests/test_page_buttons_browser.py` (no two buttons overlap, none sticks out or hides in a scrolling box,
       no sideways page scroll; 1366, 1280, 1024 and 390 px; every page of the audit).

## Corrections after UAT: colour palette (2026-11-20)

412. *(Replaced by item 413 on 2026-11-21: the owner rejected the teal / cream design; nothing of this palette is left.)*
     **The four-colour palette replaces the blue gradient of item 411** (owner, correction after the UAT; defense
     Dec 9, freeze Dec 3). Visual only: no business rule, URL, permission or database change; the layout, pill shapes,
     frosted cards, spacing scale and button hierarchy of item 411 stay. Details, the token table (old → new), every
     contrast ratio and the screenshots: `docs/design/ui-revision/README.md` section 8 and `palette-*.png`.
     - **Palette**: Teal Waters `#204654` (brand: gradient frame, sidebar, the solid primary button with white text,
       headings, focus ring), Glacial Sky `#A9E0F1` (light tints only), Morning Mist `#F7F9E1` (page; the content panel is
       the lighter mix `#FAFBED`, owner's choice), Spring Meadow `#CAFFA6` (small accents only: sidebar count badge, the
       "happening now" dot with a dark green ring, the Approved tint; never a large background, a button or a gradient end).
       Every other colour is derived from them in the `@theme` block of `frontend/tailwind/app.css`.
     - **Gradient** (app frame, sign-in and public pages): `#204654` → `#2F6170`, same angles and stops; white text 10.2:1
       on the dark part and 6.9:1 at the light end. The frosted card is white 88% (was 80%) so muted text keeps 5.6:1 or
       more on it; inputs on it get the real input border (3.0:1 or more).
     - **Derived tokens**: primary hover `#18363F` / pressed `#122A31`, `primary-soft` `#E5F5FB`, `primary-tint` `#D4F0F8`,
       ink `#1A2E35`, muted `#4A5E65`, helper `#56696F`, card border `#DCE5E4`, input border `#738890` (3.7:1 on white,
       3.6:1 on the panel, 3.5:1 on Morning Mist), focus ring `#204654` (10.2:1 on white, 9.7:1 on the panel), `info`
       `#1D5A73` on `#EAF7FC`, `success` `#2E6A12` on `#F1FFE6`, warning `#8A5300` on `#FFF8E8`; danger unchanged.
       Tailwind's `slate-*` scale is re-tinted toward teal at the same lightness (contrast within ±0.2), so the 300+ slate
       classes follow the palette without editing each template; the template `blue-*` / `green-*` / `emerald-*` classes
       became the `info` / `success` / `primary-soft` tokens.
     - **A separate link colour `#1B6580`** (6.5:1 on white): found in the preview, Teal Waters is so close to the body
       text that text links (Needs attention, AI answers, privacy and contact links) no longer looked like links.
     - **Badges**: Missing grey, Overdue red + icon, Waiting for review Glacial blue, Returned amber, Approved dark green on
       the Spring Meadow tint; all 6:1 or more. The owner's first rule made Missing red like Overdue; **decided: Missing
       stays grey** so the two stay apart by colour.
     - **Charts**: status bar Draft `#C3D1D3`, Scheduled `#5BA7C2`, Ongoing `#E2B65A`, Conducted `#4F8F2E`; category donut
       `#204654 #5BA7C2 #4F8F2E #E2B65A #7B6FA6 #8A9AA0 #C47A6A #A9E0F1`. Checked with simulated red-green colour blindness
       (the first ochre / green pair was ΔE 8 for protanopia; now ΔE 14 or more for every pair and vision type).
     - **Logo** (owner decision): the 96 px logo, favicon and touch icon were the artwork on a navy `#22378F` square, which
       clashed with the teal sidebar. Re-exported from the transparent `docs/design/logo.png` onto a Teal Waters tile at the
       same size and position (not redrawn); the old files are kept as `docs/design/logo-navy-old.png`,
       `favicon-32-navy-old.png`, `apple-touch-icon-180-navy-old.png`.
     - **Report PDF and Excel** (owner decision): PDF bars, headings, table header and the rating dots of one participant's
       evaluation, and the Excel table header, are Teal Waters (white on it 10.2:1; prints as a solid dark grey in black and
       white, apart from the light grey baseline and "off" dots).
     - **Fixed**: a small square over the content panel's rounded top-left corner on the activity page. Cause: the
       resource-person pick list was a vertical scroll box with its own rounded corners inside the scrolling panel, and
       Chromium drew its corner mask over the panel's corner. The rounded border moved to a wrapper; the list scrolls inside
       it with square corners (same look). Rule: never round a vertical scroll box inside the panel (a test scans the
       templates).
     - Tests: the colour assertions (`accounts/tests/test_staff_layout.py`, `documents/tests/test_overview.py`,
       `reports/tests/test_excel_layout.py`), new tests for the palette tokens, the contrast pairs (recomputed from the
       tokens), the link colour, Spring Meadow's small uses, Missing grey, the rounded scroll boxes and the logo tiles.

## Corrections after UAT: back to the original design, calmer (2026-11-21)

413. **The original design again, made calmer, with a clear button hierarchy** (owner, after the UAT; replaces the colours
     of items 411 and 412). The UAT comment (Community Development Project Head, written on the questionnaire): "the colors
     are too bright. The spacing is inconsistent. Finding the important buttons is hard, and there is no hierarchy on the
     level of importance on every page." The teal / cream design of item 412 (uncommitted, kept on the `ui-revision`
     branch backup commit) was rejected by the owner. Visual only: no business rule, URL, permission or database change.
     - **Identity restored** (items 254, 257, 258): the navy -> blue gradient frame around a rounded light panel, frosted
       glass sign-in / public cards, pill buttons, Inter, the navy logo tile, and on the Dashboard the two filled gradient
       blocks (welcome banner, "Next activity" / "Happening now" card) and the dark date tiles.
     - **Colours: option B** of three measured options (`docs/design/options/`, README + screenshots), chosen by the owner:
       gradient `#1E2F73` -> `#3B8AA6` (the original `#22378F` -> `#60CDEE` hues, 228° -> 196°, saturation 59% / 48% instead
       of 62% / 81%, the light end at 44% lightness instead of 65%: it no longer glows). Primary `#2A4494` (white on it
       8.9:1), link `#2C5AA8`, light text on the gradient `#D3E8F0` (9.7:1). The only place the option values live is
       between `OPTION START` / `OPTION END` in the `@theme` block of `frontend/tailwind/app.css`; every other colour
       (sidebar, focus ring, shadows, the frame, banner, spotlight and date-tile gradients) refers to them.
     - **Panel and neutrals**: the content panel is a soft neutral light grey `#F4F6F9` with white cards (not white, not
       cream); Tailwind's own slate scale (the teal re-tint of 412 is gone); ink `#1E293B`, muted `#475569`, input borders
       `#7B8798` (3.7:1 on white, 3.4:1 on the panel).
     - **Status colours softened, still apart**: Scheduled / Waiting for review blue `#2B59A8` on `#EEF3FB` (6.1:1),
       Ongoing / Returned amber `#8F5A0A` on `#FDF6E6` (5.4:1), Conducted / Approved green `#26704A` on `#EDF7F1` (5.5:1),
       Overdue red `#B42318` on `#FDF1F0` (6.0:1), Draft / Missing grey `#475467` on `#F2F4F7` (7.0:1); each with a 1 px
       border of its hue. Notices and boxes use the same tokens (the bright Tailwind amber-200 / red-200 borders are gone).
     - **Charts**: status bar Draft `#CBD5E1`, Scheduled `#5B84C4`, Ongoing `#E0A940`, Conducted `#2F7A50`; category donut
       `#2A4494 #5AAFC8 #2F7A50 #EDC06A #8C6BB1 #6B7686 #D98B7A #C7DCF2` (smallest difference dE 15 with simulated
       protanopia, deuteranopia and tritanopia). Report PDFs (headings, bars, table header) and the Excel header use the
       primary `#2A4494`.
     - **Logo**: `logo-96`, `favicon-32`, `apple-touch-icon-180` re-exported from `docs/design/logo.png` on `#1E2F73`, the
       artwork at the same size and place as the tiles before (fitted to them; mean difference under 2 levels).
     - **Type scale** (staff pages): page title 24 px bold (`.page-title`, one h1 per page), section / card heading 18 px
       semibold (`.card-title` = `.section-title`), body 14 px, caption 12 px (`.caption`). The odd sizes (11, 13, 15 px,
       20 px dialog headings) moved onto the scale. Big numbers (KPI, donut) are data; the sign-in and public hero
       headlines are display text. Public and portal pages use 16 px body text (phones).
     - **Button levels**: ONE solid `btn-primary` per page or card for the main action (top right of the page header, at
       the bottom of a form, or in the card's header for a tab's main action); `btn-secondary` (outline) for other actions;
       `btn-ghost` text links for minor ones; `btn-danger` = **red text** (Delete, Return, Remove, Deactivate), never filled
       and never the biggest button; solid red only to confirm (inside `.modal`, or `btn-danger-solid` on a confirm page).
       `btn-on-dark` (white outline) on the dashboard's gradient blocks. Changed: the activity page's "Edit" is secondary
       (each tab's main action is the primary: Upload, Mark selected as attended, Approve certificates for this activity,
       Resend evaluation links); Activities list follow-up "Mark as Conducted" and Registrations row "Mark attended" are
       secondary; the certificate preview's "Reissue with current signatures" is secondary (the card's primary is the
       approval); Members: "Invite" hides while its form is open ("Send invitation" is then the one); Return on document
       rows and Deactivate on Signatories / Positions / Certificate design / Members are red text. The page table with each
       page's primary: `docs/design/ui-revision/README.md` section 9.
     - **Needs attention and pending approvals stand out**: a 4 px accent bar along the left edge (inset shadow, follows the
       rounded corners; red when something is overdue, amber `#B87612` (3.5:1 or more) when due soon, blue when only
       approvals wait) on the Dashboard's Needs attention card, the activity page's Needs attention notice (+ a count badge)
       and the "Certificate approval" / "Documents to review" KPI cards while something waits. On the Dashboard, Needs
       attention moved up: right after the filters, beside Recent changes, before the KPI cards (its items follow the
       activity-type filter, so it stays below the filter bar); rows are tinted by urgency (red / amber / blue) and the
       first approval or review the member can do ("Review" / "Approve") is the card's one solid button
       (`DashboardPage.attention_tone`, `attention_primary_index`, display only).
     - **Kept**: `:focus-visible` rings (primary, white on the dark frame; never the browser's black outline), desktop-first
       staff pages, phones for public and portal pages, Manila dates, ₱, every functional fix of items 411 and 412 (page
       descriptions, Members row, Roles switches, Documents columns, no sideways scroll at 390 px, the rounded scroll-box
       rule, the New activity description).
     - **Screenshots**: demo data only (Demo Director, demo activities; activity #12 and #14's resource-person pick list and
       the real accounts are never captured; a script checks the captured text for real names and certificate numbers).
     - Tests: `accounts/tests/test_staff_layout.py` (option B tokens, contrast pairs, gradients, filled dashboard blocks,
       accent bars, red-text danger buttons, type scale, one primary on the activity page / list, no teal left, logo tiles),
       `reports/tests/test_dashboard_page.py` (accent + the card's one primary), the PDF / Excel / focus colour assertions.
     - **Part 2: one spacing scale everywhere** (2026-11-21). Padding, margins, gaps and `space-*` use only 4 / 8 / 12 / 16 /
       24 / 32 px (Tailwind steps 1 2 3 4 6 8), plus 48 / 64 px for page-level gutters (sign-in, error pages). Before: 650
       off-scale uses in 146 templates (half steps 2 / 6 / 10 / 14 px: `mt-0.5` ×99, `py-2.5` ×98, `mt-1.5` ×71, `gap-1.5`
       ×35; 20 px: `p-5` / `mb-5` / `space-y-5` / `mt-5` / `gap-5` ×120; 28 / 36 / 40 px: `mt-7`, `p-9`, `p-10`, `pb-10`).
       Mapping: 2 → 4, 6 → 8, 10 → 12 (padding) or 8 (gaps / margins), 14 → 16 (padding) or 12 (gaps), 20 → 24, 28 / 36 /
       40 → 32, 56 → 48. Rules (checked on the rendered pages at 1366 px, every page of the page table): page padding 16 /
       24 / 32 px; card padding 24 px (KPI cards 16 px; list cards whose rows carry the padding: 0); page header → content
       24 px; 24 px between cards, 16 px between repeated cards of one list (Documents by activity was 12, Certificates by
       activity 16) and between KPI cards (the Registrations and Evaluation tabs were 12); card heading block → content
       16 px (My profile was 24); form fields 16 px apart, label → input 8 px (was 6), help / error text 8 px below the
       field (was 6); 8 px between buttons (the New activity form, public "registered" / review pages were 12; four table
       row groups were 4); public and portal cards 24 px apart (were 16). Exceptions: a field-input's one-sided icon
       clearance (`pl-10`, `pr-12`, ...), `scroll-mt-*` (an offset, not spacing), the 2 px vertical padding inside badges
       and tags, print / PDF templates and the Django admin.
     - Side effects fixed: the AI Assistant still fits 1366 × 768 (composer and message area at 16 px; the empty state no
       longer scrolls to the bottom on load, which had hidden its icon); KPI labels are 12 px below 640 px wide, so
       "Certificates" no longer breaks inside the portal's three narrow cards; segmented items have 12 px sides below 640 px
       (the Documents page switch scrolled the page sideways by 4 px at 390 px).
     - Tests: `test_spacing_scale_in_every_template` (every class attribute and the `main_class` blocks) and
       `test_spacing_scale_in_the_component_classes` (the `@apply` rules) in `accounts/tests/test_staff_layout.py`.

## Privacy notice: retention wording (2026-11-21)

414. **Review version: kept on the researchers' computer until the person asks for deletion** (team decision,
     2026-11-21; *pending confirmation by NAVIGATU*). Replaces the retention wording of item 394 ("kept only until the
     end of the study and deleted no later than November 30, 2026").
     - Consent summary (`templates/public/_privacy_consent.html`): "In this review version, the data you enter is kept on
       the researchers' computer until you ask for it to be deleted, and it is not shared outside CSU and NAVIGATU."
     - Full notice (`templates/public/privacy.html`, "How long we keep it"): "Data entered in this review version is kept
       on the researchers' computer until you ask for it to be deleted. To ask, contact the researchers listed below."
       Version date: "Version November 21, 2026." (was November 17).
     - **Why "on the researchers' computer"** (not "on the system's server managed by the researchers"): the review
       version never ran on Azure. The November 17, 2026 UAT ran on the researchers' laptop (Docker Compose project
       `csu_tbi_uat`, `deploy/docker-compose.laptop.yml`); the Cloudflare quick tunnel only passes the traffic through. The
       database, the uploaded files and the backups are on that laptop. If the review version ever moves to a server
       (DEPLOY.md sections 1–15), the sentence must change to "on the system's server managed by the researchers".
     - A deletion request: delete that person's data, make a fresh backup and delete the older backups (DEPLOY.md,
       "Laptop review version", Back up).
     - **Open point:** the Data Privacy Act of 2012 (RA 10173, Sec. 11(e)) says personal data is kept only as long as
       needed for its purpose; this wording has no end date. NAVIGATU (or the CSU Data Protection Officer) should confirm
       it or give an end date (open question, CLAUDE.md, item 414).
     - Same day: DEPLOY.md, CLAUDE.md, the heading of items 391–398 and the questions list no longer say the review version runs
       on Azure; the Azure guide stays as an optional section, "not used for the UAT".
     - Text only: no logic, URL, permission, database or ERD change. Tests: `registrations/tests/test_privacy_consent.py`
       (new wording, new version date, "November 30, 2026" gone).

## No deployment: testing and the defense on the laptop only (2026-11-21)

415. **No deployment any more** (team decision, 2026-11-21). All further testing and the defense demo run **only on the
     researchers' laptop**: no Azure server, no Cloudflare tunnel, no other hosting, no public address.
     - The Azure guide (items 391–398, DEPLOY.md sections 1–16) was never used; the Cloudflare quick tunnel was used once,
       for the November 17, 2026 UAT. Both stay in `docs/DEPLOY.md` only as optional sections labelled "not used". The
       deployment files (`Dockerfile`, `deploy/`) and their tests stay unchanged.
     - The UAT database (Docker Compose project `csu_tbi_uat`) and its backups stay on the laptop until testers ask for
       deletion (item 414); the privacy notice's "kept on the researchers' computer" stays true, so its wording is unchanged.
     - Docs only (DEPLOY.md, CLAUDE.md, this file, the questions list): no code, setting, database or ERD change.

## Activity Design due 5 days before the start (2026-11-21)

416. **NAVIGATU's 5-day rule** (NAVIGATU, 2026-11-21; answers the Activity Design part of item 9): the Activity Design is
     due **5 days before the start date** (was 10), the same as the Request Letter. The Attendance Sheet (end date) is still
     a placeholder.
     - **Where the rule lives:** only in the DOCUMENT_TYPE row (`due_reference` = start_date, `due_offset_days` = -5). Every
       due date is calculated from it (`documents.services.due_date()`, the checklist, the reminders, Needs attention, the
       SQL counts in `reports.metrics`, the compliance report); no code assumed 10 days. `scheduling_refused_reason` (item
       405) only checks that both documents are uploaded and has no date rule.
     - **Seed:** `accounts/seed.py` creates the type with -5. It stays create-only (item 386): a rerun never overwrites a
       value set in the Django admin.
     - **Existing databases:** the owner changed the row by hand in the Django admin (-10 -> -5). Data migration
       `documents/0005_activity_design_due_five_days` does the same for any other database (the UAT database, teammates'
       copies), but ONLY when the row is still exactly start_date / -10; any other value is an admin choice and is left
       alone, and nothing happens without the row. Reverse: start_date / -5 -> -10 (same guard; note that it would also
       move a row set to -5 by hand). Due dates are never stored, so no document, activity or notification changes.
       Reminders already sent stay as they are; the next daily run uses the new date. No schema or ERD change.
     - **Effect:** both documents now fall due on the same day, so Needs attention and the reminders name them together
       ("Required documents due soon and not uploaded: Activity Design, Request Letter."). An activity starting 6 to 10
       days from now no longer shows an overdue Activity Design.
     - **Demo:** `seed_demo`'s "Product Development Workshop" now starts in 4 days (was 7), so its Activity Design (For
       Review) and Request Letter (missing) are overdue and the demo still shows an overdue Activity Design in Needs
       attention (tested). It applies to a new demo database; a rerun does not move an existing demo activity.

## Choose the status when creating an activity (2026-11-21)

417. **"Create as: Draft / Scheduled"; the Activity Design and Request Letter are optional at creation** (NAVIGATU, UAT;
     owner decisions 2026-11-21). **Amends items 405 and 409.** No database, ERD, URL or permission change, no migration.
     *(2026-11-24, item 431: the "Create as" choice is REMOVED; the documents decide the status (both = Scheduled, else
     Draft, never refused) and a Draft becomes Scheduled by itself when the last of the two is uploaded. The optional
     documents, the title rule, the kept values, the Draft reminders, Needs attention and the dashboard below are
     unchanged.)*
     - **The choice:** the New activity form has a required "Create as" field (radio: Draft / Scheduled), **default Draft**,
       in its own "Status" card after Documents (`activities.forms.ActivityPageForm`, New form only; help texts
       `CREATE_AS_HELP`; display `activities.pages.create_as_section`, `activities/_create_as.html`). One primary button
       ("Create activity"), spacing scale of item 413.
     - **Documents optional:** the two fixed rows stay (Activity Design, Request Letter; label "Needed for Scheduled", no
       star, no `required`). An untouched row is not sent (`NewDocumentRow.is_filled_in`); a title without a file gets
       "Choose a file to upload."; a file without a title is still refused (item 410, `creation_title_error`).
     - **The rule (service):** `documents.services.create_activity_with_documents(..., status=Draft)` accepts only Draft or
       Scheduled (`CREATION_STATUSES`, else ValidationError). Every activity it creates has `creation_documents_required =
       True`, so item 405's rules still apply (Draft -> Scheduled later needs both UPLOADED; never overdue once uploaded;
       the only one is never deleted outside Draft) and item 409's ready mark still decides registration ("Open
       registration"). Scheduled goes through `change_status` in the SAME transaction (the one transition rule:
       `_move_status` -> `scheduling_refused_reason`); a refusal undoes everything (no activity, document, stored file or
       notification) and comes back as `NewActivityError.status_error`, worded for the form by
       `scheduling_refused_reason(activity, on_new_form=True)`: "To create it as Scheduled, choose the Request Letter above,
       or create it as Draft." (shown under the field). `NewActivityError.missing_types` is gone.
     - **Order of errors (owner: accepted):** the Scheduled check needs the saved documents, so it runs only once the
       activity fields and every row are valid; with other errors the refusal appears on the next submit.
     - **Kept after an error:** every typed value, the chosen "Create as", the titles; browsers clear file inputs, so the
       amber note lists each file to choose again ("Activity Design (design.pdf)") and each file field says "Choose
       “design.pdf” again." (`new_documents_context.choose_again`, `NewDocumentRow.chosen_name`; names are only shown).
     - **After saving:** Scheduled -> the Forms tab and the message of item 409; Draft -> the Overview and "… was created as
       Draft. Upload the Request Letter on the Documents tab, then change the status to Scheduled." (from
       `missing_creation_types`) or "Change the status to Scheduled when it is ready." The JSON API create, the admin and
       `seed_demo` are unchanged (Draft).
     - **Reminders for Drafts** (NAVIGATU: staff are reminded while the letters are missing; owner decision: Director copy
       for Drafts only): `send_document_reminders` now also looks at Drafts created with the rule
       (`creation_documents_required`), but ONLY for the Activity Design and the Request Letter (`is_creation_type`): the
       existing rule and timing (due 5 days before the start, item 416; "due soon" within `DOCUMENT_REMINDER_DAYS_BEFORE`
       days and not uploaded; "overdue" after the due date with nothing uploaded), to the creator AND every active Director
       (`notifications.services.active_directors`, the same group as the new-activity notification; `Role.DIRECTOR` is who
       is told, never a permission). A Director who created it gets one copy. A deactivated creator is skipped and logged;
       the Directors still get theirs. One per (member, activity, type, kind) as before (unique constraint), so scheduling
       later never repeats one. Unchanged: other types on Drafts (no reminders), older Drafts (admin / demo, flag False: no
       reminders), and Scheduled-and-later reminders (creator only). In-app only, no email.
     - **Needs attention:** the activity page box and the Activities list count already used the reminders' rule for every
       status (`activities.attention`), so a Draft shows "Required documents due soon and not uploaded: …" / "Overdue
       required documents: …" without a code change (now tested).
     - **Dashboard unchanged (owner decision):** its overdue number (S3, `metrics.documents_now`) and its Needs attention
       rows (`metrics.document_attention`) stay Scheduled and later, like the compliance report; only reminders and the
       activity's Needs attention include Drafts.
     - Tests: `activities/tests/test_create_with_documents.py` (Draft default, Scheduled refused / allowed, nothing saved on a
       refusal, invalid status, title rule kept, choice and typed values kept, the re-choose notes, one primary button),
       `activities/tests/test_created_as_scheduled.py` (Scheduled via the choice), `documents/tests/test_draft_reminders.py`
       (the `clock` fixture: due soon, overdue, once only, only the two types, uploaded stops it, Director creator, inactive
       creator, older Drafts, Scheduled creator-only, Needs attention, dashboard unchanged).

## Only the Director manages certificate signatories (2026-11-21)

418. **New role flag `can_manage_signatories`: only the TBI Director adds and manages certificate signatories** (NAVIGATU,
     UAT; owner decisions 2026-11-21). Changes the "who manages the list and the signature images" part of items 357, 361,
     363 and 388; certificate approval (`can_approve_certificates`) and choosing an activity's signatories are unchanged.
     *(2026-11-24, item 431: choosing an activity's signatories and titles is now `can_approve_certificates` only; a member who manages signatories but cannot approve certificates may add a person from the tab, but not put them on the activity.)*
     *(Open question, CLAUDE.md: who stands in for the Director, items 361 and 363.)*
     *(2026-11-21, item 421: certificate approval (`can_approve_certificates`) is now the Director's only by default.)*
     - **Database:** one new column `ROLE.can_manage_signatories` (boolean, NOT NULL, default False / `db_default` False),
       migration `accounts/0009_role_can_manage_signatories`; data migration `accounts/0010_signatory_managers_data` sets
       it True for the role named exactly "Director" (looked up by name only there, like 0004–0008), skips that role if it
       manages users (with a message), does nothing without the role; reverse = False everywhere. Still 34 tables.
       Back up the development database and the UAT database (`csu_tbi_uat`) before `migrate` / `uat up -d --build`
       (DEPLOY.md "Laptop review version", Back up); only the `role` table changes, the media files do not.
     - **Defaults:** `accounts/seed.py` gives it to the Director only, **only when it creates a role** (never re-applied,
       like items 344, 357, 388, 389). `Permission.MANAGE_SIGNATORIES` ("manage certificate signatories").
     - **What needs it (services first, so the pages, the JSON API and the commands all follow):** `add_signatory`,
       `edit_signatory`, `replace_signature`, `remove_signature_background` (new versions, item 367),
       `deactivate_signatory`, `set_default_signatories`, `signature_preview` (the upload dialogs) and `upload_signature`
       (the Certificates tab). Views: the Signatories page `/certificates/signatories/` and all its actions, the tab's
       signature upload, "Add a person" on the tab and on the activity form (`signatory_add_inline`), the JSON API
       `/certificates/api/admin/signatories/` (+ deactivate). Everyone else: 403. Display: "Add signature" / "Replace
       signature", "Add a person", "All signatories" and the Certificates page's Signatories link only with the flag;
       others see "Only staff who manage signatories can add or replace signature images." under a missing signature.
     - **Unchanged:** choosing an activity's left / right signatory and its "Title on this certificate"
       (`can_choose_signatories`: can_create_activity or can_approve_certificates); approving, reissuing, the certificate
       text and Retry (`can_approve_certificates`); the approval refusal "Add the signature of … first." (now only a
       signatory manager can fix it). Viewing signature images (`certificates.views.signature_image`, thumbnails):
       can_manage_users, can_approve_certificates (approvers check them before approving) or can_manage_signatories.
     - **Separation of duties (owner decision):** `check_role_flags` refuses `can_manage_signatories` together with
       `can_manage_users` ("A role cannot both manage users and manage certificate signatories …"): whoever manages
       accounts and role flags never also decides whose signature is printed (they could otherwise switch it on for their
       own role). So the System Administrator no longer adds signatures (it did through `can_approve_certificates`, item
       361); it still approves certificates.
     - **Admin Console → Roles:** column "Manage signatories" with Turn on / Turn off (`set_role_signatory_management`,
       `POST /accounts/console/roles/<id>/signatory-management/`, can_manage_users, POST only, enabled=true|false), toast
       "<role> can now manage certificate signatories" / "… can no longer …", the `accounts.audit` line "Role flag
       changed: role=… can_manage_signatories False -> True by …", and the amber note "No active member can manage
       certificate signatories." (`active_signatory_managers`; no last-member guard). Also the Django admin role form and
       `/accounts/api/roles/` (which now also lists can_delete_activities).
     - **Demo commands:** `seed_demo` and `certificate_walkthrough design` add the demo signatories and defaults as the
       first active member with the flag (the demo Director).
     - **ERD:** ROLE gets `boolean can_manage_signatories` — "true only for Director (by default): add, edit, deactivate
       certificate signatories, their signature images and the default signatories; not together with can_manage_users".
     - Tests: `accounts/tests/test_manage_signatories_flag.py` (migration step incl. the guards, seed create-only, the
       switch with toast / audit / warning, refused with can_manage_users, who may switch, the Django admin, the roles API),
       `certificates/tests/test_signatory_management_permission.py` (every service and URL: 403 for both Project Heads,
       Admin Staff, the System Administrator and View Only; the Director allowed; what the tab, the activity form and the
       Certificates page show; approvers still see the images; choosing signatories and approving unchanged).

## Evaluation links before the end (2026-11-21)

419. **The first evaluation links go out automatically from 3 hours before the end** (NAVIGATU, UAT: participants should
     get the link while the event is closing; owner decisions 2026-11-21). **Amends item 348.** No database, ERD, URL or
     permission change, no migration.
     - **Setting:** `EVALUATION_LINK_HOURS_BEFORE_END` (default 3, `.env` may override it).
     - **The rule:** `registrations.services.automatic_link_activities(now)` now takes Conducted activities (as before) AND
       Ongoing activities once `now >= automatic_link_moment(activity)` = the end minus that many hours; both still need an
       evaluation form and an open evaluation window (`evaluation_links_closed_reason`: opens 00:00 Manila on the end date,
       closes at the deadline). So a several-day activity, or one ending shortly after midnight, gets nothing before 00:00
       on its end date even when end - 3 h is earlier.
     - **Activities without an end time** (old rows only) count as ending at **23:59 of the end date** (links from 20:59).
       This is deliberately different from `activities.services.activity_start_end()`, which uses 00:00 of the next day for
       the automatic status moves (item 346); that function is unchanged, so an activity without times still becomes
       Conducted after midnight. Only `automatic_link_moment` uses 23:59.
     - **Unchanged:** the same `deliver_evaluation_link(first_time_only=True)`, once per attendee who has not evaluated and
       has neither sent nor failed set (SKIP LOCKED), no automatic retry; people marked as attended later (still Ongoing,
       or after Conducted) get theirs on the next 5-minute run; page loads never send emails; sending never changes the
       status (Ongoing -> Conducted stays item 346's rule); staff "Resend to all who haven't answered" / "Resend link".
     - **"Sent automatically" without a new column (owner decision):** REGISTRATION cannot tell an automatic send from a
       staff resend (both set `evaluation_link_sent_at`). Each run that sent at least one link for an activity writes ONE
       History row by System: "Evaluation Links": "Sent automatically to N attendees"
       (`activities.services.write_evaluation_link_history`, label `EVALUATION_LINKS_LABEL`; failed sends are not counted).
       `registrations.services.automatic_link_sends(activity)` reads only those rows (System, that label, that wording).
     - **Evaluation tab:** a green note at the top of the "Evaluation links" card (`registrations.pages.automatic_send_notice`):
       "Evaluation links were sent automatically on Nov 21, 2026 2:00 PM to 12 attendees." + for later runs "Later, 2 more
       attendees got theirs automatically (latest …)." + "You don't need to send them. Use “Resend link” only for someone
       who didn't get theirs." The explanation line now says from when the links go out (the moment, "3 hours before the
       end, once the evaluation is open") or after Conducted. The counts, the Failed badges and the Resend buttons are
       unchanged.
     - Tests: `registrations/tests/test_evaluation_links_before_end.py` (the `clock` fixture: before the window, one-day at
       end - 3 h, whole-day 23:59 rule vs `activity_start_end`, several days waiting for 00:00, last day, the setting, never
       twice / no retry / staff resend respected, later attendees while Ongoing and after Conducted, no form, the task,
       page loads never send, the tab's note and buttons, no note for staff sends);
       `test_evaluation_links_auto.py` now checks Ongoing before end - 3 h.

## Prepare and approve certificates before the activity is Conducted (2026-11-21)

420. **"Prepare the certificates now (optional)" and early approval** (NAVIGATU UAT, Administrative Staff: "Certificates
     can be generated while creating the activity, and then the Director receives this to review and approve it.
     Generating it while creating the activity is optional; if they forget, after the activity is Conducted the system
     generates them automatically."; owner decisions 2026-11-21). **Amends items 365, 369 and 373.** Certificates are
     still CREATED only by the System at Conducted (item 362): "prepare" means choosing what they will print and asking
     for the approval early; nothing is generated before Conducted. *(Open question, CLAUDE.md.)*
     *(2026-11-24, item 431: anyone who creates activities still prepares the certificates, writes the text and sees the sample; only `can_approve_certificates` chooses the signatories and titles inside "Prepare the certificates now" (others get the defaults, shown read only; the sample shows the defaults).)*
     *(2026-11-21, item 421: early approval is the Director's only by default; "ready for your review" now goes to can_approve_certificates alone.)*
     - **Database:** two nullable columns on ACTIVITY, `certificates_prepared_by` (FK to TBI_MEMBER, PROTECT) and
       `certificates_prepared_at` (datetime), migration `activities/0010_activity_certificates_prepared` (no data step:
       existing activities are "not prepared" = the flow before this item). Set only by
       `certificates.services.prepare_activity_certificates`; never by `update_activity`, the form or the admin. Still
       34 tables. Back up the development database (and `csu_tbi_uat` if it is ever started again) before `migrate`.
     - **New activity form:** "Who signs the certificates" is replaced by the card "Prepare the certificates now
       (optional)" (`activities/_prepare_certificates.html`, `activities.pages.prepare_section`) with an unticked
       checkbox (`prepare_certificates`, New form only). **Not ticked:** the activity gets the default signatories and
       the default text, exactly as before; the posted signatory / title / text fields are ignored (the form does not
       even validate them, `signatories_used`; `save_new_activity(prepare_certificates=False)` drops them too).
       **Ticked:** the left / right signatories and optional titles (the item-365 fields, "Add a person…" still only for
       `can_manage_signatories`, item 418), "Use the default text" / "Custom text for this activity" (the item-370 fields
       with the live wording preview, `certificates:new_activity_text_preview`), and "Show the preview". Saved in the
       same transaction as the activity (`create_activity_with_documents(..., prepare_certificates=, certificate_texts=)`
       → `assign_default_signatories`, `save_activity_text`, `prepare_activity_certificates`); a refused wording is shown
       under its field ("Please correct the certificate text marked below."), nothing is saved and the box stays
       ticked. The Edit form keeps "Who signs the certificates" unchanged (owner decision).
     - **Sample preview (nothing stored):** the same certificate template as the final PDF with the active design, the
       signatories, titles and wording, the sample name "Juan Dela Cruz" (Appreciation: the activity's first resource
       person and topic, else "Dr. Maria Santos"), the corner mark PREVIEW and the number SAMPLE.
       New form: `certificates:draft_sample_preview` (GET, can_create_activity; the form's script sends only the typed
       title, dates, venue, signatory ids, titles and wording, never the CSRF token or files;
       `services.draft_sample_preview_pdf`). Tab: `activities:certificates_sample_preview`
       (`services.activity_sample_preview_pdf`, any active staff member like item 358's preview). Both are
       `xframe_options_sameorigin` (the 3rd and 4th framed views) and `private, no-store`. When it cannot be shown
       (no active design, a signatory missing, refused wording) the frame says why.
     - **Preparing** (`prepare_activity_certificates`, can_create_activity or can_approve_certificates; the New form or
       the tab's "Prepare the certificates now" + confirm dialog, `activities:certificates_prepare`): stores who / when,
       one History row "Certificates": "Prepared for review by <name>", and tells the reviewers ONCE (refused when
       already prepared or Conducted, so it cannot repeat; later signatory or text changes tell nobody).
     - **Who is told (owner decision):** `notifications.services.certificate_reviewers()` = active members whose role has
       **can_manage_signatories AND can_approve_certificates** (the Director by default; flags, never the role name;
       both, so nobody gets a review they cannot act on), except the member who prepared them. In-app only
       (NOTIFICATION type `certificates_prepared`, "Certificates of <activity> are ready for your review", opens the
       Certificates tab: "Open certificates"). Nobody qualifies = nobody is told (the tab still shows the state; the
       roles screen already warns when nobody manages signatories).
     - **Early approval:** `approval_closed_reason` before Conducted: not prepared → "Prepare the certificates first, or
       wait until the activity is Conducted."; prepared → the same checks as after Conducted (active design, both slots,
       both signature images). Allowed on Draft, Scheduled and Ongoing (owner decision). `approve_activity_certificates`
       is unchanged (History "Prepared — needs approval" → "Approved by <name> (before Conducted)"). At Conducted,
       `generate_missing_certificates` already calls `approve_ready_certificates`, so the certificates are created in an
       approved activity: a resource person's is approved at once (ready to print), a participant who already evaluated
       (links from 3 h before the end, item 419) is approved, rendered and emailed at creation, the others when they
       evaluate (the item-369 behaviour). Their status stays "Waiting for approval" until each is ready, as before.
     - **Cancelled before Conducted (owner decisions):** everything of item 369 (signatory, title, new version of a
       person, the activity's text, the default text: `save_default_text` now also cancels early-approved activities
       that use the default, `default_text_impact()["early"]` in the dialog), PLUS: an `update_activity` change to the
       title, start date, end date or venue (`PRINTED_FIELDS`: printed through {activity}, {date}, {given_date},
       {venue}; "activity details changed"), adding or removing a resource person ("resource persons changed"), and
       activating or deactivating a certificate design in the Admin Console (`cancel_early_approvals`: every approved
       activity without certificates; "certificate design changed"; activities with certificates keep their design as
       before). A change of another field (slots, deadlines, budget…) cancels nothing. The activity stays prepared.
     - **States (`approval_state`, amends item 373):** only while the activity is not Conducted and has no certificates:
       `approved` + note "Certificates will be created when the activity is Conducted.", `prepared` ("Prepared — needs
       approval", orange, the approve button for approvers + "Prepared by … on …") or `not_prepared` ("Not prepared —
       certificates will be created when the activity is Conducted", grey, no approve button; "Prepare the certificates
       now" as a secondary button for can_create_activity / can_approve_certificates). After Conducted the five states of
       item 373 are unchanged. Counts are unchanged: `needs_approval_q` counts waiting certificates only, so a prepared
       activity is in no count (S5, Needs attention, dashboard); the notification and the tab are the signals.
     - **Tab:** before Conducted the "Certificate preview" card shows the sample (Participation / Appreciation) instead of
       the certificates' own preview (`pages.sample_preview`). Item 413 kept: one primary per card (Approve), the prepare
       button is secondary, spacing scale.
     - *(2026-11-22, wording fix: the Certificates card below follows the approval card before Conducted (`certificates.pages.list_card`, from `approval_state`): prepared → "Certificates are prepared (see the sample above). …", prepared and approved → "Certificates are prepared and approved. …", each with its own description line; not prepared and every state after Conducted keep the old text. Text only.)*
     - Tests: `certificates/tests/test_prepare_early.py` (closed by default, defaults when not chosen, invalid hidden
       fields ignored, chosen with signatories / titles / custom or default text, refused text, notification once and
       only to reviewers, the previews with the sample name and their reasons, approval before Conducted needs
       preparing and both signatures, created approved and emailed after each evaluation, not prepared = today's flow,
       prepared but not approved waits, every cancel rule, unprinted fields keep it, the default-text dialog, the tab's
       states and buttons, permissions, "Add a person" still item 418's).

## Only the Director approves certificates (2026-11-21)

421. **`can_approve_certificates` is the Director's only, by default** (NAVIGATU UAT: "the Director's account is more on
     approval", "the Director reviews and approves the certificates"; owner decisions 2026-11-21). **Amends items 357,
     361, 388, 418 and 420.** No database, ERD column or URL change, no migration. *(Open question,
     CLAUDE.md.)*
     *(2026-11-24, item 431: "Unchanged for staff who create activities: choosing an activity's signatories and titles, Use the current version" no longer holds: both are now the Director's (`can_approve_certificates`). Custom text, preparing, Resend, Mark handed over, Apply the current design and the previews stay.)*
     - **Seed defaults** (`accounts/seed.py`, still create-only: a rerun never overwrites an Admin Console choice):
       can_approve_certificates True for the Director only; False for Incubation Project Head, Community Development
       Head, Admin Staff and System Administrator (all four were True, items 361, 388).
     - **No data migration (owner decision):** existing databases keep their roles as they are; the owner switches the
       flag off for the four roles in Admin Console → Roles (`set_role_certificate_approval`, one `accounts.audit` line
       each). A migration would overwrite an administrator's choice. A new database gets the new defaults from
       `seed_data` (the old data step in accounts/0006 finds no roles on an empty table).
     - **Director only (can_approve_certificates, unchanged in code):** "Approve certificates for this activity" (also
       before Conducted, item 420; the JSON API's `approve`), reissue ("Reissue all outdated certificates", "Reissue with
       current signatures", the API's `reissue`), the default certificate text page `/certificates/text/` (+ its preview
       and the tab's "Default text" link). Everyone else: 403 and no button.
     - **Moved to can_create_activity OR can_approve_certificates:** "Retry" on a failed PDF (`retry_certificate_pdfs`,
       `RETRY_PERMISSIONS`, the row action and its button: `can_retry_pdfs`); viewing signature images and the tab's
       thumbnails (`certificates.views.SIGNATURE_IMAGE_PERMISSIONS` = can_manage_users, can_approve_certificates,
       can_manage_signatories or can_create_activity: they choose the signatories, and every certificate preview already
       shows the signatures to all staff).
     - **Unchanged for staff who create activities:** choosing an activity's signatories and titles, "Use the current
       version", custom text for one activity, "Prepare the certificates now" (item 420), Resend, Mark handed over, Apply
       the current design, the sample and certificate previews.
     - **What the other roles lose:** the Project Heads and Admin Staff: approving (also early), reissuing and the default
       text. The System Administrator: those plus Retry (it has neither flag); it keeps seeing signature images through
       can_manage_users. The flag is still ALLOWED with can_manage_users (`check_role_flags`, item 361) if an
       administrator switches it on.
     - **Counts unchanged:** S5, the dashboard card and Needs attention ("Certificates need approval.") count the same
       activities for everyone; only the wording follows the flag ("need your approval" / "Approve" for approvers,
       "need approval" / "Open" for others).
     - **Notifications (in-app, to `notifications.services.certificate_reviewers()` = active members with
       can_approve_certificates; flags, never the role name):** item 420's "Certificates of <activity> are ready for your
       review" now goes to can_approve_certificates alone (was AND can_manage_signatories; the Director either way by
       default), never the preparer. NEW: "Certificates of <activity> are waiting for your approval" (type
       `certificates_waiting`, `notify_certificates_waiting`), ONCE per activity, when the System FIRST creates
       certificates for an activity that is not approved (`generate_missing_certificates`; not after an early approval,
       never again for certificates created later). Both open the Certificates tab ("Open certificates").
     - **Unchanged:** the roles screen's amber note "No active member can approve certificates." (`active_certificate_approvers`).
     - **ERD:** only the note of ROLE.can_approve_certificates changes: "true only for Director (by default): approve
       certificates (also before Conducted), reissue, the default certificate text".
     - Tests: `certificates/tests/test_director_only_approval.py` (seed defaults and create-only, the Director approves,
       403 for both Project Heads, Admin Staff and the System Administrator on approve / reissue / the default text,
       creators keep signatories, custom text, prepare, Retry and thumbnails, the System Administrator cannot Retry,
       both notifications and their targets, once per activity, none after an early approval, the amber note).

## NAVIGATU UAT: typed dates, optional evaluation deadline, participant mix, payment documents (2026-11-22)

422. **Typing dates and times on the activity form** (NAVIGATU UAT, the TBI Director; New and Edit). Form and template
     only: no service, database, ERD or API change (the JSON API keeps ISO dates and "08:00" times).
     - **Dates** (`activities/typed_fields.py`, `TypedDateField`): one text box. The server accepts "June 5, 2026",
       "Jun 5 2026", "5 June 2026", "2026-06-05" and "06/05/2026" (month/day/year, as in the Philippines), month names in
       any case; anything else gets "Enter a valid date, for example June 5, 2026 or 06/05/2026.". A stored date is shown
       as "June 5, 2026". On the page (`components/typed_datetime.html` + `static/js/typed_dates.js`, loaded before
       Alpine) the box is an ARIA combobox: typing letters suggests months ("j" -> January, June, July; "ju" -> June,
       July), then the day gives "June 5, <this year>" / "<next year>"; arrows + Enter choose, Escape closes; leaving the
       box writes a readable date the same way. The calendar button opens the browser's own date picker (`showPicker()`)
       and fills the box. Without JavaScript the box still works.
     - **Times** (`TypedTimeField`): an hh:mm box (`inputmode="numeric"`, only digits can be typed, the colon is written:
       730 -> 07:30; 1430 -> 02:30 + PM; 0015 -> 12:15 + AM) and an AM/PM select (`<name>_period`). Server rule
       (`parse_typed_time`): 1–12 o'clock needs AM or PM ("Choose AM or PM."); 00:xx and 13:xx–23:xx are 24-hour times
       (an AM/PM that contradicts them is refused, naming the 12-hour form to type instead); minutes 00–59; anything else
       "Enter a valid time, for example 07:30 AM.".
     - **Deadlines** (`TypedDateTimeField`): date box (`<name>`) + time box (`<name>_time`) + AM/PM (`<name>_period`),
       combined into a Manila date-time; one box without the other is refused. The evaluation deadline may be left empty
       (item 423).
     - The New form's certificate sample preview (item 420) reads the dates with the same parser.
     - Tests: `activities/tests/test_typed_dates.py`; page-form tests send `conftest.typed_schedule_fields(values)`.
423. **The evaluation deadline is optional** (NAVIGATU UAT). Empty = the evaluation stays open.
     - **Database:** `ACTIVITY.evaluation_deadline` becomes nullable (migration `activities/0011`, schema only). **Existing
       activities keep their deadline** (owner's choice; no data change). Reversing the migration needs a deadline on
       every row again.
     - **One rule:** `registrations.services.evaluation_window()` returns `closes = None` without a deadline;
       `evaluation_has_closed(activity, now)` (False without a deadline) and its query form `evaluation_open_q(now)` are
       used by every "closed" check: personal links (`evaluation_state`, `shows_person`), the general link, the
       automatic links (`automatic_link_activities`: item 419's links 3 hours before the end are unchanged), Needs
       attention (the late-attendance item never appears without a deadline; "Evaluation form not built yet" and the
       evaluation-link items stay while the evaluation is open) and certificates (`readiness`, `pdf_state`).
     - **"No evaluation = no certificate" is unchanged:** without a deadline a participant's certificate shows "Waiting
       for evaluation" until they evaluate (never "Evaluation closed without an answer").
     - The service still refuses a given deadline before the end date. The Overview shows "No deadline (the evaluation
       stays open)"; the Evaluation tab says "… and stays open (no evaluation deadline)".
     - **ERD:** ACTIVITY.evaluation_deadline "nullable – empty = the evaluation stays open". Still 34 tables.
     - Tests: `registrations/tests/test_optional_evaluation_deadline.py`.
424. **Participant mix and NAVIGATU's guideline** (NAVIGATU UAT; owner decisions 2026-11-22). No database or ERD change.
     - **Guideline** (settings): Faculty at most 30 % (`PARTICIPANT_MIX_FACULTY_MAX_PERCENT`), Incubatees at least 70 %
       (`PARTICIPANT_MIX_INCUBATEES_MIN_PERCENT`). **MSMEs count with the Incubatees** for the 70 %
       (`PARTICIPANT_MIX_INCUBATEES`) until NAVIGATU answers (open question, CLAUDE.md). Categories are matched by the seeded names
       (they cannot be renamed); any added category is "Other categories" (shown, counted in the total, in neither group).
     - **The numbers and the check** live only in `reports/metrics.py`: `participant_mix(activity_ids)` (two queries,
       registered and attended per group, whole percentages with `shares(units=100)`), `mix_check` (integers, never
       rounded first: exactly 30 % / 70 % is inside), `period_participant_mix(filters)` (the dashboard: the period's
       Conducted activities). The check uses **attendees once anybody is marked as attended, else registrations** (like
       the donut, item 403).
     - **Which activities are checked:** only those open to Faculty AND to Incubatees or MSMEs (a Faculty-only training is
       never "outside").
     - **Overview:** under the "Participants by category" donut, "Participant mix": two 100 % bars (Registered, Attended),
       a text table (count and %), the guideline in words, and "Within the guideline" (green) / "Outside the guideline
       (…): Faculty 45% (at most 30%) …. Registration stays open." (amber), or "Not checked: …".
       `reports.dashboard_page.mix_chart` + `components/participant_mix.html` (display only).
     - **Dashboard:** the same block in the "Attendees by category" card for the selected period (all registrations of the
       period's Conducted activities together; the guideline is shown next to it, not checked on the total), plus "N of M
       checked activities are outside the guideline".
     - **Needs attention:** "Participant mix is outside NAVIGATU's guideline: …." (Overview), a warning while Scheduled /
       Ongoing, informational (shown, never counted) once Conducted. **Registration is never blocked** (no rule in
       `registrations.services`).
     - Tests: `reports/tests/test_participant_mix.py`.
425. **Payment documents for the Supply Office** (NAVIGATU UAT; owner decisions 2026-11-22). No schema or ERD column
     change; data migration `documents/0006`.
     - **Types:** "Activity Report" and "Event Photos" (new, required, due end date + 5) and the "Attendance Sheet"
       (already required; its due rule moves from end date + 0 to + 5). `settings.DOCUMENT_TYPES_FOR_PAYMENT` names the
       three. Migration `documents/0006_payment_documents` adds the two rows only in a database that already has
       document types and only when the name is missing; it moves the Attendance Sheet only while it still has
       end_date / 0 (a value changed in the Django admin is never touched; the reverse is guarded the same way and
       removes the two rows only while unused). `seed_data` creates them (create-only, item 386).
     - **Only newer activities:** the two NEW types apply only to activities **ending on or after
       `PAYMENT_DOCUMENTS_FROM` (2026-11-22)** (`DOCUMENT_TYPES_FROM_PAYMENT_DATE`, `documents.services.type_applies`, SQL
       `reports.metrics._applies`, parity tested), so older activities never show them missing or overdue; an older
       activity lists such an upload under "Other documents", and the compliance report prints "Not required". The
       Attendance Sheet stays required for every activity.
     - **Only after Conducted:** a payment document is never overdue, never "due soon" and gets no reminder before the
       activity is Conducted (`counts_now`, `ChecklistItem.counts_now`; SQL `_counts_now`); after Conducted it counts like
       any required document. Needs attention gives them their own line: "Overdue payment documents for the Supply
       Office: …" / "… due soon and not uploaded: …".
     - **Event Photos** take JPG or PNG only (`DOCUMENT_TYPES_IMAGES_ONLY`, `file_type_error`, also on Replace); several
       files may be uploaded, one at a time ("Add a photo" stays on the row). Complete = at least one Approved photo
       (the checklist's usual rule).
     - **Documents tab:** a "For payment (Supply Office)" card with the three types (the same row template as the required
       ones, `documents/_checklist_item.html`) and "Download payment packet" (secondary).
     - **Payment packet** (`documents.services.build_payment_packet`, `activities:payment_packet`, any active staff
       member like a download, `private, no-store`): a zip made on request (never stored) with **only APPROVED files**,
       one folder per type, numbered stored file names (never the uploaded name), and `contents.txt` (Windows line
       endings) listing per type what is included, what is waiting for approval or returned (not included) and what is
       MISSING. Refused (the reason replaces the button): before Conducted, or when no payment document is approved yet.
     - **Activity Report vs Accomplishment Report:** kept SEPARATE. The Accomplishment Report stays optional (item 386)
       and has been the TBI's own report; the Activity Report is the one the Supply Office asks for before paying. They
       may be the same paper: answered by item 430 (they are different: the Accomplishment Report is made in Reports).
     - **ERD:** no column change (new DOCUMENT_TYPE rows only); the DOCUMENT_TYPE.name note gets "activity report / event
       photos". Still 34 tables.
     - Tests: `documents/tests/test_payment_documents.py`.

## NAVIGATU UAT round 2, part 2: registration fields, closing registration, objectives, position, Accomplishment Report (2026-11-24)

Owner decisions of 2026-11-23/24. Database: activities 0012 (schema), registrations 0009 (schema) + 0010 (data),
documents 0007 (data). ERD: ACTIVITY.objectives, ACTIVITY.registration_closed_by_hand, REGISTRATION.designation,
REGISTRATION.office_agency (+ notes on TBI_MEMBER.position_id, POSITION.name and DOCUMENT_TYPE.name). Still 34 tables.

426. **Designation / Position and Office / Agency are fixed fields of every registration** (NAVIGATU UAT).
     - **Database:** `REGISTRATION.designation` and `REGISTRATION.office_agency` (text, max 255, empty allowed in the
       database for older rows; migration `registrations/0009`).
     - **One rule:** `registrations.services.register()` requires both (trimmed, inner spaces collapsed, at most 255;
       "This field is required."), for every entry point: the public page and `/api/register/`, the startup portal page
       and API, and any later one (walk-in registration). The labels are reserved: a form field cannot be named
       "Designation / Position", "Office / Agency", "Designation", "Position", "Office" or "Agency".
     - **Startup portal:** Office / Agency is pre-filled with the startup's name (editable); the startup types the
       designation. The portal API uses the startup's name when `office_agency` is not sent; an empty value sent on
       purpose is refused like on the public page (`startups.services.register_as_startup`).
     - **Existing data (migration `registrations/0010`, owner's choice):** every registration's "Affiliation /
       Organization" answer is copied into `office_agency` (only where it is still empty); `designation` stays empty; the
       old FORM_ANSWER rows are kept. The DEFAULT_FORM_FIELD rows named exactly "Affiliation / Organization" are removed
       (a renamed one is left alone), and that field is removed from registration forms nobody has submitted yet (the
       `form_is_locked` test); forms with registrations keep it. `seed_data` no longer creates it. Reverse: the default
       field is put back for every activity type; fields removed from unused forms are not.
     - **Shown on:** the Registrations tab (two columns), the registration detail, the printable attendance sheet (still
       never an email or contact number), the form preview, the staff registrations API and the portal registration
       JSON. The staff search also matches Office / Agency.
     - Tests: `registrations/tests/test_fixed_registration_fields.py`. The test `defaults` fixture adds one test-only
       custom registration field (the old label) so the form-editing tests have a field to work on.
427. **Close and reopen registration by hand** (NAVIGATU UAT).
     - **Database:** `ACTIVITY.registration_closed_by_hand` (boolean, default false; migration `activities/0012`).
     - **Service:** `activities.services.set_registration_closed_by_hand(member, activity, closed)`: can_create_activity,
       only while the activity is Scheduled, refused when nothing changes; one History row "Registration"
       ("" -> "Closed by the TBI", "Closed by the TBI" -> "Reopened by the TBI"). Never through `update_activity`, the
       form or the Django admin.
     - **One rule:** `registrations.services.registration_closed_reason` gives "Registration was closed by the TBI."
       (`REGISTRATION_CLOSED_BY_TBI`) right after the status check; `activities.services.is_registration_open` and the
       `open_for_registration()` query check the same flag (so the Activities card, the dashboard KPI, `/register/` and
       the portal agree). The automatic rules stay on top: a reopened registration still closes at the deadline, when the
       slots run out, outside Scheduled and (item 409) until the registration form is ready.
     - The flag is kept when the activity goes back to Draft and forward again (the Share card then offers "Reopen
       registration").
     - **Share card** (Overview): "Close registration" / "Reopen registration" (secondary, only for can_create_activity,
       `services.registration_switch`, `activities:registration_switch`). Needs attention: nothing new.
     - Tests: `registrations/tests/test_registration_closed_by_hand.py`.
428. **Activity objectives** (NAVIGATU UAT; for the reports, Prompts 6–7).
     - **Database:** `ACTIVITY.objectives` (text, nullable; migration `activities/0012`).
     - Optional field on the activity form (New and Edit), the Django admin and the JSON API, "one objective per line";
       `activities.services.clean_objectives` trims each line, drops empty lines, nothing left = empty; at most 5000
       characters. Changed through `create_activity` / `update_activity` like every field, so History logs "Objectives".
     - Shown on the Overview (and the read-only page of a Conducted activity) as a list under the description
       (`services.objective_lines`); the API detail gives the list.
     - Tests: `activities/tests/test_objectives.py`.
429. **The member's position title printed under "Prepared by"** (NAVIGATU UAT; owner decision 2026-11-24: NO new
     column). The existing POSITION list (item 390) is used. Checked before deciding: it is an Admin-managed list (name up
     to 100 characters, unique ignoring case, deactivated, never deleted) and a member has ONE position
     (`TBI_MEMBER.position_id`), so it holds an individual official title. The System Administrator adds the official
     titles (for example "Admin Aide VI (Clerk III)", "University Research Associate I").
     - The member still chooses it on My profile; NEW: the System Administrator sets it on the Admin Console Members
       screen (`accounts.services.set_member_position`, same choices as My profile: active positions plus the member's
       own; "No position"; logged on `accounts.audit` with IDs only).
     - Still only a displayed title, never a permission. The reports and forms (later prompts) print
       `member.position.name` under "Prepared by".
     - Tests: `accounts/tests/test_member_position_admin.py`.
430. **The per-activity "Accomplishment Report" leaves the checklist** (NAVIGATU UAT; amends 386 and 425's "kept
     separate"). The Accomplishment Report is now the quarterly / semi-annual / annual report made in Reports (Prompt 7),
     NOT a DOCUMENT_TYPE; the per-activity report is the "Activity Report" (a payment document, item 425).
     - **Data migration `documents/0007`:** the DOCUMENT_TYPE "Accomplishment Report" becomes `is_active = False`, ONLY
       while it is still as 0004 left it (active, optional, no due rule); a row the Admin changed is never touched.
       Reverse: the same guard, back to active.
     - **Old uploads stay:** `document_checklist()` also lists an inactive type under "Other documents" when the activity
       has documents of it (never required, never counted); they stay downloadable. It is no longer offered for a new
       upload (inactive types never are).
     - **Nothing reactivates it:** `seed_data` no longer has the type (`accounts.seed.RETIRED_DOCUMENT_TYPES`; a new
       database never gets it, a rerun never touches the old row); `seed_demo` uploads Activity Reports instead. Only a
       person in the Django admin could switch it back on (DOCUMENT_TYPE stays editable there); the Reports' accomplishment
       reports never use it.
     - **Compliance report:** it already counted only required types (the type was optional since 386), so its numbers do
       not change; a test keeps it out.
     - The Activity Design / Request Letter rules (needed before Scheduled, due 5 days before the start) are unchanged.
     - Tests: `documents/tests/test_accomplishment_report_retired.py`.

## NAVIGATU owner decisions: who signs, and the documents decide the status (2026-11-24)

431. **Only the Director chooses who signs; the documents decide the status at creation** (NAVIGATU, owner decisions
     2026-11-24). **Amends items 363, 365, 409, 417, 418, 420 and 421.** No database, ERD column, URL or migration change.
     *(Open question, CLAUDE.md.)*
     - **Who signs (rule in `certificates.services`):** choosing an activity's left / right signatories and their "Title on
       this certificate" needs `can_approve_certificates` (`CHOOSE_SIGNATORIES_PERMISSION`, the flag of item 421: the
       Director by default; flags, never the role name). `require_signatory_choice()` refuses everyone else with
       PermissionDenied "Only the Director chooses who signs." (`ONLY_DIRECTOR_CHOOSES`) in `set_activity_signatory`,
       `set_activity_signatories` (the Edit form through `update_activity`), `use_current_version` (it changes a slot) and
       `activities.services.save_new_activity` when a choice (a person or a title, `has_signatory_choice`) comes with
       "Prepare the certificates now". The tab's views (`signatory_choose`, `signatory_current_version`) answer 403 the
       same way; any future API calls the same services. `signatory_add` from the tab still adds the person (item 418) and
       says "Not chosen for the activity: Only the Director chooses who signs." when the member cannot choose.
     - **Unchanged for staff who create activities:** "Prepare the certificates now" at creation and on the tab
       (`can_prepare_certificates` = can_create_activity or can_approve_certificates), the activity's certificate text
       (`can_edit_activity_text`, the same two flags), the sample and certificate previews, Retry, Resend, Mark handed over,
       Apply the current design. The Director still reviews and approves (item 421).
     - **Pages (display only):** without the flag the Certificates tab's Signatures card, the Edit form's "Who signs the
       certificates" and the New form's prepare card show each slot's person and printed title read only with "Only the
       Director chooses who signs." (`activities.pages.signatory_form_section` `editable`, `certificates.pages.signatures_panel`
       `only_director`); the form ignores anything posted for them (`signatory_choices()` = None). On the New form they get
       the default signatories, and the sample preview (`draft_sample_preview_pdf`) shows the defaults, never a posted
       choice.
     - **Fix found on the way:** the Edit form's "Title on this certificate" now starts with the saved title
       (`signatory_form_options` `current_titles`); before, saving the form untouched cleared a custom title and cancelled
       the approval.
     - **Text after approval (owner asked; already so, kept and tested):** `save_activity_text` and `use_default_text`
       call `cancel_certificate_approval`: an approved activity (also an early approval, item 420) goes back to "Needs
       approval" / "Prepared — needs approval", Approved certificates back to waiting, Issued ones untouched (item 369).
     - **Status at creation (replaces item 417's "Create as"):** `create_activity_with_documents` has no `status` any more
       (`CREATION_STATUSES`, `NewActivityError.status_error`, `scheduling_refused_reason(on_new_form=)`, the form field and
       `activities/_create_as.html` are gone). With BOTH the Activity Design and the Request Letter among the rows
       (`missing_creation_types` empty) it calls `change_status(..., "Scheduled")` in the same transaction (the one
       transition rule, its own savepoint; History: "Draft", then "Draft -> Scheduled" by the creator; the Forms tab and
       item 409's next step). If that move is refused for any other reason, the activity, its documents, files and
       notifications stay, it is a Draft, and the success message says "It could not be Scheduled: <reason>"
       (`activity.not_scheduled_reason`, not a column). With one or none: a Draft ("Upload the … on the Documents tab: it
       becomes Scheduled as soon as both documents are uploaded."). One primary button ("Create activity").
     - **Later upload:** `upload_document` (the Documents tab, the JSON API) calls `schedule_when_documents_complete` in its
       transaction for an Activity Design / Request Letter of a Draft with `creation_documents_required`: once both are
       uploaded (any review state) `activities.services.schedule_automatically` moves it through `_move_status` by System
       (member None) with History "Status: Draft -> Scheduled (both documents uploaded)". A refusal is logged and never
       refuses the upload. The creator gets the in-app NOTIFICATION `activity_scheduled`
       (`notifications.services.notify_creator_scheduled`) unless they uploaded it themselves (owner decision) or their
       account is inactive; no email. The tab's message and the API's `activity_scheduled` say so. Older Drafts (flag
       False: admin, demo, before 2026-11-19) are never moved. A Scheduled activity moved back to Draft by hand is
       scheduled again only by a NEW upload of one of the two types.
     - **Unchanged:** registration opens only once the registration form is marked ready (item 409); the Draft reminders,
       Needs attention, the dashboard and the JSON API create (Draft) of item 417; staff may still schedule by hand.
     - **Documents card help text:** "Upload the Activity Design and the Request Letter now: with both, the activity is
       Scheduled when you create it. Without them it stays Draft until both are uploaded."
     - **ERD:** no table or column change. Notes only (the owner edits the picture): ROLE.can_approve_certificates "…;
       chooses an activity's signatories and their titles", ACTIVITY.creation_documents_required "…; Scheduled
       automatically once both are uploaded", NOTIFICATION.type "includes activity_created, certificates_prepared,
       activity_scheduled". NOTIFICATION.type is a plain string (no choices), so the new value needs no migration.
     - Tests: `certificates/tests/test_director_chooses_signatories.py` (every service and both tab URLs refused for the
       three creator roles, the Director allowed, New / Edit forms read only and ignoring posted values, defaults when
       preparing, the sample preview, text editing kept and cancelling the approval, the Edit title fix),
       `activities/tests/test_auto_scheduled.py` (later upload by someone else / by the creator, other types, old Drafts,
       a refused move keeps the upload, the tab message, the API), `activities/tests/test_create_with_documents.py`
       (both = Scheduled, one or none = Draft with the uploads, a refused move keeps everything as Draft with the reason,
       no "Create as", the help text, one primary button).

## Testing fixes: typed times, and uploads are approved copies (2026-11-24)

432. **Time boxes typed freely; uploaded documents are approved copies** (owner, testing before the commit; NAVIGATU:
     "only approved paper documents are uploaded; the system only monitors what is missing"). **Amends items 344, 405,
     406, 422 and 431.** *(Open question, CLAUDE.md.)*
     - **Time boxes (422):** the cause was `static/js/typed_dates.js`: it rewrote the box on every key (from the 3rd
       digit it fixed the hour, so 1100 became 01:10 and 0140 became 00:14) and switched the AM/PM select by itself (to AM
       for an "00" hour, PM for 13-23), so a chosen PM could be replaced or a wrong time sent. The server parser
       (`parse_typed_time`) always read the select correctly (`<name>_period`; checked). Now: nothing is rewritten while
       typing except the colon after 4 digits in a row (1100 -> 11:00); Backspace / Delete never reformat; a colon, a/p/m
       and a space can be typed (maxlength 8); on leaving the box `tidyTime` writes hh:mm (9 -> 09:00, 130 -> 01:30,
       1:30 -> 01:30, 0015 -> 12:15 AM, 1430 -> 02:30 PM, "7:30 pm" -> 07:30 PM); the select changes ONLY for a typed a/p or
       a 24-hour time. `parse_typed_time` stays the one check (it reads 11:00, 1100, 1:30, 130, 9, 12:15 without any
       script). Tests: `activities/tests/test_typed_dates.py` (every way of typing with AM and PM, every one of the four
       boxes, New and Edit) and the opt-in `activities/tests/test_typed_time_browser.py` (typing, leaving, Backspace, a
       chosen PM kept, all four boxes saved as PM on the New and Edit forms).
     - **Approved copies (the rule, `documents.services.needs_review`):** a new column `DOCUMENT_TYPE.needs_review`
       (boolean, default False for every type; migration `documents/0008`; switched in the Django admin). False: an
       activity document is saved **Approved at upload** (`reviewed_by` empty = System, `reviewed_at` = the upload time),
       one activity History row "Documents": `Activity Design "…" uploaded as an approved copy` and one documents.audit
       line (IDs only), indexed for the AI at once (on commit), NO "waiting for your review" notification, no Approve /
       Return. True: today's review flow (items 344, 406) for that type. Certificate evidence keeps its own flow
       (`needs_review(..., related_certificate)` is always True); certificates and the later "Submit to the Director" are
       untouched.
     - **Replace / delete an approved copy (owner decisions):** its uploader OR a member with can_approve_documents
       (`_may_change`); refused: the only Activity Design / Request Letter of a non-Draft activity (`_delete_refused_reason`,
       delete only), a document an AI answer cites (`CITED_REFUSED`, delete only: the Delete button may show and the
       click gets the message, to keep the lists at a fixed number of queries), certificate evidence (its own flow). An
       Approved document of a type that needs review stays final. Every replace and delete: a History row ("… replaced
       (uploaded as an approved copy)", "… deleted") and a documents.audit line. Replacing removes the old AI chunks and
       queues the indexing again.
     - **Unchanged (owner):** a document counts as complete when uploaded (Approved); overdue = after the due date and
       not uploaded / Approved; the 5-day due rule (416); auto-Scheduled (431: uploaded is enough).
     - **Existing documents:** data migration `documents/0009` sets activity documents still "Waiting for review"
       (For Review, reviewed_at empty) of a type without review to Approved by System (reviewed_at = migration time);
       "Returned" ones are left as they are (the uploader replaces them, and the replacement is an approved copy);
       evidence and reviewed types are never touched. Reverse: Approved with reviewed_by empty of a type without review
       goes back to waiting (also later approved copies). After `migrate` run `python manage.py index_documents`.
     - **Texts:** the New form's Documents card ("Upload the approved copies. …"), the Documents tab's upload card, the
       upload / replace / create messages, the document page ("Uploaded as an approved copy on …"), the Documents page
       subtitle, the Roles screen note, the assistant's example question, the compliance report note. The Documents page
       shows the "Waiting" and "Returned" cards and filter choices only while review is in use
       (`services.review_in_use`: a type needs review, or older documents still wait / were returned). The dashboard's
       review card, Needs attention and the review queue keep their rules and simply count nothing.
     - **Demo data:** `seed_demo` creates approved copies (Approved by System) for types without review.
     - **Deleting an activity (item 389; owner decision 2026-11-24):** Approved activity documents no longer block it
       (`activities.deletion.deletion_refused_reason`); only an Issued certificate, Approved certificate evidence
       (evidence keeps its own approval) and a document an AI answer cites still do. The documents and their files go
       with the activity; the confirm dialog says how many ("2 documents (approved) and their 2 files", plus the total of
       stored files) and ACTIVITY_DELETION.counts stores `documents`, `documents_approved` and `document_files` (JSON
       keys, no schema change). Tests: `certificates/tests/test_activity_deletion.py`.
     - **ERD:** DOCUMENT_TYPE gets `needs_review` (boolean, default false). 34 tables.
     - Tests: `documents/tests/test_approved_copies.py`; review-flow tests switch needs_review on for their types.

## NAVIGATU UAT: the activity budget (2026-11-24)

433. **Budget lines, fund source and account; no budget approval in the system** (NAVIGATU UAT; owner decisions
     2026-11-24). The budget is already approved on paper inside the Activity Design: staff only copy it in. No approval
     status, notification or Needs attention item for the budget. *(Open question, CLAUDE.md.)*
     - **Database (34 -> 37 tables):** FUND_SOURCE (`fund_source_id`, `code` unique, `name`, `is_active`, `sort_order`,
       `created_at`; RAF – Regular Agency Fund, IGF – Internally Generated Fund, TRF – Trust Fund, BRF – Business Related
       Fund), BUDGET_CATEGORY (`budget_category_id`, `name` unique, `is_active`, `sort_order`, `created_at`; Meals/Snacks,
       Honorarium, Supplies, Venue, Transportation, Printing, Other), BUDGET_LINE (`budget_line_id`, `activity` FK CASCADE,
       `category` FK PROTECT, `particulars` max 255, `quantity` DECIMAL(10,2) > 0, `unit` max 30, `days` integer >= 1
       (default 1), `unit_price` DECIMAL(12,2) >= 0, `line_total` DECIMAL(12,2), `sort_order`, `created_at`; check
       constraints), and ACTIVITY.`fund_source` (FK PROTECT, nullable only for older rows) + ACTIVITY.`fund_account` (max
       255, blank). Migrations `activities/0013` (schema) and `0014` (data: the two lists, created only when missing, and
       one line "Unitemized (previous total)" (Other, 1 lot × 1 day × the old total) for every activity with a
       budget_amount above 0 and no lines; none for 0; no fund source is set: "Not set" until someone edits the activity,
       also in the reports (owner decision); reverse deletes only those lines). The two lists are managed in the Django
       admin (editable, never deleted: deactivate); `seed_data` creates missing rows only.
     - **The rules (`activities/budget.py`):** `clean_lines` is the one check (category active or already on the
       activity, particulars, quantity > 0 with at most 2 decimal places, unit, days a whole number 1–366 (empty = 1),
       unit price >= 0 with at most 2 decimal places "(centavos)", at most 50 lines, totals that fit DECIMAL(12,2)); rows
       left empty are skipped; errors as "budget-<row>-<field>" / "budget_lines". `line_total` = quantity × days × unit
       price, ROUND_HALF_UP to the centavo, Decimal only (1.5 × 1 × ₱33.33 = ₱50.00; 0.25 × 3 × ₱10.10 = ₱7.58).
       ACTIVITY.budget_amount = the grand total, written only by `save_lines` (never typed: the form has no field; the
       JSON API and `_clean_values` refuse "budget_amount" with "The budget amount is calculated from the budget lines;
       it cannot be typed."). No lines = ₱0.00 (owner decision); the fund source is still required.
     - **Saving:** the lines are the pseudo-field `budget_lines` of `create_activity` / `update_activity` (also the New
       form through `create_activity_with_documents`), checked together with the fields; replaced as a set in the same
       transaction. History (edits only; creation is its own row): "Budget" (the lines, old -> new, e.g. "Lunch
       (Meals/Snacks): 30 pax × 1 day × ₱150.00 = ₱4,500.00; …"), "Budget Amount" (the total), "Fund Source", "Fund
       Account". Conducted is read-only (`_ensure_not_conducted`). Fund source: required when creating; never cleared; an
       inactive one only when it stays; an older activity without one may stay "Not set" while other fields change, but
       the activity FORM requires it on every save. The Django admin shows the lines and budget_amount read only.
     - **Pages:** the activity form's "Budget" card (`activities/_budget_fields.html`, `pages.budget_section`): fund
       source (required select), "Account / charging", the lines table (rows added / removed with Alpine; Category,
       Particulars, Quantity, Unit with suggestions, Days, Unit price (₱), Total), totals per category and the grand total
       refreshed by `activities:budget_preview` (HTMX, the service's `budget.preview`; no arithmetic in JS); errors under
       each cell, typed values kept. The Overview's "Budget" card (`activities/_budget_card.html`, `budget.budget_summary`)
       and the read-only (Conducted) page: fund source, account, Particulars / Quantity ("30 pax × 1 day") / Unit Cost /
       Total Amount, totals per category, Total: the "Running Budget" table of NAVIGATU's Activity Report
       (`budget.budget_table`, for Prompt 6). The API detail has `budget` (fund source, account, lines, totals).
     - **Reports:** N19 `metrics.period_budget_total` (sum of budget_amount of the period's Conducted activities) and N20
       `metrics.budget_by_fund_source` (every active fund source, an inactive one only when used, "Not set" last when
       any). New section "Budget by fund source" (`budget`, default in the Accomplishment report; PDF ₱1,234.50, Excel
       `"₱"#,##0.00`, sheet "Budget"; total row = N1 and N19).
     - **AI assistant:** routing path `budget` (budget / fund source / how much / cost / expenses / charging words AND an
       activity named by its title, without another content word, a file word or a document type; checked before the
       content rule, so "What does the Activity Design say about the budget?" and a bare "What is the budget?" still
       search the documents, as before): our code answers
       (`listings.budget_answer`, no Gemini, works without the key) with the fund source, account, lines, category totals
       and total, plus "(Source: the system's records.)". Record tool
       `get_activity_budget` (amounts as text, never float) for Gemini's mixed questions.
     - **Demo:** `seed_demo` gives every demo activity a fund source and lines that add up to its old total;
       `certificate_walkthrough` a fund source.
     - **Backup:** before `migrate`, the development database (and `csu_tbi_uat` if it is ever started again).
     - Tests: `activities/tests/test_budget.py` (calculations and rounding, every refusal message, blank rows, inactive
       category, limits, fund source rules, History, Conducted, no approval, the migration both ways, the New / Edit forms,
       the preview, the API, the reports, the assistant routing / answer / tool).

## NAVIGATU UAT: QR attendance per day, signatures, walk-ins and the generated forms (2026-11-24)

434. **The system replaces the paper "Confirmation of Attendance" and "Attendance" forms** (NAVIGATU UAT; owner decisions
     2026-11-24). **Replaces item 111** (only staff marked attendance, no self check-in) and business rule 3 (no walk-ins).
     The flow: (1) pre-registration online = "Will attend" (the meal estimate); (2) on each day participants scan an
     "Attendance check-in" QR code; (3) the system generates the two forms. *(Open question, CLAUDE.md.)*
     - **Database (37 -> 39 tables).** REGISTRATION + `registration_source` (online / portal / walk_in, NOT NULL, default
       online) and `cancelled_at` (nullable: "Will not attend"); REGISTRATION − `confirmation_method`,
       `attendance_marked_at`, `attendance_marked_by_id`. New PARTICIPANT_SIGNATURE (`signature_id`, `registration_id` FK
       unique CASCADE, `kind` draw / upload / typed, `file_path` max 255 (blank for typed or deleted), `typed_name` max 150
       nullable, `signed_at`, `deleted_at` nullable; check: deleted, or typed with a name, or draw / upload with a file).
       New ATTENDANCE_DAY (`attendance_day_id`, `registration_id` FK CASCADE, `day`, `checked_in_at`, `method` qr / staff /
       email / wet_signature, `marked_by_id` FK TBI_MEMBER nullable PROTECT, `created_at`; unique (registration, day)).
       Migrations `registrations/0011` (add), `0012` (data), `0013` (drop the three columns); `documents/0010` (the
       DOCUMENT_TYPE row "Confirmation of Attendance": required, end_date + 5, no review; only in a seeded database and
       only when missing; `seed_data` creates it on new databases).
     - **What happened to the old values (owner decision):** `0012` gives every registration marked as attended ONE
       ATTENDANCE_DAY row: day = the Manila date of `attendance_marked_at` when it is inside the activity's dates, else
       the start date; method = the old `confirmation_method` ("email" / "wet_signature"; "wet_signature" when empty);
       marked_by and the time copied. `email` and `wet_signature` exist ONLY on these copied rows: staff marking is
       always "staff". Registrations of a startup get source "portal". The migration prints the counts and stops if the
       number of day rows differs from the number of attended registrations. Reversible (the first day row fills the
       old columns back; qr / staff become wet_signature).
     - **"Attended" stays one rule:** `REGISTRATION.attendance_confirmed` = attended AT LEAST ONE DAY = the registration
       has an ATTENDANCE_DAY row. Only `registrations.attendance` writes it (`_sync_attended`, same transaction as the
       rows; every change also saves the registration). Every other module keeps reading the flag (reports, the
       participant mix, Needs attention, certificates, evaluations, the portal), so they all use "attended at least one
       day" without a change. Parity test in `registrations/tests/test_attendance_days.py`. Certificate rule (owner
       decision): attended at least one day + evaluated; only people who attended at least one day can evaluate.
     - **Activity days:** every calendar day from start_date to end_date (weekends included). `update_activity` refuses
       new dates that would leave a recorded attendance day outside them.
     - **QR check-in** (`/checkin/<id>/`, `registrations.checkin_views`, rules in `registrations.attendance`):
       `checkin_state()` is the one rule: on each activity day from `PARTICIPANT_CHECKIN_EARLY_MINUTES` (60, owner
       decision) before start_time until end_time (Manila; no times = the whole day), while the activity is Scheduled or
       Ongoing (so on day 1 also while still Scheduled; the page runs `refresh_status` first). Draft = 404; Conducted =
       closed. The participant types the email they registered with: registered -> today's row (method qr); a second
       scan the same day -> "Already checked in today at 8:42 AM."; no usable signature -> the signature step (+ privacy
       consent) before anything is recorded; not registered -> the walk-in form. A cancelled registration who comes
       anyway is checked in (the Confirmation still shows what they signified). After each QR check-in an email (Django-Q2
       task `run_send_checkin_email`, queued on commit, robust: never blocks): "You were checked in to <activity> on
       <date> at <time>. If this wasn't you, contact the TBI." (owner decision c). Every POST goes through
       `attendance.guard_checkin` (CHECKIN_MAX_ATTEMPTS_PER_IP 600, _PER_EMAIL 10 per 60 minutes, HMAC cache keys,
       honeypot). The done page shows only the day and the time, never the name or the email. **Known risk:** anyone who
       knows a registered email can check that person in while the window is open, and the page reveals whether an email
       is registered (the walk-in step). Kept small by the time window, the rate limits, staff undo and the notice email.
     - **Walk-ins** (`register_walk_in`): only inside `checkin_state()`, even when online registration is closed
       (deadline, slots or by hand) and regardless of max_slots. Name, email, contact number, category, Designation /
       Position, Office / Agency (item 426, the same check as `register()`: `services.clean_registrant`), signature and
       privacy consent; source walk_in; checked in at once. The same email twice in one activity is refused ("This email
       is already registered for this activity. Go back and type it on the check-in page instead."). Their template is
       the activity's registration form (its own questions are not asked of walk-ins); without a registration form
       walk-ins are refused with "see the TBI staff". Marked "Walk-in" on the Registrations tab and the Attendance form.
     - **Staff marking per day** (`services.set_attendance` -> `attendance.mark_by_staff`): unchanged permission and
       status rule (can_create_activity, Ongoing or Conducted); one day at a time (`day`, the tab's "Day" choice = the
       activity's days up to today; before day 1 has started, the first day); method "staff"; undoing someone's LAST day
       keeps the old undo rules (refused after an evaluation or an approved certificate; a waiting certificate is
       deleted). The row's "Mark attended" for a person without a signature opens a dialog (owner decision b): they sign
       on the staff device (draw or typed name + tick) or staff choose "Mark without signature" (the sheet then says
       "Marked by staff"). Django admin actions: "Mark attended today (staff ...)" / "Undo attendance ...".
     - **Signatures** (`registrations/signatures.py`, the one place): REQUIRED at pre-registration (public page and
       `/api/register/`, startup portal page and API: they pass the raw fields to `register(signature=...)`; the Django
       admin, `seed_demo` and tests pass nothing, like the privacy consent), at the check-in for anyone without one, and
       for walk-ins. Given once per registration, reused on every form. Draw (canvas, `static/js/signature_pad.js`, a
       PNG data URL at most `PARTICIPANT_SIGNATURE_DRAWN_MAX_KB` 500; a blank canvas is refused), upload (JPG / PNG by
       name AND content, at most `PARTICIPANT_SIGNATURE_MAX_KB` 2048) or typed full name + "I confirm this is my
       signature" (printed as the typed name). Images are re-encoded by Pillow to a PNG fitting 900 x 300 (metadata
       dropped) under `signatures/activity-<id>/<random>.png`; never served from `/media/` (staff only:
       `registrations:signature_image`); data: URIs in the PDFs. Without JavaScript the participant uploads or types.
     - **Retention (owner decision, with the extra condition):** the daily job "Delete participant signatures" (7:45,
       `setup_schedules`; command `purge_participant_signatures`) deletes an activity's signatures (file removed after
       the commit, typed name cleared, `deleted_at` set; the row stays) when EITHER 1 year after the end date
       (`PARTICIPANT_SIGNATURE_RETENTION_DAYS`) OR: the activity is Conducted, an Approved Attendance Sheet,
       Confirmation of Attendance and Activity Report are all uploaded (approved copies, item 432; generated or scanned),
       AND the newest Attendance Sheet and Confirmation of Attendance are newer than the last attendance / registration
       change (`signatures.last_change`: a registration saved, a day recorded, a signature given), so the official forms
       show everyone. The generated PDFs keep the printed signatures (official records). After deletion, generating
       again is refused ("upload a scanned sheet instead"). Activity deletion (item 389) also removes the files.
     - **"I can no longer attend":** the confirmation email links to `/registration/cancel/<token>/` (signed, salt
       `registrations.cancel-link`). GET only shows the page (mail scanners open links); a POST cancels: `cancelled_at`
       set, the row kept ("Will not attend"), the slot freed (`registered_count()` and its SQL twin in
       `open_for_registration()` leave cancelled rows out). Allowed while Scheduled, before the start, and not checked in
       (`services.cancellation_state`). "I can attend after all" restores it while `registration_closed_reason()` is None
       (open, a free slot). The startup portal's My registrations links to the same page. The reports' "registered"
       numbers still count every registration (a cancellation is still a registration); "attended" is unchanged.
     - **Overview "Attendance" card:** Will attend / Will not attend (pre-registrations: online + portal) / Walk-ins, and
       how many attended each day (by QR). The Registrations tab's first card is "Will attend / max slots".
     - **Share card:** for Scheduled and Ongoing, also "Attendance check-in" (link, QR code `activities:qr_code` kind
       `checkin`, shown and printable before it opens) and "Print check-in poster" (`registrations:checkin_poster`, A4).
     - **Generated forms** (`registrations/attendance_forms.py`, template `registrations/attendance_forms_pdf.html`, A4
       portrait through `certificates.pdf.get_renderer()`): "ATTENDANCE", one sheet per activity day with attendance
       (Title of Event, Venue, Date; No., Name of Participant (walk-ins marked), Designation/Position, Office, Signature
       (image / typed name / "Marked by staff" / "Signed on paper" for copied rows), Time in), and "CONFIRMATION OF
       ATTENDANCE", one sheet (Date "October 21-22, 2026"; No., Name, Designation/Position, the signature under "Will
       attend" or "Will not attend"; online and portal registrations only: walk-ins never signified). Both: "Prepared
       by:" the member who generates it with their POSITION (item 429), the NOTE about meals, the form code "F-PROC-COA-005
       Rev.03, 10-10-2023" and a plain placeholder header (the CSU letterhead since item 469). Buttons on
       the Documents tab's payment group (can_create_activity); saved through `upload_document` as approved copies of
       the Attendance Sheet / Confirmation of Attendance types, so they count in the checklist and the payment packet.
       Generating again adds a new document. Staff can still upload a scanned paper sheet instead.
     - **Confirmation of Attendance as a document type: yes, and a payment document** (`DOCUMENT_TYPES_FOR_PAYMENT`;
       attached to the Activity Report after the Attendance), required only for activities ending on or after
       `CONFIRMATION_DOCUMENTS_FROM` (2026-11-25). `type_applies` / `metrics._applies` now read one dict
       `DOCUMENT_TYPES_REQUIRED_FROM` (name -> date; the item 425 types keep PAYMENT_DOCUMENTS_FROM).
     - **AI assistant (owner decision a):** `ASSISTANT_EXCLUDED_DOCUMENT_TYPES` is now EMPTY: the assistant reads every
       approved file, names included, also the Attendance Sheet and the Confirmation of Attendance (they are part of the
       Activity Report). Signature images are pictures, never text, so they never reach Gemini. (Item 165's question to
       NAVIGATU about the free tier still applies.)
     - **Privacy notice** (version November 24, 2026): signatures, daily check-in times, the check-in email and the
       signature retention added to `/privacy/` and the consent summary.
     - **Backup:** before `migrate`, the development database and the `MEDIA_ROOT` folder (signatures are stored there
       from now on).
     - Tests: `registrations/tests/test_attendance_days.py` (window per day, duplicate check-in, the email, walk-ins and
       duplicate email, staff per day, parity, cancellation, the three kinds, upload checks, retention, the page steps,
       rate limits, Share card), `test_attendance_forms.py` (the right people per page, Prepared by, documents),
       `test_migration_attendance_days.py`, `certificates/tests/test_automatic_flow.py` (one day is enough); opt-in
       Chromium `registrations/tests/test_checkin_browser.py` (drawing, the check-in page at 390 px).

## NAVIGATU UAT: participant photos with the evaluation (2026-11-25)

435. **Participants may add photos (or their work) to their evaluation, for the TBI's reports** (NAVIGATU UAT; owner
     decisions 2026-11-25). Stored and shown now; Prompts 6–7 put the ticked ones into the reports (Activity Report,
     Evaluation Summary, Accomplishment Reports, annual report).
     - **Database (39 -> 40 tables).** New EVALUATION_ATTACHMENT (`attachment_id`, `submission_id` FK EVALUATION_SUBMISSION
       CASCADE, `file_path` max 255, `file_name` max 255 (the participant's name for display only, never in a path),
       `file_size_kb` (the stored file), `content_type` image/jpeg / image/png / application/pdf, `use_in_reports`
       default False, `marked_by_id` FK TBI_MEMBER nullable PROTECT, `marked_at` nullable, `uploaded_at`; check:
       use_in_reports false OR marked_at set). Migration `registrations/0014` (schema only, reversible).
     - **Where:** the personal evaluation page's review step ("Upload your photos from the event (optional)"), inside the
       "Confirm and submit" form (a browser cannot keep chosen files between two pages). Every attendee reaches that page:
       the general link (`/evaluation/<id>/`, item 334) only emails the personal link, the startup portal links to it,
       walk-ins (item 434) evaluate like everyone else. The JSON `/api/evaluate/<token>/` stays text only (owner decision).
       A refused file shows the review step again with the reason; nothing is saved and the files are chosen again. Works
       without JavaScript.
     - **Rules** (`registrations/evaluation_photos.py`, the one place; `submit_evaluation(..., attachments=)` calls it):
       optional; at most `EVALUATION_PHOTO_MAX_FILES` (5) files, each at most `EVALUATION_PHOTO_MAX_MB` (10); JPG / PNG /
       PDF by name AND content (first bytes like document uploads; images must also open in Pillow as that type).
       Photos are re-encoded by Pillow: turned upright (EXIF orientation), then saved fresh without metadata (EXIF, GPS,
       camera, PNG text; only the colour profile kept), the long side at most `EVALUATION_PHOTO_MAX_SIDE` (2400 px).
       **PDFs are stored as they are** (owner decision: no new library to clean PDF metadata; the privacy notice promises
       the location removal for photos only). Stored under `evaluation-photos/activity-<id>/<random>.<ext>` in the same
       transaction as the evaluation (one submission, never anonymous); a failure removes the files already written.
     - **Staff** (Evaluation tab, card "Participant photos"; `registrations.pages.participant_photos`): everyone sees the
       count; only can_create_activity (`evaluation_photos.VIEW_PERMISSION`, owner decision; Director included) sees the
       thumbnails (24 a page, `photos_page`), who uploaded (the registration's name, "Walk-in"), when, a larger view
       (dialog), Download, and the "Use in reports" tick box. Files only through `registrations:evaluation_photo`
       (inline) / `evaluation_photo_download` (attachment), `private, no-store`, nosniff; never `/media/`.
     - **"Use in reports"** default OFF (photos can show other people); ticked / unticked only by
       `evaluation_photos.set_use_in_reports` (can_create_activity): `marked_by` / `marked_at` = the latest change,
       plus a `registrations.audit` line (IDs only) for every change. `photos_for_reports(activities)` is THE query for
       reports and anything sent to the AI: ticked files only.
     - **Not documents** (item 425's staff "Event Photos" type stays separate): no DOCUMENT row, so never in the checklist,
       the payment packet, reminders, the compliance report or the AI index (`index_documents`).
     - **Deleting:** a `post_delete` receiver (connected in `RegistrationsConfig.ready`) removes the file after the
       commit whenever a row goes, also by cascade (evaluation, registration, activity deletion item 389; a rollback
       keeps the file). The activity deletion dialog and ACTIVITY_DELETION.counts show `participant_photos`.
     - **Retention:** none for now (owner decision: report material), kept with the evaluation and deleted with the
       activity. Pending NAVIGATU / the CSU Data Protection Officer (open question, CLAUDE.md).
     - **Privacy notice** (version November 25, 2026): what is collected, that the TBI may use selected photos and files
       in its reports, "do not add photos of people who did not agree", location data removed from photos (not PDFs),
       who sees them, kept with the evaluation; the consent summary says the same in one sentence.
     - **Backup:** before `migrate`, the development database and the `MEDIA_ROOT` folder (photos are stored there from
       now on, next to the signatures).
     - Tests: `registrations/tests/test_evaluation_photos.py` (no photos still submits; 5 / 6 files; size; wrong type and
       fake content; nothing left behind; EXIF / GPS removed and upright; PNG text removed; shrinking; PDF unchanged;
       permissions 403 / login / no /media/; the tab for both groups; "Use in reports" with who and when; only ticked
       files for reports; not documents; files removed on activity / registration delete, kept on rollback); opt-in
       Chromium `registrations/tests/test_evaluation_photos_browser.py` (uploading at 390 px; ticking + the larger view
       at 1280 px).

## Browser checks fixed: budget lines, the Members table, the Return dialog (2026-11-25)

436. **The budget lines and the Members table never hide a button; the modal check tests Return again** (owner, the two
     opt-in browser tests that failed since items 432–434; layout and test data only, no rule, database or URL change).
     - **Budget lines** (item 433, `activities/_budget_fields.html` + `_budget_row.html`): no longer a table with a 56rem
       minimum (it scrolled sideways and hid "Remove this budget line" at 1280 / 1366 px). A list of lines in a container
       query: from 52rem of card width (the desktop form) one grid row per line with a header row (labels sr-only); narrower
       each line is a small card with visible labels (Category and Particulars full width, then Quantity / Unit / Days /
       Unit price, then Total and the Remove button). Same names, ids, HTMX preview and server totals.
     - **Members table** (Admin Console): three columns (Member with the status badge, "Role and position" with the two
       small forms stacked, the actions stacked) so it fits the panel from 1280 px and Reset password / Deactivate are
       never past its right edge. Both selects 17rem wide.
     - **`config/tests/test_modals_browser.py`**: since item 432 the demo data has no upload waiting for review, so the test
       switches `needs_review` on for the Invitation Letter type in its own data and uploads one as another member
       (`_waiting_document`); the Return dialog is measured again.
     - Tests: the opt-in `documents/tests/test_page_buttons_browser.py` (1366 / 1280 / 1024 / 390 px) and
       `config/tests/test_modals_browser.py` pass.

## NAVIGATU UAT: the Director's approvals, unapprove, submissions and assigned work (2026-11-25)

437. **"For your approval", unapprove, editing other members' work, "Submit to the Director" with a stamped signature,
     and assigning work** (NAVIGATU UAT: "the Director's account is mainly for approval, but the Director can also
     create, unapprove and edit other members' work, and assign work to members. Members submit their work to the
     Director, who approves it or returns it and can sign it."; owner decisions 2026-11-25). Built in two parts (A, B).
     Already done before: approved copies (432), only the Director approves certificates (421), manages signatories
     (418) and chooses an activity's signatories (431). There is NO budget approval (433).
     *(Open question, CLAUDE.md.)*
     - **New app `approvals`** (owner decision): SUBMISSION, SUBMISSION_EVENT, WORK_ASSIGNMENT; services in
       `approvals/services.py` (submissions), `approvals/assignments.py` (assignments), `approvals/signing.py` (the stamp),
       `approvals/inbox.py` (the inbox). Pages under `/approvals/`.
     - **Database (40 -> 43 tables).** Migrations: `approvals/0001_submissions` (SUBMISSION, SUBMISSION_EVENT),
       `approvals/0002_work_assignments` (WORK_ASSIGNMENT), `certificates/0017_signatory_member`
       (CERTIFICATE_SIGNATORY.member_id + partial unique "one active person per account"), `notifications/0003`
       (NOTIFICATION.submission_id), `notifications/0004` (NOTIFICATION.work_assignment_id), `accounts/0011` (ROLE
       .can_assign_work) + `accounts/0012` (guarded data step: the role named "Director" only, skipped if it manages
       users), `activities/0015` (ACTIVITY_AUDIT_LOG.on_behalf_of_id). All reversible; no existing row is changed except
       the Director role's new flag.
     - **1. "For your approval"** (`/approvals/`, sidebar "Approvals" with a count badge, Dashboard card "For your
       approval" below the KPI cards): everything waiting for THIS member, oldest first, each kind from its existing rule
       (one query per kind, fixed count, tested): activities whose certificates wait for approval or were prepared before
       Conducted (can_approve_certificates; `needs_approval_q`, item 420's "prepared"), submissions waiting
       (can_approve_documents, never your own), documents of types with needs_review waiting (`review_queue`; usually none
       since 432), and the assignments THIS member gave that are past their due date (can_assign_work). Filters (kind,
       activity, search) in the URL (HTMX); one Open button per item to the page where it is decided (nothing is approved
       from the list: the work is checked on its own page first). A member who approves nothing goes to "My
       submissions". Tabs: For your approval / All submissions (reviewers) / Assigned to me / My submissions.
     - **2. Unapprove** (a reason, max 300, required; History / event log; the person concerned notified). What depends
       on each approval and how it is undone:
       - *An activity's certificate approval* (`certificates.services.unapprove_activity_certificates`,
         can_approve_certificates, the Certificates tab's red-text "Unapprove" + dialog): only while NO certificate of
         the activity is Issued (`unapprove_closed_reason`; an Issued one was sent / handed over: change and reissue
         instead). The approval (who / when) is cleared, every Approved certificate goes back to "Waiting for approval"
         and its PDF is removed (`cancel_certificate_approval` -> `_refresh_certificates`), so nothing more is rendered,
         issued or emailed (`prepare_certificate` re-checks Approved under a lock: a queued render does nothing). An
         early approval (420) is simply cleared. History "Certificate approval": "Approved by …" -> "Needs approval
         (unapproved by <name>: <reason>)"; the activity's creator and the member who prepared the certificates are told
         (`certificates_unapproved`, never the member who unapproved).
       - *An approved submission* (`approvals.services.unapprove_submission`, can_approve_documents): back to
         Returned (the submitter can submit again); the signed copy is deleted after the commit (the original stays); a
         document saved to the activity from it is deleted with the documents' guards
         (`documents.services.remove_saved_submission_document`: the only Activity Design / Request Letter of a
         non-Draft activity, a document an AI answer cites) - refused = nothing changes. Submitter told.
       - *A document of a type with needs_review* (`documents.services.unapprove_document`, can_approve_documents, the
         document's "Unapprove" + reason dialog): Approved -> Returned (For Review, reviewed_by / reviewed_at = this
         member), its AI chunks removed (only Approved documents are searchable), it counts as not done in the checklist
         again (it can become overdue; reminders already sent are not repeated); an activity that was Scheduled
         automatically (431) stays Scheduled. Refused for approved copies (needs_review off: they are replaced or deleted,
         432), certificate evidence (its own flow) and a document an AI answer cites (`UNAPPROVE_CITED`). History
         "Documents" + documents.audit; uploader told (`document_unapproved`).
     - **3. Editing other members' work.** What the Director could NOT do before (checked in the code): rename any
       document (nobody could change a title after upload), and replace / delete another member's document still
       waiting or returned (uploader only; only approved copies were open to the Director). Registration and evaluation
       form edits were allowed but NOT logged. Activities, budget lines (through `update_activity`) and certificate text
       were already editable by every member with the flag and logged. Now: `documents.services.rename_document` (the
       uploader or can_approve_documents, any state; the one title rule; History "Documents": old -> new title; an
       indexed document is indexed again because its title goes with the embeddings); `_may_change` lets
       can_approve_documents replace / delete waiting / returned activity documents (evidence stays uploader-only);
       every form change writes History ("Registration form" / "Evaluation form": created, field added / changed /
       removed with its type and required, order old -> new; an unchanged save writes nothing). Every edit shows who and
       when, and "(on behalf of …)" when done through an assignment.
     - **4. Submit to the Director** (`approvals.services`): any active member uploads a letter, report or other file
       (the document upload checks: PDF / DOCX / XLSX / JPG / PNG by content, DOCUMENT_MAX_UPLOAD_MB; stored as
       `submissions/<year>/submission-<random>.<ext>`, never the uploaded name), a title, an optional note, optionally
       the activity and the document type. Reviewers = can_approve_documents (owner decision; the Director by default;
       flags, never a role name), all told; refused when nobody ELSE could approve it. Approve / return (a reason): never
       your own submission; the submitter is told either way; a returned one is submitted again with a new file (same
       row; the old file removed after the commit; SUBMISSION_EVENT keeps each round). `submit_generated()` is ready for
       the letters and reports of Prompts 6-7. The Documents tab links to it ("Submit it to the Director").
       - *Signing (owner decisions):* **PDF only**; DOCX and images are approved without signing (no converter without
         LibreOffice). While approving, the approver may stamp THEIR OWN signature: the active CERTIFICATE_SIGNATORY
         linked to their account (`member_id`, set by can_manage_signatories on the Signatories page, "Account" dialog;
         one account per active person; new versions keep the link; `certificates.services.link_signatory_account`,
         `signatory_for`). Placement: the LAST "Noted:" in the PDF's text (pypdf text positions; the image goes just
         below and right of the word) or a chosen page and one of six positions; a scanned PDF has no text, so the
         Director chooses a place. "Preview the signed copy" opens the stamped copy in a new tab (never stored). The stamp
         is drawn with pypdf only (already used by the assistant; no new library): a PDF image object (transparent PNGs
         keep their transparency) plus a small grey line "Stamped in CSU-TBI Track, <date>". Encrypted PDFs are refused.
         The stamped copy is a NEW file `submissions/<year>/submission-<id>-signed-<random>.pdf` (SUBMISSION
         .signed_file_path, signed_by, signed_at, signatory = the exact signature version); **the original is never
         changed**. Pages say it is a stamped image, not a cryptographic digital signature. Signing is never delegated.
       - *Save to the activity's documents:* an approved submission linked to an activity is saved (by the submitter or
         a reviewer, once) through `upload_document` as an approved copy (the signed copy when there is one); for a type
         with needs_review it is saved Approved in the reviewer's name (`approved_by`), never reviewed twice. History
         "… saved from an approved submission".
       - Files only through `approvals:file` (the submitter, reviewers, an assignee; 404 for anyone else; attachment,
         `?inline=1` for a PDF in a new tab; nosniff, private no-store), never `/media/`.
     - **5. Assign work** (WORK_ASSIGNMENT, `approvals/assignments.py`): a member with the new ROLE flag
       **can_assign_work** (seed: the Director only, create-only; Roles screen switch `set_role_work_assignment`, amber
       note when nobody active has it; NEVER together with can_manage_users, `check_role_flags`) who ALSO holds the flag
       of the work assigns one piece of work to another active member with an optional note and due date (not in the
       past): an activity ("Work on the activity": edit it, its forms, attendance, certificate text), its certificate
       approval, choosing its signatories, a document of a needs_review type (waiting), a submission (waiting), a report
       to prepare (a title). One open assignment per piece of work (partial unique constraints). The "Assign" button +
       dialog sits on the activity page header, the document page, the submission page and Reports; it lists the open
       assignments of that work with Remove.
       - *Permission for THAT task only:* `accounts.permissions.has_permission_for / require_permission_for /
         require_any_permission_for / object_permission_required` = the role flag OR an OPEN assignment of the matching
         kind on that object (`OBJECT_KINDS`; signatory choice is its own kind). Never a role change. An inactive member,
         a done or removed assignment never counts. The services check it (update_activity, change_status, resource
         persons, close registration, the form services, attendance, certificate text, prepare, approve certificates,
         choose signatories, approve / return documents and submissions, generating a report); the page views use
         `object_permission_required` so the assignee is not stopped at the door.
       - *"On behalf of":* the check that allowed it records the assigner (a context variable, cleared at the start of
         every staff request and by every plain permission check); History (`ACTIVITY_AUDIT_LOG.on_behalf_of`, shown as
         "Ana Cruz (on behalf of Dr. Reyes)"), SUBMISSION_EVENT.on_behalf_of and the audit lines read it. The assignment
         itself is a History row ("Assignment": "… assigned to Ana Cruz, due …", "Done", "Assignment removed").
       - *Done / removed:* reviews close themselves when approved or returned (`complete_for`, whoever did it); an
         activity, signatory choice and a report are closed with "Mark done" (the assignee or the assigner; a report may
         link the report the assignee generated). Remove = the assigner or another member who could assign it. The
         assignee is told when assigned / removed, the assigner when the assignee marks it done. "Assigned to me"
         (`/approvals/assigned/`, Dashboard card) lists them, due first, overdue in red.
     - **6. Separation of duties:** nobody approves or returns their own submission (also the Director); nobody is
       assigned to review their own upload / submission, or certificates of an activity they created or prepared (and
       the service refuses it again at approval time); an assignee may approve a submission but never sign it and never
       unapprove; a member whose role manages users is never given work that role may not have (activities, signatories,
       documents, submissions, reports; certificate approval stays allowed, item 361); you cannot assign to yourself.
     - **Backup before `migrate`:** the development database and the `MEDIA_ROOT` folder (submissions and signed copies
       are stored there from now on).
     - Tests: `approvals/tests/test_submissions.py`, `test_inbox_and_pages.py`, `test_assignments.py`,
       `certificates/tests/test_unapprove.py`, `documents/tests/test_unapprove_and_rename.py`,
       `registrations/tests/test_form_history.py`; opt-in Chromium `approvals/tests/test_approvals_browser.py` (the
       inbox and the Assign dialog at 1366 and 390 px; the Approvals tabs now wrap on phones). The certificate migration
       tests now re-apply every app after going back (going back also unapplies approvals / notifications).

## NAVIGATU UAT: the AI Assistant reads every file, new record tools, a bigger chat box, cancelled registrations (2026-11-25)

NAVIGATU UAT: "they want their work to be easier". Built in two parts (owner decision): Part 1 = the AI Assistant
(items 438-439) and the cancelled-registrations rule (440); Part 2 = the report generator and the Activity Report /
Evaluation Summary (items 441 onwards).

438. **The AI Assistant reads every file in the system, with its state on the source card** (owner decisions
     2026-11-25; **amends** business rule "Only Approved documents get embeddings", items 151, 165, 382, 432's indexing
     note and 437's "chunks removed" on unapprove). *(Open question, CLAUDE.md: Privacy, Gemini.)*
     - **What is indexed** (`assistant/indexing.py`, the one place): activity documents in EVERY review state
       (approved copies, waiting for review, returned, approved; `why_not_indexable`), never certificate files /
       evidence and never `ASSISTANT_EXCLUDED_DOCUMENT_TYPES` (still empty); the generated Attendance / Confirmation of
       Attendance PDFs (they are documents, item 434); **generated reports** (Reports page) once their file exists;
       **approved submissions** (item 437) that were not saved to the activity's documents (a saved one is indexed as the
       document: never the same text twice).
     - **Database (no new table; still 43 tables).** DOCUMENT_EMBEDDING and AI_MESSAGE_SOURCE: `document_id` becomes
       nullable, new `generated_report_id` (FK GENERATED_REPORT) and `submission_id` (FK SUBMISSION), both nullable;
       check constraint "exactly one of the three"; DOCUMENT_EMBEDDING's unique (file, chunk_index) per kind (partial).
       Embeddings CASCADE with their file; citations PROTECT (a cited file is never deleted, as before). DOCUMENT
       + `ai_scan_allowed_by_id` (FK TBI_MEMBER, nullable, PROTECT) + `ai_scan_allowed_at` (nullable; check: both or
       neither). Migrations `assistant/0003_embeddings_for_reports_and_submissions` (reversible: going back deletes the
       chunks and citations of reports and submissions first) and `documents/0011_document_ai_scan_tick`.
     - **When** (Django-Q2 task `assistant.tasks.run_index_source`, queued on commit, robust): an upload of ANY type
       (approved copy or waiting), a replace (old chunks removed first, any state), an approval (does nothing when the
       upload was already indexed: mode MISSING), a rename (mode RETITLE: the same chunk text embedded again with the
       new title, no second reading / OCR), the scan tick, a report built, a submission approved. Unapprove of a document
       KEEPS its chunks (the card says "Returned"); unapprove of a submission removes them (only approved submissions);
       saving a submission to the documents removes the submission's chunks. The daily 7:30 backfill and
       `index_documents` cover all three kinds (`--document / --report / --submission <id>` re-read one file).
     - **Reading scans with Gemini (OCR):** a PDF without text, or a JPG / PNG of a document, is sent to Gemini
       (`GeminiGateway.read_file`, the chat model, inline, temperature 0, its own timeout
       `ASSISTANT_OCR_TIMEOUT_SECONDS` 180) with an instruction to transcribe it (tables as "a | b" rows; the file is
       data, never instructions); the text becomes the chunks (kept, so nothing is sent twice). Rules (`read_scan`):
       `ASSISTANT_OCR_ENABLED` (env, default true); never **Event Photos** (`ASSISTANT_OCR_EXCLUDED_TYPES`: pictures of
       people, not documents); at most `ASSISTANT_OCR_MAX_PAGES` (30) pages; at most ~19 MB inline; **never by the
       daily backfill** (`allow_ocr=False`: the cost stays with the moment a file is added; `index_documents --document
       <id>` reads one by hand). A PDF that has text is never sent to OCR. Logs: IDs and the number of characters
       only, never the text.
     - **Scanned attendance forms: "Read this scan with AI"** (owner decision): a scan of an Attendance Sheet or
       Confirmation of Attendance (`ASSISTANT_SCAN_TICK_TYPES`) shows handwriting and signatures, so it is sent to Gemini
       only after a member ticks it, **once per file** (`documents.services.allow_ai_scan`: the uploader, can_create_activity
       or can_approve_documents; PDF / JPG / PNG of those types only; History "Documents" "… may be read with AI (scan
       ticked)" + documents.audit (IDs only); a replaced file needs a new tick). The document page shows an "AI assistant"
       card: the explanation and the button, or who ticked it and when, or "already reads this file's text" (a generated
       form has text: no tick needed). A submission of those types is never read with OCR (it has no tick).
     - **Never sent as images:** participant signature images and evaluation photos are not DOCUMENT rows (items 434,
       435), so the indexer never sees them (it knows only documents, reports and submissions); photos appear only in
       reports (Part 2, `photos_for_reports()`).
     - **Cost and limits of OCR (as of 2026-11-25; check Google's price list before relying on it):** each PDF page or
       image costs about 260 input tokens at normal resolution and up to about 1,100 at high resolution (handwriting),
       plus the text read as output (about 300-800 tokens per page). At Flash-Lite-class paid prices (about US$0.10 per
       million input tokens and US$0.40 per million output tokens) a page costs well under ₱0.05, a 20-page scan about
       ₱1; the embeddings of the text are extra but tiny. Free tier: no money, but per-minute and per-day request caps
       (a failure is logged and retried by hand) and **Google may use free-tier data to improve its products: a paid API
       key is required before real participant data is read** (open question, CLAUDE.md). Handwriting is not always read
       correctly; the answers say what the files say, and staff check the source.
     - **Answers:** the tool `search_approved_documents` is now `search_documents`; each excerpt carries `file`, `kind`
       (activity document / generated report / submission to the Director), `type`, `activity`, `state` and `dated`; the
       instruction tells Gemini to say when a file is still waiting for review or returned and to prefer approved and
       recent files. "Not found" is now "I couldn't find the answer in the system's files." With an activity chosen, only
       its documents and submissions are searched (reports cover periods).
     - **Who sees what:** documents and generated reports are open to any staff member (as their downloads are);
       submission excerpts only for members who may open that submission (`approvals.services.visible_submissions`:
       submitter, reviewers, an assignee), so an answer never shows a file the member would get a 404 for.
     - **Source cards** (item 382): the state badge for every kind ("Approved copy", "Approved", "Waiting for review",
       "Returned", "Generated report", "Approved submission" / "Returned submission"); reports: Download only
       (`reports:download`); submissions: Open = `approvals:detail`, Download = `approvals:file` (their own 404 rule).
       The answer badge "From approved documents" is now "From the files".
     - Tests: `assistant/tests/test_indexing_all_files.py` (every state, queueing, MISSING / RETITLE, OCR of images and
       scanned PDFs with the fake only, never a PDF with text, Event Photos never, the tick before any OCR of an
       attendance scan, the backfill never reads scans, page limit, OCR off, failures keep old chunks, no key, the tick's
       rules / page / History / constraint, reports, submissions (approved only, unapprove, saved, who may see), cards for
       every kind, exactly one file per source); updated `test_indexing.py`, `test_answering.py`, `test_find_files.py`,
       `documents/tests/test_unapprove_and_rename.py` (chunks kept).

439. **New record tools; the chat box is bigger** (NAVIGATU UAT; owner decisions 2026-11-25). **The chat box layout below is replaced by item 441** (compact one-line bar, Send on the right, filter in the header).
     - **Record tools** (`assistant/tools.py`, counts only, never names; the existing ones are unchanged):
       `get_attendance_by_day` (`registrations.attendance.checkins_by_day`: per day attended, by QR, by staff; attended at
       least one day), `get_registration_counts` (`reports.metrics.activity_registration_counts`, item 440's rule:
       registered, cancelled, walk-ins, attended, rate; the Overview's Will attend / Will not attend; max slots and slots
       left), `get_participant_mix` (`metrics.participant_mix` + the guideline check), `get_evaluation_results`
       (`metrics.activity_evaluation_summary`, numbers only: per rating question the answers, mean and counts 5..1,
       response rate; never the written answers or comments) and `get_pending_approvals` (the asking member's own
       `approvals.inbox.inbox` + open `assignments.assigned_to_me`: titles, kinds, dates).
     - **Routing** (`assistant/routing.py`): new path `participation` (attendance / registered / walk-ins / cancelled /
       mix / guideline / evaluation / ratings / response rate words, WITHOUT a file word, a document type or a
       "says / according to" word, and either an activity named by its title or neither an activity word nor a period)
       -> Gemini with find_activities + the four participation tools. Checked after the budget rule and before the
       content rule ("the evaluation results of X" is answered from the records; "What does the evaluation form say?"
       still searches the files). The document-status path also offers `get_pending_approvals` ("Which submissions are
       waiting for my approval?"). Phrasing tests in `assistant/tests/test_record_tools.py`.
     - **The chat box** (owner feedback "too small"): the shared `components/chat_box.html` + `static/js/chat_box.js`
       (loaded in `base.html` before Alpine; a page mixes `tbiChatBox.state()` into its x-data and provides `busy`;
       Part 2's generator uses the same component). It starts 4 lines tall (`.chat-box-input`, min-height 6.5rem),
       grows with the text up to 40% of the screen height (`max-height: 40dvh`) and then scrolls inside; full width of
       the conversation column with the activity filter and Send on one row below it. Keys (owner decision): Enter sends,
       Shift + Enter is a new line, **Ctrl / Cmd + Enter also sends**; on phones (touch, no hover) Enter is a new line and
       the Send button sends (44 px tall). The 2,000-character limit stays; a counter appears from 1,800. 16 px text on
       phones (no zoom on focus). Behaviour only: the server still checks the length and refuses an empty question.
     - **The page fits the screen on phones too:** below 1024 px the panel is the screen's height (`max-lg:h-dvh`, so it
       shrinks with an on-screen keyboard that resizes the page) and only the messages scroll; My chats starts closed on
       phones (a saved choice wins) and opens above the chat (at most 40% of the height). 1024 px and up: item 380 as
       before.
     - Tests: `assistant/tests/test_record_tools.py` (every tool's numbers with the cancelled rule, no names, own inbox,
       routing and offered tools), `test_chat_features.py` (help text, keys, counter, script rules, phone layout classes),
       `test_pages.py`; opt-in Chromium `assistant/tests/test_chat_browser.py` at 1366 and 390 px (starts about 4 lines,
       full width, one row below, grows to at most 40% then scrolls inside, nothing hidden, no sideways or page scroll,
       counter near the limit, Ctrl + Enter sends, Enter is a new line on a phone, Send 44 px, My chats closed on a phone).

440. **Cancelled registrations do not count as registered in the reports** (owner decision 2026-11-25; **amends item
     434's** "the reports' registered numbers still count every registration").
     - **The rule** (`reports.metrics.COUNTED` / `NOT_COUNTED` / `counted_registrations()`, the one place): registered =
       not cancelled, OR attended at least one day (a cancelled person who came anyway counts as registered AND
       attended). Walk-ins count. "Cancelled" = cancelled and never attended.
     - **Where:** every report and dashboard number (`period_summary` + `cancelled`, `activity_rows` + `cancelled`,
       `by_type`, `by_category`, `per_month`, `participants_by_category`, the participant mix, `activity_evaluation_results`
       / `activity_evaluation_summary` + `cancelled`, new `activity_registration_counts`), so the attendance rate and the
       mix use it too; Needs attention (`activities.attention`, "No one registered" when only cancellations). The report
       summary and the evaluation summary show "Cancelled ("Will not attend", not counted …)" as their own row.
     - **Unchanged:** the slot rule (`activities.services.registered_count`, item 434: every cancellation frees its slot,
       also one who came later), the Overview's Will attend / Will not attend (what people signified), certificates.
     - Tests: `reports/tests/test_cancelled_registrations.py`; `assistant/tests/test_record_tools.py`.

## NAVIGATU UAT, Part 2: the report generator, the Activity Report and the Evaluation Summary (2026-11-26)

441. **The AI Assistant's layout, corrected** (owner feedback 2026-11-26: item 439's request was misdescribed; the
     CONVERSATION was too small, not the typing box). **Replaces item 439's "4 lines / 40%" and its filter row.**
     - The conversation (messages and suggestion chips) fills everything between the chat header and the typing bar:
       at least about 70% of the chat panel (measured: 76% at 1366 × 768, 73% at 390 × 844).
     - The shared `components/chat_box.html` is now a compact bar: [the box][Send]. The box starts ONE line tall
       (44 px, `.chat-box-input`, min-height 2.75rem), grows with the text up to about 5 lines (`max-height:
       calc(5lh + …)`), then scrolls inside; an empty box is always one line (`static/js/chat_box.js`: a wrapped
       placeholder never counts). Send is an icon button on the RIGHT, on the same row (`.chat-send`, 44 × 44 px). The
       counter shows inside the bar only from 1,800 of 2,000. The help text is for screen readers only.
     - The activity filter moved to the chat header (a small pill select next to the title, `form="ask-form"`, sent with
       the question); "Stop waiting" moved next to the typing indicator. Keys unchanged: Enter sends, Shift + Enter new
       line, Ctrl / Cmd + Enter sends, phones: Enter = new line and Send sends. Only the messages scroll.
     - The report generator's draft page uses the same bar.
     - Tests: `assistant/tests/test_chat_features.py`; opt-in Chromium `assistant/tests/test_chat_browser.py`
       (`test_the_compact_chat_bar_at_1366_and_390`: conversation ≥ ~70% of the panel, one line at the start, growth
       stops at ~5 lines then scrolls inside, Send on the right and 44 px, the filter in the header, no sideways or page
       scroll, counter near the limit only, Ctrl + Enter sends, Enter is a new line on a phone, My chats closed on a
       phone).

442. **The report generator** (NAVIGATU UAT: "make their work easier"; owner decisions 2026-11-25). A page
     `/reports/generator/` (sidebar "Report generator"), open ANYTIME (never locked behind documents), like a chat:
     - **Who:** can_create_activity, or an open "Prepare a report" assignment (item 437, `require_permission_for`). A
       draft is its maker's only (404 for anyone else); the Director sees what is submitted to them.
     - **A draft:** what to make (Activity Report, Evaluation Summary, or "Other" free form; Prompt 7 added the
       Accomplishment Report and the draft Request Letter / Activity Design, items 446-448), one activity (always for the
       Activity Report) OR a period (both dates; the draft letter / design: neither, typed details, item 448), optional notes,
       a title and the office line / implementing unit / project leader, and files: "for the AI to read" (program,
       speaker list, notes; the document upload checks, by content) or "photos for the report" (JPG / PNG, NEVER sent to
       the AI). At most `REPORT_GENERATOR_MAX_ATTACHMENTS` (10). Stored as `report-drafts/<year>/draft-<id>-<random>.<ext>`;
       a failure stores nothing; downloads only through `reports:generator_file` (the maker, attachment, no-store).
     - **Versions:** a Django-Q2 task (`reports.tasks.run_generate_draft`) writes version 1 (progress shown, the page
       polls every 3 s and reloads when it is done); each follow-up message writes the next version from the previous one
       (one at a time); the member may edit a section by hand (a new version, "Edited by the member"); "Try again" after a
       failure. Every version is kept (REPORT_DRAFT_VERSION), with the conversation (REPORT_CHAT_MESSAGE).
     - **The numbers rule (`reports/generator.py`):** every number, date, name list and amount is a FACT collected from
       the database and the generated forms when the version is written (`reports/draft_facts.py`, stored on the version:
       a version always prints what it showed). The AI writes only the narrative sections and puts figures in as
       `[[placeholders]]` that our code fills. A number the AI wrote itself that is not among the facts, or an unknown
       placeholder, is listed "Not from the system" on its section; export, save and submit are refused until the
       member edits that section (their own text is theirs) or asks for a new version.
     - **What the AI reads:** the activity's / period's files through their indexed text (documents in any review state,
       approved submissions; item 438), the draft's document attachments (text, or OCR for a scan / an image: the member
       chose "for the AI to read"), the notes, the resource persons and topics, objectives, counts, the budget lines'
       particulars and the mix. NOT the participants' names (not needed for the narrative), never photos, never signature
       images. For the Evaluation Summary also the written answers and comments, without names.
     - **Without the Gemini key:** a version is still made: every fact, table, photo and the annex; the narrative sections
       are empty for the member to write; exports, save and submit work.
     - **"AI-generated draft – check before use"** on every AI-written section in the editor only, never in an export.
     - **Exports** (`reports/draft_export.py`): DOCX (python-docx, editable; the annex name lists as tables, no signature
       images) and PDF (WeasyPrint, A4 portrait, `reports/templates/reports/draft_pdf.html`). **Save to the activity's
       documents:** the PDF through `upload_document` as an approved copy (item 432) of the Activity Report type (other
       kinds: "Other"), once per version, only for one activity. **Submit to the Director:** item 437's
       `submit_generated` (PDF, stampable), once per version.
     - **Database (43 -> 49 tables, migration `reports/0002_report_generator`, schema only):** REPORT_DRAFT,
       REPORT_DRAFT_VERSION, REPORT_CHAT_MESSAGE, REPORT_ATTACHMENT (+ PAPER_SHEET_READ, PAPER_SHEET_ROW, item 443).
       Drafts CASCADE with their activity (item 389's dialog and ACTIVITY_DELETION.counts now list `report_drafts` and
       `paper_sheets`; files removed after the commit by a `post_delete` receiver, `ReportsConfig.ready`).
     - Tests: `reports/tests/test_generator.py`; opt-in Chromium `reports/tests/test_generator_browser.py` (1366 and 390
       px: the compact bar, the conversation most of its card, the Chat / Draft switch on a phone, nothing sideways).

443. **Paper attendance sheets read by the AI** (owner decisions 2026-11-25; DTI and partner agencies count as MSMEs:
     confirmed 2026-11-26).
     - Staff choose a scanned Attendance / Confirmation of Attendance (an activity document of those types, PDF / JPG /
       PNG, from its document page; or a scan attached to a draft about one activity) and click "Read the rows with AI".
       That click is the per-file consent of item 438 (for a document it also sets its "Read this scan with AI" tick). A
       task (`run_read_paper_sheet`) asks Gemini for the rows (name, designation, office) as JSON; our code maps each
       office to a category by `settings.PAPER_SHEET_CATEGORY_RULES` (whole words, case ignored, first rule wins: CSU /
       faculty words -> Faculty, incubatee / startup -> Incubatees, DTI, partner agencies, enterprises -> MSMEs;
       DTI and partner agencies as MSMEs is confirmed, 2026-11-26).
     - Staff check every row on `/reports/paper-sheets/<id>/` (name, designation, office, category, "Not a participant",
       "Add a row"; who and when per corrected row), see the counts and percentages (`metrics.paper_sheet_mix`: the mix
       groups and NAVIGATU's guideline), then **Confirm** (refused while an included row has no name or category). A
       confirmed sheet is locked until "Reopen". Only CONFIRMED sheets are used, and only in the generated reports
       ("From the paper sheet": rows and mix in the Activity Report's annex); never added to REGISTRATION, never in the
       dashboard or the period reports (except the generated Accomplishment Report's attendance, item 447). Without the
       key or when nothing is read: the rows can be typed by hand.
     - Database: PAPER_SHEET_READ (exactly one of document / attachment; confirmed with who and when) and PAPER_SHEET_ROW.
     - Tests: `reports/tests/test_paper_sheets.py`.

444. **The Activity Report (= Narrative Report), per activity, following NAVIGATU's sample.**
     - Placeholder letterhead (replaced by the CSU letterhead, item 469), "ACTIVITY REPORT",
       the optional office line, the title; Period Covered (the activity's dates), Venue, Implementing Unit
       (`REPORT_IMPLEMENTING_UNIT` or typed), Project Leader (the activity's creator or typed).
     - A. Highlights of the Activity (AI) with "Major Accomplishments" bullets (AI); B. Problems/Constraints Met (AI);
       C. Running Budget = `activities.budget.budget_summary` (Particulars, Quantity, Unit Cost, Total Amount, Total, the
       fund source) and "Actual spending is reconciled with the liquidation."; D. Recommendation(s) (AI).
     - "Prepared by:" the member generating it with their POSITION (item 429); "Noted:" ALWAYS the TBI Director: the
       active signatory linked to an active Director's account (item 437; `Role.DIRECTOR` only says who), its name and
       position; a blank line to sign when none is linked.
     - Photo Documentation (6 a page): Event Photos documents (JPG / PNG), the draft's photos and the participants'
       photos from `photos_for_reports()` ONLY (ticked "Use in reports", item 435; an unticked one is never printed, also
       if it was unticked after the version was written); shrunk by Pillow, metadata dropped.
     - Annex: the participant mix (chart + table, the guideline), confirmed paper sheets, then (PDF) the activity's newest
       Attendance Sheet and Confirmation of Attendance documents appended (pypdf; a JPG / PNG scan as an image page),
       or the generated forms rendered in memory when there is none (never saved). DOCX: the same name lists as tables.
     - Footer: form code "F-COR-001 Rev. 3 10/06/2023" (`ACTIVITY_REPORT_FORM_CODE`).
     - Tests: `reports/tests/test_generator.py`; real WeasyPrint `reports/tests/test_report_pdf_layout.py`
       (`test_no_report_generator_pdf_is_cut_off`: nothing past the page or out of a cell; number headers may wrap).

445. **The Evaluation Summary Report, per activity and per period.** Numbers from `reports.metrics` only: registered (item
     440), cancelled, attended, evaluations, response rate; per rating question the answers, the mean and how many gave
     each rating 5..1 with shares (per activity `activity_evaluation_summary`; per period the new
     `period_rating_frequency` and `period_text_answers`); per period also the activities with their response rates and
     averages. No interpretation scale (still pending). The AI writes "Summary", "What the participants wrote" (from the
     written answers and comments WITHOUT names, sorted A-Z) and "Recommendations". The photos from
     `photos_for_reports()` as Photo Documentation. Same exports, save (one activity only) and submit.
     - Tests: `reports/tests/test_generator.py`, `reports/tests/test_period_evaluation_metrics.py`.

## NAVIGATU UAT, Prompt 7: the Accomplishment Report and the draft Request Letter / Activity Design (2026-11-26)

446. **The Accomplishment Report in the report generator** (NAVIGATU's "3rd Quarter Accomplishment Report" sample). A new
     REPORT_DRAFT kind `accomplishment_report` (item 442 reused: versions, the numbers rule, DOCX / PDF, submit).
     - **Period:** a quarter (Q1-Q4), a half (January-June, July-December) or a whole year, plus the year; the dates come
       ONLY from `reports.services.preset_range` (the Reports page's presets, item 399; `generator.accomplishment_dates`).
       A custom range or an activity is refused (`PRESET_REQUIRED`, `PERIOD_ONLY`).
     - **Header:** the letterhead (item 469; was a placeholder), "<1ST/2ND/3RD/4TH> QUARTER ACCOMPLISHMENT REPORT" /
       "SEMI-ANNUAL ACCOMPLISHMENT REPORT" / "ANNUAL ACCOMPLISHMENT REPORT" (`draft_facts.accomplishment_heading`), the
       program or unit name (REQUIRED, typed: CSU has two TBIs, NAVIGATU and TARA; `options.program_name`; the form starts
       with the member's last value, `generator.last_options`, no column), "Period Covered: July–September 2026"
       (`draft_facts.months_covered`).
     - **Sections:** A. Highlights of Accomplishments = an introduction (AI), "Figures for the period (from the system)"
       and one numbered subsection A.n per Conducted activity ending in the period (date, venue, categories, registered,
       attended, then the AI's highlights); B. Accomplishments vis-à-vis Objectives = a two-column table: the activity's
       Objectives field (item 428) or, when it is empty, an AI section "Objectives (read from the Activity Design)"
       (marked "Read from the Activity Design." in the export), and the AI's accomplishments per activity; C. Major
       Outputs / Deliverables (numbered); D. Problems / Constraints Met; E. Recommendations / Next Steps (numbered). The
       per-activity sections depend on the facts: `generator.section_specs(kind, facts)` replaces the fixed
       `SECTIONS[kind]` everywhere (edit, export check, prompt, exports, page); keys
       `activity_<id>_highlights|_objectives|_accomplishments`.
     - **Figures (reports.metrics only):** activities conducted, registered (cancelled not counted, item 440), cancelled,
       attended, evaluations, response rate, average rating, ratings per question, the participant mix, the budget per fund
       source (N20) and its total (N19); per activity from `activity_rows`. Placeholders `[[attended]]`,
       `[[act<id>_attended]]`, `[[budget_raf]]` ...; any other number the AI writes is "Not from the system" (item 442).
     - **Photos:** the Activity Report's rules (Event Photos documents, the draft's photos, `photos_for_reports()` only),
       6 a page. "Prepared by" the member + POSITION; "Noted" the Director's linked signatory; footer F-COR-001 Rev. 3.
     - **Not saved to the documents** (no single activity); Submit to the Director is offered.
     - **The older Accomplishment report on the Reports page (GENERATED_REPORT, item 399) stays** (owner: nothing is
       removed without asking). They are linked, not merged: the Reports page makes the figures-and-tables report
       (PDF / Excel); the generator makes NAVIGATU's narrative report. The Reports page has a note linking to the generator
       (`?start_kind=accomplishment_report`); both read the same `reports.metrics`, so the counts agree (parity test),
       except attended when confirmed paper sheets are used (item 447), which the report says in words.
     - Tests: `reports/tests/test_accomplishment_report.py`; real WeasyPrint `test_report_pdf_layout.py` (the year and a
       quarter of the demo data); Chromium `test_generator_browser.py` (`test_the_new_draft_form_for_every_kind_at_1366_and_390`).

447. **Confirmed paper Attendance sheets in the Accomplishment Report's attendance** (owner decision 2026-11-26; amends
     item 443's "never in the period reports" for this report only). `metrics.attendance_with_paper_sheets(activity_ids)`:
     - Only CONFIRMED sheets of kind **Attendance** count. A Confirmation of Attendance sheet means "will attend", never
       attended: it never replaces attendees.
     - **Per day, not per activity:** a day with a confirmed Attendance sheet uses that sheet's included rows (several
       sheets for one day add up); the other days use the system's attendance (ATTENDANCE_DAY). A sheet without a day
       counts only for a one-day activity.
     - **Each person once per activity:** system attendees by email (case ignored); a paper row by name + office (case and
       spacing ignored; paper has no email); a paper row matching a system attendee's name + office is that person.
       Attended people without any day row (older data) count while at least one day comes from the system.
     - The report's mix is then taken over those people (`metrics.paper_sheet_mix`); without any confirmed sheet it is
       exactly `period_participant_mix` and attended is exactly the system's count (parity test).
     - Labelled: each activity lists "Day 1 (date): N – attendance from the paper sheet" / "... from the system"; the
       report says "Attended includes the confirmed paper attendance sheets of N activities (they replace the system's
       attendance for their day); the system alone recorded M. The Reports page counts the system's attendance only."
       Registration counts never change; nothing is written to REGISTRATION or the dashboard.

448. **The draft Request Letter and the draft Activity Design** (kinds `request_letter`, `activity_design`), made possibly
     BEFORE the activity exists. *(Open question, CLAUDE.md: NAVIGATU's own templates.)*
     - **About:** a Draft or Scheduled activity (listed Draft first: staff revise an Activity Design after a date or
       budget change; its fields are read again for every version) or **typed details** (title, dates, optional times
       hh:mm, venue, participants, objectives one per line, description, fund source, budget lines checked by
       `activities.budget.clean_lines`; `generator.clean_proposal`, stored in `REPORT_DRAFT.options["proposal"]`). A
       period is refused (`NO_PERIOD_HERE`), another status too (`PROPOSAL_ACTIVITY`).
     - **The REPORT_DRAFT rule changed** (migration reports/0003, check constraint `report_draft_activity_period_or_typed`
       replaces `report_draft_activity_or_period`): one activity and no dates, OR no activity and both dates (end ≥ start),
       OR (only for these two kinds) neither.
     - **Request Letter:** a memorandum: the letterhead (item 469), "MEMORANDUM", FOR / THRU / FROM names and titles typed by
       the member and remembered from their last letter (FROM defaults to the project leader typed, else the Director's
       linked signatory), SUBJECT "Request for the conduct of <title>", DATE = the day the version was written; the body
       (AI); "Recommending Approval" (the THRU person) and "Approved by" (the FOR person). An empty name prints as a line.
     - **Activity Design:** the top table (Title, Duration, Date, Time, Venue, Participants, Funds), I. Rationale (AI),
       II. Objectives (the activity's or the typed ones; the AI writes them ONLY when none were given), III. Expected
       Outputs (AI), IV. Budget (Particulars, Quantity, Unit Cost, Total: the activity's `budget_summary`, or
       `budget_table` of the typed lines; totals always computed by `activities.budget`), three signature blocks:
       Prepared by (the member + POSITION), Recommending Approval (the Director's linked signatory), Approved by (typed).
     - **Numbers rule:** every date, name and amount comes from the activity or from what the member typed (facts and
       placeholders `[[title]] [[dates]] [[venue]] [[participants]] [[budget_total]] [[fund_source]]` ...).
     - **NEVER saved to the activity's documents** (an approved copy would complete the checklist and Schedule the
       activity, item 431, before anything was approved on paper): `generator.save_refused_reason` = `DRAFT_NOT_SAVED`,
       no Save button (a note instead). Submit to the Director IS offered, without a document type, and Approvals never
       saves such a submission either (`approvals.services.allowed_actions()["save"]` False, `["draft_only"]` True,
       `save_to_documents` refuses with `SAVE_DRAFT_ONLY`; `generator.is_draft_only_submission`). Staff download the
       DOCX, print and route it, then upload the approved scan as usual.
     - Neutral templates until NAVIGATU gives theirs (open question).
     - Tests: `reports/tests/test_draft_letters.py`; real WeasyPrint `test_report_pdf_layout.py` (a typed letter, a typed
       design, a demo Draft / Scheduled activity's design).
     - Database: no new table or column (still 49 tables); migration reports/0003 (kind choices, the `options` help text,
       the constraint).

449. **`python manage.py tailwind build` in the OneDrive folder.** The pinned CLI failed with "EEXIST: file already
     exists, mkdir '...\static\css'" whenever the CSS CHANGED (identical output is never written, which is why it seemed
     to work sometimes). The command now writes to a temporary file (`%TEMP%\tbi-tailwind-app.css`) and copies it into
     `static/css/app.css`; the manual steps are in CLAUDE.md ("Commands"). `tailwind watch` still writes directly.

## Owner feedback before the user testing, Part 1: small fixes (2026-11-26)

450. **Enter never submits a long staff form** (owner). `static/js/form_keys.js` (loaded on every page by `base.html`,
     self-hosted): inside a form marked `data-enter-submit="off"`, Enter in a text, date, time, number or select box does
     nothing; only clicking the submit button (or Enter / Space ON it) submits. A textarea still makes a new line;
     buttons, links and the shared chat box (its own keys, item 441) are untouched. Marked: New / Edit activity (with its
     budget lines and typed dates / times), the Forms tab's field editor, the per-activity "Certificate text" and
     `/certificates/text/`, the report generator's New draft, Approvals' "Submit to the Director" and the Assign dialog,
     the Signatories "Add a person" (page and inline) and "Edit", the paper-sheet rows and the staff "Add a walk-in".
     NOT marked: login, the search boxes, the Reports prompt box (Enter sends, item 384), the AI chat and every public
     page. Display help only: the server checks every value. Tests: `config/tests/test_owner_feedback_part1.py`; Chromium
     `registrations/tests/test_part1_browser.py` (also items 453, 454).

451. **TRF is the "Trust Fund"** (owner: not "Thrust Fund"). `accounts/seed.py`, ASSUMPTIONS 433, CLAUDE.md and the test.
     Guarded data step in migration `activities/0016`: only a TRF row still named "Thrust Fund" is renamed (a name the
     Admin changed is left alone; reversible). History rows written before keep their old text (append-only).

452. **Close / open the evaluation by hand** (owner; same pattern as item 427). New `ACTIVITY.evaluation_closed_by_hand`
     (default False = the old rules; migration `activities/0016`), changed ONLY by
     `activities.services.set_evaluation_closed_by_hand` (can_create_activity or the activity's assignment; Ongoing or
     Conducted; History row "Evaluation": "Closed by the TBI" / "Reopened by the TBI"). It works also without a deadline
     (item 423). It is read ONLY by `registrations.services.evaluation_has_closed` / `evaluation_open_q` (the one
     "closed" rule), so everything follows: the personal link and the API refuse new answers ("The TBI closed the
     evaluation of this activity…", `EVALUATION_CLOSED_BY_TBI`), the general link shows closed, no links are sent (staff or
     automatic), Needs attention treats it as closed. Opening again only removes the hand closing (the deadline still
     applies). Evaluation tab: "Close the evaluation" / "Open the evaluation again" + an amber note while closed.
     - **Certificates** ("No evaluation = no certificate", unchanged): while closed, an attendee who has not evaluated
       gets "Evaluation closed without an answer" (`certificates.services.readiness`): no certificate is approved, issued
       or emailed for them. Opening it again puts them back to "Waiting for evaluation". Evaluations already submitted,
       approved and issued certificates are never touched.
     - Tests: `registrations/tests/test_evaluation_closed_by_hand.py`.

453. **The three signature choices are equal and visible** (owner: "Upload a picture" was easy to miss). The shared
     `public/_signature_field.html` shows three tiles (radio + icon + one line of help): **Draw** ("With your finger, a
     pen or the mouse"), **Upload a picture** ("A photo of your signature on paper (JPG or PNG)"), **Type my name** ("Your
     full name, used as your signature"); one row from 640 px, stacked on phones; the chosen tile is highlighted. Used by
     the public registration, the QR check-in, the QR walk-in and the staff "Add a walk-in"; the staff "Mark attended"
     dialog keeps Draw / Type (staff=True). The rules (`signatures.clean_signature`) are unchanged.

454. **The QR check-in window** (owner). Before: each activity day from `PARTICIPANT_CHECKIN_EARLY_MINUTES` (60) before
     the start time until the END time (Scheduled or Ongoing; without times the whole day). Now: from 60 minutes before
     the start until `PARTICIPANT_CHECKIN_LATE_MINUTES` (60, new setting) AFTER the start time, each day
     (`registrations.attendance.day_window`); without times still the whole day. After today's window the check-in page
     says "Check-in for today closed at 9:00 AM. Please ask the TBI staff at the registration desk to mark your
     attendance." (`CHECKIN_CLOSED_TODAY`); QR walk-ins close with it. Staff marking by hand is unchanged
     (`attendance_closed_reason`). Tests: `registrations/tests/test_checkin_window_and_staff_walk_in.py`.

455. **Staff "Add a walk-in"** (owner decision 2026-11-26, because QR walk-ins now close an hour after the start). The
     Registrations tab's "Add a walk-in" (can_create_activity or the activity's assignment) opens
     `/registrations/activities/<id>/walk-in/`: the participant fills it in on the staff device: full name, email,
     contact number, Designation / Position, Office / Agency, the participant category, the signature (draw, upload or
     typed name: the three tiles of item 453) and the privacy consent (`services.check_privacy_consent`). The rule is
     `registrations.attendance.add_walk_in_by_staff`: the same checks as the QR walk-in (`clean_registrant`, signature
     required, the same email refused, no slot limit, also when registration is closed), on any day of the activity
     while it is **Ongoing**; it marks TODAY's attendance with method "staff" (`marked_by` = the member) and writes a
     History row "Walk-in" ("<name> added by staff, attended <date>"); the check-in email is queued as for a QR walk-in.

456. **No Director-to-self actions** (owner). Decided by flags, never by the role's name:
     - "Submit to the Director" (Approvals button, the "My submissions" empty state, the Documents tab link, the report
       generator's Submit) is shown only when somebody ELSE could approve it (`approvals.services.offers_submit` = an
       active member with can_approve_documents other than you). That is exactly the case `submit()` already refused
       (separation of duties: never your own), so the Director never sees it, while a second approver (a stand-in) still
       can submit to the Director.
     - "Assigned to me" (Approvals tab) is hidden for a member with can_assign_work who has never been given work
       (`approvals.assignments.shows_assigned_to_me`); the Dashboard card already appears only with open assignments.
     - Both are on the staff layout (`layout.offers_submit`, `layout.shows_assigned`). Separation rules unchanged
       (`accounts/tests/test_separation_of_duties.py`, `approvals/tests`).

457. **The certificate number and the recipient's name open its preview** (owner). On the Certificates page
     (`certificates/_table.html`: `certificates:preview` in a new tab) and on the Certificates tab (the same large preview
     as the row's Preview button; without JavaScript its preview page). The preview of an Issued certificate is the
     stored final PDF (item 358). The existing buttons stay.

458. **The startup portal is removed** (owner, before the user testing: a Chapter 5 recommendation). Removed: the portal
     (login, pages, layout, sidebar, set-password pages and emails, "My certificates"), the staff "Startups" pages and
     the Admin Console link, the Navigatu sync (StartupProvider, the mock data, the 7:00 schedule, `sync_startups`,
     `create_review_startup`, `STARTUP_*` settings, `.env.example` lines), the search group, the login-page link, the
     N18 "Startups engaged" number (dashboard card, Summary row) and the Participation report's "Startups engaged"
     section (older GENERATED_REPORT rows that list it are read safely: `content.ordered_sections` skips unknown keys;
     their files stay as they are).
     - **Database (49 -> 48 tables):** table STARTUP; columns REGISTRATION.startup_id and NOTIFICATION.startup_id; the
       check "notification has exactly one recipient" (NOTIFICATION.member_id becomes required); the value "portal" of
       REGISTRATION.registration_source (now online / walk_in).
     - **Registrations are never lost:** migration `registrations/0015` turns every "portal" registration into an
       "online" one (name, email, answers, attendance, evaluation and certificates unchanged) and prints how many, then
       drops the column. `notifications/0005` deletes the notifications addressed to a startup (only the portal showed
       them; they have no recipient any more; it prints how many); member notifications stay. `startups/0003` removes
       the sync schedule row and drops STARTUP. The `startups` app stays installed as an empty shell (apps.py, an empty
       models.py, its migrations) only so this migration runs on existing databases.
     - Demo data: a few representatives of invented incubatee teams register online.
     - Tests: `registrations/tests/test_migration_startup_portal_removed.py`.

459. **Green theme** (owner: "close to CSU's green", same layout and gradients). The logo files in `static/img/` have no
     CSU green (a teal mark #2ABAB2 and white strokes on the navy tile), so the proposed palette is used: only the tokens
     between OPTION START / END changed: frame `#0F4A2C` -> `#3F9A6B`, primary `#1F6B3E` (hover `#195A34`, pressed
     `#144A2B`, soft `#E6F1EA`, tint `#D2E6D9`), link `#1E7244`, on-dark muted `#D4EEDD`, accent `#9FDDB8` (white on the
     primary 6.5:1, primary text on the panel 6.0:1, links 5.5-5.9:1, primary on its soft 5.6:1: every text pair at least
     4.5:1). Everything else derives from the tokens (sidebar, focus ring, shadows, gradients). Chart colours: the first is
     the primary green and the old green slot became the former navy (`CATEGORY_COLORS`, colour-blind check). The logo
     tiles are re-exported on `#0F4A2C` (the mark at 72%, as item 256). The generated PDFs (report, draft, one
     evaluation, attendance forms), the Excel header (`excel.BRAND_NAVY`, name kept) and the DOCX headings
     (`draft_export.DOCX_HEADING_COLOR`) follow the same green. Status colours (info blue, success green, …) unchanged.
     Real-WeasyPrint check: `reports/tests/test_report_pdf_layout.py`.

## Owner feedback before the user testing, Part 2: assigned work, one Reports page, For payment (2026-11-26)

460. **All the Director's work can be assigned** (owner; extends item 437, same WORK_ASSIGNMENT and helpers). Every
     Director-only action is listed in `approvals.assignments.ASSIGNABLE_ACTIONS` with the kind that gives it (a test
     keeps the list and `KIND_PERMISSIONS` in step):

     | Director-only action | Assignment kind |
     |---|---|
     | Approve or return a document that needs review | document_review |
     | Rename, replace or delete another member's waiting document | document_review |
     | Approve or return a submission (never your own) | submission_review |
     | Stamp the Director's signature on a submission | submission_review + "May stamp my signature" |
     | Approve an activity's certificates; reissue its outdated certificates | certificate_approval |
     | Choose an activity's signatories and their titles | signatory_choice |
     | Edit the default certificate text | **certificate_text (new, no object)** |
     | Manage the signatory list (add, edit, images, deactivate, defaults) | **signatories (new, no object)** |
     | Close / reopen registration and the evaluation, edit another member's activity, the activity's evaluation answers, links, photos and attendance forms | activity |
     | Prepare any report (figures report or report generator) | report |
     | Unapprove a document / a submission / an activity's certificates | never (the Director's own decision) |
     | Link a signature to an account, change a signature linked to an account | never (it is the Director's stamp) |
     | Assign work | never |

     - **Two new kinds without an object:** `certificate_text` (gives can_approve_certificates for the default text only:
       `save_default_text` uses `require_permission_for(..., None, kind="certificate_text")`) and `signatories` (gives
       can_manage_signatories for the list only; a signature linked to an account stays the Director's:
       `certificates.services.LINKED_SIGNATURE_DIRECTOR_ONLY`; never to a can_manage_users member, like the flag). One open
       assignment of each at a time (`one_open_assignment_per_global_task`); done by hand ("Mark as done"). Views use the
       new `accounts.permissions.task_permission_required(permission, kind)`; the Signatories and Default certificate text
       pages have the Assign button.
     - **The per-activity actions accept the activity's assignment** (`require_permission_for(member, perm, activity)` and
       `object_permission_required` on the views): one participant's evaluation and its PDF, resending evaluation links,
       participant photos ("Use in reports", view, download), the generated attendance forms, and Apply the current
       design / Mark handed over / Resend / Retry on the Certificates tab; reissue accepts the certificate approval
       assignment (also when the activity is approved but has outdated certificates).
     - **Signing on behalf of the Director:** the Assign dialog of a submission has "May stamp my signature"
       (WORK_ASSIGNMENT.allow_signing, only on a submission review: check constraint
       `work_assignment_signing_only_for_submissions`; only when the assigner's account is linked to a signature with an
       image). Ticked: the assignee may approve WITH signing; the stamp is the ASSIGNER's signature
       (`approvals.services.stamp_owner`), never the assignee's own. Logged: SUBMISSION_EVENT "signed" with
       `on_behalf_of` = the Director, an activity History row "Submission: ... signed by <assignee> on behalf of
       <Director>", `approvals.audit`, and a notification to the Director ("... stamped your signature ..."); the
       submission page says "on behalf of". Not ticked: the assignee approves without signing (`SIGN_NOT_DELEGATED`).
       Still never your own submission, still a stamped image (not a cryptographic signature).
     - Database: WORK_ASSIGNMENT.kind gains `certificate_text`, `signatories`; new column WORK_ASSIGNMENT.allow_signing
       (boolean, default false); migration `approvals/0003`. Still 48 tables.
     - Tests: `approvals/tests/test_assign_all_work.py`.

461. **One Reports page** (owner). `/reports/` merges the Reports page and the Report generator; the sidebar has one
     "Reports" item. "What do you want to make?" is ONE list (`reports/hub.py`, display only): Figures (PDF / Excel:
     accomplishment, participation, evaluation, document compliance, certificates; GENERATED_REPORT, rules unchanged in
     `reports.services`) and Written with AI (Activity Report, Accomplishment Report, Evaluation Summary, Request Letter,
     Activity Design, Other; REPORT_DRAFT, rules unchanged in `reports.generator`: versions, the numbers rule, save /
     submit, item 448). `?kind=` chooses the form (works without JavaScript); `/reports/generator/` (GET) redirects to
     `/reports/?kind=...` (its POST still creates a draft; a refusal comes back on the one page, 400).
     - **The two tables stay** (owner approved): GENERATED_REPORT (figures, built in the background, any staff downloads)
       and REPORT_DRAFT (the maker's own, versions). Merging them would mix two life cycles and every rule of 301 and 442.
     - **Notes and files can always guide the AI:** the figures form has "Notes for the AI summary"
       (GENERATED_REPORT.notes, max 2000) and "Files for the AI to read" (REPORT_ATTACHMENT rows now belong to exactly ONE
       of a draft or a figures report: `report_attachment_draft_or_generated_report`; checked like a draft's files, text
       only, no OCR). They are sent as data after `narrative.GUIDANCE_RULE`; the summary goes through the generator's
       numbers rule (`generator.check_numbers`): a number not in the report's data leaves the summary out ("the AI wrote
       numbers that are not in the report (...)"). Never a number from the notes.
     - **One list of past reports** (`services.past_reports`, newest first): every figures report (as before) and the
       member's own drafts (a draft stays its maker's only).
     - The prompt box and its summary card still fit 1366x768 (item 384): the kinds are compact chips in two rows, the
       group labels short ("Figures", "Written with AI").
     - Database: GENERATED_REPORT.notes (text, default empty); REPORT_ATTACHMENT.draft_id nullable + new
       REPORT_ATTACHMENT.generated_report_id (FK, CASCADE, nullable) with the "exactly one" check; migration
       `reports/0004`. Still 48 tables.
     - Tests: `reports/tests/test_one_reports_page.py`; browser `reports/tests/test_reports_page_browser.py`.

462. **The activity's Reports tab** (owner). A "Reports" tab on the activity page lists only the kinds that make sense for
     one activity (`hub.PER_ACTIVITY`: Activity Report, Evaluation Summary, Attendance form, Confirmation of Attendance,
     Request Letter, Activity Design, Other); each opens `/reports/?kind=...&activity=<id>` with the activity chosen
     (never the period-only figures reports or the Accomplishment Report). The attendance forms are generated there with
     the Documents tab's own rule and URLs (`registrations.attendance_forms`). Below: this activity's past reports (the
     member's drafts about it and the Activity Report / Attendance Sheet / Confirmation of Attendance documents saved on
     it). Without can_create_activity (or an assignment) the tab only lists. No database change.

463. **For payment per activity** (owner). The payment documents still needed are listed ONE CARD PER ACTIVITY, each with
     its own missing files (`documents.services.payment_missing`: Conducted activities, `document_checklist()`'s own items
     via `activity_document_overview`, payment types that count now and are not Approved; oldest due first). Each file has
     "Generate" when the system can make it (Attendance Sheet, Confirmation of Attendance, Activity Report:
     `reports.hub.PAYMENT_GENERATE_KINDS`; it only OPENS the Reports page with the activity and kind chosen, nothing is
     made without the member) or "Upload" (the Documents tab with the type chosen). The Evaluation Summary is NOT a
     payment document (owner); it stays on the activity's Reports tab. Shown on: the Dashboard card "For payment (Supply
     Office)" (`dashboard_page.payment_card`, at most 4 activities + the total), the Documents page view "For payment"
     (`?view=payment`, every activity; on a phone the switch shows "All" and no icons so the three choices fit 390 px)
     and the Generate buttons in the activity's "For payment" group. The Dashboard's Needs attention no longer lists
     payment documents by type: ONE line "N activities are missing payment documents" -> the For payment view. The
     activity page's own Needs attention line (item 425) is unchanged. No database change.
     - Tests: `documents/tests/test_payment_per_activity.py`; browser `reports/tests/test_reports_page_browser.py`.

## User testing day: testing database, darker green, logo, three-step Reports page, Assign everywhere (2026-11-26)

464. **The user-testing database (`seed_testing`)** (owner, for the UAT with 7 respondents). `python manage.py
     seed_testing --reset [--testing-date YYYY-MM-DD] [--checkin-start HH:MM] [--skip-ai-index]` fills an EMPTIED
     database with invented, realistic data that shows every feature, built ONLY through the real services (activities,
     registrations, attendance, signatures, evaluations with photos, documents, certificates, approvals, assignments, the
     report generator, paper sheets), so History, notifications and every rule are the real ones.
     - **A separate database and uploads folder:** `DATABASE_NAME` (new setting: the database Django uses in the same
       container; empty = `POSTGRES_DB`) and `MEDIA_DIR` (new: the uploads folder; empty = `media`). For the testing:
       `DATABASE_NAME=csu_tbi_testing`, `MEDIA_DIR=media_testing` (git-ignored); the development data and `media\` are
       never touched. Switching back = empty both lines.
     - **Safety:** only with DEBUG on and only with `--reset`, which first prints the database name and the uploads
       folder it EMPTIES (`flush` + the folder's contents); it refuses a MEDIA_ROOT that is the project folder, a folder
       above it or a drive. Repeatable: every run gives the same activities and states (new temporary passwords).
     - **Past events at their own time:** the command moves `django.utils.timezone.now` (the one clock every service
       reads, like the tests' `clock` fixture) for each event and puts it back at the end; `--testing-date` is "today".
     - **Accounts** (temporary passwords printed once, `must_change_password` off): System Administrator Regine Paclibar;
       Director Riah E. Encarnacion, DIT ("Director, TBI & Manager, NAVIGATU", signature image, linked signatory, the
       left default signatory); John Mark B. Correa, Jenelyn Asis, Teodoro Jr. Abrea (Incubation Project Head) and Mary
       Pilar Barber (Community Development Head) with can_create_activity; Meledy Ganoy and Adelberto G. Laplana (Admin
       Staff). No role is missing: "Manager, TARA" and "Portfolio Manager" are POSITIONS on the Incubation Project Head
       role (same flags); no new role. Emails `<first>.<last>@example.com`.
     - **Activities** (NAVIGATU and TARA, fund sources RAF / IGF / TRF / BRF, objectives, budget lines): a Draft without
       documents (flagged: uploading the two creation documents schedules it); a Scheduled one a week ahead with
       registration open, prepared certificates and an Invitation Letter waiting for review; one Ongoing yesterday and
       today from `--checkin-start` for 3 hours (QR check-in from 60 min before to 60 min after the start, 3 "Will not
       attend", day-1 attendance with drawn / uploaded / typed signatures and a walk-in, certificates prepared and
       approved early so a respondent who checks in and evaluates gets the certificate email at Conducted); four
       Conducted: complete (previous quarter: attendance per day, walk-ins, evaluations, photos with 2 ticked, generated
       Attendance and Confirmation forms, Activity Report, Event Photos, certificates approved / issued and emailed / one
       handed over, a confirmed paper sheet), waiting for certificate approval with the Attendance Sheet MISSING (For
       payment: Generate), an older complete one (the quarter before), and one ended 3 days ago with the evaluation
       closed by hand and the Activity Report / Event Photos missing and a scanned Attendance Sheet not yet read.
     - **The Director's work:** the Invitation Letter type set to needs review (a Django admin switch) with two documents
       waiting; submissions waiting (one to approve and sign), returned and unapproved (with its reason); one assignment
       of EVERY kind (one done, one overdue, one with "May stamp my signature"). Reports: three figures reports, an
       Activity Report draft with 2 versions and its chat, an Accomplishment Report for the previous quarter, a Request
       Letter draft. Then `setup_schedules` and the AI index (`index_pending_documents`, no OCR; skipped without a key or
       with `--skip-ai-index`: run `index_documents` later). The confirmed paper sheet is "read" from the rows the seed
       printed on its own invented scan (no AI call); the second scan is left for "Read the rows with AI".
     - **Nothing is emailed while seeding:** every email goes to an in-memory mailbox (counted at the end), queued
       confirmation / check-in / evaluation-link / chat-title tasks are dropped, certificate PDFs, reports and drafts run
       at once. **`EMAIL_BLOCKED_DOMAINS` is now `example.com`, `example.org`, `example.net` and their subdomains**
       (reserved names, RFC 2606; was only demo.example.com), so the seeded accounts and participants are never really
       emailed by the worker either, while the respondents' real addresses are (EMAIL_REAL_SENDING=True + qcluster).
     - Tests: `accounts/tests/test_seed_testing.py` (every state on an empty test database at a fixed date; twice).

465. **Deep forest green** (owner: "the green is too bright"; replaces 459's hues, same layout and gradients). Tokens:
     frame `#0B3D24` -> `#24603F`, primary `#1A5C38` (hover `#144A2D`, pressed `#0F3B23`, soft `#E8F0EB`, tint `#D3E4D9`),
     on-dark accent `#B3DCC4` (calmer). Measured: white on the primary 8.0:1, primary on the panel 7.4:1, on its soft
     6.9:1, on-dark muted on the frame 10.0:1. **The link colour is `#1E6B42`, not the suggested `#1A5C38`:** with the
     primary's value links were only 1.8:1 against the body text (the rule `test_links_stay_apart_from_body_text` wants
     more than 2:1 so links stay recognisable); `#1E6B42` is 6.0:1 on the panel and 2.3:1 to the ink. The report /
     draft / evaluation / attendance-form PDFs, the Excel header (`excel.BRAND_NAVY`) and the DOCX headings use
     `#1A5C38`; the first chart colour too.

466. **The logo in the new green** (owner). The tiles (`static/img/favicon-32.png`, `logo-96.png` = the sidebar and
     sign-in logo, `apple-touch-icon-180.png`) are re-made from `docs/design/logo.png` by `docs/design/recolor_logo.py`:
     the deep forest tile `#0B3D24`, the mark cropped to its drawing at 72 %, the teal arrow in soft gold `#D8B45C` and the
     strokes off-white `#F4F7F4`; the previous green tiles are kept as `docs/design/*-green-old.png`, the before / after
     sheet is `docs/design/logo-before-after.png`. The generated PDFs have no logo (only the placeholder letterhead text,
     open question "Letterhead"), so there is no PDF header logo to change.

467. **The Reports page in three steps** (owner: "too many ways to do the same thing"; replaces 384's prompt box and
     461's chips / type cards; the rules of 301, 399, 442-448, 461 unchanged). One card, one screen:
     1. **"What do you want to make?"** = ONE list (`reports/hub.py` `kind_choices`): every kind with a one-line
        description (`FIGURES_HELP`, `WRITTEN_HELP`, `FORMS`), captions Figures / Written with AI / Attendance forms;
        the attendance forms are now on the general page too.
     2. **"For which activity / period?"**: only what the kind needs, with defaults (`hub.generator_data`,
        `pages.empty_form`): the newest Conducted activity (`hub.latest_conducted`; the first Draft / Scheduled one for a
        draft letter / design), the current quarter and year (figures, Accomplishment Report, which also gets the
        remembered or a default program name, `DEFAULT_PROGRAM_NAME`), one activity or a period (Evaluation Summary,
        Other). Attendance forms: an activity picker (Ongoing / Conducted, newest first) posting to the new
        `reports:attendance_form` (the same `attendance_forms.generate_form`, 403 without the permission).
     3. **"Anything to add? (optional)"**: one notes box and Attach files (figures: notes + files for the AI summary;
        drafts: the notes and files for the AI).
     One **Generate** button. Format, included sections (all of the type by default), the activity type filter and the
     AI summary (on by default when the AI is available, so step 3 is used) are under a closed **"Options"** (drafts: the
     extra photos, title, office line, unit, leader, the memorandum's names, who approves). With the defaults a report
     takes three clicks: Reports, the kind, Generate (browser test). The activity's Reports tab still opens the page with
     steps 1 and 2 filled. **Removed:** "What report do you need?" (`reports/request_reader.py`, `fill_report_form`,
     `reports:fill`, the summary card, the example chips, the duplicate type cards and "Main" badges,
     `REPORT_FORM_FILL_*` settings). Lost: typing a sentence instead of choosing; the choices are now one list and two
     pickers, so nothing a report can contain is lost. Tests: `reports/tests/test_reports_three_steps.py`, browser
     `reports/tests/test_reports_page_browser.py`.

468. **Assign where the Director's work happens** (owner: the dialog seemed to offer only "Prepare a report").
     - Where Assign is: the activity page (Work on the activity, Approve the certificates, Choose who signs), a document
       waiting for review (Review the document), a submission (Review the submission, "May stamp my signature"), the
       Default certificate text page, the Signatories page, the Reports page (Prepare a report) - all since 437 / 460 -
       and NEW: **"Assign work"** on the Dashboard and the Approvals pages (every kind), and **Assign on each
       For-your-approval item** (certificate approval, submission review, document review; opens the general dialog with
       that work chosen; none on an overdue assignment).
     - The general dialog (`approvals/_assign_any.html`, form `approvals:assign_any`, loaded when opened so the pages
       stay fast; its own page without JavaScript): WHAT (`assignments.KIND_DESCRIPTIONS`, plain words), the target (only
       the work that can be assigned NOW: `assignments.general_choices` / `refused_now`, the same checks as
       `assign_work`; a kind with nothing open says why), the member, due date, note. Each kind's picker is
       `target-<kind>`; the `approvals:assign` view maps it to activity / document / submission.
     - **The work's own dialog now offers only what can still be assigned** (`refused_now`: open and not assigned yet),
       so it never offers work the service then refuses (e.g. an approved activity's certificate approval).
     - Tests: `approvals/tests/test_assign_general.py`; browser `approvals/tests/test_approvals_browser.py`
       (every kind from the Dashboard's dialog at 1366 and 390 px, the inbox's Assign).

469. **The CSU letterhead on every office-style PDF** (owner, for the final defense; amends 434 and 444's placeholder
     header). One shared header, `templates/pdf/_letterhead.html` + `pdf/_letterhead_styles.html`, built by
     `config/letterhead.py` and repeated on EVERY page (a WeasyPrint running element), as in NAVIGATU's sample documents.
     - **From the CSU logo, never redrawn:** `static/img/letterhead/csu-logo.png` (seal + wordmark, supplied by the owner)
       is CUT into the seal and the "Caraga State University" wordmark at the first empty column where everything to its
       right stays in the wordmark's band of rows (`split_column`; a logo without such a gap is kept whole; no file = a
       text header). Then "Republic of the Philippines" above the wordmark, "Ampayon, Butuan City 8600, Philippines"
       (`LETTERHEAD_ADDRESS`) and "Competence Service Uprightness" (green initials) below it.
     - **The official marks** (Bagong Pilipinas, SOCOTEC ISO 9001, PAB) appear in a bordered box only when their files
       are in that folder (`bagong-pilipinas.png`, `socotec-iso9001.png`, `pab.png`); they are never drawn or imitated
       (open question, CLAUDE.md).
     - **Office line** under the letterhead, in capitals: the draft's own, else `LETTERHEAD_OFFICE_LINE` = "NAVIGATU –
       TECHNOLOGY BUSINESS INCUBATOR" (owner). The generator's Options suggest it and `LETTERHEAD_VP_OFFICE_LINE`
       ("VICE PRESIDENT FOR RESEARCH AND DEVELOPMENT, INNOVATION, AND EXTENSION": the photos' "EXTENTION" is a typo).
       The Accomplishment Report adds "ACTIVITY REPORT" under it on every page (NAVIGATU's sample).
     - **Footer:** the form code bottom left (F-COR-001 for the Activity / Accomplishment Report, F-PROC-COA-005 for the
       attendance forms; "Generated by CSU-TBI Track" on the figures reports and one participant's evaluation),
       **"SAMPLE – FOR SYSTEM DEMONSTRATION"** in the centre while `LETTERHEAD_SAMPLE_LINE` is on (DEFAULT ON, owner:
       turned off only after NAVIGATU approves the use of the CSU seal; env `LETTERHEAD_SAMPLE_LINE=False`), "Page X of
       Y" bottom right. Footer strings are CSS-escaped (`css_string`).
     - **Where:** every report-generator PDF (Activity Report, Accomplishment Report, Evaluation Summary, draft Request
       Letter / Activity Design, other), the generated Attendance / Confirmation of Attendance forms, one participant's
       evaluation (portrait), and the landscape figures reports with a COMPACT header (owner; still landscape, the side
       margins and `PAGE_CONTENT_WIDTH_MM` unchanged). The DOCX gets the logo and the same lines in its page header.
       **Not:** certificates (their own uploaded design), the stamped copy of a submission (the uploaded PDF), the
       Excel files (unchanged). Images as data: URIs only.
     - **Office format** (NAVIGATU's photos): A4 portrait, 25 mm side margins, 11 pt justified text, bold capital section
       headings, bordered tables with bold centred headers, STACKED signature blocks (label, room to sign, bold capital
       name, position). Activity Report: the title left in the header block; C. Running Budget opens with "The approved
       budget for the conduct of the … amounted to Twenty-Seven Thousand Pesos (₱27,000.00) …" (`reports/money_words.py`,
       Decimal, half-up; `facts["budget"]["total_words"]`, printed by the template, never written by the AI) and ends
       with the italic note about reconciling with the liquidation records. Accomplishment Report: C. opens with "The
       major outputs and deliverables accomplished during the third quarter include:" (`period_phrase`) and the numbered
       outputs end in ";" / "; and" / "." (`draft_export.listed_outputs`); NEW section **F. Overall Assessment**
       (`generator.SECTIONS`; versions written before it simply have no F).
     - No database, URL or permission change. Tests: `config/tests/test_letterhead.py`,
       `reports/tests/test_office_format.py`; the opt-in real-WeasyPrint layout check passes.

470. **`seed_defense`: the final-defense / testing-site mock data** (owner). `python manage.py seed_defense
     --yes-this-is-a-test-database [--today YYYY-MM-DD] [--index]` EMPTIES the connected database and uploads folder and
     fills them with a realistic year (January–December) of the Navigatu TBI. `seed_testing` (464) is unchanged; this
     command reuses its helpers (moved clock, in-memory mailbox, registrations, check-ins, evaluations) by subclassing it.
     - **Safety:** only with the flag AND a database whose name contains "test" or "demo" (never `csu_tbi_track`); it
       refuses a MEDIA_ROOT that is the project folder, a folder above it or a drive. Works with DEBUG off (the Docker
       testing site, item 471). Running it again rebuilds the same data: never duplicated.
     - **People:** all INVENTED (no real CSU / NAVIGATU official's name; a test checks the names from the sample
       documents), emails `@demo.example.com` (never emailed: `EMAIL_BLOCKED_DOMAINS`, 464), one account per role +
       a second Incubation Project Head and Admin Staff, unusable passwords (`changepassword`), no forced change; an
       invented University President and Vice President on the letters; drawn signature images
       (`make_sample_certificate_images`).
     - **Data:** 22 activities: 17 Conducted in every quarter (one ended four days before `--today`, evaluation closed by
       hand), one Ongoing (yesterday–tomorrow), two Scheduled (registration open with prepared certificates; one whose
       registration form is not opened yet), two Drafts (no documents; Activity Design only). Registrations, per-day
       attendance with signatures, walk-ins, "Will not attend", evaluations with photos, certificates waiting / approved /
       issued / handed over, budgets, resource persons, objectives. Story per activity (`DSpec.variant`): complete, an
       Invitation Letter returned, certificates waiting, Attendance Sheet missing (For payment: Generate), an Invitation
       Letter waiting for review, a scanned attendance sheet not read yet, evaluation closed by hand with the Activity
       Report and Event Photos missing.
     - **Documents on the letterhead** (`accounts/defense_documents.py`, `templates/defense/`): Activity Design
       (rationale, objectives, participants, program of activities, resource persons, budget table, Prepared by /
       Recommending Approval / Approved by), Request Letter (memorandum FOR / THRU / FROM / SUBJECT / DATE, tracking
       number "TBI-<year>-<00101…>" + its QR code bottom right), Invitation Letter (to a resource person), Pre-registration
       Form, Evaluation Form, Program of Activities ("Other") and plain memos for the submissions. The Attendance Sheet /
       Confirmation of Attendance are the system's generated forms and the Activity Reports come from the report
       generator (saved as approved copies), so they are the real outputs. All text PDFs (the AI assistant can read
       them); every one prints "SAMPLE – FOR SYSTEM DEMONSTRATION" whatever `LETTERHEAD_SAMPLE_LINE` says. Event photos
       are JPGs. The "Accomplishment Report" document type stays inactive (430): the quarterly reports are generator
       drafts (Q1–Q3; the last one submitted to the Director) and figures reports.
     - **No AI while seeding:** the report generator's narrative comes from a stand-in (`NarrativeGateway`: the seed's
       own text without numbers; every figure from the system through `[[placeholders]]`, so the numbers rule passes).
       Nothing is indexed unless `--index`; the summary reminds to run `index_documents` once GEMINI_API_KEY is set.
     - Tests: `accounts/tests/test_seed_defense.py` (safety, no real names, documents, the stand-in). The full seed is
       run by hand on a test database (it empties it).

471. **The testing site on the shared database server** (owner; amends 415's "no Cloudflare tunnel": the testing site
     runs on the researchers' laptop behind a Cloudflare quick tunnel). `deploy/docker-compose.shared-db.yml` on top of
     `docker-compose.prod.yml` + `docker-compose.laptop.yml` (project `csu_tbi_uat`): the prod file's own db container is
     off (profile "not-used"); release, web and worker connect to the DEVELOPMENT Postgres container (`csu_tbi_db`,
     127.0.0.1:5433) through `host.docker.internal`, to their own database `POSTGRES_DB=csu_tbi_testing` (uat.env);
     uploads in the separate volume `testing_media`. Needs Docker Compose 2.24+ (`!reset` / `!override`). While it is up,
     no `qcluster` (or `runserver`) on Windows against `csu_tbi_testing` (two workers would take the same tasks and write
     files to two places). Mock data: `uat exec web python manage.py seed_defense --yes-this-is-a-test-database`.
     docs/DEPLOY.md "Testing site (shared database server)"; tests in `config/tests/test_deploy_files.py`.
