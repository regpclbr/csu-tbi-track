#!/bin/sh
# The release step (the "release" service in deploy/docker-compose.prod.yml), before web and worker start.
# Every command is safe to run on every deploy. If one fails, web and worker do not start: read
#   sudo deploy/compose.sh logs release
set -eu

echo "== Server settings check"
python manage.py check --deploy --fail-level ERROR

echo "== Database migrations"
python manage.py migrate --noinput

echo "== Cache table (login lockout, rate limits)"
python manage.py createcachetable

echo "== Lookups, roles, document types, default form fields (never demo data)"
python manage.py seed_data

echo "== Background schedules"
python manage.py setup_schedules

echo "== Static files"
python manage.py collectstatic --noinput --clear --verbosity 0

echo "== Release finished"
