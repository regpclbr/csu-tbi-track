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
    - Open question: other moves (for example Approved back to Generated) are not blocked yet. To be decided in Step 9.
43. **File path columns are 255 characters** (`max_length=255` on every FileField/ImageField). Django's default of 100 was too short for long upload names.
44. **NOTIFICATION is in its own `notifications` app** (moved out of `documents`).
45. **Activities are never deleted.** ACTIVITY_AUDIT_LOG.activity is PROTECT, so an activity with history cannot be deleted. Cancelled or wrong activities should be handled through status changes, not deletion. (Other links to ACTIVITY that are still CASCADE — resource-person links, form templates, notifications — never fire because activities are not deleted.)
