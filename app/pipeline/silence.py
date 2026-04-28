"""Silence removal using auto-editor + word-timing remap.

auto-editor analyzes the audio and decides which segments to keep based on
loudness. We use it on the TTS audio, get a trimmed file, AND a JSON
timeline that tells us exactly which segments of the original were kept.
We then walk the original word timings and rewrite them onto the trimmed
timeline, dropping any words that fell entirely inside a cut.

The timeline export from auto-editor v23+ uses the v3 schema; we read its
`v3` block which contains `timebase` and per-track lists of `[start, dur, src]`
where start/dur are in timebase units and `src` is `[file_index, src_start]`.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List
import json
import subprocess
import sys

from app.pipeline.tts import TimedWord
from app.config import FFMPEG, TMP_DIR


@dataclass
class KeptSegment:
    src_start: float     # seconds in original audio
    src_end: float
    out_start: float     # seconds in trimmed audio
    out_end: float


_DEFAULT_MARGIN = "0.4s"   # bigger than auto-editor's 0.2s default — TTS audio
                            # is already tight, the wider margin prevents the
                            # micro-cuts that produce a glitchy, choppy sound.


def _auto_editor_timeline(audio_in: Path, v3_out: Path, margin: str) -> None:
    """Ask auto-editor to dump a v3 JSON timeline (no media output)."""
    if v3_out.suffix != ".v3":
        v3_out = v3_out.with_suffix(".v3")
    cmd = [
        sys.executable, "-m", "auto_editor",
        str(audio_in),
        "--margin", margin,
        "--export", "v3",
        "-o", str(v3_out),
        "--no-open",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _ffmpeg_cut_lossless(audio_in: Path, audio_out: Path,
                         kept: list["KeptSegment"]) -> None:
    """Build the trimmed audio by concatenating kept segments via ffmpeg,
    losslessly (pcm_s16le passthrough). We DON'T let auto-editor render the
    audio because it routes the file through a lossy intermediate codec
    even when no cuts are made — that re-encode mangles TTS waveforms and
    sounds glitchy/robotic in the final mix."""
    if not kept:
        raise RuntimeError("no kept segments — nothing to render")

    if len(kept) == 1 and kept[0].src_start == 0.0:
        # nothing actually trimmed — just copy the raw file unchanged
        # (still re-mux to enforce pcm_s16le and shed any odd metadata)
        cmd = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(audio_in),
            "-c:a", "pcm_s16le",
            str(audio_out),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return

    # Build a filter graph that trims and concatenates each kept range.
    parts = []
    inputs = []
    for i, seg in enumerate(kept):
        parts.append(
            f"[0:a]atrim=start={seg.src_start:.6f}:end={seg.src_end:.6f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
        inputs.append(f"[a{i}]")
    filter_complex = ";".join(parts) + ";" + "".join(inputs) + \
                     f"concat=n={len(kept)}:v=0:a=1[out]"
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(audio_in),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:a", "pcm_s16le",
        str(audio_out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _parse_timeline(v3_path: Path) -> tuple[float, list[KeptSegment]]:
    """Parse auto-editor's v3 JSON timeline. Returns (timebase, kept segments).

    v29 v3 schema (top-level JSON):
        {"version":"3","timebase":"30/1","resolution":[w,h],"samplerate":N,
         "v":[...], "a":[ [ {clip}, {clip}, ... ] ]}
    Each clip dict has start/dur/offset (in timebase units) and optional speed.
    """
    data = json.loads(Path(v3_path).read_text())
    tb = data.get("timebase", "30/1")
    if isinstance(tb, str) and "/" in tb:
        n, d = tb.split("/")
        timebase = float(n) / float(d)
    elif isinstance(tb, list):
        timebase = float(tb[0]) / float(tb[1])
    else:
        timebase = float(tb)

    tracks = data.get("a") or []
    if not tracks:
        raise RuntimeError("auto-editor timeline has no audio tracks")
    clips = tracks[0]

    kept: list[KeptSegment] = []
    for clip in clips:
        if isinstance(clip, dict):
            start_o = float(clip["start"])
            dur     = float(clip["dur"])
            src_o   = float(clip["offset"])
            speed   = float(clip.get("speed", 1.0))
        else:
            start_o, dur, src_o = float(clip[0]), float(clip[1]), float(clip[2])
            speed = 1.0
        if speed != 1.0:
            continue                        # cut region, skip
        src_start = src_o / timebase
        src_end   = (src_o + dur) / timebase
        out_start = start_o / timebase
        out_end   = (start_o + dur) / timebase
        kept.append(KeptSegment(src_start, src_end, out_start, out_end))

    kept.sort(key=lambda k: k.src_start)
    return timebase, kept


def remove_silences(audio_in: Path, words: list[TimedWord], work_dir: Path,
                    margin: str = _DEFAULT_MARGIN) -> tuple[Path, list[TimedWord]]:
    """Run auto-editor, return (trimmed audio path, remapped word timings).

    `margin` is auto-editor's --margin parameter. 0.4s is the right default
    for TTS audio (already tight); 0.2s (the auto-editor default) makes
    audible micro-cuts that sound glitchy. Real recorded narration with
    hesitations may want a smaller value.

    Words that fall entirely inside a cut region are dropped. Words that span
    a cut boundary get their times clamped.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    timeline_v3 = work_dir / "ae_timeline.v3"
    audio_out   = work_dir / "speech_trimmed.wav"

    _auto_editor_timeline(audio_in, timeline_v3, margin)
    _, kept = _parse_timeline(timeline_v3)
    _ffmpeg_cut_lossless(audio_in, audio_out, kept)

    new_words: list[TimedWord] = []
    for w in words:
        # find a kept segment containing the word's center
        center = (w.start + w.end) / 2
        seg = next((k for k in kept if k.src_start <= center <= k.src_end), None)
        if seg is None:
            continue
        # clamp to segment, then map to output timeline
        s = max(w.start, seg.src_start)
        e = min(w.end,   seg.src_end)
        new_start = seg.out_start + (s - seg.src_start)
        new_end   = seg.out_start + (e - seg.src_start)
        if new_end > new_start:
            new_words.append(TimedWord(text=w.text, start=new_start, end=new_end))

    return audio_out, new_words
