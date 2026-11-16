#!/bin/sh
# docker compose for the hosted review version, always with the server's .env (docs/DEPLOY.md). Run with sudo:
#   sudo deploy/compose.sh ps
#   sudo deploy/compose.sh exec web python manage.py create_first_admin
# ENV_FILE: the .env to use (default /opt/csu-tbi/.env, owned by root, mode 600).
set -eu
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=${ENV_FILE:-/opt/csu-tbi/.env}
if [ ! -r "$ENV_FILE" ]; then
  echo "Cannot read $ENV_FILE (run with sudo, or create it first: docs/DEPLOY.md, step 6)." >&2
  exit 1
fi
export ENV_FILE
exec docker compose --project-name "${COMPOSE_PROJECT:-csu-tbi}" \
  --file "$APP_DIR/deploy/docker-compose.prod.yml" \
  --env-file "$ENV_FILE" "$@"
