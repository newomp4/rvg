"""Shared helpers used by all title-card renderers."""
from __future__ import annotations
from pathlib import Path
from typing import Tuple
import math
import subprocess
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS, ASSETS_DIR


# ---- easing -----------------------------------------------------------------

def ease_out_cubic(p: float) -> float:
    p = max(0.0, min(1.0, p))
    return 1.0 - (1.0 - p) ** 3


def ease_out_back(p: float, overshoot: float = 1.70158) -> float:
    """Bouncy ease-out that overshoots before settling. Good for pop-ins."""
    p = max(0.0, min(1.0, p)) - 1.0
    return 1.0 + (overshoot + 1.0) * p ** 3 + overshoot * p ** 2


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ---- color ------------------------------------------------------------------

def hex_to_rgba(h: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


# ---- fonts ------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

# Map weight name -> filename inside ./assets/fonts/
_INTER_FILES = {
    "Regular":  "Inter-Regular.ttf",
    "SemiBold": "Inter-SemiBold.ttf",
    "Bold":     "Inter-Bold.ttf",
}


def load_inter(weight: str = "Bold", size: int = 56) -> ImageFont.FreeTypeFont:
    key = (weight, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    fname = _INTER_FILES.get(weight, _INTER_FILES["Bold"])
    path = ASSETS_DIR / "fonts" / fname
    if not path.exists():
        raise FileNotFoundError(f"missing Inter font: {path} — run ./setup.sh")
    f = ImageFont.truetype(str(path), size=size)
    _FONT_CACHE[key] = f
    return f


def wrap_to_box(text: str, weight: str, max_size: int, min_size: int,
                max_w: int, max_h: int, line_spacing_ratio: float = 0.18,
                step: int = 2) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Try font sizes from max_size down to min_size; for each, word-wrap and
    check if the wrapped block fits in (max_w, max_h). Returns (font, lines)."""
    words = text.split()
    if not words:
        return load_inter(weight, max_size), []
    for size in range(max_size, min_size - 1, -step):
        font = load_inter(weight, size)
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if font.getlength(trial) <= max_w:
                cur = trial
            else:
                # if a single word doesn't fit, hard-break it (very long URLs etc)
                if not cur:
                    lines.append(w)
                    cur = ""
                else:
                    lines.append(cur)
                    cur = w
        if cur:
            lines.append(cur)
        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        block_h = int(line_h * (1 + line_spacing_ratio) * len(lines))
        if block_h <= max_h and all(font.getlength(l) <= max_w for l in lines):
            return font, lines
    # didn't fit at minimum — return as-is, caller can clip
    font = load_inter(weight, min_size)
    return font, words


def measure_block(font: ImageFont.FreeTypeFont, lines: list[str],
                  line_spacing_ratio: float = 0.18) -> tuple[int, int]:
    if not lines:
        return 0, 0
    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    spacing = int(line_h * line_spacing_ratio)
    block_h = line_h * len(lines) + spacing * (len(lines) - 1)
    block_w = max(int(font.getlength(l)) for l in lines)
    return block_w, block_h


def draw_lines(canvas: Image.Image, x: int, y: int, lines: list[str],
               font: ImageFont.FreeTypeFont, fill: tuple[int, int, int, int],
               line_spacing_ratio: float = 0.18) -> int:
    """Draw left-aligned lines starting at (x, y). Returns new y after block."""
    d = ImageDraw.Draw(canvas)
    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    spacing = int(line_h * line_spacing_ratio)
    cur = y
    for line in lines:
        d.text((x, cur), line, font=font, fill=fill)
        cur += line_h + spacing
    return cur - spacing


# ---- alpha-channel video writer --------------------------------------------

def open_alpha_writer(out_path: Path):
    """Open an ffmpeg subprocess that consumes raw RGBA frames at OUTPUT_W ×
    OUTPUT_H × OUTPUT_FPS and writes a ProRes 4444 .mov with alpha."""
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{OUTPUT_W}x{OUTPUT_H}", "-r", str(OUTPUT_FPS),
        "-i", "-",
        "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc, proc.stdin


def write_frame(stdin, frame: Image.Image) -> None:
    if frame.mode != "RGBA":
        frame = frame.convert("RGBA")
    stdin.write(frame.tobytes())


# ---- compositing helpers ---------------------------------------------------

def paste_pixmap(canvas: Image.Image, png_path: Path, x: int, y: int,
                 width: int, *, alpha: int = 255, scale: float = 1.0) -> None:
    """Paste a PNG onto canvas, resized to `width` (height auto), centered at (x,y).
    `alpha` 0..255 multiplies into the image's existing alpha channel.
    `scale` is a multiplier on top of `width` (used by pop animations)."""
    img = Image.open(png_path).convert("RGBA")
    target_w = max(1, int(width * scale))
    aspect = img.height / img.width
    target_h = max(1, int(target_w * aspect))
    img = img.resize((target_w, target_h), Image.LANCZOS)
    if alpha < 255:
        a = img.split()[3]
        a = a.point(lambda v: int(v * alpha / 255))
        img.putalpha(a)
    canvas.alpha_composite(img, (x - target_w // 2, y - target_h // 2))


def stroked_rounded_rect(size: tuple[int, int], radius: int, fill_rgba: tuple[int, int, int, int],
                          stroke_rgba: tuple[int, int, int, int] | None = None,
                          stroke_width: int = 0,
                          shadow: tuple[int, int, int, int] | None = None,
                          shadow_offset: tuple[int, int] = (0, 8),
                          shadow_blur: int = 24) -> Image.Image:
    """Build a small RGBA image of a rounded rectangle with optional stroke
    and an outset drop shadow. Returned image is exactly `size` — caller is
    responsible for compositing it onto the larger canvas."""
    w, h = size
    pad = max(shadow_blur * 2, abs(shadow_offset[0]) + shadow_blur, abs(shadow_offset[1]) + shadow_blur)
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    if shadow is not None:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        sd.rounded_rectangle((pad + shadow_offset[0], pad + shadow_offset[1],
                              pad + shadow_offset[0] + w, pad + shadow_offset[1] + h),
                             radius=radius, fill=shadow)
        if shadow_blur > 0:
            sh = sh.filter(ImageFilter.GaussianBlur(shadow_blur))
        canvas = Image.alpha_composite(canvas, sh)
    d = ImageDraw.Draw(canvas)
    if stroke_width > 0 and stroke_rgba is not None:
        d.rounded_rectangle((pad - stroke_width, pad - stroke_width,
                             pad + w + stroke_width, pad + h + stroke_width),
                            radius=radius + stroke_width, fill=stroke_rgba)
    d.rounded_rectangle((pad, pad, pad + w, pad + h), radius=radius, fill=fill_rgba)
    return canvas, pad


def draw_heart_outline(canvas: Image.Image, cx: int, cy: int, size: int,
                       color: tuple[int, int, int, int], stroke: int = 4) -> None:
    """Hand-drawn heart outline (Reddit's style is a thin gray outline). We
    can't rely on a Unicode heart since stroke widths matter and PIL won't
    stroke text glyphs cleanly."""
    # heart curve: two arcs at the top + downward V at bottom
    d = ImageDraw.Draw(canvas)
    s = size
    # bounding box for left lobe and right lobe (half-circles)
    left_box  = (cx - s, cy - s, cx,         cy)
    right_box = (cx,     cy - s, cx + s,     cy)
    # arcs (180° each)
    d.arc(left_box,  start=180, end=360, fill=color, width=stroke)
    d.arc(right_box, start=180, end=360, fill=color, width=stroke)
    # bottom V — connect arc endpoints to the bottom point
    bottom_y = cy + int(s * 0.95)
    d.line((cx - s, cy, cx, bottom_y), fill=color, width=stroke)
    d.line((cx + s, cy, cx, bottom_y), fill=color, width=stroke)
