"""Top-level pipeline runner.

Drives every step in order, emits progress strings via a callback so the UI
can show what's happening, and stages all intermediates under tmp/<job_id>.

The audio for the final video is structured as:
    [title spoken aloud] [natural pause] [story body]
The title card animates in at t=0, holds while the title is spoken, and
plays its 1s exit animation as the title speech finishes. Captions only
appear once the title card is on its way out, so the title card itself
is uncluttered.
"""
from __future__ import annotations
from pathlib import Path
from typing import Callable
import re
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

_TOKEN_RE = re.compile(r"\S+")
# We want a noticeable beat between the title and the story so the title
# card has time to play its exit animation while the listener processes
# the title. The exit animation itself is 1.0s; we let it finish before
# captions kick in.
_TITLE_CARD_EXIT_S = 1.0
# A small buffer before captions appear so the screen isn't crowded right
# as the card leaves frame.
_CAPTION_LEAD_S    = 0.10


def render(settings: RenderSettings, progress: ProgressFn | None = None) -> Path:
    p = progress or (lambda msg, frac: None)
    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    work = TMP_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)

    p("parsing story", 0.02)
    plain, story_words = story_mod.parse_story(settings.story)
    if not plain.strip():
        raise RuntimeError("story is empty")
    story_plain = story_mod.strip_for_tts(plain)

    title_text = (settings.title or "").strip()
    # Build the TTS input as title + period + story so the voice reads the
    # title aloud first, then naturally pauses before launching into the
    # story body.
    if title_text:
        title_for_tts = title_text
        if title_for_tts[-1] not in ".!?":
            title_for_tts += "."
        tts_text = f"{title_for_tts} {story_plain}"
    else:
        tts_text = story_plain
    title_word_count = len(_TOKEN_RE.findall(title_text)) if title_text else 0

    p("synthesizing voice", 0.08)
    raw_wav = work / "speech_raw.wav"
    timed = tts_mod.synthesize(
        text=tts_text,
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

    # Title-speech end timing: in the post-trim timeline, the (k-1)th word
    # is the last title word. We fall back gracefully if alignment dropped
    # title words for some reason.
    if title_word_count and len(timed) >= title_word_count:
        title_end_t = float(timed[title_word_count - 1].end)
    else:
        title_end_t = 1.5    # safe default if no title or alignment failed

    # The title card stays on screen for the full title speech, then runs
    # its 1.0s exit animation. After that the card is gone and captions
    # take over.
    tc_dur = title_end_t + _TITLE_CARD_EXIT_S

    p("building background video", 0.30)
    bg = bg_mod.build_background(
        clips_dir=Path(settings.clips_dir),
        target_duration=audio_len + 0.5,
        work_dir=work,
        seg_min=settings.seg_min_s,
        seg_max=settings.seg_max_s,
    )

    p("rendering title card", 0.55)
    tc_path = work / "titlecard.mov"
    tc_mod.render_titlecard_track(
        title=title_text or "(no title)",
        duration=tc_dur,
        channel=settings.channel,
        out_path=tc_path,
    )

    p("rendering captions", 0.65)
    cap_path = work / "captions.mov"
    # Captions cover the story body only — title words are skipped because
    # merge_words walks story_words (which has only the story body) and
    # advances through `timed` until the text matches, naturally jumping
    # past the title-word entries.
    on_screen = cap_mod.merge_words(story_words, timed)
    # Suppress any caption that would land while the title card is still
    # visible. Words with start < (tc_dur - lead) are dropped, otherwise
    # captions would appear behind the falling title card.
    cutoff = max(0.0, tc_dur - _CAPTION_LEAD_S)
    on_screen = [w for w in on_screen if w.start >= cutoff]
    cap_mod.render_captions_track(
        words=on_screen,
        style=settings.captions,
        duration=audio_len,
        start_offset=0.0,                 # word timings are already absolute
        out_path=cap_path,
    )

    p("composing final video", 0.85)
    out_dir = OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / settings.output_filename
    compose_mod.compose(
        background=bg,
        titlecard=tc_path,
        captions=cap_path,
        audio=trimmed_audio,              # audio starts immediately with title
        settings=settings,
        out=out_file,
        title_card_duration=tc_dur,
    )

    p("done", 1.0)
    return out_file
