"""Title card renderer for the @storiesandtexts channel.

Replicates the Figma design:
  - White rounded card (~960px wide, height auto-grows with the headline)
  - 30px corner radius, subtle gradient stroke, drop shadow
  - Top-left:    orange Reddit-alien avatar (108×108 in design space)
  - Top-right:   "AskReddit" in Inter Bold + blue verified checkmark
  - Below that:  emoji row of small Reddit award icons
  - Middle:      headline in Inter Bold (auto-shrinks to fit)
  - Bottom-left: gray heart outline + "99+" in Inter SemiBold
  - Bottom-right: channel handle (e.g. "@storiesandtexts") in Inter SemiBold

Animation timeline (relative to t=0):
    0.00–0.45s   card pops in (back-ease scale 0.7 → 1.05 → 1.0, opacity 0 → 1)
    0.20–0.50s   header (avatar + "AskReddit" + verified) slides + fades in
    0.30–0.60s   emoji row pop-in, staggered 30ms apart per emoji
    0.40–0.70s   headline fades in with a small upward translate
    0.50–0.80s   bottom row (heart + 99+ + handle) fades in
    0.80s+       hold steady until duration ends
"""
from __future__ import annotations
from pathlib import Path
import math

from PIL import Image, ImageDraw

from app.config import (
    OUTPUT_W, OUTPUT_H, OUTPUT_FPS, CHANNELS_DIR,
)
from app.pipeline.titlecards.common import (
    ease_out_cubic, ease_out_back, ease_out_quart, lerp, hex_to_rgba, load_inter,
    wrap_to_box, measure_block, draw_lines, paste_pixmap,
    stroked_rounded_rect, open_alpha_writer, draw_heart_outline,
)


# ---- design constants (in CARD-LOCAL pixel units; will scale at render time)

CARD_W = 920                # final card width on screen (~85% of 1080 frame)
CARD_RADIUS = 36
CARD_BORDER_RGBA = (224, 224, 224, 255)
CARD_FILL = "#ffffff"
CARD_SHADOW = (0, 0, 0, 60)
CARD_SHADOW_OFFSET = (0, 14)
CARD_SHADOW_BLUR = 30

PAD_X = 52
PAD_TOP = 52
PAD_BOTTOM = 36
HEADER_AVATAR_SIZE = 112    # slightly bigger avatar reads cleaner at 1080p
HEADER_GAP = 22
HEADER_NAME_SIZE = 42
VERIFIED_SIZE = 38
EMOJI_SIZE = 44             # bumped from 36 — small icons looked low-bitrate
EMOJI_GAP = 8
HEADLINE_TOP_GAP = 32
HEADLINE_MAX_SIZE = 64
HEADLINE_MIN_SIZE = 40
HEADLINE_LINE_SPACING = 0.18
BOTTOM_GAP = 32
BOTTOM_TEXT_SIZE = 30
HEART_RADIUS = 18
HEART_STROKE = 5

# Vertical positions WITHIN the avatar's 112px band (relative to avatar top):
# - name aligns to upper third
# - emoji row aligns to lower third
NAME_Y_OFFSET   = 6                                   # name top relative to avatar top
EMOJI_Y_OFFSET  = HEADER_AVATAR_SIZE - EMOJI_SIZE//2 - 4   # emoji center near avatar bottom

# colors
TEXT_BLACK = "#0F1418"
MUTED_GRAY = "#A4A4A4"

# Animation phase windows (start, end) in seconds, relative to t=0.
# Card uses ease_out_quart (fast then slow, no overshoot). Other elements
# use ease_out_cubic and stagger with a smoother spread.
PHASE_CARD     = (0.00, 0.55)
PHASE_HEADER   = (0.25, 0.65)
PHASE_EMOJIS   = (0.35, 0.75)
PHASE_HEADLINE = (0.40, 0.80)
PHASE_BOTTOM   = (0.50, 0.90)


def _phase_progress(t: float, phase: tuple[float, float]) -> float:
    s, e = phase
    if t <= s:
        return 0.0
    if t >= e:
        return 1.0
    return (t - s) / (e - s)


