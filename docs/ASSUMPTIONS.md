# Assumptions (to be validated with the TBI)

## Business rules

1. Registration has no approval step. Everyone who registers is registered.
2. When all slots are taken, registration closes. There is no waiting list.
3. Walk-ins are not allowed. Only registered people can be marked present or evaluate.
4. Evaluation is tied to the person (not anonymous). It opens after the activity ends and only for people marked present.
5. Certificates are emailed automatically after the certificate is approved and the evaluation is submitted.
6. Participants get a Certificate of Participation by email. Resource persons get a Certificate of Appreciation in hard copy.
7. One certificate design is used for all activities. Admin manages it.
8. Documents that are uploaded must be reviewed and approved before they count as approved (For Review, then Approved).
9. Required documents for every activity: invitation letter, activity design, request letter, attendance sheet, accomplishment report. Due dates are placeholders until the TBI confirms them.
10. Missing-document reminders go to the person who created the activity, inside the app.
11. Navigatu startup data is read-only and reached through one mock provider for now. Startups are identified by email. *(Refined in Step 7, item 135: matched by Navigatu ID first, then by email.)*
12. Any TBI staff member can see every startup.
13. The admins are the researchers.
14. Certificate numbering format is decided by the researchers.

## Technical (Step 0)

15. The tech-stack document is `docs/Tech Stack for TBI Activity Tracker.docx` (added after Step 0; read in the Step 4 review). It matches `CLAUDE.md`: PostgreSQL + pgvector (documents, form definitions **as JSON**, and embeddings in one database), Django, RAG in Python, Gemini Flash and the Gemini embedding model, Django Templates + HTMX + Alpine.js (React optional, only for the AI Assistant), local file storage, WeasyPrint and openpyxl, Django email with Django-Q2 on the database, and local login now with Navigatu SSO later. *(Updated in the Step 4 review; it changes nothing in the plan.)*
16. Python 3.12 is used (3.14 is also installed, but some libraries may not have wheels for it yet).
17. Django 6.1.2 (latest stable at setup time) and the `pgvector/pgvector:pg17` Docker image (PostgreSQL 17).
18. Time zone is `Asia/Manila`. All datetimes are stored in UTC and shown in Manila time.
19. During development, emails are printed to the console. Real SMTP is configured in Step 9.
20. Default Gemini model names are `gemini-2.5-flash` (answers) and `gemini-embedding-001` (embeddings, requested at 768 dimensions). Both can be changed in `.env` without code changes.
21. The pgvector extension is enabled by `assistant/migrations/0001_enable_pgvector.py` (moved there from `accounts` in Step 1, see 29).
22. The Docker database listens on host port **5433** (not 5432), because another PostgreSQL already uses 5432 on the development machine.

## Models (Step 1) — decisions confirmed by the owner

