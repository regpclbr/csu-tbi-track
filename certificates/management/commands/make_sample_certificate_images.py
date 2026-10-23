"""DEMO ONLY: draw a sample certificate background (A4 landscape PNG) and two sample signature PNGs,
so the certificate flow can be tried before the TBI's real design and signatures are available.

    python manage.py make_sample_certificate_images C:\\temp\\tbi-samples

Writes sample-template.png, sample-signature-vp.png and sample-signature-director.png.
The images are invented; they are not real signatures.
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw

A4_150_DPI = (1754, 1240)


def draw_template(path: Path) -> None:
    """A plain border design that leaves the central text area empty."""
    width, height = A4_150_DPI
    image = Image.new("RGB", (width, height), (253, 251, 245))
    draw = ImageDraw.Draw(image)
    navy, gold = (22, 46, 92), (191, 151, 64)
    draw.rectangle([30, 30, width - 31, height - 31], outline=navy, width=18)
    draw.rectangle([62, 62, width - 63, height - 63], outline=gold, width=5)
    for x, y in ((62, 62), (width - 63, 62), (62, height - 63), (width - 63, height - 63)):
        draw.ellipse([x - 28, y - 28, x + 28, y + 28], fill=gold)
    draw.rectangle([62, 150, width - 63, 196], fill=navy)  # band above the text area
    image.save(path, "PNG")


def draw_signature(path: Path, seed: int) -> None:
    """A scribble on a transparent background."""
    image = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    points = [(40 + i * 26, 110 + ((i * (37 + seed)) % 70) - 35) for i in range(20)]
    draw.line(points, fill=(20, 30, 90, 255), width=6, joint="curve")
    draw.line([(60, 160), (540, 150)], fill=(20, 30, 90, 255), width=4)
    image.save(path, "PNG")


class Command(BaseCommand):
    help = "DEMO ONLY: write a sample certificate background and two sample signatures (PNG) to a folder."

    def add_arguments(self, parser) -> None:
        parser.add_argument("folder", help="Folder to write the three PNG files to (created if missing).")

    def handle(self, *args, **options) -> None:
        folder = Path(options["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        draw_template(folder / "sample-template.png")
        draw_signature(folder / "sample-signature-vp.png", seed=3)
        draw_signature(folder / "sample-signature-director.png", seed=11)
        for name in ("sample-template.png", "sample-signature-vp.png", "sample-signature-director.png"):
            self.stdout.write(f"Wrote {folder / name}")