def _icons_dir(channel: str) -> Path:
    return CHANNELS_DIR / channel / "icons"


def _emoji_paths(channel: str) -> list[Path]:
    d = _icons_dir(channel)
    if not d.exists():
        return []
    return sorted([p for p in d.glob("emoji_*.png") if p.is_file()])


# ---- layout: compute the card height needed for this title -----------------

def _layout(title: str, channel: str) -> dict:
    """Pre-compute a static layout (positions, sizes, line wraps). Returns
    a dict the per-frame renderer can read without re-computing."""
    pad_x = PAD_X
    inner_w = CARD_W - 2 * pad_x

    # Header row
    name_font = load_inter("Bold", HEADER_NAME_SIZE)
    name_text = "AskReddit"
    name_w = int(name_font.getlength(name_text))
    name_h = name_font.getbbox("Ag")[3] - name_font.getbbox("Ag")[1]

    header_y0 = PAD_TOP
    avatar_x = pad_x + HEADER_AVATAR_SIZE // 2
    avatar_y = header_y0 + HEADER_AVATAR_SIZE // 2

    # Name sits at the TOP of the avatar's vertical band (matches Figma);
    # we measure font metrics and pin the cap-height area up there.
    name_x = pad_x + HEADER_AVATAR_SIZE + HEADER_GAP
    # The bbox y0 of "AskReddit" is the top of caps, so subtract bbox y0 to
    # align caps to the desired y position rather than the font's draw-origin.
    name_bbox = name_font.getbbox("AskReddit")
    cap_top = name_bbox[1]                                    # > 0 typically
    name_y = header_y0 + NAME_Y_OFFSET - cap_top              # offset so caps land on NAME_Y_OFFSET
    name_caps_h = name_bbox[3] - name_bbox[1]                 # actual caps height
    verified_x = name_x + name_w + 10 + VERIFIED_SIZE // 2
    verified_y = header_y0 + NAME_Y_OFFSET + name_caps_h // 2  # vertical center of name caps

    # Emoji row aligns to the BOTTOM of the avatar's vertical band.
    emojis = _emoji_paths(channel)
    emoji_row_y = header_y0 + EMOJI_Y_OFFSET                  # center of emoji
    emoji_row_x_start = name_x + EMOJI_SIZE // 2

    header_bottom = header_y0 + HEADER_AVATAR_SIZE

    # Headline: word-wrap to fit width, find a font size that fits.
    headline_top = header_bottom + HEADLINE_TOP_GAP
    headline_max_w = inner_w
    # we don't yet know card height, so we let headline wrap freely and the
    # card height grows. The cap is HEADLINE_MIN_SIZE — if headline overflows
    # 5 lines at min size, we keep it (the card just gets taller).
    font, lines = wrap_to_box(
        title, "Bold",
        max_size=HEADLINE_MAX_SIZE, min_size=HEADLINE_MIN_SIZE,
        max_w=headline_max_w, max_h=10_000,            # effectively unlimited
        line_spacing_ratio=HEADLINE_LINE_SPACING,
    )
    _, headline_block_h = measure_block(font, lines, HEADLINE_LINE_SPACING)

    # Bottom row
    bottom_y = headline_top + headline_block_h + BOTTOM_GAP
    bottom_text_font = load_inter("SemiBold", BOTTOM_TEXT_SIZE)
    bottom_text_h = bottom_text_font.getbbox("Ag")[3] - bottom_text_font.getbbox("Ag")[1]

    card_h = bottom_y + bottom_text_h + PAD_BOTTOM

    return dict(
        card_w=CARD_W, card_h=card_h,
        pad_x=pad_x, inner_w=inner_w,
        avatar_x=avatar_x, avatar_y=avatar_y,
        name_x=name_x, name_y=name_y, name_w=name_w, name_h=name_caps_h, name_font=name_font,
        verified_x=verified_x, verified_y=verified_y,
        emojis=emojis,
        emoji_row_y=emoji_row_y, emoji_row_x_start=emoji_row_x_start,
        headline_top=headline_top, headline_lines=lines, headline_font=font,
        headline_block_h=headline_block_h,
        bottom_y=bottom_y, bottom_text_font=bottom_text_font, bottom_text_h=bottom_text_h,
    )


