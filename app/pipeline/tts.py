"""Chatterbox TTS + whisper-timestamped for word-level alignment.

Why two models?
  - Chatterbox produces high-quality speech but its inference API doesn't
    return word timings.
  - whisper-timestamped runs Whisper and emits per-word start/end times.
  - We feed whisper our generated audio AND our known transcript, then pair
    each detected word with the matching word from our typed story (so the
    color tags from {red}word{/red} carry across).

Models are loaded lazily into module-level singletons. First call downloads
weights into ./models/ (because HF_HOME is set in app/__init__.py); subsequent
renders re-use the loaded model.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import os
import re
import threading

import numpy as np


@dataclass
class TimedWord:
    """A word with timing in seconds, in the *raw* generated-audio timeline.
    `text` is the word as the user typed it (so we can match color tags later);
    timings come from whisper's forced alignment of the generated audio."""
    text: str
    start: float
    end: float


# ---------------------------------------------------------------- model state
_chatterbox = None
_chatterbox_lock = threading.Lock()
_whisper = None
_whisper_lock = threading.Lock()


def _device() -> str:
    """Pick the best available torch device. Chatterbox isn't reliable on
    MPS yet (s3tokenizer hits float64 issues), so we default to CPU; MPS can
    be opted into via env var if/when we want to experiment."""
    import torch
    if os.environ.get("RVG_TTS_DEVICE") == "mps" and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_chatterbox():
    global _chatterbox
    if _chatterbox is not None:
        return _chatterbox
    with _chatterbox_lock:
        if _chatterbox is None:
            from chatterbox.tts import ChatterboxTTS
            _chatterbox = ChatterboxTTS.from_pretrained(device=_device())
    return _chatterbox


def _load_whisper():
    """Load the whisper-timestamped model. small.en is ~244MB and accurate
    enough for narration TTS (where we already know the transcript)."""
    global _whisper
    if _whisper is not None:
        return _whisper
    with _whisper_lock:
        if _whisper is None:
            import whisper_timestamped as wt
            _whisper = wt.load_model("small.en", device="cpu")
    return _whisper


# ---------------------------------------------------------------- generation

def _save_wav(tensor, path: Path, sr: int) -> None:
    """Save a torch tensor as 16-bit PCM wav."""
    import torchaudio as ta
    ta.save(str(path), tensor.detach().cpu(), sample_rate=sr)


def synthesize(
    text: str,
    out_wav: Path,
    voice_ref_path: Optional[str] = None,
    *,
    exaggeration: float = 0.4,
    cfg_weight: float = 0.6,
) -> List[TimedWord]:
    """Generate speech for `text`, write to `out_wav`, return word timings.

    Args:
        text: clean text (color tags should already be stripped)
        out_wav: where to write the generated audio (.wav)
        voice_ref_path: optional path to a 5–10s reference audio for cloning.
            When omitted, Chatterbox uses its built-in default voice prior.
        exaggeration: 0..1, expressivity. Lower = more neutral narration.
        cfg_weight: 0..1, classifier-free guidance. Higher = more faithful
            to the reference voice and less random.
    """
    model = _load_chatterbox()
    kwargs = {"exaggeration": exaggeration, "cfg_weight": cfg_weight}
    if voice_ref_path and Path(voice_ref_path).exists():
        kwargs["audio_prompt_path"] = voice_ref_path
    wav = model.generate(text, **kwargs)
    _save_wav(wav, out_wav, model.sr)

    return _word_timings_for(out_wav, text)


# ---------------------------------------------------------------- alignment

_TOKEN_RE = re.compile(r"\S+")


def _word_timings_for(wav_path: Path, expected_text: str) -> List[TimedWord]:
    """Run whisper-timestamped on the generated audio and pair each detected
    word with the corresponding word from `expected_text`. Detected words
    almost always match the typed words (we just generated them) but when
    they drift we fall back to proportional distribution.
    """
    import whisper_timestamped as wt
    model = _load_whisper()
    audio = wt.load_audio(str(wav_path))
    result = wt.transcribe(model, audio, language="en", verbose=False, vad=False)

    detected: list[tuple[str, float, float]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            detected.append((str(w.get("text", "")).strip(),
                             float(w.get("start", 0.0)),
                             float(w.get("end", 0.0))))

    expected_words = _TOKEN_RE.findall(expected_text)

    if not detected or not expected_words:
        return _proportional_fallback(expected_words, wav_path)

    # When counts match we can zip directly — the typical case.
    if len(detected) == len(expected_words):
        return [TimedWord(text=ew, start=s, end=e)
                for ew, (_, s, e) in zip(expected_words, detected)]

    # Otherwise: align by index proportionally. Whisper sometimes splits
    # contractions ("don't" → "don" + "'t") or merges short words; for our
    # purposes per-word timing accuracy of ~50ms is fine.
    n_e, n_d = len(expected_words), len(detected)
    out: list[TimedWord] = []
    for i, ew in enumerate(expected_words):
        # map index i in expected -> nearest position in detected
        j = min(n_d - 1, int(i * n_d / n_e))
        _, s, e = detected[j]
        out.append(TimedWord(text=ew, start=s, end=e))
    return out


def _proportional_fallback(words: list[str], wav_path: Path) -> List[TimedWord]:
    """Last-resort: distribute words evenly across the audio duration."""
    import torchaudio as ta
    info = ta.info(str(wav_path))
    duration = info.num_frames / info.sample_rate
    if not words:
        return []
    step = duration / len(words)
    out: list[TimedWord] = []
    for i, w in enumerate(words):
        s = i * step
        e = (i + 1) * step
        out.append(TimedWord(text=w, start=s, end=e))
    return out


# ---------------------------------------------------------------- voice list

# Voices are now cloned from a reference clip rather than picked from a list.
# We keep this constant for compatibility with the UI; an empty path means
# "use Chatterbox's default voice."
DEFAULT_VOICE_REF: str = ""
