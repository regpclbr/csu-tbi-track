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
21. The pgvector extension is enabled by `accounts/migrations/0001_enable_pgvector.py`, the first migration in the project.
22. The Docker database listens on host port **5433** (not 5432), because another PostgreSQL already uses 5432 on the development machine.
