"""The files of the hosted review version (deploy/, Dockerfile, requirements): checks that keep them working on the
Linux server and keep secrets and development tools out of it (ASSUMPTIONS 391, 397)."""

import re
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
DEPLOY = BASE_DIR / "deploy"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def requirement_names(path: Path) -> set[str]:
    names = set()
    for line in text(path).splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.add(re.split(r"[=<>\[ ]", line, maxsplit=1)[0].lower())
    return names


def test_server_requirements_have_gunicorn_and_no_development_tools() -> None:
    names = requirement_names(BASE_DIR / "requirements.txt")
    assert "gunicorn" in names
    assert not names & {"pytest", "pytest-django", "pytest-xdist", "playwright", "pypdfium2", "whitenoise"}


def test_development_tools_stay_in_requirements_dev() -> None:
    names = requirement_names(BASE_DIR / "requirements-dev.txt")
    assert {"pytest", "playwright", "pypdfium2"} <= names


def test_dockerfile_installs_only_the_app_requirements_and_runs_as_a_normal_user() -> None:
    dockerfile = text(BASE_DIR / "Dockerfile")
    assert "pip install -r requirements.txt" in dockerfile
    assert not re.search(r"^(COPY|RUN).*requirements-dev", dockerfile, re.MULTILINE)
    assert "USER app" in dockerfile
    for package in ("libpango-1.0-0", "fonts-dejavu-core", "tzdata", "fontconfig"):
        assert package in dockerfile
    assert "deploy/fonts/gelasio/" in dockerfile


def test_dockerignore_keeps_secrets_and_local_data_out_of_the_image() -> None:
    lines = set(text(BASE_DIR / ".dockerignore").splitlines())
    assert {".env", ".git", ".venv", "media", "tools"} <= lines


@pytest.mark.parametrize("path", sorted(DEPLOY.glob("*.sh")) + [BASE_DIR / "Dockerfile", DEPLOY / "Caddyfile",
                                                               DEPLOY / "docker-compose.prod.yml",
                                                               DEPLOY / "Caddyfile.laptop",
                                                               DEPLOY / "docker-compose.laptop.yml",
                                                               DEPLOY / "docker-compose.shared-db.yml"])
def test_server_files_have_unix_line_endings(path) -> None:
    assert b"\r\n" not in path.read_bytes(), f"{path.name} has Windows line endings (see .gitattributes)"


def test_gitattributes_forces_lf_for_server_files() -> None:
    attributes = text(BASE_DIR / ".gitattributes")
    assert "*.sh text eol=lf" in attributes and "Dockerfile text eol=lf" in attributes


