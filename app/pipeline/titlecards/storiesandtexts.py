"""Title card renderer for the @storiesandtexts channel — clean redesign.

The card has three regions, top to bottom:

  HEADER
    [PFP]  display_name ✓
           @handle
  HEADLINE
    The Reddit question text, in Inter Bold. Auto-shrinks 64 → 40px and
    word-wraps; card height grows to fit however many lines it ends up.
  FOOTER
    ♡ 99+

No achievement-badge row, no Reddit-alien logo. The profile picture is
loaded from channels/<name>/<profile_picture> and circle-masked, so it's
hot-swappable per channel just by editing channel.json.

The verified badge is drawn programmatically at 4× supersampling so it
stays crisp at any pixel size — no rasterized PNGs.

Animation timeline (relative to t=0):
  0.00–0.55  card pops in, ease_out_quart (fast → slow, no overshoot)
  0.30–0.65  PFP, name, handle, verified fade in together
  0.40–0.80  headline fade-in with a small upward slide
  0.50–0.85  ♡ 99+ fades in
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image, ImageDraw

from app.config import OUTPUT_W, OUTPUT_H, OUTPUT_FPS, CHANNELS_DIR
from app.pipeline.titlecards.common import (
    ease_out_cubic, ease_out_quart, lerp, hex_to_rgba, load_inter,
    wrap_to_box, measure_block, draw_lines, paste_pixmap,
    stroked_rounded_rect, open_alpha_writer,
    circle_mask_image, draw_verified_badge, draw_heart_outline,
)


# ---- design tokens ---------------------------------------------------------

CARD_W = 920
CARD_RADIUS = 32
CARD_FILL = "#ffffff"
CARD_SHADOW = (0, 0, 0, 56)
CARD_SHADOW_OFFSET = (0, 18)
CARD_SHADOW_BLUR = 36

# inner padding
PAD_X = 48
PAD_TOP = 44
PAD_BOTTOM = 36

# header row
PFP_SIZE = 96
HEADER_GAP = 20             # space between pfp and the name/handle column
NAME_SIZE = 36
HANDLE_SIZE = 28
NAME_HANDLE_GAP = 4         # vertical space between name and handle
VERIFIED_SIZE = 22          # outer radius of the badge in target px (matches name x-height)
VERIFIED_GAP = 8            # space between name and verified

# headline
HEADLINE_TOP_GAP = 36
HEADLINE_MAX_SIZE = 60
HEADLINE_MIN_SIZE = 38
HEADLINE_LINE_SPACING = 0.16

# footer
FOOTER_TOP_GAP = 32
FOOTER_TEXT_SIZE = 28
HEART_RADIUS = 16
HEART_STROKE = 4

# colors
TEXT_BLACK = "#0F1418"
TEXT_MUTED = "#7A8085"

# animation phases (t, in seconds, relative to card start)
PHASE_CARD     = (0.00, 0.55)
PHASE_HEADER   = (0.30, 0.65)
PHASE_HEADLINE = (0.40, 0.80)
PHASE_FOOTER   = (0.50, 0.85)


def _phase_progress(t: float, phase: tuple[float, float]) -> float:
    s, e = phase
    if t <= s: return 0.0
    if t >= e: return 1.0
    return (t - s) / (e - s)


# ---- channel asset resolution ----------------------------------------------

def _channel_dir(channel: str) -> Path:
    return CHANNELS_DIR / channel


def _profile_path(channel: str, meta: dict) -> Path | None:
    p = (meta.get("profile_picture") or "").strip()
    if not p:
        return None
    candidate = (_channel_dir(channel) / p).resolve()
    return candidate if candidate.exists() else None


# ---- layout pre-computation ------------------------------------------------

def _layout(title: str, channel: str, meta: dict) -> dict:
    name_font   = load_inter("Bold",     NAME_SIZE)
    handle_font = load_inter("Regular",  HANDLE_SIZE)
    footer_font = load_inter("SemiBold", FOOTER_TEXT_SIZE)

    pad_x = PAD_X
    inner_w = CARD_W - 2 * pad_x

    # ---- header ----
    pfp_x = pad_x + PFP_SIZE // 2
    pfp_y = PAD_TOP + PFP_SIZE // 2

    # display_name and @handle stack to the right of the PFP, vertically
    # centered against the PFP as a pair.
    display_name = (meta.get("display_name") or "").strip() or "channel"
    handle       = (meta.get("handle") or "").strip()
    verified     = bool(meta.get("verified"))

    name_bbox = name_font.getbbox(display_name)
    name_caps_h = name_bbox[3] - name_bbox[1]
    name_cap_top = name_bbox[1]
    name_w = int(name_font.getlength(display_name))

    handle_bbox = handle_font.getbbox(handle or "Ag")
    handle_caps_h = handle_bbox[3] - handle_bbox[1]
    handle_cap_top = handle_bbox[1]

    has_handle = bool(handle)
    name_handle_total_h = name_caps_h + (NAME_HANDLE_GAP + handle_caps_h if has_handle else 0)
    block_top_y = pfp_y - name_handle_total_h // 2          # vertical center against PFP

    name_x = pad_x + PFP_SIZE + HEADER_GAP
    name_draw_y = block_top_y - name_cap_top                # PIL draws from cap-top + ascender area
    handle_draw_y = (block_top_y + name_caps_h + NAME_HANDLE_GAP - handle_cap_top
                     if has_handle else 0)

    # Verified badge sits to the right of the display name, vertically
    # centered against the name caps.
    badge_cx = name_x + name_w + VERIFIED_GAP + VERIFIED_SIZE
    badge_cy = block_top_y + name_caps_h // 2

    header_bottom = pfp_y + PFP_SIZE // 2

    # ---- headline ----
    headline_top = header_bottom + HEADLINE_TOP_GAP
    font, lines = wrap_to_box(
        title, "Bold",
        max_size=HEADLINE_MAX_SIZE, min_size=HEADLINE_MIN_SIZE,
        max_w=inner_w, max_h=10_000,
        line_spacing_ratio=HEADLINE_LINE_SPACING,
    )
    _, headline_block_h = measure_block(font, lines, HEADLINE_LINE_SPACING)

    # ---- footer ----
    footer_top = headline_top + headline_block_h + FOOTER_TOP_GAP
    footer_bbox = footer_font.getbbox("Ag")
    footer_h = footer_bbox[3] - footer_bbox[1]
    card_h = footer_top + footer_h + PAD_BOTTOM

    return dict(
        card_w=CARD_W, card_h=card_h,
        pad_x=pad_x,
        pfp_x=pfp_x, pfp_y=pfp_y, pfp_size=PFP_SIZE,
        display_name=display_name, name_font=name_font,
        name_x=name_x, name_draw_y=name_draw_y, name_caps_h=name_caps_h,
        handle=handle, handle_font=handle_font,
        handle_draw_y=handle_draw_y,
        verified=verified, badge_cx=badge_cx, badge_cy=badge_cy,
        headline_top=headline_top, headline_lines=lines, headline_font=font,
        headline_block_h=headline_block_h,
        footer_top=footer_top, footer_font=footer_font, footer_h=footer_h,
    )


# ---- render passes ---------------------------------------------------------

def _draw_chrome(card: Image.Image, layout: dict, alpha: int) -> None:
    cw, ch = layout["card_w"], layout["card_h"]
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, cw, ch), radius=CARD_RADIUS, fill=hex_to_rgba(CARD_FILL, alpha))


def _draw_header(card: Image.Image, layout: dict, channel: str, meta: dict, p: float) -> None:
    if p <= 0:
        return
    eased = ease_out_cubic(p)
    alpha = int(255 * eased)
    dy = int(lerp(8, 0, eased))   # tiny upward slide

    # PFP — circle-masked source image, drawn at the layout position.
    src_path = _profile_path(channel, meta)
    if src_path is not None:
        try:
            src = Image.open(src_path)
            pfp = circle_mask_image(src, layout["pfp_size"])
            if alpha < 255:
                a = pfp.split()[3]
                a = a.point(lambda v: int(v * alpha / 255))
                pfp.putalpha(a)
            card.alpha_composite(
                pfp,
                (layout["pfp_x"] - layout["pfp_size"] // 2,
                 layout["pfp_y"] - layout["pfp_size"] // 2 + dy),
            )
        except Exception:
            pass
    else:
        # fallback: solid gray circle so the layout still reads
        cx, cy, s = layout["pfp_x"], layout["pfp_y"] + dy, layout["pfp_size"]
        ImageDraw.Draw(card).ellipse(
            (cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2),
            fill=hex_to_rgba("#cccccc", alpha))

    # display name
    d = ImageDraw.Draw(card)
    d.text((layout["name_x"], layout["name_draw_y"] + dy),
           layout["display_name"], font=layout["name_font"],
           fill=hex_to_rgba(TEXT_BLACK, alpha))

    # handle (smaller, gray, second line)
    if layout["handle"]:
        d.text((layout["name_x"], layout["handle_draw_y"] + dy),
               layout["handle"], font=layout["handle_font"],
               fill=hex_to_rgba(TEXT_MUTED, alpha))

    # verified badge — drawn as RGBA-anti-aliased polygon, then alpha-mixed
    if layout["verified"]:
        # Render onto a separate canvas then alpha-fade
        badge_canvas = Image.new("RGBA", card.size, (0, 0, 0, 0))
        draw_verified_badge(badge_canvas,
                            layout["badge_cx"], layout["badge_cy"] + dy,
                            VERIFIED_SIZE)
        if alpha < 255:
            a = badge_canvas.split()[3]
            a = a.point(lambda v: int(v * alpha / 255))
            badge_canvas.putalpha(a)
        card.alpha_composite(badge_canvas)


def _draw_headline(card: Image.Image, layout: dict, p: float) -> None:
    if p <= 0:
        return
    eased = ease_out_cubic(p)
    alpha = int(255 * eased)
    dy = int(lerp(12, 0, eased))
    draw_lines(card, layout["pad_x"], layout["headline_top"] + dy,
               layout["headline_lines"], layout["headline_font"],
               fill=hex_to_rgba(TEXT_BLACK, alpha),
               line_spacing_ratio=HEADLINE_LINE_SPACING)


def _draw_footer(card: Image.Image, layout: dict, p: float) -> None:
    if p <= 0:
        return
    eased = ease_out_cubic(p)
    alpha = int(255 * eased)
    cw = layout["card_w"]
    pad_x = layout["pad_x"]
    fy = layout["footer_top"]
    font = layout["footer_font"]
    fh = layout["footer_h"]
    # heart at left
    heart_cy = fy + fh // 2 + 1
    heart_cx = pad_x + HEART_RADIUS + 2
    draw_heart_outline(card, heart_cx, heart_cy, HEART_RADIUS,
                       hex_to_rgba(TEXT_MUTED, alpha), stroke=HEART_STROKE)
    # "99+" right of heart
    label_x = heart_cx + HEART_RADIUS + 14
    ImageDraw.Draw(card).text(
        (label_x, fy), "99+", font=font, fill=hex_to_rgba(TEXT_MUTED, alpha))


def _build_card_image(layout: dict, channel: str, meta: dict, t: float):
    cw, ch = layout["card_w"], layout["card_h"]
    card_p = _phase_progress(t, PHASE_CARD)
    eased_card = ease_out_quart(card_p)
    body_alpha = int(255 * ease_out_cubic(min(1.0, card_p * 1.5)))

    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if body_alpha > 0:
        _draw_chrome(card, layout, body_alpha)
    _draw_header(card, layout, channel, meta, _phase_progress(t, PHASE_HEADER))
    _draw_headline(card, layout, _phase_progress(t, PHASE_HEADLINE))
    _draw_footer(card, layout, _phase_progress(t, PHASE_FOOTER))
    return card, eased_card


# ---- public entry ----------------------------------------------------------

def render(*, title: str, duration: float, channel: str, meta: dict, out_path: Path) -> Path:
    layout = _layout(title or "(no title)", channel, meta)

    cw, ch = layout["card_w"], layout["card_h"]
    cx, cy = OUTPUT_W // 2, OUTPUT_H // 2
    total_frames = max(1, int(round(duration * OUTPUT_FPS)))

    # Static drop shadow under the card. Built once; resized per frame to
    # follow the card's pop scale.
    shadow_img, shadow_pad = stroked_rounded_rect(
        size=(cw, ch), radius=CARD_RADIUS,
        fill_rgba=(0, 0, 0, 0),
        stroke_rgba=None, stroke_width=0,
        shadow=CARD_SHADOW, shadow_offset=CARD_SHADOW_OFFSET, shadow_blur=CARD_SHADOW_BLUR,
    )

    proc, stdin = open_alpha_writer(out_path)
    try:
        for f in range(total_frames):
            t = f / OUTPUT_FPS
            card_img, scale = _build_card_image(layout, channel, meta, t)

            canvas = Image.new("RGBA", (OUTPUT_W, OUTPUT_H), (0, 0, 0, 0))
            if scale > 0.02:
                sw = max(2, int(cw * scale))
                sh = max(2, int(ch * scale))
                scaled_card = card_img.resize((sw, sh), Image.LANCZOS)
                scaled_shadow = shadow_img.resize(
                    (sw + shadow_pad * 2, sh + shadow_pad * 2), Image.LANCZOS)
                canvas.alpha_composite(
                    scaled_shadow,
                    (cx - scaled_shadow.width // 2, cy - scaled_shadow.height // 2))
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