# ---- per-frame rendering ---------------------------------------------------

def _draw_card_chrome(card: Image.Image, layout: dict, alpha: int) -> None:
    """White rounded body with the subtle stroke and drop shadow already
    handled outside; this just fills the card if alpha < 255."""
    cw, ch = layout["card_w"], layout["card_h"]
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, cw, ch), radius=CARD_RADIUS,
                        fill=hex_to_rgba(CARD_FILL, alpha))


def _draw_header(card: Image.Image, layout: dict, channel: str, header_p: float) -> None:
    """Avatar + AskReddit name + verified badge. Slides in from -16px and fades."""
    if header_p <= 0:
        return
    eased = ease_out_cubic(header_p)
    alpha = int(255 * eased)
    dx = int(lerp(-20, 0, eased))     # slide-in offset

    # avatar
    paste_pixmap(card, _icons_dir(channel) / "reddit_logo.png",
                 layout["avatar_x"] + dx, layout["avatar_y"],
                 width=HEADER_AVATAR_SIZE, alpha=alpha)
    # name
    d = ImageDraw.Draw(card)
    d.text((layout["name_x"] + dx, layout["name_y"]),
           "AskReddit", font=layout["name_font"],
           fill=hex_to_rgba(TEXT_BLACK, alpha))
    # verified
    paste_pixmap(card, _icons_dir(channel) / "verified.png",
                 layout["verified_x"] + dx, layout["verified_y"],
                 width=VERIFIED_SIZE, alpha=alpha)


def _draw_emojis(card: Image.Image, layout: dict, channel: str, t: float) -> None:
    """Each emoji pops in with its own back-ease, staggered 30ms apart."""
    emojis = layout["emojis"]
    if not emojis:
        return
    base_y = layout["emoji_row_y"]
    cur_x = layout["emoji_row_x_start"]
    stagger = 0.03
    for i, e in enumerate(emojis):
        local_phase = (PHASE_EMOJIS[0] + i * stagger,
                       PHASE_EMOJIS[1] + i * stagger)
        p = _phase_progress(t, local_phase)
        if p <= 0:
            cur_x += EMOJI_SIZE + EMOJI_GAP
            continue
        eased = ease_out_back(p)
        alpha = int(255 * min(1.0, p * 1.4))
        paste_pixmap(card, e, cur_x, base_y, width=EMOJI_SIZE,
                     alpha=alpha, scale=eased)
        cur_x += EMOJI_SIZE + EMOJI_GAP


def _draw_headline(card: Image.Image, layout: dict, headline_p: float) -> None:
    if headline_p <= 0:
        return
    eased = ease_out_cubic(headline_p)
    alpha = int(255 * eased)
    dy = int(lerp(12, 0, eased))
    draw_lines(card, layout["pad_x"], layout["headline_top"] + dy,
               layout["headline_lines"], layout["headline_font"],
               fill=hex_to_rgba(TEXT_BLACK, alpha),
               line_spacing_ratio=HEADLINE_LINE_SPACING)


def _draw_bottom(card: Image.Image, layout: dict, handle: str, bottom_p: float) -> None:
    if bottom_p <= 0:
        return
    eased = ease_out_cubic(bottom_p)
    alpha = int(255 * eased)
    cw = layout["card_w"]
    by = layout["bottom_y"]
    pad_x = layout["pad_x"]
    font = layout["bottom_text_font"]
    text_h = layout["bottom_text_h"]

    # heart icon (left) + "99+" right of it
    heart_cy = by + text_h // 2 + 2
    heart_cx = pad_x + HEART_RADIUS + 2
    draw_heart_outline(card, heart_cx, heart_cy, HEART_RADIUS,
                       hex_to_rgba(MUTED_GRAY, alpha), stroke=HEART_STROKE)
    label_x = heart_cx + HEART_RADIUS + 14
    d = ImageDraw.Draw(card)
    d.text((label_x, by), "99+", font=font, fill=hex_to_rgba(MUTED_GRAY, alpha))

    # handle on the right
    if handle:
        handle_w = font.getlength(handle)
        d.text((cw - pad_x - handle_w, by), handle, font=font,
               fill=hex_to_rgba(MUTED_GRAY, alpha))


