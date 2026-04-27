"""Fallback bland card for channels without a custom renderer."""
from __future__ import annotations
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

from app.config import FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS
from app.pipeline.titlecards.common import (
    ease_out_cubic, hex_to_rgba, load_inter, wrap_to_box, open_alpha_writer,
)


def render(*, title: str, duration: float, channel: str, meta: dict, out_path: Path) -> Path:
    total = max(1, int(round(duration * OUTPUT_FPS)))
    pop = max(1, int(0.4 * OUTPUT_FPS))
    proc, stdin = open_alpha_writer(out_path)
    try:
        for f in range(total):
            t = f / OUTPUT_FPS
            p = ease_out_cubic(min(1.0, t / 0.4)) if f < pop else 1.0
            scale = 0.7 + 0.3 * p
            alpha = int(255 * p)
            cw = int(920 * scale); ch = int(520 * scale)
            cx, cy = OUTPUT_W // 2, OUTPUT_H // 2
            canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
            sub = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            d = ImageDraw.Draw(sub)
            d.rounded_rectangle((0, 0, cw, ch), radius=int(56 * scale),
                                fill=hex_to_rgba("#0a0a0a", alpha),
                                outline=hex_to_rgba("#262626", alpha), width=2)
            font, lines = wrap_to_box(title, "Bold", int(72 * scale), int(36 * scale),
                                       cw - int(96 * scale), ch - int(96 * scale))
            line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
            spacing = int(line_h * 0.2)
            block_h = line_h * len(lines) + spacing * (len(lines) - 1)
            ty = (ch - block_h) // 2
            for line in lines:
                tw = font.getlength(line); tx = (cw - tw) // 2
                d.text((tx, ty), line, font=font, fill=hex_to_rgba("#ffffff", alpha))
                ty += line_h + spacing
            canvas.alpha_composite(sub, (cx - cw // 2, cy - ch // 2))
            stdin.write(canvas.tobytes())
        stdin.close()
        proc.wait()
    finally:
        if not stdin.closed:
            stdin.close()
    return out_path
