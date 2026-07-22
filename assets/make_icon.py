"""Generate the Zen Mouse Jiggler app icon.

Produces a branded icon: a dark rounded tile with a bright-green mouse cursor
and three "jiggle" motion arcs. Writes a multi-resolution .ico (for the exe and
window) plus a 256px .png (for the Tk window icon / docs).

Run:  python assets/make_icon.py
"""
import os
import math
from PIL import Image, ImageDraw

ACCENT = (3, 178, 0, 255)        # bright green (--acc2)
ACCENT_DK = (2, 85, 0, 255)      # dimmed green (--acc)
TILE = (14, 14, 16, 255)         # near-black tile
BORDER = (42, 42, 46, 255)       # neutral line
WHITE = (240, 240, 240, 255)

MASTER = 512


def rounded_rect(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                           width=width)


def draw_cursor(draw, ox, oy, scale, fill, outline):
    """Draw a classic arrow mouse pointer with its tip at (ox, oy)."""
    # Arrow pointer polygon (pointing up-left), then the tail notch.
    pts = [
        (0.0, 0.0), (0.0, 0.72), (0.20, 0.55), (0.33, 0.86),
        (0.46, 0.80), (0.32, 0.50), (0.56, 0.50),
    ]
    poly = [(ox + x * scale, oy + y * scale) for x, y in pts]
    draw.polygon(poly, fill=fill, outline=outline)
    # Thicken the outline for a crisp edge.
    draw.line(poly + [poly[0]], fill=outline, width=max(2, int(scale * 0.03)),
              joint="curve")


def draw_arc(draw, cx, cy, r, start, end, color, width):
    draw.arc([cx - r, cy - r, cx + r, cy + r], start, end, fill=color,
             width=width)


def build(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 512.0

    # Background tile.
    m = int(24 * s)
    rounded_rect(d, [m, m, size - m, size - m], radius=int(96 * s),
                 fill=TILE, outline=BORDER, width=max(1, int(6 * s)))

    # Motion arcs (jiggle) emanating to the right of the cursor.
    cx, cy = size * 0.60, size * 0.46
    for i, r in enumerate((0.16, 0.24, 0.32)):
        col = ACCENT if i == 0 else ACCENT_DK
        draw_arc(d, cx, cy, int(size * r), -55, 55, col,
                 width=max(2, int((14 - i * 3) * s)))

    # Mouse cursor, accent green with a white edge.
    cur = size * 0.30
    draw_cursor(d, size * 0.30, size * 0.26, cur, ACCENT, WHITE)
    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    master = build(MASTER)
    png_path = os.path.join(here, "zen-jiggler.png")
    master.resize((256, 256), Image.LANCZOS).save(png_path)

    ico_path = os.path.join(here, "zen-jiggler.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    master.save(ico_path, format="ICO",
                sizes=[(x, x) for x in sizes])
    print("wrote", png_path)
    print("wrote", ico_path)


if __name__ == "__main__":
    main()
