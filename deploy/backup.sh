#!/bin/sh
# Daily backup of the hosted review version (docs/DEPLOY.md, "Backups"): the database (pg_dump custom format) and the
# uploaded files (the media volume as .tar.gz), into BACKUP_DIR; only the newest KEEP of each are kept.
# Installed by server-setup.sh as a root cron job at 02:00 Manila time:
#   0 2 * * * root sh /opt/csu-tbi/app/deploy/backup.sh >> /var/log/csu-tbi-backup.log 2>&1
# By hand: sudo sh /opt/csu-tbi/app/deploy/backup.sh
# The files hold personal data: the folder is readable only by root and the app folder's owner (for scp).
set -eu
APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-/var/backups/csu-tbi}
KEEP=${KEEP:-14}
OWNER_GROUP=$(stat -c %G "$APP_DIR")
COMPOSE="sh $APP_DIR/deploy/compose.sh"
STAMP=$(TZ=Asia/Manila date +%Y%m%d-%H%M)

umask 027
mkdir -p "$BACKUP_DIR"
chown root:"$OWNER_GROUP" "$BACKUP_DIR"
chmod 750 "$BACKUP_DIR"

echo "$(date '+%F %T') backup $STAMP started"

DB_FILE="$BACKUP_DIR/db-$STAMP.dump"
$COMPOSE exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$DB_FILE.part"
[ -s "$DB_FILE.part" ] || { echo "Database dump is empty: backup failed." >&2; rm -f "$DB_FILE.part"; exit 1; }
mv "$DB_FILE.part" "$DB_FILE"

MEDIA_FILE="$BACKUP_DIR/media-$STAMP.tar.gz"
$COMPOSE exec -T web tar czf - -C /app media > "$MEDIA_FILE.part"
mv "$MEDIA_FILE.part" "$MEDIA_FILE"

chown root:"$OWNER_GROUP" "$DB_FILE" "$MEDIA_FILE"
chmod 640 "$DB_FILE" "$MEDIA_FILE"

# Keep the newest KEEP of each kind.
for pattern in 'db-*.dump' 'media-*.tar.gz'; do
  # shellcheck disable=SC2012
  ls -1t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f -- "$old"; done
done

echo "$(date '+%F %T') backup $STAMP finished: $(du -h "$DB_FILE" | cut -f1) database, $(du -h "$MEDIA_FILE" | cut -f1) files"
