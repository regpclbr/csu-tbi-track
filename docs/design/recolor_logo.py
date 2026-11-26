"""Recolour docs/design/logo.png (white strokes + teal mark) onto the deep forest green tile (ASSUMPTIONS 466)."""
import colorsys, sys
from pathlib import Path
from PIL import Image

root = Path(sys.argv[1])
TILE = (0x0B, 0x3D, 0x24)
GOLD = (0xD8, 0xB4, 0x5C)   # soft gold mark
WHITE = (0xF4, 0xF7, 0xF4)  # strokes: a calm off-white

src = Image.open(root / "docs/design/logo.png").convert("RGBA")
src = src.crop(src.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox())  # the drawing only
side = max(src.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.alpha_composite(src, ((side - src.width) // 2, (side - src.height) // 2))
work = square.resize((720, 720), Image.LANCZOS)
pixels = work.load()
for y in range(work.height):
    for x in range(work.width):
        r, g, b, a = pixels[x, y]
        if a == 0:
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0.25 and 0.40 < h < 0.60:  # the teal mark
            pixels[x, y] = (*GOLD, a)
        else:
            pixels[x, y] = (*WHITE, a)
work.save(root / "docs/design/logo-mark-forest.png")

for name, size in (("favicon-32", 32), ("logo-96", 96), ("apple-touch-icon-180", 180)):
    tile = Image.new("RGBA", (size, size), (*TILE, 255))
    mark_size = round(size * 0.72)
    mark = work.resize((mark_size, mark_size), Image.LANCZOS)
    offset = (size - mark_size) // 2
    tile.alpha_composite(mark, (offset, offset))
    tile.convert("RGB").save(root / f"static/img/{name}.png", optimize=True)

# Before / after sheet for the owner.
sheet = Image.new("RGB", (2 * 220 + 60, 300), (244, 246, 249))
for column, folder in enumerate(("docs/design/apple-touch-icon-180-green-old.png", "static/img/apple-touch-icon-180.png")):
    sheet.paste(Image.open(root / folder).convert("RGB"), (30 + column * 250, 60))
sheet.save(Path(sys.argv[2]) / "logo-before-after.png")
print("done")
