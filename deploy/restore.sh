#!/bin/sh
# Restore the hosted review version from a backup made by backup.sh (docs/DEPLOY.md, "Restore").
# REPLACES the server's database (and, when a media file is given, every uploaded file). Asks you to type the
# database name first. For a test restore on your laptop, follow docs/DEPLOY.md instead (csu_tbi_restore_check).
#   sudo sh /opt/csu-tbi/app/deploy/restore.sh /var/backups/csu-tbi/db-20261120-0200.dump [/var/backups/csu-tbi/media-20261120-0200.tar.gz]
set -eu
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE="sh $APP_DIR/deploy/compose.sh"
DB_FILE=${1:?"Usage: restore.sh <db-....dump> [media-....tar.gz]"}
MEDIA_FILE=${2:-}
[ -s "$DB_FILE" ] || { echo "Not found or empty: $DB_FILE" >&2; exit 1; }
[ -z "$MEDIA_FILE" ] || [ -s "$MEDIA_FILE" ] || { echo "Not found or empty: $MEDIA_FILE" >&2; exit 1; }

DB_NAME=$($COMPOSE exec -T db sh -c 'printf %s "$POSTGRES_DB"')
echo "This REPLACES the database \"$DB_NAME\" on this server with $DB_FILE."
[ -z "$MEDIA_FILE" ] || echo "It also replaces every uploaded file with $MEDIA_FILE."
printf 'Type the database name to continue: '
read -r answer
[ "$answer" = "$DB_NAME" ] || { echo "Cancelled; nothing changed."; exit 1; }

echo "== Stopping web and worker"
$COMPOSE stop web worker

echo "== Replacing the database"
$COMPOSE exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --force --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'
$COMPOSE exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < "$DB_FILE"

if [ -n "$MEDIA_FILE" ]; then
  echo "== Replacing the uploaded files"
  $COMPOSE run --rm --no-deps -T --entrypoint sh web -c 'find /app/media -mindepth 1 -delete && tar xzf - -C /app' < "$MEDIA_FILE"
fi

echo "== Starting again (the release step runs the migrations)"
$COMPOSE up -d
echo "== Restore finished. Sign in and check a few activities, documents and certificates."
