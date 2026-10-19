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
11. Navigatu startup data is read-only and reached through one mock provider for now. Startups are identified by email.
12. Any TBI staff member can see every startup.
13. The admins are the researchers.
14. Certificate numbering format is decided by the researchers.

## Technical (Step 0)

15. No separate tech-stack document was found in `docs/`; the tech stack in the project brief (copied into `CLAUDE.md`) is used as the source of truth.
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
62. **Startups are never staff.** The staff login only checks TBI_MEMBER. A startup can never be `request.user`; the startup login (Step 7) will keep its id under the separate session key `startups.services.STARTUP_SESSION_KEY`. Staff views only admit an active TBI_MEMBER.
63. The login, change-password and landing (`/`) pages are temporary plain forms until the frontend phase. The Admin Console is a small JSON API under `/accounts/api/`.
64. Tests use a fast password hasher (MD5) so the suite runs in seconds. The real server always uses Django's default PBKDF2 hasher.
65. **Open item for Step 6:** in development (`DEBUG=True`) uploaded files under `/media/` are served without a login check. Step 6 must serve documents through a view that checks permissions.

## Activities (Step 4)

66. **Activity status is changed manually by staff.** *(Pending validation by the TBI.)* There is no automatic status job. Allowed moves: Draft → Scheduled, Scheduled → Ongoing, Ongoing → Conducted, and Scheduled → Draft (refused if anyone has registered). Conducted is final.
67. Any member whose role has `can_create_activity` may edit **any** activity, not only the ones they created. *(Pending validation by the TBI.)*
68. **Date rules** compare the deadline's date in Manila time: a registration deadline *on* the start date (for example 7:00 AM) is allowed, and an evaluation deadline *on* the end date is allowed.
69. The "activity type must be active" rule is checked only when the type is set or changed. An activity whose type is deactivated later can still be edited.
70. Only Conducted activities are read-only. Draft, Scheduled and Ongoing activities can be edited, and every validation rule still applies.
71. An empty description is stored as no description (NULL).
72. **Audit log rows.**
    - `field_changed` uses readable labels: Title, Description, Activity Type, Participant Category, Start Date, End Date, Time Schedule, Venue, Max Slots, Registration Deadline, Evaluation Deadline, Budget Amount, Status, Resource Person Added, Resource Person Removed.
    - Values are formatted by one function, `activities.formatting.format_audit_value()`: dates "Oct 5, 2026"; date-times "Oct 15, 2026 5:00 PM" (Manila time); money "₱30,000.00"; lookups and resource persons by name; empty values "". The description is stored in full.
    - Creating an activity writes one row (Status: "" → "Draft"). A resource person row stores the name (in `new_value` when added, in `old_value` when removed).
73. **The audit log is append-only**, enforced in the model: saving an existing row, deleting a row, and bulk update/delete all raise `AuditLogIsAppendOnly`. There is no service, endpoint or admin screen that edits or deletes it. Its default order is newest first (migration `activities/0002`, options only; no data change).
74. An edit and its audit rows are saved in one database transaction. The activity row is locked while it is edited, so two people editing at once cannot overwrite each other's "old value".
75. **Resource persons.** A link's topic cannot be edited: remove the person and add them again (each step is audited). `create_resource_person` refuses an email that already exists (ignoring upper/lower case). People without an email are not checked for duplicates. Removing a link never deletes the person.
76. **Registration open/closed.** Registration is still open at the exact deadline time. `registered_count` counts every registration (there is no cancellation yet).
77. **Activity list cards.** "Conducted this month" means status Conducted with an end date in the current month (Manila time). "Open for registration" uses the same rule as `is_registration_open()`.
78. **List and calendar.** List filters use IDs (activity type, status, category). The date range keeps activities that overlap it. Sort options: title, start_date, registered, created_at (prefix `-` for descending; the default is newest start date first). Pages hold 20 rows. The calendar shows every status, including Draft (staff only).
79. **JSON API** (`/activities/api/`): dates are `YYYY-MM-DD`; date-times are ISO 8601 (`2026-11-13T17:00`, Manila time if no offset); money is a string (`"30000.00"`). An update sends only the fields to change. Errors: 400 with `{"errors": {field: [...]}}` or `{"error": "..."}`; 403 with a clear message when `can_create_activity` is missing. As in Step 3, someone not logged in is redirected to the login page.
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
