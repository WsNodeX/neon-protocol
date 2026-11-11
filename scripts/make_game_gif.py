#!/usr/bin/env python3
"""
Turn a directory of neon-protocol screenshots into an animated GIF.

GitHub renders GIFs inline and autoplays them. For a game, that is worth more
than any amount of prose: a stranger can see what it looks like without cloning
anything or believing a claim.

Two constraints drive the settings:

  * GitHub caps inline images at 10 MB, and a README GIF should be far smaller
    than that — a 3-second loop that takes ten seconds to load costs more than it
    earns.
  * Animated GIF stores a full palette per frame. 51 frames at 1200x750 with 256
    colours comes out around 8 MB. Halving the dimensions quarters the pixels;
    dropping to 64 colours and letting Pillow share one palette across frames
    takes it to a few hundred KB.

Usage:
    python scripts/make_game_gif.py --shots DIR --out assets/gameplay.gif
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image

# The game logic runs at 120 Hz. Capturing every 12th frame therefore yields a
# 10 fps sequence, which is the frame rate GIF viewers expect anyway.
CAPTURE_STRIDE = 12
TICK_HZ = 120


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=560)
    ap.add_argument("--colors", type=int, default=64)
    ap.add_argument("--stride", type=int, default=CAPTURE_STRIDE,
                    help="frames skipped between captures, as passed to --shot-frames")
    args = ap.parse_args()

    src = Path(args.shots)
    # Only the gameplay captures; the title card and the full map do not belong
    # in a loop.
    frames = sorted(
        (p for p in src.glob("*play_f*.png")),
        key=lambda p: int(re.search(r"f(\d+)", p.name).group(1)),
    )
    if not frames:
        print(f"no gameplay frames in {src}")
        return 1

    first = Image.open(frames[0]).convert("RGB")
    ratio = args.width / first.width
    size = (args.width, round(first.height * ratio))

    images = []
    resized = []
    for p in frames:
        img = Image.open(p).convert("RGB").resize(size, Image.LANCZOS)
        resized.append(img)

    # One palette for the whole animation, derived from a contact sheet of every
    # frame. Quantising each frame independently gives each its own palette, and
    # the colours visibly shift frame to frame — on a neon scene, the dark
    # gradients band into flat patches that were not in the render at all.
    cols = 8
    rows = (len(resized) + cols - 1) // cols
    sheet = Image.new("RGB", (size[0] * cols, size[1] * rows), (0, 0, 0))
    for i, img in enumerate(resized):
        sheet.paste(img, ((i % cols) * size[0], (i // cols) * size[1]))
    palette_img = sheet.quantize(colors=args.colors, method=Image.MEDIANCUT)

    for img in resized:
        # `dither=NONE` keeps the small text in the HUD legible; dithering turns
        # it into speckle at this size.
        images.append(img.quantize(palette=palette_img, dither=Image.Dither.NONE))

    duration_ms = round(1000 * args.stride / TICK_HZ)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out,
        "GIF",
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )

    kb = out.stat().st_size / 1024
    print(f"{len(images)} frames  {size[0]}x{size[1]}  {duration_ms} ms/frame  -> {out}  ({kb:.0f} KB)")
    if kb > 2000:
        print("warning: over 2 MB — drop --width or --colors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
