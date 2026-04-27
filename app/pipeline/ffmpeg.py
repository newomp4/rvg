"""Thin shell wrappers around the ./bin/ffmpeg and ./bin/ffprobe binaries."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable
import json
import subprocess

from app.config import FFMPEG, FFPROBE


def run(args: Iterable[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *args]
    return subprocess.run(cmd, check=True, capture_output=capture, text=capture)


def probe(path: Path) -> dict:
    """Return ffprobe JSON for a media file."""
    cmd = [str(FFPROBE), "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def duration(path: Path) -> float:
    info = probe(path)
    return float(info["format"]["duration"])


def video_streams(path: Path) -> list[dict]:
    return [s for s in probe(path).get("streams", []) if s.get("codec_type") == "video"]
