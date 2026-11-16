# CSU-TBI Track: the app image of the hosted client review version (docs/DEPLOY.md).
# One image for three services in deploy/docker-compose.prod.yml: release (migrate, seed, static files), web (gunicorn)
# and worker (qcluster). Development on Windows does not use it (runserver + the dev database in docker-compose.yml).
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Manila

# WeasyPrint needs Pango (with HarfBuzz) and fontconfig; report PDFs use DejaVu Sans; tzdata gives Python's zoneinfo
# Asia/Manila (slim images may not have it).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libjpeg62-turbo \
      fontconfig fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

# Gelasio (SIL OFL): the certificate's Georgia has no Linux copy; Gelasio has the same letter widths (ASSUMPTIONS 397).
COPY deploy/fonts/gelasio/ /usr/share/fonts/truetype/gelasio/
RUN fc-cache -f

WORKDIR /app

# Only the app's requirements: pytest, Playwright and pypdfium2 (requirements-dev.txt) never reach the server.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Fail the build early (not on the first certificate) if the time zone or WeasyPrint cannot load.
RUN python -c "import zoneinfo; zoneinfo.ZoneInfo('Asia/Manila'); import weasyprint; print('WeasyPrint', weasyprint.__version__)"

COPY . .

# A non-root user. /app/media and /app/staticfiles are created here so the named volumes mounted on them start out
# owned by this user (Docker copies the folder's owner into a new, empty volume).
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app/media /app/staticfiles \
 && chown -R app:app /app/media /app/staticfiles
USER app

EXPOSE 8000
CMD ["sh", "deploy/start-web.sh"]
