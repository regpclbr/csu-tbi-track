#!/bin/sh
# Update the hosted review version after a commit was pushed to GitHub (docs/DEPLOY.md, "Updating after a commit"):
# pull (as the folder's owner, who has the deploy key), rebuild, restart (the release step runs the migrations before
# web and worker start), remove old images.
#   sudo sh /opt/csu-tbi/app/deploy/update.sh
set -eu
cd "$(dirname "$0")/.." && sudo -u "$(stat -c %U .)" git pull --ff-only && sh deploy/compose.sh up -d --build --remove-orphans && docker image prune -f