def service_block(compose: str, name: str) -> str:
    """The lines of one service in the compose file (up to the next service at the same indentation)."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z]+:\n|^volumes:)", compose, re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def test_database_port_is_never_published_and_only_caddy_has_ports() -> None:
    compose = text(DEPLOY / "docker-compose.prod.yml")
    for name in ("db", "release", "web", "worker"):
        assert not re.search(r"^\s+ports:", service_block(compose, name), re.MULTILINE), name
    caddy = service_block(compose, "caddy")
    assert '"${HTTP_PORT:-80}:80"' in caddy and '"${HTTPS_PORT:-443}:443"' in caddy


def test_web_and_worker_wait_for_the_release_step_and_share_the_media_volume() -> None:
    compose = text(DEPLOY / "docker-compose.prod.yml")
    for name in ("web", "worker"):
        assert "release: { condition: service_completed_successfully }" in service_block(compose, name)
    assert "image: pgvector/pgvector:pg17" in service_block(compose, "db")
    assert "- media:/app/media" in compose  # the shared x-app volumes (web and worker)
    assert 'command: ["python", "manage.py", "qcluster"]' in service_block(compose, "worker")
    for name in ("db", "web", "worker", "caddy"):
        assert "mem_limit:" in service_block(compose, name), name


def test_release_step_checks_migrates_seeds_and_never_loads_demo_data() -> None:
    release = text(DEPLOY / "release.sh")
    for command in ("check --deploy --fail-level ERROR", "migrate --noinput", "createcachetable", "seed_data",
                    "setup_schedules", "collectstatic --noinput --clear"):
        assert command in release
    assert "seed_demo" not in release
    assert release.index("check --deploy") < release.index("migrate")


def test_no_access_logs_anywhere() -> None:
    """Personal links carry tokens in the URL: neither Caddy nor gunicorn writes an access log."""
    assert not re.search(r"^\s*log\b", text(DEPLOY / "Caddyfile"), re.MULTILINE)
    assert "--access-logfile" not in text(DEPLOY / "start-web.sh")


# --- The laptop review version (Cloudflare quick tunnel in front, docs/DEPLOY.md) ----------------------------------


def test_laptop_override_has_its_own_project_and_listens_on_this_laptop_only() -> None:
    laptop = text(DEPLOY / "docker-compose.laptop.yml")
    assert re.search(r"^name: csu_tbi_uat$", laptop, re.MULTILINE)  # own volumes, never the development database
    assert "ports: !override" in laptop and '"127.0.0.1:8080:80"' in laptop
    assert not re.search(r'^\s+- "(?!127\.0\.0\.1:)[^"]*:\d+"', laptop, re.MULTILINE)  # no port on all addresses
    assert "./Caddyfile.laptop:/etc/caddy/Caddyfile:ro" in laptop
    # The database volume comes only from the prod file, under the UAT project name (never an external volume).
    assert not re.search(r"^\s+- \S*pgdata\S*:", laptop, re.MULTILINE) and "external:" not in laptop


def test_laptop_caddy_trusts_cloudflare_for_https_and_the_visitor_ip(settings) -> None:
    caddy = text(DEPLOY / "Caddyfile.laptop")
    assert "auto_https off" in caddy and re.search(r"^:80 \{", caddy, re.MULTILINE)
    assert "header_up X-Forwarded-Proto https" in caddy
    assert "header_up X-Forwarded-For {http.request.header.CF-Connecting-IP}" in caddy
    assert not re.search(r"^\s*log\b", caddy, re.MULTILINE)
    assert int(re.search(r"max_size (\d+)MB", caddy).group(1)) > settings.DOCUMENT_MAX_UPLOAD_MB
    assert "handle_path /static/*" in caddy and "reverse_proxy web:8000" in caddy


def test_testing_site_uses_the_shared_database_server_and_its_own_upload_volume() -> None:
    """ASSUMPTIONS 471: no db container of its own; release, web and worker reach the development Postgres through
    host.docker.internal and keep their uploads in the testing_media volume (never the project's media folder)."""
    shared = text(DEPLOY / "docker-compose.shared-db.yml")
    assert re.search(r'^  db:\n    profiles: \["not-used"\]', shared, re.MULTILINE)
    assert "POSTGRES_HOST: host.docker.internal" in shared and '"host.docker.internal:host-gateway"' in shared
    for name in ("release", "web", "worker"):
        block = service_block(shared, name)
        assert "environment: *shared-db" in block and "- testing_media:/app/media" in block, name
        assert "volumes: !override" in block and "      - media:/app/media" not in block, name
    assert re.search(r"^volumes:\n  testing_media:", shared, re.MULTILINE)
    assert "csu_tbi_track" not in shared.split("\nx-shared-db", 1)[1]  # never names the development database


def test_laptop_files_have_lf_in_gitattributes() -> None:
    assert "deploy/Caddyfile.laptop text eol=lf" in text(BASE_DIR / ".gitattributes")


def test_gunicorn_timeout_is_above_the_assistant_limit(settings) -> None:
    timeout = int(re.search(r"--timeout (\d+)", text(DEPLOY / "start-web.sh")).group(1))
    assert timeout > settings.ASSISTANT_QUESTION_TIMEOUT_SECONDS


def test_caddy_upload_limit_is_above_the_document_limit(settings) -> None:
    max_mb = int(re.search(r"max_size (\d+)MB", text(DEPLOY / "Caddyfile")).group(1))
    assert max_mb > settings.DOCUMENT_MAX_UPLOAD_MB


def test_backups_keep_fourteen_and_restore_asks_first() -> None:
    backup = text(DEPLOY / "backup.sh")
    assert "KEEP=${KEEP:-14}" in backup and "pg_dump" in backup and "-Fc" in backup and "chmod 640" in backup
    restore = text(DEPLOY / "restore.sh")
    assert "Type the database name to continue" in restore and "pg_restore" in restore


def test_server_setup_turns_off_password_login_and_opens_only_three_ports() -> None:
    setup = text(DEPLOY / "server-setup.sh")
    assert "PasswordAuthentication no" in setup and "PermitRootLogin no" in setup
    assert re.findall(r"ufw allow (\S+)", setup) == ["OpenSSH", "80/tcp", "443/tcp"]
    assert "unattended-upgrades" in setup and "/swapfile" in setup


def test_env_example_has_the_server_settings_without_real_values() -> None:
    example = text(BASE_DIR / ".env.example")
    for name in ("BEHIND_HTTPS_PROXY=", "REVIEW_VERSION_LABEL=", "SITE_DOMAIN=", "ACME_EMAIL=",
                 "Q_CLUSTER_WORKERS=", "GUNICORN_WORKERS="):
        assert name in example
    assert re.search(r"^EMAIL_HOST_PASSWORD=$", example, re.MULTILINE)
    assert re.search(r"^SITE_DOMAIN=$", example, re.MULTILINE)


def test_gelasio_font_and_its_license_are_included() -> None:
    folder = DEPLOY / "fonts" / "gelasio"
    assert (folder / "OFL.txt").is_file()
    assert (folder / "Gelasio-VariableFont_wght.ttf").read_bytes()[:4] == b"\x00\x01\x00\x00"  # a TrueType file
    template = text(BASE_DIR / "certificates" / "templates" / "certificates" / "certificate_pdf.html")
    assert "font-family: Georgia, Gelasio," in template
