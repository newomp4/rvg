"""Render a transparent caption track as a single .mov with alpha.

The approach: walk every video frame, decide which word (if any) is active
at that frame's timestamp, render that word centered with stroke + drop
shadow at the right color and opacity, and write the frame as RGBA. Pipe
the RGBA stream into ffmpeg, which encodes it as ProRes 4444 (alpha-aware).

Why a separate mov instead of drawtext? Per-word color + drop shadow + pop
animation is awkward in ffmpeg's filter graph, but trivial in PIL. One
overlay filter at the end is cheaper than a giant filtergraph anyway.

We use a small font cache so we only load each font/size once.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List
import math
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import (
    FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS, CaptionStyle,
)
from app.pipeline.tts import TimedWord
from app.pipeline.story import Word


@dataclass
class WordOnScreen:
    text: str
    color: str
    start: float
    end: float


def merge_words(plain_words: list[Word], timed: list[TimedWord]) -> list[WordOnScreen]:
    """Pair the color-tagged Words with the timed words from TTS.

    Edge-tts emits one WordBoundary per spoken word in order, so we just
    zip — when counts mismatch (rare, for hyphenated/contraction edge cases)
    we fall back to assigning by index, padding with default colors.
    """
    out: list[WordOnScreen] = []
    n = min(len(plain_words), len(timed))
    for i in range(n):
        w = plain_words[i]
        t = timed[i]
        out.append(WordOnScreen(text=w.text, color=w.color, start=t.start, end=t.end))
    # any extra timed words (shouldn't happen often) — show with default color
    for i in range(n, len(timed)):
        out.append(WordOnScreen(text=timed[i].text, color="#ffffff",
                                start=timed[i].start, end=timed[i].end))
    return out


# ---- font lookup -----------------------------------------------------------

# We try a few common system font names. PIL's ImageFont needs a path on most
# platforms; on macOS we walk the standard font directories.
import os

_FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
]


def _find_font_file(family: str, weight: str) -> str | None:
    targets = []
    fam = family.replace(" ", "")
    if weight.lower() in ("bold", "black"):
        targets += [f"{fam}-Bold.ttf", f"{fam}-Bold.otf", f"{fam}Bold.ttf",
                    f"{fam}-Black.ttf", f"{fam}-Heavy.ttf"]
    targets += [f"{fam}.ttf", f"{fam}.otf", f"{fam}-Regular.ttf"]
    # Helvetica special-case: macOS ships a TTC
    if fam.lower() == "helvetica":
        targets = ["Helvetica.ttc"] + targets

    for d in _FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if name in targets:
                return os.path.join(d, name)
    return None


_FONT_CACHE: dict[tuple, ImageFont.FreeTypeFont] = {}


def _load_font(family: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    key = (family, weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = _find_font_file(family, weight) or _find_font_file("Helvetica", weight) \
        or "/System/Library/Fonts/Helvetica.ttc"
    try:
        f = ImageFont.truetype(path, size=size)
    except Exception:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


# ---- frame rendering -------------------------------------------------------

def _hex_to_rgba(h: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _draw_word_centered(canvas: Image.Image, word: str, font: ImageFont.FreeTypeFont,
                        color: str, style: CaptionStyle, scale: float = 1.0) -> None:
    if not word:
        return
    text = word.upper() if style.uppercase else word

    # Measure with the font's bbox for accurate centering
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cx, cy = OUTPUT_W // 2, OUTPUT_H // 2
    x = cx - (tw // 2) - bbox[0]
    y = cy - (th // 2) - bbox[1]

    # If we need to scale (pop animation), draw onto a sub-canvas first and paste
    if scale != 1.0:
        sub_w, sub_h = int(OUTPUT_W * 0.9), int(OUTPUT_H * 0.6)
        sub = Image.new("RGBA", (sub_w, sub_h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sub)
        sx = sub_w // 2 - tw // 2 - bbox[0]
        sy = sub_h // 2 - th // 2 - bbox[1]
        # shadow
        if style.shadow_opacity > 0:
            sh = Image.new("RGBA", (sub_w, sub_h), (0, 0, 0, 0))
            shd = ImageDraw.Draw(sh)
            shd.text((sx + style.shadow_offset[0], sy + style.shadow_offset[1]),
                     text, font=font, fill=_hex_to_rgba(style.shadow_color,
                                                        int(255 * style.shadow_opacity)))
            if style.shadow_blur > 0:
                sh = sh.filter(ImageFilter.GaussianBlur(style.shadow_blur))
            sub = Image.alpha_composite(sub, sh)
            sd = ImageDraw.Draw(sub)
        # stroke + fill in one pass via PIL's stroke kwargs
        sd.text((sx, sy), text, font=font, fill=_hex_to_rgba(color),
                stroke_width=style.stroke_width,
                stroke_fill=_hex_to_rgba(style.stroke_color))

        new_w, new_h = int(sub_w * scale), int(sub_h * scale)
        sub = sub.resize((new_w, new_h), Image.LANCZOS)
        canvas.alpha_composite(sub, (cx - new_w // 2, cy - new_h // 2))
        return

    draw = ImageDraw.Draw(canvas)
    if style.shadow_opacity > 0:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shd = ImageDraw.Draw(sh)
        shd.text((x + style.shadow_offset[0], y + style.shadow_offset[1]),
                 text, font=font,
                 fill=_hex_to_rgba(style.shadow_color, int(255 * style.shadow_opacity)))
        if style.shadow_blur > 0:
            sh = sh.filter(ImageFilter.GaussianBlur(style.shadow_blur))
        canvas.alpha_composite(sh)
    draw.text((x, y), text, font=font, fill=_hex_to_rgba(color),
              stroke_width=style.stroke_width,
              stroke_fill=_hex_to_rgba(style.stroke_color))


def render_captions_track(
    words: list[WordOnScreen],
    style: CaptionStyle,
    duration: float,
    start_offset: float,
    out_path: Path,
) -> Path:
    """Render an alpha-channel .mov of length `duration` with captions.

    `start_offset` shifts all word times forward (so captions only begin
    after the title card). Words past the duration are clipped.
    """
    font = _load_font(style.font_family, style.font_weight, style.font_size)
    total_frames = max(1, int(round(duration * OUTPUT_FPS)))

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

    pop_d = max(1, int(style.pop_duration_ms / 1000 * OUTPUT_FPS))

    try:
        # pre-shift word times
        shifted = [WordOnScreen(w.text, w.color, w.start + start_offset,
                                w.end + start_offset) for w in words]
        idx = 0
        for f in range(total_frames):
            t = f / OUTPUT_FPS
            # advance idx until current word ends at or after t
            while idx < len(shifted) and shifted[idx].end < t:
                idx += 1
            canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
            if idx < len(shifted) and shifted[idx].start <= t < shifted[idx].end:
                w = shifted[idx]
                # pop animation: scale ramps from 0.85 -> pop_scale -> 1.0
                age_frames = f - int(round(w.start * OUTPUT_FPS))
                if age_frames < pop_d:
                    # ease-out cubic 0..1
                    p = age_frames / pop_d
                    eased = 1 - (1 - p) ** 3
                    scale = 0.85 + (style.pop_scale - 0.85) * eased
                else:
                    settle = min(1.0, (age_frames - pop_d) / max(1, pop_d))
                    scale = style.pop_scale + (1.0 - style.pop_scale) * settle
                _draw_word_centered(canvas, w.text, font, w.color, style, scale=scale)
            proc.stdin.write(canvas.tobytes())
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
            raise RuntimeError(f"caption ffmpeg failed: {err[-400:]}")
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    return out_path
