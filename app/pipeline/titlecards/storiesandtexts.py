"""Title card renderer for @storiesandtexts — Playwright + HTML/CSS edition.

Why this approach instead of PIL frame-by-frame:
  - The browser rasterizes each layer ONCE and applies CSS transforms
    (scale, translate, opacity) with consistent sub-pixel sampling on every
    frame. PIL re-rasterizes at every scale step, which is what produces
    the shimmery "rasterized" look on previous versions.
  - CSS animation curves match Figma's smart-animate engine (cubic-bezier),
    so the motion feels designerly rather than mechanical.
  - Native font hinting + antialiasing — text looks like it does in Chrome.
  - Crisp SVG icons (verified badge, heart) at any size.

Pipeline:
  1. Read channel.json for display_name, handle, profile_picture, verified.
  2. Auto-fit the headline by binary-searching font-size against viewport.
  3. Load the HTML template into headless Chromium (one-time, ~300ms).
  4. Inject data; for each frame, call setTime(t) and screenshot.
  5. Pipe RGBA frames into ffmpeg → ProRes 4444 .mov with alpha.
"""
from __future__ import annotations
from pathlib import Path
import base64
import json
import math
import subprocess
from urllib.parse import quote

from app.config import (
    OUTPUT_W, OUTPUT_H, OUTPUT_FPS, CHANNELS_DIR, ASSETS_DIR, FFMPEG,
)


# ---------------------------------------------------------------- constants

_TEMPLATE_FILE = Path(__file__).parent / "storiesandtexts" / "template.html"
_REDDIT_ICON = ASSETS_DIR / "reddit-icon.png"
HEADLINE_MAX = 64
HEADLINE_MIN = 38


# ---------------------------------------------------------------- helpers

def _channel_dir(channel: str) -> Path:
    return CHANNELS_DIR / channel


def _profile_data_url(channel: str, meta: dict) -> str:
    """Return a base64 data: URL for the profile picture so the headless
    page doesn't need filesystem access."""
    p = (meta.get("profile_picture") or "").strip()
    if not p:
        return ""
    candidate = (_channel_dir(channel) / p).resolve()
    if not candidate.exists():
        return ""
    suffix = candidate.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "octet-stream")
    encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def _font_dir_url() -> str:
    return (ASSETS_DIR / "fonts").resolve().as_uri()


def _build_html(headline_size: int) -> str:
    src = _TEMPLATE_FILE.read_text()
    return (src
        .replace("__VIEWPORT_W__", str(OUTPUT_W))
        .replace("__VIEWPORT_H__", str(OUTPUT_H))
        .replace("__FONT_DIR__", _font_dir_url())
        .replace("__HEADLINE_SIZE__", str(headline_size)))


def _pick_headline_size(title: str) -> int:
    """Quick estimate of a font-size that fits the headline in the card.
    Browser does the final layout; this just biases toward a clean look.

    Card inner width ≈ 732px (820 − 44*2). At size S, Inter ExtraBold
    average char width ≈ 0.52 * S px. We want ~3 lines max with ~22-26
    chars per line at the larger sizes.
    """
    n = len(title)
    if n <= 32: return HEADLINE_MAX        # 64
    if n <= 50: return 56
    if n <= 70: return 50
    if n <= 95: return 44
    return HEADLINE_MIN                    # 38


def _reddit_icon_url() -> str:
    """Return a base64 data: URL for the bundled Reddit icon, or '' if
    missing. Same approach as profile pic — keeps the page sandbox clean."""
    if not _REDDIT_ICON.exists():
        return ""
    encoded = base64.b64encode(_REDDIT_ICON.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# ---------------------------------------------------------------- render

def render(*, title: str, duration: float, channel: str, meta: dict, out_path: Path) -> Path:
    from playwright.sync_api import sync_playwright

    headline_size = _pick_headline_size(title or "")
    html = _build_html(headline_size)
    profile_url = _profile_data_url(channel, meta)

    data_for_page = {
        "display_name": (meta.get("display_name") or "").strip(),
        "handle": (meta.get("handle") or "").strip(),
        "profile_url": profile_url,
        "reddit_url": _reddit_icon_url(),
        "verified": bool(meta.get("verified")),
        "headline": title or "(no title)",
    }

    total_frames = max(1, int(round(duration * OUTPUT_FPS)))

    # Open ffmpeg writer for ProRes 4444 with alpha
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
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--font-render-hinting=none"])
            context = browser.new_context(
                viewport={"width": OUTPUT_W, "height": OUTPUT_H},
                device_scale_factor=1,
                color_scheme="light",
            )
            page = context.new_page()
            # data: URL avoids filesystem permissions on the page
            page.set_content(html, wait_until="networkidle")
            page.evaluate("(d) => window.setData(d)", data_for_page)
            # Tell the JS timeline how long the .mov is so it can place the
            # exit animation in the last `EXIT_DUR` seconds.
            page.evaluate(f"window.setTotalDur({duration})")
            # Give fonts a moment to settle
            page.wait_for_function("document.fonts.ready.then(() => true)")

            for f in range(total_frames):
                t = f / OUTPUT_FPS
                page.evaluate(f"window.setTime({t})")
                buf = page.screenshot(omit_background=True, type="png", full_page=False,
                                      clip={"x": 0, "y": 0, "width": OUTPUT_W, "height": OUTPUT_H})
                # Convert PNG → raw RGBA the cheap way: PIL decode (fast for small images)
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(buf)).convert("RGBA")
                if img.size != (OUTPUT_W, OUTPUT_H):
                    img = img.resize((OUTPUT_W, OUTPUT_H), Image.LANCZOS)
                proc.stdin.write(img.tobytes())
            browser.close()
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
            raise RuntimeError(f"titlecard ffmpeg failed: {err[-400:]}")
    finally:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()

    return out_path
