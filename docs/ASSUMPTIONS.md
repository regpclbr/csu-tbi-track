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
25. ROLE, ACTIVITY_STATUS, PARTICIPANT_CATEGORY and DOCUMENT_STATUS have **no** `is_active`. They are never deleted (PROTECT) and the app depends on their fixed names. ACTIVITY_TYPE and DOCUMENT_TYPE keep `is_active`.
26. DOCUMENT.related_certificate is a nullable FK to CERTIFICATE (PROTECT). Documents with it set belong to the Certificates tab and are excluded from the document checklist and the Document Repository: use `Document.objects.activity_documents()` there, and `Document.objects.certificate_documents()` in the Certificates tab.

## Models (Step 1) — decisions made while building (please review)

27. Primary keys use the ERD names (`member_id`, `activity_id`, ...), and every table uses the ERD name in lower case (`tbi_member`, `activity`, ...).
28. ACTIVITY_RESOURCE_PERSON has no primary key in the ERD. It gets `activity_resource_person_id` as its key, plus the unique (activity, resource_person) constraint.
29. The pgvector migration moved from `accounts` to `assistant`. Django's admin/auth migrations depend on the **first** migration of the user model's app, so `accounts/0001` must create TBI_MEMBER.
30. App placement: NOTIFICATION lives in `documents` (its main use is document reminders, and this avoids a circular dependency between apps). DOCUMENT_EMBEDDING lives in `assistant`.
31. ERD `string` columns that can hold long text (`options` on form fields, `answer_value`) are stored as unlimited text. Other strings get sensible lengths (names 255, contact numbers 30).
32. `registration.email` is unique per activity **ignoring upper/lower case**.
33. CERTIFICATE: `certificate_template`, `certificate_number` (unique), `file_path`, `reviewed_at`, `director_signed_at`, `vp_signed_at`, `issued_at` and `sent_at` are nullable. `status` is required and starts as "For Review" (choices: For Review, Approved; the sign-off steps are tracked by the timestamps and may be extended in Step 9). `vp_signatory` and `director_signatory` are required.
34. Extra safety constraints not in the brief:
    - one EVALUATION_SUBMISSION per registration;
    - a NOTIFICATION has exactly one recipient (member or startup);
    - a required DOCUMENT_TYPE must have `due_reference` and `due_offset_days`;
    - a CERTIFICATE's filled recipient column matches `recipient_type`;
    - ACTIVITY `end_date >= start_date` and `budget_amount >= 0`;
    - one DOCUMENT_EMBEDDING per (document, chunk_index).
35. Delete rules: **PROTECT** for lookups, members, signatories, templates and official records (an activity with registrations, documents or certificates cannot be deleted; a document cited by the assistant cannot be deleted). **CASCADE** only for rows that belong to one parent (audit log rows, resource-person links, form templates and fields, a registration's evaluation and answers, notifications, embeddings, chat messages). REGISTRATION.startup is SET_NULL.
36. `registration.confirmation_method` is empty until attendance is confirmed.
37. `manage.py createsuperuser` gives the new account the Admin role, so the roles must be loaded first (Step 2 seed command).
38. In the Django admin, lookups, signatories and templates have no Delete button, and the activity audit log is read-only.
