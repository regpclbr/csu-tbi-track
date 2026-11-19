# Hosting the client review version on Azure

> **This is a client review version, not the final production deployment.**
> It is a hosted copy of CSU-TBI Track (hosted UAT) so NAVIGATU staff can check the system with real accounts.
> Full live deployment is out of scope of the study. Everything runs on **one** small Azure virtual machine
> (Azure for Students) with Docker Compose. Details and reasons: `docs/ASSUMPTIONS.md` items 391–398.

Commands marked **PowerShell** run on your Windows laptop. Commands marked **server** run on the Azure machine after
you connect with `ssh` (step 3). Never paste a password, App Password or secret key into a chat, a commit or a screenshot.

**No Azure server?** See [Laptop review version (fallback)](#laptop-review-version-fallback) at the end: the same
production setup on your own laptop, reached through a Cloudflare quick tunnel.

## Before the review starts (checklist)

- [ ] NAVIGATU approved using real data on the hosted version (TBI_QUESTIONS, "Hosted client review version").
- [ ] Privacy placeholders filled in: contact person, retention period, approval date
      (`templates/public/privacy.html` and `templates/public/_privacy_consent.html`), committed and deployed.
- [ ] The certificate design file is ready (A4 landscape PNG or JPG, at least 1754 × 1240 pixels).
- [ ] `SITE_URL` in the server's `.env` is the **https** Azure address (step 6).
- [ ] `EMAIL_REAL_SENDING=True` in the server's `.env`, and `send_test_email` reached your inbox (step 8).
- [ ] The first System Administrator is created (step 9).
- [ ] Staff are invited from Admin Console → Members (step 10).
- [ ] One full test on the hosted version **with your own email**: register → mark attended → evaluate → approve the
      activity's certificates → the certificate email arrives with the PDF (step 11).

---

## 1. What it costs (check the current terms on Azure's site first)

Terms change; read the free services list on the [Azure for Students](https://azure.microsoft.com/free/students) page
and your subscription's **Education** page in the Azure Portal before you create anything.

| Part | Free? |
|---|---|
| Virtual machine **B2ats v2** (2 vCPU, 1 GB) | Free: 750 hours a month for 12 months (one machine running all month ≈ 730 hours). |
| OS disk | The free services have listed 2 × 64 GB **P6 Premium SSD** disks. Choose **Premium SSD, 64 GiB** (step 2). Check this. |
| Public IP address (static, Standard) | May use a few dollars a month of the **$100 credit**. Check this. |
| Outbound data | A review is far below the free monthly amount. |
| Anything bigger (for example B2als v2, 4 GB) | Paid from the $100 credit. |

**Budget alert:** Azure Portal → **Cost Management** → **Budgets** → **Add**: amount $20, alerts at 25 %, 50 % and
90 %, your email. If budgets are not available for your offer, check the remaining credit regularly in the Portal's
**Education** page (Overview).

**When the credit or the 12 months run out:** the subscription is disabled and the machine stops. Nothing is charged
unless you upgrade to pay-as-you-go. After a grace period the data is deleted, so keep your own backups (step 13).

**Do not** turn on Azure Backup, Azure Monitor agents, Defender plans or a second VM: they use the credit.

## 2. Create the virtual machine (Azure Portal)

1. Make an SSH key on your laptop (**PowerShell**; press Enter for the default file, and type a passphrase):
   ```powershell
   ssh-keygen -t ed25519 -C "csu-tbi-review"
   Get-Content $HOME\.ssh\id_ed25519.pub     # the PUBLIC key: you paste this into Azure
   ```
   The file without `.pub` is your private key: never share it, never commit it.
2. Find your public IP address (for the SSH rule): open <https://ifconfig.me> in the browser.
3. Azure Portal → **Virtual machines** → **Create** → **Azure virtual machine**:
   - **Resource group:** new, `csu-tbi-review` (everything goes in it, so it can be deleted in one step later).
   - **Name:** `csu-tbi-review`.
   - **Region:** **(Asia Pacific) Southeast Asia** (Singapore), or **East Asia** (Hong Kong) if the size is not offered.
   - **Image:** **Ubuntu Server 24.04 LTS - x64 Gen2**.
   - **Size:** **B2ats_v2** (shown as "free services eligible"). If you get "insufficient quota", try the other region,
     or **B1s** (1 GB, also free), or **B2pts_v2** (ARM, also free; the images used here work on ARM too).
   - **Authentication:** **SSH public key**, username `azureuser`, **Use existing public key**, paste the `.pub` text.
   - **Inbound ports:** allow **SSH (22)**, **HTTP (80)**, **HTTPS (443)**.
   - **Disks:** OS disk type **Premium SSD (locally-redundant)**, size **64 GiB (P6)**; **Delete with VM** on.
   - **Networking:** a new public IP, **Static**; **Delete public IP and NIC when VM is deleted** on.
   - **Management / Monitoring:** turn off boot diagnostics with a storage account, auto-shutdown off (the review needs
     the site up), no backup.
   - **Review + create** → **Create**.
4. **Limit SSH to your IP:** the VM → **Networking** → **Network settings** → the rule for port 22 → **Source:** IP
   Addresses, your IP from step 2 (`x.x.x.x/32`) → Save. When your home IP changes, update this rule (same place).
5. **DNS name:** the VM → **Overview** → **DNS name: Not configured** → **DNS name label**: for example
   `csu-tbi-review` → Save. Your address is now `csu-tbi-review.southeastasia.cloudapp.azure.com` (the region is in it).

## 3. Connect (PowerShell)

```powershell
ssh azureuser@csu-tbi-review.southeastasia.cloudapp.azure.com
```
Type `yes` the first time. On the server, `exit` disconnects.

## 4. Prepare the server (once)

The setup script is in the repository, which is not on the server yet; copy just that file (**PowerShell**, in the
project folder):
```powershell
scp deploy\server-setup.sh azureuser@csu-tbi-review.southeastasia.cloudapp.azure.com:~/
```
Then on the **server**:
```sh
sudo sh ~/server-setup.sh
```
It sets the Manila time zone, adds a 2 GB swap file, installs Docker and automatic security updates (restart at 03:30
when needed), allows SSH with a key only (no passwords, no root), turns on the firewall (22, 80, 443 only), creates
`/opt/csu-tbi` and the daily 02:00 backup job. At the end it shows the memory and swap. Disconnect and connect again
so the changes apply.

## 5. Get the code (read-only deploy key)

On the **server**, make a key used only for reading the repository:
```sh
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "csu-tbi-review deploy key"
cat ~/.ssh/github_deploy.pub
printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```
GitHub → the repository → **Settings** → **Deploy keys** → **Add deploy key**: title `csu-tbi-review`, paste the
`.pub` text, **leave "Allow write access" OFF** → Add. Then on the **server**:
```sh
git clone git@github.com:regpclbr/csu-tbi-track.git /opt/csu-tbi/app
```
(Type `yes` for GitHub's host key.)

## 6. The server's `.env` (secrets live only here)

On the **server**:
```sh
sudo cp /opt/csu-tbi/app/.env.example /opt/csu-tbi/.env
sudo chown root:root /opt/csu-tbi/.env
sudo chmod 600 /opt/csu-tbi/.env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # copy: the secret key
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # copy: the database password
sudo nano /opt/csu-tbi/.env
```
Change these lines (keep the rest as in `.env.example`); `Ctrl+O`, Enter saves, `Ctrl+X` closes:

```ini
DJANGO_SECRET_KEY=<the first random value>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=csu-tbi-review.southeastasia.cloudapp.azure.com
TRUSTED_PROXY_HEADER=X-Forwarded-For
BEHIND_HTTPS_PROXY=True
STARTUP_SYNC_ENABLED=False
POSTGRES_PASSWORD=<the second random value>
GEMINI_API_KEY=
SITE_URL=https://csu-tbi-review.southeastasia.cloudapp.azure.com
REVIEW_VERSION_LABEL=Review version
DEFAULT_FROM_EMAIL=CSU-TBI Track <your.address@gmail.com>
EMAIL_HOST_USER=your.address@gmail.com
EMAIL_HOST_PASSWORD=<the 16-letter Gmail App Password, no spaces>
EMAIL_REAL_SENDING=True
SEED_ADMIN_EMAIL=
SEED_ADMIN_PASSWORD=
SEED_ADMIN_NAME=
SITE_DOMAIN=csu-tbi-review.southeastasia.cloudapp.azure.com
ACME_EMAIL=your.address@gmail.com
Q_CLUSTER_WORKERS=1
GUNICORN_WORKERS=2
GUNICORN_THREADS=2
```
The Gmail App Password: Google Account → Security → 2-Step Verification (must be on) → **App passwords** → create one
named "CSU-TBI Track". Never use your normal Gmail password. To see the file again later: `sudo cat /opt/csu-tbi/.env`
(do it only on your own screen).

## 7. First deploy

On the **server**:
```sh
cd /opt/csu-tbi/app
sudo sh deploy/compose.sh up -d --build
```
The first build takes about 10–20 minutes on the small machine (the swap file makes it possible). Then:
```sh
sudo sh deploy/compose.sh ps                 # db, web, worker, caddy "running"; release "exited (0)"
sudo sh deploy/compose.sh logs release       # the release step: check, migrate, seed, schedules, static files
sudo sh deploy/compose.sh logs caddy | tail -20    # "certificate obtained successfully"
sudo sh deploy/compose.sh exec web python manage.py check_pdf
```
Open `https://csu-tbi-review.southeastasia.cloudapp.azure.com/healthz/` in the browser: it shows `ok`. The sign-in
page shows the amber "Review version" label.

## 8. Test the email

```sh
sudo sh deploy/compose.sh exec web python manage.py send_test_email your.address@gmail.com
```
It shows the SMTP server, whether the password is set (never the password) and the SITE_URL; the email arrives with
the https address in it. If it fails, see Troubleshooting → Email.

## 9. The first System Administrator

```sh
sudo sh deploy/compose.sh exec web python manage.py create_first_admin
```
It asks for the email, the full name and the password (typed twice, not shown). It works only once: after that,
people are invited from the Admin Console.

## 10. Set up the system (in the browser, as the System Administrator)

1. **Certificate design:** Admin Console → Certificate design → upload the design file → set it active.
2. **Positions:** Admin Console → Positions → add the TBI's job titles.
3. **Staff:** Admin Console → Members → Add member (name, email, role). Each person gets an email with a temporary
   password and must choose their own at the first sign-in. If the email cannot be sent, the member is not added.
4. **Signatories** (a member with certificate approval, for example the Director): Certificates → Signatories → add
   the two people, upload their signatures, set the left and right defaults.
5. **TEST startup** (only if the review includes the startup portal; on the **server**):
   ```sh
   sudo sh deploy/compose.sh exec web python manage.py create_review_startup --email your.address@gmail.com
   ```
   The set-password link goes to that email.

## 11. The full test (with your own email)

As a staff member who can create activities: create an activity starting today with a registration and an evaluation
form, choose who signs, set it Scheduled. Open its registration link (Share card) in a private browser window,
register with your own email, tick the privacy box. Move it to Ongoing, mark yourself attended, move it to Conducted.
Open the evaluation link from the email, submit (tick the privacy box). As an approver: Certificates tab → **Approve
certificates for this activity**. Within a few minutes the certificate email arrives with the PDF. Check that every
link in the emails starts with `https://csu-tbi-review…`. Then delete the test activity if you like (System
Administrator; refused once a certificate is Issued, which is expected).

## 12. Updating after a commit

Push the commit to GitHub from your laptop, then on the **server**:
```sh
sudo sh /opt/csu-tbi/app/deploy/update.sh
```
It pulls, rebuilds, and restarts; the release step runs the migrations before web and worker start. If the release
step fails, web and worker are not started (the site is down until it is fixed): `sudo sh deploy/compose.sh logs
release` shows why. Update outside review hours.

## 13. Backups and restore

**Daily (automatic):** at 02:00 the server writes `db-YYYYMMDD-HHMM.dump` (database) and `media-YYYYMMDD-HHMM.tar.gz`
(uploaded files) to `/var/backups/csu-tbi/` and keeps the newest 14 of each. Log: `/var/log/csu-tbi-backup.log`.
By hand: `sudo sh /opt/csu-tbi/app/deploy/backup.sh`.

**Copy them to your laptop** into an **encrypted** folder: a BitLocker-encrypted USB drive or a VeraCrypt container.
They contain participants' personal data: **never** put them in the OneDrive project folder (it syncs to the cloud).
**PowerShell** (E: = the encrypted drive):
```powershell
New-Item -ItemType Directory -Force E:\csu-tbi-backups | Out-Null
scp "azureuser@csu-tbi-review.southeastasia.cloudapp.azure.com:/var/backups/csu-tbi/*" E:\csu-tbi-backups\
```

**Test a restore on your laptop** (never into your development database): with the development database running
(`docker compose up -d` in the project folder), **PowerShell**, using one downloaded dump:
```powershell
$dump = "E:\csu-tbi-backups\db-20261120-0200.dump"
docker compose cp $dump db:/tmp/restore_check.dump
docker compose exec -T db createdb -U csu_tbi csu_tbi_restore_check
docker compose exec -T db pg_restore -U csu_tbi -d csu_tbi_restore_check --no-owner /tmp/restore_check.dump
docker compose exec -T db psql -U csu_tbi -d csu_tbi_restore_check -c "SELECT (SELECT count(*) FROM activity) AS activities, (SELECT count(*) FROM registration) AS registrations, (SELECT count(*) FROM certificate) AS certificates;"
docker compose exec -T db dropdb -U csu_tbi csu_tbi_restore_check
docker compose exec -T db rm /tmp/restore_check.dump
```
The name is always `csu_tbi_restore_check`; your development database `csu_tbi_track` is never touched. (`csu_tbi` is
the `POSTGRES_USER` of your development `.env`.) To look at the uploaded files, open the `media-….tar.gz` with 7-Zip
inside the encrypted folder.

**Restore on the server** (replaces the server's database, and the files if you give the media file):
```sh
sudo sh /opt/csu-tbi/app/deploy/restore.sh /var/backups/csu-tbi/db-20261120-0200.dump /var/backups/csu-tbi/media-20261120-0200.tar.gz
```
It asks you to type the database name first.

## 14. Turn on the AI later (after NAVIGATU approves)

Until then `GEMINI_API_KEY` stays empty: the AI Assistant still answers activity and find-files questions, and Reports
shows the normal form. To turn it on, on the **server**:
```sh
sudo nano /opt/csu-tbi/.env                      # GEMINI_API_KEY=<the key>
cd /opt/csu-tbi/app && sudo sh deploy/compose.sh up -d
sudo sh deploy/compose.sh exec web python manage.py check_gemini
sudo sh deploy/compose.sh exec web python manage.py index_documents
```

## 15. After the defense: stop and delete everything

1. Make a last backup and copy it to the encrypted folder (step 13).
2. Azure Portal → **Resource groups** → `csu-tbi-review` → **Delete resource group** (type the name). This deletes the
   VM, its disk, the public IP, the network security group and the network card together.
3. GitHub → repository → Settings → Deploy keys → delete `csu-tbi-review`.
4. Google Account → App passwords → delete "CSU-TBI Track". If a Gemini key was used, delete or rotate it.
5. Check Cost Management a day later: nothing should be running.

To only pause it (keeps the disk, the static IP may still cost a little): VM → **Stop** (it must say "Stopped
(deallocated)").

## 16. Troubleshooting

**Low memory** (pages slow, containers restarting):
```sh
free -m                                   # swap "used" above ~500 MB most of the time = too small
sudo dmesg | grep -i -E "oom|killed process" | tail
sudo docker stats --no-stream             # memory per container
```
Fixes: keep `Q_CLUSTER_WORKERS=1` and `GUNICORN_WORKERS=2`; or **resize** (paid from the credit): VM → **Size** →
**B2als_v2** (2 vCPU, 4 GB) → Resize (it restarts; the static IP and DNS name stay). Then you may set
`GUNICORN_WORKERS=3` and `Q_CLUSTER_WORKERS=2` and run `sudo sh deploy/compose.sh up -d`. Check the monthly price in
the Azure pricing calculator first: the credit covers only a few months of a paid size.

**Certificate (HTTPS) does not work:** `sudo sh deploy/compose.sh logs caddy`.
- "connection refused"/"timeout": ports 80 and 443 must be open in the VM's Networking rules; `SITE_DOMAIN` must be
  exactly the DNS name.
- "rateLimited" / "too many certificates": the Azure name shares Let's Encrypt limits with other Azure users. Caddy
  then tries ZeroSSL by itself; wait an hour and look again. Never delete the `caddy_data` volume (it would ask again).
  Last resort: a cheap own domain with a CNAME record to the Azure name, then `SITE_DOMAIN`, `SITE_URL` and
  `DJANGO_ALLOWED_HOSTS` changed to it.

**The site answers "Bad Request (400)":** `DJANGO_ALLOWED_HOSTS` must contain the DNS name. **"CSRF verification
failed" on sign-in:** `SITE_URL` must be the https address and `BEHIND_HTTPS_PROXY=True`.

**The release step failed** (web/worker not starting): `sudo sh deploy/compose.sh logs release`. A `csu_tbi.E00x`
line names the setting to fix in `.env` (for example SITE_URL not https).

**Email:**
- "Username and Password not accepted": use an App Password (2-Step Verification on), no spaces.
- Port 587 blocked: Azure allows authenticated SMTP on 587; check `EMAIL_PORT=587`, `EMAIL_USE_TLS=True`.
- Emails arrive but the sender looks different: Gmail always sends from the account's own address; set
  `DEFAULT_FROM_EMAIL` to it.
- Addresses `@demo.example.com` are never sent (on purpose).

**PDFs:** `sudo sh deploy/compose.sh exec web python manage.py check_pdf`. Admin Console → System status shows the
worker, the schedules (the startup sync shows "Turned off"), email and the PDF check.

**Logs:** `sudo sh deploy/compose.sh logs -f web worker` (Ctrl+C stops). Personal link tokens are shown as `<hidden>`.

## Laptop review version (fallback)

Used when no Azure server is available (Nov 17, 2026 UAT). The same production setup (`docker-compose.prod.yml`) runs
on the laptop under its own project `csu_tbi_uat` (own volumes, fresh database). `docker-compose.laptop.yml` adds two
changes: Caddy listens on `127.0.0.1:8080` only, and it uses `Caddyfile.laptop`. A **Cloudflare quick tunnel** gives
testers an `https://<random>.trycloudflare.com` address:

```
phone/laptop --https--> Cloudflare --tunnel--> cloudflared (laptop) --> 127.0.0.1:8080 Caddy --> web:8000
```

Cloudflare ends HTTPS. Caddy serves the static files and tells Django that the visit is HTTPS (`X-Forwarded-Proto`)
and gives it the visitor's own IP (`CF-Connecting-IP`), so Secure cookies, CSRF and the login lockout work.
**Your development database (`csu_tbi_db`, volume `pgdata`) is never used.**

**The env file** is `C:\Users\Admin\tbi-uat\uat.env`, outside OneDrive, readable only by you. It already has a
generated `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`. Fill in every `<...>` with `notepad C:\Users\Admin\tbi-uat\uat.env`:
the tunnel address (3 places: `SITE_URL`, `SITE_DOMAIN`, `DJANGO_ALLOWED_HOSTS`), the Gmail address and App Password,
`TBI_CONTACT_EMAIL`, and the UAT Gemini key (sample documents only). `CSRF_TRUSTED_ORIGINS` comes from `SITE_URL`.

**Every PowerShell window**, in the project folder, first:
```powershell
$env:ENV_FILE = "C:\Users\Admin\tbi-uat\uat.env"
function uat { docker compose --project-name csu_tbi_uat -f deploy/docker-compose.prod.yml -f deploy/docker-compose.laptop.yml --env-file $env:ENV_FILE @args }
```

**Start (in this order):**
1. Plug in the laptop and turn off sleep (the `powercfg` commands below). Pause Windows Update for 7 days.
2. A **separate** PowerShell window, left open all the time: `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8080`
   (if it cannot connect: add `--protocol http2`). Copy the `https://....trycloudflare.com` address into `uat.env`.
3. `uat up -d --build`, then `uat ps` (db, web, worker, caddy running; release exited 0) and `uat logs release`.
4. On a phone **on mobile data**: `https://<address>/healthz/` shows `ok`; the sign-in page shows "Review version".
5. `uat exec web python manage.py create_first_admin`
6. `uat exec web python manage.py send_test_email you@gmail.com` (check Spam too: `trycloudflare.com` links are
   sometimes flagged).
7. AI: `uat exec web python manage.py check_gemini`; after uploading the sample documents,
   `uat exec web python manage.py index_documents`.

**Logs:** `uat logs -f web`, `uat logs -f worker`, `uat logs --tail 50 caddy` (Ctrl+C stops following).

**Stop and start again, data kept:** `uat stop` / `uat start` (or `uat down` / `uat up -d`). **Never `uat down -v`**
until the study is over: `-v` deletes the database.

**If the tunnel restarts it gets a NEW address** (closing its window, a reboot, sleep). Links in emails already sent
(invitations, evaluation links, password resets) and QR codes already shown then point to the old address, and
everyone must sign in again. Container restarts alone change nothing. If it happens: start the tunnel again, put the
new address in the 3 places in `uat.env`, run `uat up -d` (data kept), then show the activity's general evaluation
link / QR code again from its Share card, use **Resend** for personal evaluation links, and re-send staff invitations.
An old personal link still works if the tester replaces only the address part (`/evaluate/<token>/` stays valid).

**Back up** into `C:\Users\Admin\tbi-backups` (never with `>`: PowerShell 5.1 corrupts binary output):
```powershell
$stamp = Get-Date -Format yyyyMMdd-HHmm
uat exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB" > /tmp/uat.dump'
uat cp db:/tmp/uat.dump "C:\Users\Admin\tbi-backups\uat-db-$stamp.dump"
uat exec -T db rm /tmp/uat.dump
uat exec -T web tar czf /tmp/uat-media.tar.gz -C /app media
uat cp web:/tmp/uat-media.tar.gz "C:\Users\Admin\tbi-backups\uat-media-$stamp.tar.gz"
uat exec -T web rm /tmp/uat-media.tar.gz
```
The backups contain testers' personal data: delete them by November 30, 2026 (the privacy notice's promise).

**Updating to a new version** (for example the November 19 corrections, which add migration `activities/0009`): make
the backup above FIRST, then `uat up -d --build`. The release step runs `migrate` by itself; check `uat logs release`
(exited 0) and `uat ps`. To go back: restore the dump (`pg_restore --clean`) and run the previous version.

**Power settings** (run once, as yourself, before the UAT):
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```
If the lid command says the setting does not exist, simply keep the lid open. Undo after the UAT (this laptop's values
before the UAT: sleep after 30 minutes, hibernate after 180 minutes, lid closed = sleep):
```powershell
powercfg /change standby-timeout-ac 30
powercfg /change hibernate-timeout-ac 180
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1
powercfg /setactive SCHEME_CURRENT
```

**Remove everything after the study** (data is gone after this; back up first if needed):
```powershell
uat down -v
docker image rm csu-tbi-app:latest caddy:2.8.4-alpine
Remove-Item -Recurse -Force C:\Users\Admin\tbi-uat
winget uninstall --id Cloudflare.cloudflared
```
The `pgvector/pgvector:pg17` image stays (the development database uses it).

## Files

| File | What it is |
|---|---|
| `Dockerfile` | The app image (Python 3.12, WeasyPrint with Pango, DejaVu and Gelasio fonts, gunicorn). |
| `deploy/docker-compose.prod.yml` | db, release, web, worker, caddy on one machine. |
| `deploy/compose.sh` | `docker compose` with the server's `.env`; use it for every compose command. |
| `deploy/release.sh` / `start-web.sh` | The release step and the web server command. |
| `deploy/Caddyfile` | HTTPS, static files, proxy to the app. |
| `deploy/server-setup.sh` | One-time server preparation. |
| `deploy/update.sh` | Update after a commit. |
| `deploy/backup.sh` / `restore.sh` | Daily backup and restore. |
| `deploy/docker-compose.laptop.yml` / `Caddyfile.laptop` | The laptop review version behind a Cloudflare quick tunnel. |
| `deploy/fonts/gelasio/` | The certificate font for Linux (SIL Open Font License). |
