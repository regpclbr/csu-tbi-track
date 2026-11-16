#!/bin/sh
# One-time setup of a fresh Ubuntu 24.04 LTS server for the hosted review version (docs/DEPLOY.md, step 4).
# Run once, as your normal login user with sudo (not as root directly):
#   sudo sh server-setup.sh
# It: sets the Manila time zone, adds a 2 GB swap file, installs Docker and the automatic security updates, allows
# SSH with a key only, turns on the firewall (22, 80, 443), prepares /opt/csu-tbi and the daily backup job.
# Safe to run again.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
LOGIN_USER=${SUDO_USER:-}
[ -n "$LOGIN_USER" ] && [ "$LOGIN_USER" != root ] || { echo "Run with sudo from your normal login user." >&2; exit 1; }
SWAP_SIZE=${SWAP_SIZE:-2G}

echo "== Time zone: Asia/Manila (cron, logs and backup names)"
timedatectl set-timezone Asia/Manila

echo "== Swap file ($SWAP_SIZE): needed with about 1 GB of memory"
if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l "$SWAP_SIZE" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/60-csu-tbi.conf
sysctl -p /etc/sysctl.d/60-csu-tbi.conf

echo "== Packages and automatic security updates"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y ca-certificates curl git ufw unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
cat > /etc/apt/apt.conf.d/52csu-tbi-upgrades <<'EOF'
// Restart at 03:30 Manila time when an update needs it (the containers start again by themselves).
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:30";
EOF

echo "== Docker (official repository)"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "== SSH: key only, no root login"
cat > /etc/ssh/sshd_config.d/60-csu-tbi.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t
systemctl reload ssh

echo "== Firewall: only 22 (SSH), 80 and 443 (the Azure network security group also limits 22 to your IP)"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "== Folders"
mkdir -p /opt/csu-tbi
chown "$LOGIN_USER":"$LOGIN_USER" /opt/csu-tbi
mkdir -p /var/backups/csu-tbi
chown root:"$LOGIN_USER" /var/backups/csu-tbi
chmod 750 /var/backups/csu-tbi

echo "== Daily backup at 02:00 (runs once the app is in /opt/csu-tbi/app)"
cat > /etc/cron.d/csu-tbi-backup <<'EOF'
# CSU-TBI Track: database + uploaded files, newest 14 kept (deploy/backup.sh)
0 2 * * * root [ -f /opt/csu-tbi/app/deploy/backup.sh ] && sh /opt/csu-tbi/app/deploy/backup.sh >> /var/log/csu-tbi-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/csu-tbi-backup
systemctl restart cron

echo "== Done. Memory and swap:"
free -m
echo "Next: docs/DEPLOY.md, step 5 (deploy key and git clone)."
