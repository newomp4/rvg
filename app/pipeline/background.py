"""Build a background video by stitching random segments of random clips
from the user's clips folder. Each segment is between min/max seconds long;
we keep adding segments until the total duration covers `target_duration`.

We strip audio (the video is silent — TTS audio gets muxed in later) and
scale-and-crop each segment to 1080x1920 so concat doesn't re-encode oddly.
"""
from __future__ import annotations
from pathlib import Path
import random
import subprocess

from app.config import FFMPEG, OUTPUT_W, OUTPUT_H, OUTPUT_FPS, TMP_DIR
from app.pipeline.ffmpeg import duration

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def list_clips(clips_dir: Path) -> list[Path]:
    if not clips_dir.exists():
        return []
    return [p for p in clips_dir.rglob("*") if p.suffix.lower() in VIDEO_EXTS and p.is_file()]


def _scale_crop_filter() -> str:
    # scale to fit (cover), then center-crop to 1080x1920, then pin fps + format
    return (
        f"scale=w='if(gt(a,{OUTPUT_W}/{OUTPUT_H}),-2,{OUTPUT_W})':"
        f"h='if(gt(a,{OUTPUT_W}/{OUTPUT_H}),{OUTPUT_H},-2)':flags=lanczos,"
        f"crop={OUTPUT_W}:{OUTPUT_H},"
        f"fps={OUTPUT_FPS},format=yuv420p,setsar=1"
    )


def _extract_segment(clip: Path, start: float, dur: float, out: Path) -> None:
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(clip),
        "-an",
        "-vf", _scale_crop_filter(),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _solid_background(target_duration: float, work_dir: Path,
                      color: str = "black") -> Path:
    """Single ffmpeg call that produces a solid-color background — used when
    no clips are available, mostly for fast iteration during render testing."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "background.mp4"
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={OUTPUT_W}x{OUTPUT_H}:r={OUTPUT_FPS}:d={target_duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out


def build_background(
    clips_dir: Path,
    target_duration: float,
    work_dir: Path,
    seg_min: float = 4.0,
    seg_max: float = 8.0,
    seed: int | None = None,
) -> Path:
    """Build the background video. Returns path to the stitched .mp4. When
    `clips_dir` is empty/missing, falls back to a solid black background so
    rendering still works (faster too, useful for testing the foreground)."""
    rng = random.Random(seed)
    clips = list_clips(clips_dir)
    if not clips:
        return _solid_background(target_duration, work_dir)

    work_dir.mkdir(parents=True, exist_ok=True)
    seg_dir = work_dir / "bg_segs"
    seg_dir.mkdir(exist_ok=True)
    # clear any previous run
    for p in seg_dir.iterdir():
        p.unlink()

    segments: list[Path] = []
    accumulated = 0.0
    idx = 0
    last_clip: Path | None = None
    safety = 0

    while accumulated < target_duration:
        safety += 1
        if safety > 1000:
            raise RuntimeError("background builder failed to converge — clips too short?")

        # pick a different clip from last when possible
        candidates = [c for c in clips if c != last_clip] or clips
        clip = rng.choice(candidates)
        try:
            cd = duration(clip)
        except Exception:
            continue
        if cd < seg_min:
            # short clip — use the whole thing
            seg_dur = cd
            start = 0.0
        else:
            seg_dur = rng.uniform(seg_min, min(seg_max, cd))
            start = rng.uniform(0, cd - seg_dur)

        # don't overshoot — last segment fills the remainder
        remaining = target_duration - accumulated
        seg_dur = min(seg_dur, remaining + 0.5)   # small tail so we always cover

        out = seg_dir / f"seg_{idx:04d}.mp4"
        _extract_segment(clip, start, seg_dur, out)
        segments.append(out)
        accumulated += seg_dur
        idx += 1
        last_clip = clip

    # Concat with the demuxer (no re-encode needed since segments share codec/params).
    concat_list = work_dir / "bg_concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments))
    out = work_dir / "background.mp4"
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out
