"""Draw brand/icon.png (256) and brand/icon@2x.png (512).

Run: docker run --rm -u "$(id -u):$(id -g)" --entrypoint python3 -v "$PWD":/repo -w /repo \
    ghcr.io/home-assistant/home-assistant:2026.10.4 scripts/make_icon.py
"""
import math

from PIL import Image, ImageDraw

S = 2048  # draw large, then scale down for smooth edges
WARM, MID, COOL = (255, 138, 42), (255, 194, 122), (244, 248, 255)
TILE = (27, 29, 36)
X0, X1, BASE, PEAK = 0.11 * S, 0.89 * S, 0.78 * S, 0.20 * S


def colour(x):
    """Warm at the edges, cool white at the centre."""
    t = abs((x - X0) / (X1 - X0) - 0.5) * 2  # 0 centre, 1 edge
    a, b, f = (COOL, MID, t / 0.4) if t < 0.4 else (MID, WARM, (t - 0.4) / 0.6)
    return tuple(round(p + (q - p) * f) for p, q in zip(a, b))


def y(x):
    t = (x - X0) / (X1 - X0) * 2 - 1  # -1..1
    return BASE - (BASE - PEAK) * (1 + math.cos(math.pi * t)) / 2  # raised cosine


tile = Image.new("RGBA", (S, S))
ImageDraw.Draw(tile).rounded_rectangle((0, 0, S - 1, S - 1), radius=S * 0.22, fill=TILE)

# Fill under the curve, fading out towards the base.
fill = Image.new("RGBA", (S, S))
fd = fill.load()
for x in range(int(X0), int(X1)):
    c, top = colour(x), int(y(x))
    for yy in range(top, int(BASE)):
        fd[x, yy] = (*c, round(140 * (1 - (yy - top) / (BASE - top + 1)) ** 1.5))
tile = Image.alpha_composite(tile, fill)

# The curve: overlapping round dots along it, each in its own colour.
d, r = ImageDraw.Draw(tile), S * 0.028
steps = 1600
for i in range(steps + 1):
    x = X0 + (X1 - X0) * i / steps
    d.ellipse((x - r, y(x) - r, x + r, y(x) + r), fill=colour(x))

for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
    tile.resize((size, size), Image.LANCZOS).save(f"custom_components/lighting_curves/brand/{name}", optimize=True)
