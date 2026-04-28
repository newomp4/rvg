"""Qwen3-TTS + whisper-timestamped for word-level alignment.

Why two models?
  - Qwen3-TTS (the "CustomVoice" variant) produces high-quality speech with
    built-in named voices — no reference clip required. Perfect default UX.
  - It does NOT expose word-level timings. We feed the generated audio
    through whisper-timestamped to get per-word start/end times, then pair
    them with the typed words from the story (so the {color} tags carry).

Models are loaded lazily into module-level singletons. First call downloads
weights into ./models/ via HF_HOME (set in app/__init__.py); subsequent
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


# Built-in English voices on the Qwen3-TTS-CustomVoice checkpoint. The user
# picks one of these in the UI; no reference clip is required.
PRESET_VOICES_EN: list[tuple[str, str]] = [
    ("Aiden (US, male, warm)", "Aiden"),
    ("Ryan (US, male, neutral)", "Ryan"),
]
DEFAULT_VOICE = "Aiden"


@dataclass
class TimedWord:
    """A word with timing in seconds, in the *raw* generated-audio timeline.
    `text` is the word as the user typed it; timings come from whisper's
    forced alignment of the generated audio."""
    text: str
    start: float
    end: float


# ---------------------------------------------------------------- model state
_qwen = None
_qwen_sr: int | None = None
_qwen_lock = threading.Lock()
_whisper = None
_whisper_lock = threading.Lock()


def _device() -> str:
    """Default to CPU — MPS produces glitchy output with Qwen3-TTS as of
    qwen-tts 0.1.x (suspected fp16 codec ops). CPU is actually faster on
    M-series for this model AND produces clean audio. Set RVG_TTS_DEVICE=mps
    to opt back in if a future qwen-tts release fixes it."""
    import torch
    requested = os.environ.get("RVG_TTS_DEVICE", "cpu")
    if requested == "mps" and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_qwen():
    global _qwen
    if _qwen is not None:
        return _qwen
    with _qwen_lock:
        if _qwen is not None:
            return _qwen
        import torch
        from qwen_tts import Qwen3TTSModel
        device = _device()
        # MPS dislikes flash-attn and bf16; use eager + fp16 there.
        if device == "mps":
            dtype = torch.float16
            attn = "eager"
        else:
            dtype = torch.float32
            attn = "eager"
        _qwen = Qwen3TTSModel.from_pretrained(
            "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
            device_map=device,
            dtype=dtype,
            attn_implementation=attn,
        )
    return _qwen


def _load_whisper():
    global _whisper
    if _whisper is not None:
        return _whisper
    with _whisper_lock:
        if _whisper is None:
            import whisper_timestamped as wt
            _whisper = wt.load_model("small.en", device="cpu")
    return _whisper


# ---------------------------------------------------------------- generation

def synthesize(
    text: str,
    out_wav: Path,
    voice: Optional[str] = None,
    *,
    voice_ref_path: Optional[str] = None,
    voice_ref_text: Optional[str] = None,
    instruct: Optional[str] = None,
    seed: int = 1,
) -> List[TimedWord]:
    """Generate speech for `text`, write to `out_wav`, return word timings.

    Args:
        text: clean text (color tags should already be stripped)
        out_wav: where to write the generated audio (.wav)
        voice: one of PRESET_VOICES_EN names ("Aiden", "Ryan"). Used when no
            reference clip is provided.
        voice_ref_path: optional reference audio path for voice cloning. If
            provided, switches to clone mode (uses the Base checkpoint
            internally — currently we just use the CustomVoice path and a
            note here for future expansion).
        voice_ref_text: required if voice_ref_path is set — exact transcript
            of the reference clip.
        instruct: optional style prompt, e.g. "say it cheerfully".
        seed: PRNG seed. Qwen3-TTS samples codec tokens stochastically
            (temperature=0.9 default) so each render rolls a different
            sequence; bad rolls produce audible glitches. Locking the seed
            makes a story render reproducibly. Bump to a different value to
            re-roll if a particular seed sounds bad on a particular story.
    """
    import soundfile as sf
    import torch

    model = _load_qwen()
    speaker = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE

    # voice cloning would need the Base checkpoint; for the v1 we use preset
    # voices on the CustomVoice model, which are explicitly what the user
    # asked for ("use whatever voice it wants"). Reference-clip support can
    # be re-added later if needed.
    kwargs = dict(text=text, language="English", speaker=speaker)
    if instruct:
        kwargs["instruct"] = instruct

    torch.manual_seed(int(seed))
    wavs, sr = model.generate_custom_voice(**kwargs)
    audio = wavs[0]
    if hasattr(audio, "detach"):                 # torch tensor → numpy
        audio = audio.detach().cpu().numpy()
    audio = np.asarray(audio, dtype=np.float32).squeeze()
    sf.write(str(out_wav), audio, sr)

    return _word_timings_for(out_wav, text)


# ---------------------------------------------------------------- alignment

_TOKEN_RE = re.compile(r"\S+")


def _word_timings_for(wav_path: Path, expected_text: str) -> List[TimedWord]:
    """Run whisper-timestamped on the generated audio and pair each detected
    word with the corresponding word from `expected_text`. Detected words
    almost always match the typed words (we just generated them) but when
    they drift we fall back to proportional distribution."""
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

    if len(detected) == len(expected_words):
        return [TimedWord(text=ew, start=s, end=e)
                for ew, (_, s, e) in zip(expected_words, detected)]

    # mismatch: align by proportional index
    n_e, n_d = len(expected_words), len(detected)
    out: list[TimedWord] = []
    for i, ew in enumerate(expected_words):
        j = min(n_d - 1, int(i * n_d / n_e))
        _, s, e = detected[j]
        out.append(TimedWord(text=ew, start=s, end=e))
    return out


def _proportional_fallback(words: list[str], wav_path: Path) -> List[TimedWord]:
    import torchaudio as ta
    info = ta.info(str(wav_path))
    duration = info.num_frames / info.sample_rate
    if not words:
        return []
    step = duration / len(words)
    return [TimedWord(text=w, start=i * step, end=(i + 1) * step) for i, w in enumerate(words)]
