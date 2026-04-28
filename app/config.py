"""Project paths and defaults. Everything is anchored under PROJECT_ROOT so the
folder is fully self-contained — delete the folder and it's all gone."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Tuple
import json
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN = PROJECT_ROOT / "bin"
FFMPEG = BIN / "ffmpeg"
FFPROBE = BIN / "ffprobe"
CHANNELS_DIR = PROJECT_ROOT / "channels"
CLIPS_DIR_DEFAULT = PROJECT_ROOT / "clips"
ASSETS_DIR = PROJECT_ROOT / "assets"
OUTPUT_DIR = PROJECT_ROOT / "output"
TMP_DIR = PROJECT_ROOT / "tmp"
LOGS_DIR = PROJECT_ROOT / "logs"
SETTINGS_FILE = PROJECT_ROOT / "settings.json"

OUTPUT_W = 1080
OUTPUT_H = 1920
OUTPUT_FPS = 30

# Map color tag name -> hex. Add or rename freely; story.py will accept any
# tag name that matches a key here (case-insensitive).
COLOR_PALETTE: dict[str, str] = {
    "red":     "#ff3b30",   # injury, fight, blood, danger
    "green":   "#34c759",   # vomit, sickness, money, gross-but-funny
    "blue":    "#0a84ff",   # sad, cold, water, calm
    "yellow":  "#ffd60a",   # warning, surprise, money
    "orange":  "#ff9f0a",   # fire, embarrassment, energy
    "purple":  "#bf5af2",   # mystery, weird, royalty
    "pink":    "#ff375f",   # love, romance, awkward
    "white":   "#ffffff",   # default
    "gray":    "#8e8e93",
    "black":   "#000000",
}
DEFAULT_CAPTION_COLOR = "#ffffff"


@dataclass
class CaptionStyle:
    font_family: str = "Helvetica"
    font_size: int = 96
    font_weight: str = "Bold"            # "Regular" | "Bold" | "Black"
    stroke_color: str = "#000000"
    stroke_width: int = 8
    shadow_color: str = "#000000"
    shadow_offset: Tuple[int, int] = (0, 6)
    shadow_blur: int = 12
    shadow_opacity: float = 0.6
    default_color: str = DEFAULT_CAPTION_COLOR
    uppercase: bool = True
    pop_scale: float = 1.10              # words pop in slightly bigger then settle
    pop_duration_ms: int = 80


@dataclass
class LogoConfig:
    path: str = ""           # absolute path to PNG
    x: int = 540             # center of logo, in 1080x1920 frame coords
    y: int = 200
    width: int = 240         # logo render width in px
    opacity: float = 1.0


@dataclass
class RenderSettings:
    channel: str = ""
    title: str = ""                         # the Reddit-post title shown in the title card
    story: str = ""                         # body of the story, may contain {color}word{/color} tags
    # Qwen3-TTS preset voice name (Aiden / Ryan). No reference clip needed.
    voice: str = "Aiden"
    voice_instruct: str = ""                # optional style prompt e.g. "say it casually"
    # Qwen3-TTS samples codec tokens stochastically; locking the seed makes a
    # render reproducible. Bump this value to re-roll if a story sounds glitchy.
    voice_seed: int = 1
    # auto-editor silence-removal margin. Higher = less aggressive cutting.
    # 0.4s is right for TTS, 0.2s for human narration with hesitations.
    silence_margin: str = "0.4s"
    clips_dir: str = str(CLIPS_DIR_DEFAULT)
    seg_min_s: float = 4.0
    seg_max_s: float = 8.0
    title_card_duration_s: float = 3.0      # how long the title card holds
    captions: CaptionStyle = field(default_factory=CaptionStyle)
    logo: LogoConfig = field(default_factory=LogoConfig)
    saturation: float = 1.10
    volume_db: float = 4.0                  # +4 dB volume bump
    speed: float = 1.35                     # video & audio speed; pitch rises (no preserve)
    output_filename: str = "output.mp4"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "RenderSettings":
        d = json.loads(s)
        d["captions"] = CaptionStyle(**d.get("captions", {}))
        d["logo"] = LogoConfig(**d.get("logo", {}))
        return cls(**d)


def load_settings() -> RenderSettings:
    if SETTINGS_FILE.exists():
        try:
            return RenderSettings.from_json(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return RenderSettings()


def save_settings(s: RenderSettings) -> None:
    SETTINGS_FILE.write_text(s.to_json())


def channel_dirs() -> list[Path]:
    if not CHANNELS_DIR.exists():
        return []
    return sorted([p for p in CHANNELS_DIR.iterdir() if p.is_dir()])


def channel_template(channel: str) -> Path | None:
    """Return the title-card template .mov for a channel if present."""
    if not channel:
        return None
    for name in ("template.mov", "template.mp4"):
        p = CHANNELS_DIR / channel / name
        if p.exists():
            return p
    return None


# Make sure all the runtime dirs exist.
for d in (CHANNELS_DIR, CLIPS_DIR_DEFAULT, ASSETS_DIR, OUTPUT_DIR, TMP_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)
