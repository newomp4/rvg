"""Final composition: stack the layers, apply saturation/volume/speed, encode.

Speed handling — the user wants 1.35x with NO pitch preservation, i.e. the
voice should sound chipmunk-y. In ffmpeg:
    audio:  asetrate=<sr>*1.35,aresample=<sr>      # raises pitch + speeds up
    video:  setpts=PTS/1.35

Saturation: eq=saturation=1.10
Volume:     volume=<X>dB
"""
from __future__ import annotations
from pathlib import Path
import subprocess

from app.config import (
    FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS,
    RenderSettings, LogoConfig,
)


def _logo_filter_chain(logo: LogoConfig) -> tuple[list[str], str]:
    """Returns (filter_complex_lines, last_video_label).
    Caller composes this AFTER captions overlay, BEFORE final eq/setpts."""
    if not logo.path or not Path(logo.path).exists():
        return [], "[v3]"
    # we'll feed the logo as input index 4 (after bg, title, caps, audio order)
    return [], ""   # actual handling done inline in compose()


def compose(
    *,
    background: Path,
    titlecard: Path,
    captions: Path,
    audio: Path,
    settings: RenderSettings,
    out: Path,
    title_card_duration: float,
) -> Path:
    """Final compose. Returns the output path on success."""
    have_logo = bool(settings.logo.path) and Path(settings.logo.path).exists()

    # Build inputs list
    inputs: list[str] = []
    inputs += ["-i", str(background)]    # 0
    inputs += ["-i", str(titlecard)]     # 1
    inputs += ["-i", str(captions)]      # 2
    inputs += ["-i", str(audio)]         # 3
    if have_logo:
        inputs += ["-i", str(settings.logo.path)]   # 4

    fc_lines: list[str] = []
    # background trimmed to caption duration (we pass through)
    fc_lines.append("[0:v]format=yuva444p10le[bg]")
    # title card overlays for its duration only (it's already exactly title_card_duration long)
    fc_lines.append(f"[1:v]format=yuva444p10le[tc]")
    fc_lines.append(
        f"[bg][tc]overlay=0:0:enable='between(t,0,{title_card_duration:.3f})':"
        f"format=auto[v1]"
    )
    # captions track is full length, alpha is 0 outside word windows
    fc_lines.append("[2:v]format=yuva444p10le[cap]")
    fc_lines.append("[v1][cap]overlay=0:0:format=auto[v2]")

    last_v = "[v2]"
    if have_logo:
        # scale logo to requested width, set opacity, position centered at (logo.x, logo.y)
        fc_lines.append(
            f"[4:v]scale={settings.logo.width}:-1,"
            f"format=rgba,colorchannelmixer=aa={settings.logo.opacity:.3f}[lg]"
        )
        # overlay places top-left of overlay at x,y; convert center -> top-left
        fc_lines.append(
            f"{last_v}[lg]overlay="
            f"{settings.logo.x}-w/2:{settings.logo.y}-h/2:format=auto[v3]"
        )
        last_v = "[v3]"

    # Saturation pass + final pixel format for h264
    fc_lines.append(f"{last_v}eq=saturation={settings.saturation}[v4]")
    # Speed-up: video PTS divided
    fc_lines.append(f"[v4]setpts=PTS/{settings.speed},fps={OUTPUT_FPS},format=yuv420p[vout]")

    # Audio chain: volume bump, then asetrate to raise pitch + speed, then resample back
    # We probe audio sample rate at 48000 (TTS comes as 24kHz mp3 but we'll resample first)
    fc_lines.append(
        f"[3:a]aresample=48000,volume={settings.volume_db}dB,"
        f"asetrate=48000*{settings.speed},aresample=48000[aout]"
    )

    fc = ";".join(fc_lines)

    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17",
        "-r", str(OUTPUT_FPS),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