23. TBI_MEMBER and STARTUP both use a hashed `password` field (Django's hashing). This **replaces the ERD's `password_hash` name**. On STARTUP it is nullable.
24. TBI_MEMBER also has Django's `last_login` and an `is_superuser` column. `is_staff` is **not** a column: it is a read-only property that is True only when the member's role has `can_manage_users`, so only Admin can open the Django admin site. Django's groups/permissions tables are not used (no PermissionsMixin); admin access is decided by the role.
25. ROLE, ACTIVITY_STATUS, PARTICIPANT_CATEGORY and DOCUMENT_STATUS have **no** `is_active` (no new columns). ROLE, ACTIVITY_STATUS and DOCUMENT_STATUS are never deleted (PROTECT) and the app depends on their fixed names. PARTICIPANT_CATEGORY is master data the Admin manages (see 84): the three seeded categories are never renamed or deleted; an Admin-added category can be renamed, and deleted only while no activity uses it. ACTIVITY_TYPE and DOCUMENT_TYPE keep `is_active`. *(Updated in the Step 4 review.)*
26. DOCUMENT.related_certificate is a nullable FK to CERTIFICATE (PROTECT). Documents with it set belong to the Certificates tab and are excluded from the document checklist and the Document Repository: use `Document.objects.activity_documents()` there, and `Document.objects.certificate_documents()` in the Certificates tab.

## Models (Step 1) — decisions made while building (reviewed by the owner)

27. Primary keys use the ERD names (`member_id`, `activity_id`, ...), and every table uses the ERD name in lower case (`tbi_member`, `activity`, ...). Keys are `bigint` (Django's default) instead of the ERD's `int`.
28. ACTIVITY_RESOURCE_PERSON has no primary key in the ERD. It gets `activity_resource_person_id` as its key, plus the unique (activity, resource_person) constraint.
29. The pgvector migration lives in `assistant`, not `accounts`. Django's admin/auth migrations depend on the **first** migration of the user model's app, so `accounts/0001` must create TBI_MEMBER.
30. App placement: NOTIFICATION lives in its own `notifications` app (see 42). DOCUMENT_EMBEDDING lives in `assistant`.
31. ERD `string` columns that can hold long text (`options` on form fields, `answer_value`, `chunk_text`) are stored as unlimited text. Other strings get set lengths (names/titles 255, contact numbers 30, emails 254). Optional strings are stored as an empty string, not NULL.
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
37. `manage.py createsuperuser` gives the new account the Admin role, so the roles must be loaded first (Step 2 seed command).
38. In the Django admin, lookups, signatories and templates have no Delete button, and the activity audit log is read-only.
39. Lookup tables (ROLE, ACTIVITY_STATUS, ACTIVITY_TYPE, PARTICIPANT_CATEGORY, DOCUMENT_STATUS, DOCUMENT_TYPE) have **unique names**, so the seed command and the code can look rows up by name. *(Confirmed by the owner.)*

## Step 1 review — decisions confirmed by the owner

40. **One evaluation per registration.** EVALUATION_SUBMISSION.registration is a one-to-one field (the ERD will be updated from one-to-many to one-to-one). DOCUMENT.reviewed_by is a nullable FK to TBI_MEMBER (the ERD type will be corrected from `string` to `int`).
41. **CERTIFICATE columns.** A certificate row is only created when the certificate is generated, so `certificate_template` and `certificate_number` (unique) are **required**. `file_path` is nullable because the PDF is rendered later by a background job. `reviewed_at`, `director_signed_at`, `vp_signed_at`, `issued_at` and `sent_at` are nullable. `status` is required. `vp_signatory` and `director_signatory` are required.
42. **Certificate status** has four stored values: `generated` (Generated, the default), `pending_signoff` (Pending Sign-off), `approved` (Approved), `issued` (Issued). The rules are enforced in `certificates.services.set_certificate_status()`, not in the database:
    - Approved requires `reviewed_at`, `director_signed_at` and `vp_signed_at`.
    - Issued requires the certificate to be Approved and, for participants (`recipient_type = registration`), a submitted evaluation. Resource persons (hard copy) do not need an evaluation.
    - Issuing sets `issued_at`, and links the participant's evaluation to the certificate if it was not linked yet.
    - Which moves are allowed at all: see 46.
43. **File path columns are 255 characters** (`max_length=255` on every FileField/ImageField). Django's default of 100 was too short for long upload names.
44. **NOTIFICATION is in its own `notifications` app** (moved out of `documents`).
45. **Activities are never deleted.** ACTIVITY_AUDIT_LOG.activity is PROTECT, so an activity with history cannot be deleted. Cancelled or wrong activities should be handled through status changes, not deletion. (Other links to ACTIVITY that are still CASCADE — resource-person links, form templates, notifications — never fire because activities are not deleted.)

## Certificate status moves — pending validation by the TBI

46. **Allowed certificate status moves** (enforced in `set_certificate_status()`, *to be validated with the TBI*):
    - Forward, one step at a time: Generated → Pending Sign-off → Approved → Issued. Skipping a step (for example Generated → Approved) is refused.
    - One backward move: Pending Sign-off → Generated, used when the reviewer **rejects** the certificate. It clears `reviewed_at`, `director_signed_at` and `vp_signed_at` (`reviewed_by` is kept).
    - **Issued is final.** Nothing can leave it. A wrong certificate is replaced by a new certificate with a new number.
    - Moving to the same status is refused.
47. Activities cannot be deleted from the Django admin either (no Delete button, no bulk delete action). The other CASCADE links to ACTIVITY stay as they are.

## Seed data (Step 2)

48. `python manage.py seed_data` loads the lookups listed in the brief. Document type names are stored in Title Case: Invitation Letter, Activity Design, Request Letter, Attendance Sheet, Accomplishment Report, Pre-registration Form, Evaluation Form, Certificate. The code relies on these exact names.
49. Running `seed_data` again never duplicates rows. It **resets only the fields it defines**:
    - roles: `can_create_activity`, `can_manage_users`;
    - document types: `is_required`, `due_reference`, `due_offset_days`.
    Descriptions and `is_active` are only set when a row is first created, so an admin's edits survive. Statuses, categories and activity types are only created, never changed.
50. The first Admin account comes from `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD` and `SEED_ADMIN_NAME` (optional, default "TBI Admin") in `.env`.
    - If email or password is missing, the account is skipped with a warning (lookups are still loaded).
    - The password must pass Django's password validators, otherwise the command stops.
    - The account is created with the Admin role, `is_superuser = True` and `must_change_password = False`.
    - On later runs the password and `must_change_password` are **never** reset; only the Admin role is re-applied if it was changed.
51. Both seed commands live in the `accounts` app (`accounts/management/commands/`); the lookup lists are in `accounts/seed.py`.
52. `python manage.py seed_demo` is **demo-only**:
    - It refuses to run when `DEBUG` is False.
    - All people and companies are invented; every email ends in `@demo.example.com`. Demo staff accounts (one per non-Admin role) have unusable passwords and cannot log in.
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
    - The IP is `REMOTE_ADDR`. If the system is later put behind a reverse proxy, this must be changed to read the real client IP.
    - Limits are settings: `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_MAX_FAILED_ATTEMPTS_PER_IP`, `LOGIN_LOCKOUT_MINUTES`.
55. The Django admin's own login page (`/admin/login/`) redirects to the staff login, so the lockout also protects the admin site.
56. Logout only accepts POST (a link on another site cannot log a member out).
57. **First-sign-in password change.** While `must_change_password` is true, every URL — including the Django admin and the JSON API — redirects to the change-password page. Only the change-password page, logout and static files (CSS/JS) are reachable; uploaded files are not. The new password must pass Django's password validators and must differ from the current one.
58. **Permissions** are written once in `accounts/permissions.py`: `has_permission()`, `require_permission()` (raises a 403), and the view decorators `staff_login_required` and `permission_required(Permission.X)`. Someone not logged in is sent to the login page; a logged-in member without the permission gets a 403 with the reason (JSON `{"error": ...}` for `/api/` URLs, plain text otherwise).
59. **Admin guardrails.** "Admin" means an active member whose role has `can_manage_users` (not the role's name).
    - An Admin cannot deactivate their own account.
    - The last active Admin can be neither deactivated nor moved to a role without `can_manage_users` (the role-change check is how "at least one active Admin" is kept when only one Admin exists).
    - The check locks the Admin rows, so two Admins cannot remove each other at the same moment.
    - An Admin may change their own role if another active Admin exists.
    - Members are never deleted (no delete in the API or the Django admin).
60. **Temporary passwords** are 16 random characters (Python `secrets`). They are hashed before saving, appear only in the invitation email, are never logged, and are never returned by the API. During development the console email backend prints the email to the terminal (standard output, not the log). Real sending replaces `accounts.emails.send_invitation_email()` in Step 9.
61. **Django admin member screen** uses the same services: adding a member asks only for email, name and role and sends the invitation; `is_active` and `must_change_password` are read-only; members are (de)activated with the "Deactivate/Reactivate selected members" actions; demoting the last Admin is refused. An Admin cannot yet reset another member's password (open item: add a "reset password" service if the TBI needs it).
62. **Startups are never staff.** The staff login only checks TBI_MEMBER. A startup can never be `request.user`; the startup login (Step 7) will keep its id under the separate session key `startups.services.STARTUP_SESSION_KEY`. Staff views only admit an active TBI_MEMBER. *(Built in Step 7: items 140–141.)*
63. The login, change-password and landing (`/`) pages are temporary plain forms until the frontend phase. The Admin Console is a small JSON API under `/accounts/api/`.
64. Tests use a fast password hasher (MD5) so the suite runs in seconds. The real server always uses Django's default PBKDF2 hasher.
65. **Closed in Step 6 (see 120).** Was an open item: in development (`DEBUG=True`) uploaded files under `/media/` are served without a login check. Step 6 must serve documents through a view that checks permissions.

## Activities (Step 4)

66. **Activity status is changed manually by staff.** *(Pending validation by the TBI.)* There is no automatic status job. Allowed moves: Draft → Scheduled, Scheduled → Ongoing, Ongoing → Conducted, and Scheduled → Draft (refused if anyone has registered). Conducted is final.
67. Any member whose role has `can_create_activity` may edit **any** activity, not only the ones they created. *(Pending validation by the TBI.)*
68. **Date rules** compare the deadline's date in Manila time: a registration deadline *on* the start date (for example 7:00 AM) is allowed, and an evaluation deadline *on* the end date is allowed.
69. The "activity type must be active" rule is checked only when the type is set or changed. An activity whose type is deactivated later can still be edited.
70. Only Conducted activities are read-only. Draft, Scheduled and Ongoing activities can be edited, and every validation rule still applies.
71. An empty description is stored as no description (NULL).
72. **Audit log rows.**
    - `field_changed` uses readable labels: Title, Description, Activity Type, Participant Categories, Start Date, End Date, Time Schedule, Venue, Max Slots, Registration Deadline, Evaluation Deadline, Budget Amount, Status, Resource Person Added, Resource Person Removed.
    - Values are formatted by one function, `activities.formatting.format_audit_value()`: dates "Oct 5, 2026"; date-times "Oct 15, 2026 5:00 PM" (Manila time); money "₱30,000.00"; lookups and resource persons by name; empty values "". The description is stored in full.
    - Creating an activity writes one row (Status: "" → "Draft"). A resource person row stores the name (in `new_value` when added, in `old_value` when removed).
73. **The audit log is append-only**, enforced in the model: saving an existing row, deleting a row, and bulk update/delete all raise `AuditLogIsAppendOnly`. There is no service, endpoint or admin screen that edits or deletes it. Its default order is newest first (migration `activities/0002`, options only; no data change).
74. An edit and its audit rows are saved in one database transaction. The activity row is locked while it is edited, so two people editing at once cannot overwrite each other's "old value".
75. **Resource persons.** A link's topic cannot be edited: remove the person and add them again (each step is audited). `create_resource_person` refuses an email that already exists (ignoring upper/lower case). People without an email are not checked for duplicates. Removing a link never deletes the person.
76. **Registration open/closed.** Registration is still open at the exact deadline time. `registered_count` counts every registration (there is no cancellation yet).
77. **Activity list cards.** "Conducted this month" means status Conducted with an end date in the current month (Manila time). "Open for registration" uses the same rule as `is_registration_open()`.
78. **List and calendar.** List filters use IDs (activity type, status, category; an activity matches the category filter if the category is one of its categories). The date range keeps activities that overlap it. Sort options: title, start_date, registered, created_at (prefix `-` for descending; the default is newest start date first). Pages hold 20 rows. The calendar shows every status, including Draft (staff only).
79. **JSON API** (`/activities/api/`): dates are `YYYY-MM-DD`; date-times are ISO 8601. In requests, a date-time without an offset (`2026-11-13T17:00`) is read as Manila time. In responses, every API (activities, registrations, public pages, accounts) shows date-times in Manila time with the offset, e.g. `2026-10-26T18:00:00+08:00`, never UTC "Z" (all views use `config.json.JsonResponse`; fixed in the Step 5 review); money is a string (`"30000.00"`). An update sends only the fields to change. Errors: 400 with `{"errors": {field: [...]}}` or `{"error": "..."}`; 403 with a clear message when `can_create_activity` is missing. As in Step 3, someone not logged in is redirected to the login page.
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
85. **To be decided with the TBI before the defense:** whether the Admin role keeps `can_create_activity`. For now Admin keeps it (seeded flags unchanged).
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
106. **Public registration (no login).** Only Scheduled activities are visible; Draft, Ongoing and Conducted return 404. The public response shows the activity details, its categories, slots left, whether registration is open (and why not), and the form fields — never who registered. Registration is open when `activities.services.is_registration_open()` is true (Scheduled, at or before the deadline, slots left) **and** a registration form exists. The closed reasons are "not open", "deadline has passed", "all slots are taken" and "form is not ready".
107. The activity row is locked while registering, so two people cannot take the last slot (tested with two threads at once).
108. Emails are stored trimmed and in lower case, and are unique per activity ignoring case and surrounding spaces (database constraint on `LOWER(TRIM(email))`). A second registration gets "This email is already registered for this activity."
109. The REGISTRATION and its FORM_ANSWER rows are saved in one transaction. The confirmation email (console backend) is sent only after the save succeeds; it names the activity, dates, venue and the chosen category, and nothing about other registrants. `startup` stays empty until Step 7.
110. The public endpoints are JSON (`/register/<id>/`, `/evaluate/<token>/`). Their POSTs keep Django's CSRF protection; the frontend pages will include the token.
111. **Attendance.**
    - Marked or unmarked by a member with `can_create_activity`, one or many at a time (all or nothing), only while the activity is Ongoing or Conducted. **Conducted locks the activity's own fields, not its registrations.**
    - Marking needs a method: `email` or `wet_signature`. Unmarking clears the method and is refused for anyone who has already submitted the evaluation.
    - Attendance changes are not written to ACTIVITY_AUDIT_LOG (they are not activity fields).
    - *(Pending validation by the TBI.)* "Email" confirmation means staff record attendance after the participant confirms by email; there is no self check-in.
112. Any signed-in staff member can list an activity's registrations (with category, contact details and answers), filtered by attended / not attended and by category.
113. **Evaluation window.** The evaluation opens at 00:00 Manila time on the activity's `end_date` (so people can answer at the closing of the event) and closes at `evaluation_deadline` (still open at that exact time). The brief's "after end_date" was read this way because item 68 allows the deadline on the end date. Only registrations with `attendance_confirmed` can evaluate; one evaluation per registration (item 40); never anonymous.
114. **Personal evaluation links.** Participants have no login. Each link carries the registration ID signed with Django's signing module (salted, using `SECRET_KEY`); no new column and no ERD change. A tampered, forged or unknown link is refused with "This evaluation link is not valid." Nobody can identify themselves by typing an email. A person always gets the same link, and it has no expiry of its own: the evaluation window decides when it works. Changing `SECRET_KEY` makes all old links invalid. Links use the `SITE_URL` setting (`.env`).
115. **Sending links.** A staff action emails the link to every attended registration that has not submitted yet (console backend). It is refused if the activity has no evaluation form or the evaluation window is not open. Running it again only reaches people who still have not submitted.
116. **Django admin.** Registrations, answers, evaluation submissions and activity forms are view only. Attendance is changed with the registration list actions ("Mark attended (confirmed by email)", "Mark attended (wet signature)", "Unmark attendance"). The activity list has "Create registration and evaluation forms (from defaults)" and "Send evaluation links …" actions. Default form fields are editable (options must be a JSON list).
117. **Demo data.** `seed_demo` creates both forms from the defaults (through `create_form`), gives every registration one of its activity's categories, and checks every answer with `validate_answers`. Demo registrations are written directly because most demo activities are already closed for registration.


## Documents, checklist and reminders (Step 6)

118. **Who may do what with a document.** *(Pending validation by the TBI.)*
     - **Upload:** any active staff member (any role, also Admin Staff), for any activity, in every activity status, including Conducted (the accomplishment report is due after the end date). **Conducted locks the activity's own fields, not its documents.** Only active document types can be chosen. The title is optional and defaults to the type name.
     - **Approve / return:** a member whose role has `can_create_activity`, and **never the uploader** (no self-review).
     - **Replace the file / delete:** only the uploader, and only while the document is For Review. Delete also removes the file.
     - **Approved is final:** no replacement, no type change, no deletion. A wrong approved document is handled by uploading a new one. There is no "change type" action at all: a For Review document of the wrong type is deleted and uploaded again.
119. **Returned documents (no Rejected status).** DOCUMENT_STATUS stays For Review / Approved. Returning sets `reviewed_by` and `reviewed_at` (the reviewer and the time) and keeps the status For Review. The review state is calculated, not stored:
     - For Review, `reviewed_at` empty: **waiting for a reviewer**;
     - For Review, `reviewed_at` set: **returned**, waiting for the uploader;
     - Approved: **approved**.

     A returned document cannot be approved (or returned again) until the uploader replaces the file; replacing clears `reviewed_by` and `reviewed_at`. The reason (required, at most 300 characters) is **not stored on DOCUMENT** (the ERD has no column for it). It is only in the uploader's in-app NOTIFICATION (type `document_returned`), which names the document title, type, activity and reviewer. NOTIFICATION has no document column, so it links to the activity and document type. The API (detail and repository) shows `review_state` (`waiting_for_review`, `returned`, `approved`).
120. **Serving files (closes item 65).** `/media/` is no longer served, not even in DEBUG (the `static()` line was removed from `config/urls.py`). Documents are downloaded only through `/documents/<id>/download/`, which requires an active staff login (anyone else is sent to the login page) and always sends the file as an attachment under its original name, with `X-Content-Type-Options: nosniff`. **Any active TBI member may download any activity document** *(pending TBI)*. Certificate files (`related_certificate` set) give 404 there until Step 9. Nothing else used `/media/` URLs: `seed_demo` only writes files to disk, and certificate templates/signature images will need their own permission-checked view in Step 9.
121. **File checks.** Allowed: pdf, docx, xlsx, jpg (`.jpeg` is accepted and stored as `jpg`), png. `.doc`, `.xls`, `.txt` and everything else are refused. The real content is checked, not only the name: PDF starts with `%PDF-`, PNG and JPEG with their signatures, and DOCX/XLSX must be a ZIP containing `[Content_Types].xml` plus `word/document.xml` or `xl/workbook.xml` (so an XLSX renamed to .docx is refused). No extra library is needed (python-magic needs a separate DLL on Windows). Maximum size: `DOCUMENT_MAX_UPLOAD_MB` (default 10). Empty files are refused. Stored columns: `file_name` = the original name without any folder part (cut to 255 characters, keeping the extension), `file_extension` lowercase without the dot, `file_size_kb` rounded up (at least 1).
122. **Stored file path.** Built from DOCUMENT_TYPE.`naming_pattern` as `documents/<pattern>.<ext>`. Placeholders: `{activity_id}`, `{activity_slug}` (title, at most 40 characters), `{type}` (type name as a slug), `{date}` (upload date in Manila, `YYYYMMDD`), `{year}`. The uploaded name is never used in the path. Patterns may only contain letters, digits, `- _ /` and these placeholders (checked when an Admin saves a document type). If a name is taken Django adds a random suffix; the path always fits in 255 characters. When a type has no pattern, the fallback `activity-{activity_id}/{type}/{date}-{type}` is used. Migration `documents/0002` only records the new `upload_to` function (no database change).
123. **Open item for the TBI:** ask for their official file-naming convention. An Admin can enter it per document type in the Django admin (`naming_pattern`); until then all seeded types use the fallback pattern. `seed_data` does not set patterns.
124. **Checklist** (`documents.services.document_checklist`, database checks only; the Step 8 assistant will reuse it for "what is missing"):
     - every **active required** type with its due date, state and overdue flag; **optional** active types are listed separately with their documents;
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
129. **Repository and review queue (JSON API, `/documents/api/`).** Any active staff member can list and search activity documents across activities, filtered by activity, document type, status, uploader and review state, searching the title and the file name, 20 per page, newest first. The "waiting for my review" list shows only documents waiting for a reviewer that are not my own uploads (returned ones are left out); a member without `can_create_activity` gets 403 from the API (and an empty list from the service).
130. **Notifications API (`/notifications/api/`).** A member sees only their own notifications (newest first, optional `unread=1`), the unread count, and can mark one or all as read; another member's notification gives 404.
131. **Django admin.** Documents and notifications are view only. The document screens show a "Download" link through the permission-checked view (never a `/media/` link). Document types validate `naming_pattern` and show the placeholders.
132. **Demo data.** `seed_demo` still writes small `.txt` placeholder files directly (demo only); they can be downloaded through the new view but could not be uploaded through the service.
133. The embedding job is **not** built yet: `approve_document` has one clearly marked place where Step 8 will queue it after the approval is saved.


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
141. **Staff and startups never cross.** A startup session has no Django user, so every staff URL (pages, APIs, Django admin) sends it to the staff login. Every portal URL refuses a session with a logged-in staff member (403 "Staff accounts cannot use the startup portal.") and answers 401 "Startup login required." when nobody is logged in. A staff member cannot use the portal login either (403); they must log out first.
142. **Set-password link** (STARTUP has no `must_change_password`, so the staff invitation is not copied).
     - Only a member with `can_manage_users` sends it (staff API or the Django admin action), and only to an active startup. It is emailed (console backend in development) and never returned by the API or logged.
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
