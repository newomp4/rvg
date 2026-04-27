"""Title card renderer — dispatches to a per-channel implementation.

Each channel folder under ./channels/<name>/ may contain:
    channel.json     {"renderer": "<name>", "handle": "@channel", ...}
    icons/           per-channel icon assets (per-renderer schema)
    template.mov     optional pre-rendered animation overlay (legacy escape hatch)

If a channel has a registered renderer, we call it. Otherwise we fall back
to a simple bland placeholder card so the rest of the pipeline still runs.
"""
from __future__ import annotations
from pathlib import Path
import json

from app.config import CHANNELS_DIR
from app.pipeline.titlecards import storiesandtexts as _storiesandtexts
from app.pipeline.titlecards import placeholder as _placeholder


_RENDERERS = {
    "storiesandtexts": _storiesandtexts.render,
    # add more channel renderers here as they're built
}


def _channel_meta(channel: str) -> dict:
    if not channel:
        return {}
    p = CHANNELS_DIR / channel / "channel.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def render_titlecard_track(title: str, duration: float, channel: str, out_path: Path) -> Path:
    """Render a transparent .mov of length `duration` containing the title
    card animation for the given channel. The pipeline composites this on
    top of the background video for the first `duration` seconds."""
    meta = _channel_meta(channel)
    name = (meta.get("renderer") or channel or "").lower()
    fn = _RENDERERS.get(name, _placeholder.render)
    return fn(title=title, duration=duration, channel=channel, meta=meta, out_path=out_path)
