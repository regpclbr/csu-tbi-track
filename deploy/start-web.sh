#!/bin/sh
# The web server of the hosted review version (the "web" service in deploy/docker-compose.prod.yml).
# gthread: a long AI Assistant question (up to 90 s, ASSISTANT_QUESTION_TIMEOUT_SECONDS) holds one thread, not a
# whole worker; --timeout 120 is above it. --max-requests: each worker is replaced now and then, so memory stays low.
# No access log: personal evaluation and set-password links carry a token in the URL.
set -eu
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --worker-class gthread \
  --workers "${GUNICORN_WORKERS:-2}" \
  --threads "${GUNICORN_THREADS:-2}" \
  --timeout 120 \
  --graceful-timeout 30 \
  --max-requests 500 \
  --max-requests-jitter 50 \
  --worker-tmp-dir /dev/shm \
  --error-logfile -
