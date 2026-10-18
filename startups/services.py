"""Business logic for startups (read through the StartupProvider).

Views stay thin and call the functions in this module.
"""

# Startup login (Step 7) keeps the logged-in startup's id under this session key.
# It is deliberately separate from Django's staff login: a startup is never
# `request.user`, so staff views (which check for an active TBI_MEMBER) never let it in.
STARTUP_SESSION_KEY = "startup_id"
