from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple

from PIL import Image, ImageFilter


RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    bg: RGB = (11, 14, 20)          # #0b0e14
    panel: RGB = (15, 23, 42)       # #0f172a
    panel2: RGB = (11, 16, 32)      # #0b1020
    border: RGB = (51, 65, 85)      # #334155
    neon: RGB = (14, 165, 233)      # #0ea5e9
    neon2: RGB = (34, 211, 238)     # #22d3ee
    purple: RGB = (168, 85, 247)    # #a855f7
    text: RGB = (230, 230, 230)     # #e6e6e6


def _put(img: Image.Image, x: int, y: int, c: RGB) -> None:
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def _hline(img: Image.Image, x0: int, x1: int, y: int, c: RGB) -> None:
    for x in range(x0, x1 + 1):
        _put(img, x, y, c)


def _vline(img: Image.Image, x: int, y0: int, y1: int, c: RGB) -> None:
    for y in range(y0, y1 + 1):
        _put(img, x, y, c)


def _rect(img: Image.Image, x0: int, y0: int, x1: int, y1: int, c: RGB) -> None:
    for y in range(y0, y1 + 1):
        _hline(img, x0, x1, y, c)


def _frame(img: Image.Image, x0: int, y0: int, x1: int, y1: int, c: RGB) -> None:
    _hline(img, x0, x1, y0, c)
    _hline(img, x0, x1, y1, c)
    _vline(img, x0, y0, y1, c)
    _vline(img, x1, y0, y1, c)


def _draw_pixel_art(size: int = 32, pal: Palette | None = None) -> Image.Image:
    pal = pal or Palette()
    img = Image.new("RGB", (size, size), pal.bg)

    # Outer neon frame (pixel-y, with tech gaps)
    _frame(img, 1, 1, size - 2, size - 2, pal.border)
    for i in range(3, size - 3, 6):
        _put(img, i, 1, pal.neon)
        _put(img, i + 1, 1, pal.neon2)
        _put(img, size - 2, i, pal.neon)
        _put(img, size - 2, i + 1, pal.neon2)

    # Inner panel
    _rect(img, 3, 3, size - 4, size - 4, pal.panel)
    _frame(img, 3, 3, size - 4, size - 4, pal.border)

    # Top bar with 3 status pixels
    _rect(img, 4, 4, size - 5, 7, pal.panel2)
    _put(img, 6, 6, pal.neon)
    _put(img, 8, 6, pal.purple)
    _put(img, 10, 6, pal.neon2)

    # Center "A" badge (hex-ish)
    cx, cy = size // 2, size // 2 + 1
    badge = [
        (cx - 6, cy - 4, cx + 6, cy + 4),
    ]
    for (x0, y0, x1, y1) in badge:
        _rect(img, x0, y0, x1, y1, pal.panel2)
        _frame(img, x0, y0, x1, y1, pal.neon)
        _put(img, x0 + 1, y0 + 1, pal.neon2)
        _put(img, x1 - 1, y1 - 1, pal.neon2)

    # Pixel "A" inside
    ax0, ay0 = cx - 3, cy - 2
    a_pixels: Iterable[Tuple[int, int]] = [
        (0, 4),
        (1, 3), (1, 4),
        (2, 2), (2, 4),
        (3, 1), (3, 2), (3, 3), (3, 4),
        (4, 2), (4, 4),
        (5, 3), (5, 4),
        (6, 4),
    ]
    for px, py in a_pixels:
        _put(img, ax0 + px, ay0 + py, pal.text)

    # Circuit traces at bottom
    _hline(img, 6, size - 7, size - 8, pal.border)
    _put(img, 8, size - 8, pal.neon)
    _put(img, size - 9, size - 8, pal.neon2)
    _vline(img, 8, size - 8, size - 6, pal.border)
    _vline(img, size - 9, size - 8, size - 6, pal.border)
    _put(img, 8, size - 6, pal.neon)
    _put(img, size - 9, size - 6, pal.neon2)

    return img


def icon_image(size: int = 64) -> "Image.Image":
    """
    Generate an original pixel-art sci‑fi icon at the requested size.
    """
    base = _draw_pixel_art(32)
    out = base.resize((size, size), resample=Image.Resampling.NEAREST)
    # Subtle glow overlay for larger sizes
    if size >= 96:
        glow = out.copy().filter(ImageFilter.GaussianBlur(radius=max(1, size // 64)))
        out = Image.blend(out, glow, alpha=0.18)
    return out.convert("RGBA")


def write_png(path: Path, size: int = 256) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    icon_image(size=size).save(path, format="PNG")
    return path


def write_ico(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    base = icon_image(size=256)
    base.save(path, format="ICO", sizes=sizes)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-ico", type=str, required=True)
    parser.add_argument("--out-png", type=str, default="")
    args = parser.parse_args()

    ico = write_ico(Path(args.out_ico))
    if args.out_png:
        write_png(Path(args.out_png))
    print(str(ico))


if __name__ == "__main__":
    main()
