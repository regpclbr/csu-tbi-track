"""Build the site's CSS with the standalone Tailwind CLI (no Node.js needed).

    python manage.py tailwind install   # once: download the pinned CLI into tools/ (checked by SHA-256)
    python manage.py tailwind build     # frontend/tailwind/app.css -> static/css/app.css (minified)
    python manage.py tailwind watch     # rebuild on every template change (Ctrl+C stops)

The version and the checksums are pinned here. To upgrade: change TAILWIND_VERSION and copy the
new lines from the release's sha256sums.txt into CHECKSUMS.
"""

import hashlib
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TAILWIND_VERSION = "v4.3.3"
CHECKSUMS = {  # from https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/sha256sums.txt
    "tailwindcss-windows-x64.exe": "e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955",
    "tailwindcss-linux-x64": "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    "tailwindcss-linux-arm64": "55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195",
    "tailwindcss-macos-arm64": "cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d",
    "tailwindcss-macos-x64": "7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1",
}
DOWNLOAD_URL = "https://github.com/tailwindlabs/tailwindcss/releases/download/{version}/{name}"

SOURCE = Path(settings.BASE_DIR) / "frontend" / "tailwind" / "app.css"
OUTPUT = Path(settings.BASE_DIR) / "static" / "css" / "app.css"
TOOLS = Path(settings.BASE_DIR) / "tools"


def binary_name() -> str:
    """The release file for this computer."""
    system, machine = platform.system(), platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system == "Windows":
        return "tailwindcss-windows-x64.exe"
    if system == "Darwin":
        return f"tailwindcss-macos-{arch}"
    return f"tailwindcss-linux-{arch}"


def binary_path() -> Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    return TOOLS / f"tailwindcss-{TAILWIND_VERSION}{suffix}"


class Command(BaseCommand):
    help = "Install the pinned Tailwind CLI, build static/css/app.css, or watch and rebuild."

    def add_arguments(self, parser) -> None:
        parser.add_argument("action", choices=["install", "build", "watch"])

    def handle(self, *args, action: str, **options) -> None:
        if action == "install":
            self.install()
            return
        if not binary_path().exists():
            raise CommandError("The Tailwind CLI is not installed yet. Run: python manage.py tailwind install")
        # build: the CLI writes to a temporary file and we copy it into static/css/. Writing into the OneDrive folder
        # directly fails with "EEXIST: file already exists, mkdir ...static\css" whenever the CSS changed (the CLI's
        # mkdir on a OneDrive folder; ASSUMPTIONS 446). watch still writes directly (it may fail there; see CLAUDE.md).
        temporary = Path(tempfile.gettempdir()) / "tbi-tailwind-app.css" if action == "build" else OUTPUT
        command = [str(binary_path()), "-i", str(SOURCE), "-o", str(temporary), "--minify"]
        if action == "watch":
            command.append("--watch")
            self.stdout.write("Watching templates and frontend/tailwind/app.css (Ctrl+C stops)...")
        try:
            subprocess.run(command, cwd=settings.BASE_DIR, check=True)
        except KeyboardInterrupt:
            return
        except subprocess.CalledProcessError as error:
            raise CommandError(f"Tailwind failed (exit code {error.returncode}).") from error
        if action == "build":
            shutil.copyfile(temporary, OUTPUT)
            self.stdout.write(self.style.SUCCESS(f"Built {OUTPUT.relative_to(settings.BASE_DIR)} ({OUTPUT.stat().st_size // 1024} KB)."))

    def install(self) -> None:
        name = binary_name()
        target = binary_path()
        if target.exists() and self._sha256(target) == CHECKSUMS[name]:
            self.stdout.write(f"Tailwind {TAILWIND_VERSION} is already installed ({target.relative_to(settings.BASE_DIR)}).")
            return
        url = DOWNLOAD_URL.format(version=TAILWIND_VERSION, name=name)
        self.stdout.write(f"Downloading {url} ...")
        TOOLS.mkdir(exist_ok=True)
        partial = target.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as file:  # noqa: S310 (fixed https URL)
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
        if self._sha256(partial) != CHECKSUMS[name]:
            partial.unlink()
            raise CommandError("The downloaded file does not match the pinned SHA-256 checksum. Nothing was installed.")
        partial.replace(target)
        target.chmod(0o755)
        self.stdout.write(self.style.SUCCESS(f"Installed Tailwind {TAILWIND_VERSION} at {target.relative_to(settings.BASE_DIR)}."))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
