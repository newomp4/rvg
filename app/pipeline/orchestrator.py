"""Top-level pipeline runner.

Drives every step in order, emits progress strings via a callback so the UI
can show what's happening, and stages all intermediates under tmp/<job_id>.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable
import shutil
import time
import uuid

from app.config import (
    RenderSettings, OUTPUT_DIR, TMP_DIR, OUTPUT_W, OUTPUT_H, OUTPUT_FPS,
)
from app.pipeline import story as story_mod
from app.pipeline import tts as tts_mod
from app.pipeline import silence as silence_mod
from app.pipeline import background as bg_mod
from app.pipeline import captions as cap_mod
from app.pipeline import titlecard as tc_mod
from app.pipeline import compose as compose_mod
from app.pipeline.ffmpeg import duration as audio_duration

ProgressFn = Callable[[str, float], None]    # (message, fraction 0..1)


def render(settings: RenderSettings, progress: ProgressFn | None = None) -> Path:
    p = progress or (lambda msg, frac: None)
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    work = TMP_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)

    p("parsing story", 0.02)
    plain, words = story_mod.parse_story(settings.story)
    if not plain.strip():
        raise RuntimeError("story is empty")
    plain = story_mod.strip_for_tts(plain)

    p("synthesizing voice", 0.08)
    raw_wav = work / "speech_raw.wav"
    timed = tts_mod.synthesize(
        text=plain,
        out_wav=raw_wav,
        voice=settings.voice,
        instruct=(settings.voice_instruct or None),
        seed=settings.voice_seed,
    )
    if not timed:
        raise RuntimeError("TTS returned no word timings")

    p("removing silences", 0.20)
    trimmed_audio, timed = silence_mod.remove_silences(
        raw_wav, timed, work, margin=settings.silence_margin)
    audio_len = audio_duration(trimmed_audio)

    # Total video length = title card duration + speech audio length
    tc_dur = float(settings.title_card_duration_s)
    total_len = tc_dur + audio_len

    p("building background video", 0.30)
    bg = bg_mod.build_background(
        clips_dir=Path(settings.clips_dir),
        target_duration=total_len + 0.5,
        work_dir=work,
        seg_min=settings.seg_min_s,
        seg_max=settings.seg_max_s,
    )

    p("rendering title card", 0.55)
    tc_path = work / "titlecard.mov"
    tc_mod.render_titlecard_track(
        title=settings.title or "(no title)",
        duration=tc_dur,
        channel=settings.channel,
        out_path=tc_path,
    )

    p("rendering captions", 0.65)
    cap_path = work / "captions.mov"
    on_screen = cap_mod.merge_words(words, timed)
    cap_mod.render_captions_track(
        words=on_screen,
        style=settings.captions,
        duration=total_len,
        start_offset=tc_dur,            # captions only after the title card
        out_path=cap_path,
    )

    p("composing final video", 0.85)
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Audio for the final mux: we need silence for the title-card portion,
    # then the trimmed speech. Make a combined wav using ffmpeg's concat.
    combined_audio = work / "audio_combined.wav"
    _build_combined_audio(trimmed_audio, tc_dur, combined_audio)

    out_file = out_dir / settings.output_filename
    compose_mod.compose(
        background=bg,
        titlecard=tc_path,
        captions=cap_path,
        audio=combined_audio,
        settings=settings,
        out=out_file,
        title_card_duration=tc_dur,
    )

    p("done", 1.0)
    return out_file


def _build_combined_audio(speech: Path, lead_silence_s: float, out: Path) -> None:
    """Prepend `lead_silence_s` of silence to `speech`, write to `out`."""
    import subprocess
    from app.config import FFMPEG
    cmd = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", f"{lead_silence_s:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
        "-i", str(speech),
        "-filter_complex",
        "[0:a]aresample=48000[s];[1:a]aresample=48000[v];[s][v]concat=n=2:v=0:a=1[a]",
        "-map", "[a]",
        "-c:a", "pcm_s16le",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
