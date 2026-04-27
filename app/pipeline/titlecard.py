"""Title card renderer.

INTEGRATION POINT — once you have the Figma file for a channel, replace
`render_titlecard_track` with the channel-specific animation. The contract
this module promises to the rest of the pipeline:

  Input:
    - title:    the Reddit-post question text
    - duration: how long the card should hold on screen, in seconds
    - channel:  string like "channel_a" or "channel_b" — directory under
                ./channels/ that may contain a template.mov (alpha) and
                channel.json with style overrides
    - out_path: where to write a transparent .mov (yuva444p10le ProRes 4444)

  Output:
    - a .mov file at `out_path`, exactly `duration` seconds long, 1080x1920,
      30fps, with alpha. The pipeline will composite it on top of the
      background video for the first `duration` seconds.

The placeholder below draws a centered rounded card with the title text in
PIL — it's intentionally bland so that when you swap in the real animation
you'll see a clear visual difference.

If `channels/<channel>/template.mov` exists we use it as the animated
background of the card (so you can pre-render a Figma-export from After
Effects as a transparent mov and get the real thing). The title text is
drawn on top, scaled to fit a content box.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import math
import subprocess

from PIL import Image, ImageDraw, ImageFilter

from app.config import (
    FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS, CHANNELS_DIR,
)
from app.pipeline.captions import _load_font, _hex_to_rgba


@dataclass
class TitleCardStyle:
    card_w: int = 920                # logical card box
    card_h: int = 520
    card_radius: int = 56
    card_fill: str = "#0a0a0a"
    card_border: str = "#262626"
    border_width: int = 2
    text_color: str = "#ffffff"
    font_family: str = "Helvetica"
    font_weight: str = "Bold"
    text_padding: int = 64
    text_max_size: int = 72
    text_min_size: int = 36
    pop_duration: float = 0.40       # seconds; card pops in over this window


def _channel_style(channel: str) -> TitleCardStyle:
    s = TitleCardStyle()
    cfg = CHANNELS_DIR / channel / "channel.json"
    if cfg.exists():
        try:
            d = json.loads(cfg.read_text())
            for k, v in d.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        except Exception:
            pass
    return s


def _wrap_to_box(draw: ImageDraw.ImageDraw, text: str, font_path_finder, weight: str,
                 max_w: int, max_h: int, max_size: int, min_size: int):
    """Try sizes from max_size down to min_size; for each, word-wrap and
    check if the wrapped block fits in (max_w, max_h). Returns (font, lines)."""
    from PIL import ImageFont
    words = text.split()
    for size in range(max_size, min_size - 1, -2):
        font = _load_font("Helvetica", weight, size)
        # greedy word wrap
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if font.getlength(trial) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        block_h = int(line_h * 1.2 * len(lines))
        if block_h <= max_h and all(font.getlength(l) <= max_w for l in lines):
            return font, lines
    # fallback: smallest
    font = _load_font("Helvetica", weight, min_size)
    return font, words


def _draw_card(canvas: Image.Image, title: str, style: TitleCardStyle, progress: float) -> None:
    """Draw the placeholder card at the given pop progress (0..1)."""
    # ease-out scale and fade
    eased = 1 - (1 - progress) ** 3
    scale = 0.7 + 0.3 * eased
    alpha = int(255 * eased)

    cw = int(style.card_w * scale)
    ch = int(style.card_h * scale)
    cx = OUTPUT_W // 2
    cy = OUTPUT_H // 2
    x0 = cx - cw // 2
    y0 = cy - ch // 2

    sub = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sub)
    sd.rounded_rectangle((0, 0, cw, ch), radius=int(style.card_radius * scale),
                         fill=_hex_to_rgba(style.card_fill, alpha),
                         outline=_hex_to_rgba(style.card_border, alpha),
                         width=style.border_width)
    # text
    pad = int(style.text_padding * scale)
    inner_w = cw - 2 * pad
    inner_h = ch - 2 * pad
    font, lines = _wrap_to_box(sd, title, None, style.font_weight, inner_w, inner_h,
                               int(style.text_max_size * scale),
                               int(style.text_min_size * scale))
    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    spacing = int(line_h * 0.2)
    block_h = line_h * len(lines) + spacing * (len(lines) - 1)
    ty = (ch - block_h) // 2
    for line in lines:
        tw = font.getlength(line)
        tx = (cw - tw) // 2
        sd.text((tx, ty), line, font=font, fill=_hex_to_rgba(style.text_color, alpha))
        ty += line_h + spacing

    # subtle drop shadow under card
    if alpha > 50:
        shadow = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
        sshd = ImageDraw.Draw(shadow)
        sshd.rounded_rectangle((40, 40, cw + 40, ch + 40),
                               radius=int(style.card_radius * scale),
                               fill=(0, 0, 0, int(alpha * 0.5)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(24))
        canvas.alpha_composite(shadow, (x0 - 40, y0 - 40 + 16))
    canvas.alpha_composite(sub, (x0, y0))


def render_titlecard_track(title: str, duration: float, channel: str, out_path: Path) -> Path:
    """Render a transparent .mov of length `duration` containing the title card.
    Replace this function with the Figma-driven animation when ready."""
    style = _channel_style(channel)
    total_frames = max(1, int(round(duration * OUTPUT_FPS)))
    pop_frames = max(1, int(round(style.pop_duration * OUTPUT_FPS)))

    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{OUTPUT_W}x{OUTPUT_H}", "-r", str(OUTPUT_FPS),
        "-i", "-",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    try:
        for f in range(total_frames):
            if f < pop_frames:
                progress = f / pop_frames
            else:
                progress = 1.0
            canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
            _draw_card(canvas, title, style, progress)
            proc.stdin.write(canvas.tobytes())
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
            raise RuntimeError(f"titlecard ffmpeg failed: {err[-400:]}")
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    return out_path
