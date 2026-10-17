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
- Frontend (Django Templates, HTMX, Alpine.js) comes AFTER the backend. Until then, don't
  build pages; expose logic through service functions and simple views or a small JSON API.

## Apps
`accounts`, `activities`, `registrations`, `documents`, `certificates`, `reports`,
`startups`, `assistant`. The project package is `config`.

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
- ROLE, ACTIVITY_STATUS, PARTICIPANT_CATEGORY, DOCUMENT_STATUS have fixed names the code
  relies on and no `is_active`.
- Checklist and Document Repository must use `Document.objects.activity_documents()`
  (excludes certificate files, i.e. `related_certificate` set).
- Primary keys and table names follow the ERD (`activity_id`, `db_table = "activity"`).

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
- Write pytest-django tests for every business rule (`<app>/tests/test_*.py`).
- Never put secrets in code. Never commit `.env`.
- Use clear names that match the ERD. Use type hints and short docstrings.
- After each step, report what changed, how to run it, and what to check. Then stop.
- Don't commit unless asked.

## Roadmap (one step at a time, only when asked)
0. Project setup — done
1. Models matching the ERD, constraints, admin — written, awaiting owner review
2. Seed data command + fake demo data command (safe to run twice)
3. Staff login, role permissions, first-sign-in password change
4. Activities, status changes, audit log
5. Public registration, attendance, evaluation flow
6. Documents, checklist, reminders (Django-Q2 scheduled task)
7. Startups module with a mock StartupProvider
8. RAG pipeline: parse, chunk, embed on approval, retrieve, answer with sources
9. Certificates (WeasyPrint), sign-off flow, automatic emails
10. Reports and dashboard numbers

## Commands
```powershell
.\.venv\Scripts\Activate.ps1
docker compose up -d
python manage.py migrate
pytest
```