def _build_card_image(layout: dict, channel: str, handle: str, t: float) -> Image.Image:
    """Build a fully-composited card image (still on a transparent background,
    sized exactly card_w × card_h). The caller applies the pop scale to it.
    Card pop uses ease_out_quart — fast at start, decelerates smoothly, lands
    at exactly 1.0 with no overshoot."""
    cw, ch = layout["card_w"], layout["card_h"]
    card_p = _phase_progress(t, PHASE_CARD)
    eased_card = ease_out_quart(card_p)
    body_alpha = int(255 * ease_out_cubic(min(1.0, card_p * 1.5)))

    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if body_alpha > 0:
        _draw_card_chrome(card, layout, body_alpha)
    _draw_header(card, layout, channel, _phase_progress(t, PHASE_HEADER))
    _draw_emojis(card, layout, channel, t)
    _draw_headline(card, layout, _phase_progress(t, PHASE_HEADLINE))
    _draw_bottom(card, layout, handle, _phase_progress(t, PHASE_BOTTOM))
    return card, eased_card


# ---- public entry ----------------------------------------------------------

def render(*, title: str, duration: float, channel: str, meta: dict, out_path: Path) -> Path:
    layout = _layout(title or "(no title)", channel)
    handle = (meta.get("handle") or "").strip()

    total_frames = max(1, int(round(duration * OUTPUT_FPS)))
    cw, ch = layout["card_w"], layout["card_h"]
    cx, cy = OUTPUT_W // 2, OUTPUT_H // 2

    # pre-compute the static drop shadow + outer stroke. We blit them under the
    # card every frame, but build the image once.
    shadow_img, shadow_pad = stroked_rounded_rect(
        size=(cw, ch), radius=CARD_RADIUS,
        fill_rgba=(0, 0, 0, 0),                    # transparent fill, just shadow
        stroke_rgba=None, stroke_width=0,
        shadow=CARD_SHADOW, shadow_offset=CARD_SHADOW_OFFSET, shadow_blur=CARD_SHADOW_BLUR,
    )
    border_img, border_pad = stroked_rounded_rect(
        size=(cw, ch), radius=CARD_RADIUS,
        fill_rgba=(0, 0, 0, 0),
        stroke_rgba=CARD_BORDER_RGBA, stroke_width=2,
        shadow=None,
    )

    proc, stdin = open_alpha_writer(out_path)
    try:
        for f in range(total_frames):
            t = f / OUTPUT_FPS
            card_img, scale = _build_card_image(layout, channel, handle, t)

            canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
            # During frame 0 the pop has barely begun and scale is essentially
            # zero — skip rendering rather than crashing PIL's resize.
            if scale > 0.02:
                sw = max(2, int(cw * scale))
                sh = max(2, int(ch * scale))
                scaled_card    = card_img.resize((sw, sh), Image.LANCZOS)
                scaled_shadow  = shadow_img.resize(
                    (sw + shadow_pad * 2, sh + shadow_pad * 2), Image.LANCZOS)
                scaled_border  = border_img.resize(
                    (sw + border_pad * 2, sh + border_pad * 2), Image.LANCZOS)
                canvas.alpha_composite(
                    scaled_shadow,
                    (cx - scaled_shadow.width // 2, cy - scaled_shadow.height // 2))
                canvas.alpha_composite(
                    scaled_border,
                    (cx - scaled_border.width // 2, cy - scaled_border.height // 2))
                canvas.alpha_composite(
                    scaled_card, (cx - sw // 2, cy - sh // 2))
            stdin.write(canvas.tobytes())
        stdin.close()
        proc.wait()
        if proc.returncode != 0:
            err = proc.stderr.read().decode("utf-8", "ignore") if proc.stderr else ""
            raise RuntimeError(f"titlecard ffmpeg failed: {err[-400:]}")
    finally:
        if not stdin.closed:
            stdin.close()
    return out_path
