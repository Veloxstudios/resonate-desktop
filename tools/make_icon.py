"""Builds resonate.ico from the source artwork, zoomed so the rings fill the icon."""
import sys
from PIL import Image, ImageDraw

src = Image.open(sys.argv[1]).convert("RGBA")
tile = src.crop(src.getchannel("A").getbbox())
# Rings = bright pixels inside the tile (skip the anti-aliased tile edge).
m = 40
lum = tile.crop((m, m, tile.width - m, tile.height - m)).convert("L").point(lambda v: 255 if v > 60 else 0)
x0, y0, x1, y1 = lum.getbbox()
cx, cy = (x0 + x1) / 2 + m, (y0 + y1) / 2 + m
side = int(max(x1 - x0, y1 - y0) * 1.06)  # a little breathing room around the rings
crop = tile.crop((int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2)))
crop = Image.alpha_composite(Image.new("RGBA", crop.size, (0, 0, 0, 255)), crop)

N = 1024
art = crop.resize((N, N), Image.LANCZOS)
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle((0, 0, N - 1, N - 1), radius=int(N * .2), fill=255)
art.putalpha(mask)
art.save(sys.argv[2], sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)])
