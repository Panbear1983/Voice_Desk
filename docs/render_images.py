"""Turn the SVG screenshots into PNGs and the animated loop GIF for the README.

  python3 docs/render_images.py      # needs playwright (chromium) + Pillow; run after screenshots.py

PNG because GitHub shows SVG screenshots without their fonts; the GIF is the hero image.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

IMG = Path(__file__).resolve().parent / "img"
LOOP = ["01_listening", "02_hearing", "03_thinking", "04_speaking"]
LOOP_MS = [1400, 1100, 1700, 2400]


def render(names: list[str] | None = None) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=2)
        sources = [IMG / (name + ".svg") for name in names] if names else sorted(IMG.glob("*.svg"))
        for svg in sources:
            page.goto(svg.as_uri())
            page.wait_for_timeout(400)                      # web font
            box = page.locator("svg").bounding_box()
            page.set_viewport_size({"width": int(box["width"]) + 1, "height": int(box["height"]) + 1})
            png = svg.with_suffix(".png")
            page.locator("svg").screenshot(path=str(png), omit_background=True)
            im = Image.open(png)
            im.crop(im.getbbox()).save(png, optimize=True)          # drop the transparent margin
        browser.close()


def gif() -> None:
    frames = [Image.open(IMG / f"{n}.png").convert("RGBA") for n in LOOP]
    w = 900
    frames = [f.resize((w, int(f.height * w / f.width)), Image.LANCZOS) for f in frames]
    flat = []
    for f in frames:
        bg = Image.new("RGB", f.size, (13, 17, 23))
        bg.paste(f, mask=f.split()[3])
        flat.append(bg.convert("P", palette=Image.ADAPTIVE, colors=128))
    flat[0].save(IMG / "loop.gif", save_all=True, append_images=flat[1:], duration=LOOP_MS, loop=0, optimize=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", help="Render only these SVG basenames")
    parser.add_argument("--no-gif", action="store_true", help="Keep the existing hero animation")
    args = parser.parse_args()
    render(args.only)
    if not args.no_gif:
        gif()
    for f in sorted(IMG.iterdir()):
        print(f"{f.name:22} {f.stat().st_size // 1024:>5} KB")
