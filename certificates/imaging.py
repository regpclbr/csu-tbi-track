"""Signature images: make a white (or near-white) background transparent (ASSUMPTIONS 366). Pillow only.

Called only by certificates.services (add, replace, "Remove white background", the upload dialog's preview). A scanned
or photographed signature usually has a white background, which shows as a white box on the cream certificate design.

How it works, pixel by pixel, on the darkest of the three colour channels (ink of any colour is dark in at least one):
- at or above WHITE (245): the paper -> fully transparent;
- at or below INK (200): ink -> fully kept;
- in between: a soft edge, more transparent the lighter it is (anti-aliased strokes stay smooth).
Edge pixels are then "un-blended" from white: a stroke drawn at opacity t over white looks like 255 - t * (255 - ink),
so the ink colour is 255 - (255 - seen) / t. Without this, the soft edge keeps a light halo on the cream paper.
"""

from io import BytesIO

from PIL import Image, ImageChops, ImageMath, ImageOps

WHITE = 245  # from here up: background (fully transparent)
INK = 200  # from here down: ink (fully kept)


def _alpha_table() -> list[int]:
    table = []
    for value in range(256):
        if value <= INK:
            table.append(255)
        elif value >= WHITE:
            table.append(0)
        else:
            table.append(round(255 * (WHITE - value) / (WHITE - INK)))
    return table


ALPHA_TABLE = _alpha_table()


def has_transparency(image: Image.Image) -> bool:
    """True when the image already has at least one (partly) transparent pixel: it is stored unchanged."""
    if image.mode == "P" and "transparency" in image.info:
        image = image.convert("RGBA")
    if image.mode in ("RGBA", "LA", "PA"):
        return image.getchannel("A").getextrema()[0] < 255
    return False


def _unblend(channel: Image.Image, alpha: Image.Image) -> Image.Image:
    """The ink colour of one channel for the given opacity (see the module docstring), clamped to 0..255."""
    return ImageMath.lambda_eval(
        lambda e: e["convert"](
            e["min"](e["max"](255 - (255 - e["float"](e["c"])) * 255 / e["max"](e["float"](e["t"]), 1), 0), 255), "L"),
        c=channel, t=alpha,
    )


def remove_white_background(image: Image.Image) -> Image.Image:
    """A new RGBA image: white and near-white pixels transparent, soft edges, the ink unchanged. An existing alpha
    channel is kept (multiplied in)."""
    rgba = ImageOps.exif_transpose(image).convert("RGBA")
    red, green, blue, existing = rgba.split()
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    alpha = ImageChops.multiply(darkest.point(ALPHA_TABLE), existing)
    return Image.merge("RGBA", [_unblend(red, alpha), _unblend(green, alpha), _unblend(blue, alpha), alpha])


def png_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
