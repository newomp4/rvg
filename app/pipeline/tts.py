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
    """Run whisper-timestamped on the generated audio and pair each typed
    word with the right detected word, accounting for two real failure modes:
      1. Whisper hallucinates extra words past the end of audio (especially
         on the trailing silence) — those have timestamps > audio_dur and
         must be dropped before pairing.
      2. Whisper occasionally misses a word, splits one (e.g. "I'd" ->
         "I", "would"), or merges two. We DTW-align the typed sequence
         against the detected sequence on lowercased-stripped tokens so
         each typed word picks up the timing of its best match.
    """
    import whisper_timestamped as wt
    import soundfile as sf
    model = _load_whisper()
    audio = wt.load_audio(str(wav_path))
    # condition_on_previous_text=False prevents whisper from looping its own
    # output back into the prompt for later segments, which is what was
    # causing the trailing-hallucination dump.
    result = wt.transcribe(
        model, audio, language="en", verbose=False, vad=False,
        condition_on_previous_text=False,
    )

    audio_arr, sr = sf.read(str(wav_path))
    audio_dur = len(audio_arr) / sr

    detected: list[tuple[str, float, float]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            s = float(w.get("start", 0.0))
            e = float(w.get("end", 0.0))
            txt = str(w.get("text", "")).strip()
            # drop whisper hallucinations past the audio's actual length
            if e > audio_dur + 0.1 or s > audio_dur:
                continue
            if not txt:
                continue
            detected.append((txt, s, min(e, audio_dur)))

    expected_words = _TOKEN_RE.findall(expected_text)
    if not expected_words:
        return []
    if not detected:
        return _proportional_fallback(expected_words, wav_path)

    return _dtw_pair(expected_words, detected, audio_dur)


def _norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tok.lower())


def _dtw_pair(expected: list[str], detected: list[tuple[str, float, float]],
              audio_dur: float) -> List[TimedWord]:
    """Sequence-align expected vs detected tokens with a small DP, then
    fill timings for unmatched expected tokens by interpolating between
    their matched neighbors. Robust to drops, splits, and merges."""
    n, m = len(expected), len(detected)
    exp_n = [_norm(t) for t in expected]
    det_n = [_norm(t) for t, _, _ in detected]

    # DP: best (matches, -mismatches) score taking either skip-expected,
    # skip-detected, or match-current-pair. Standard Needleman–Wunsch.
    INF = -10**9
    dp = [[(INF, INF)] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = (0, 0)
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            cands = []
            if i > 0 and j > 0:
                a, b = dp[i - 1][j - 1]
                if a > INF:
                    if exp_n[i - 1] and exp_n[i - 1] == det_n[j - 1]:
                        cands.append((a + 2, b))           # exact match: +2
                    elif exp_n[i - 1] and det_n[j - 1] and (
                        exp_n[i - 1].startswith(det_n[j - 1][:3]) or
                        det_n[j - 1].startswith(exp_n[i - 1][:3])
                    ):
                        cands.append((a + 1, b))           # near-match: +1
                    else:
                        cands.append((a, b - 1))           # mismatch
            if i > 0:                                       # skip expected
                a, b = dp[i - 1][j]
                if a > INF:
                    cands.append((a, b - 1))
            if j > 0:                                       # skip detected
                a, b = dp[i][j - 1]
                if a > INF:
                    cands.append((a, b - 1))
            if cands:
                dp[i][j] = max(cands)

    # backtrack to find which detected index each expected[i] aligned to
    pair: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        cur = dp[i][j]
        # try diagonal
        if i > 0 and j > 0:
            prev = dp[i - 1][j - 1]
            if prev[0] > INF:
                if exp_n[i - 1] and exp_n[i - 1] == det_n[j - 1]:
                    if cur == (prev[0] + 2, prev[1]):
                        pair[i - 1] = j - 1
                        i -= 1; j -= 1; continue
                elif exp_n[i - 1] and det_n[j - 1] and (
                    exp_n[i - 1].startswith(det_n[j - 1][:3]) or
                    det_n[j - 1].startswith(exp_n[i - 1][:3])
                ):
                    if cur == (prev[0] + 1, prev[1]):
                        pair[i - 1] = j - 1
                        i -= 1; j -= 1; continue
                if cur == (prev[0], prev[1] - 1):
                    pair[i - 1] = j - 1
                    i -= 1; j -= 1; continue
        if i > 0 and dp[i - 1][j][0] > INF and cur == (dp[i - 1][j][0], dp[i - 1][j][1] - 1):
            i -= 1; continue                                # skipped expected[i-1]
        if j > 0 and dp[i][j - 1][0] > INF and cur == (dp[i][j - 1][0], dp[i][j - 1][1] - 1):
            j -= 1; continue                                # skipped detected[j-1]
        break

    # build output, interpolating timings for unmatched expected tokens
    out: list[TimedWord] = []
    last_end = 0.0
    for i in range(n):
        if pair[i] is not None:
            _, s, e = detected[pair[i]]
            out.append(TimedWord(text=expected[i], start=max(s, last_end), end=e))
            last_end = e
        else:
            # find next matched index
            nxt = next((k for k in range(i + 1, n) if pair[k] is not None), None)
            if nxt is not None:
                next_s = detected[pair[nxt]][1]
                gap = max(1, nxt - i + 1)
                step = (next_s - last_end) / gap
                s = last_end
                e = last_end + step
                out.append(TimedWord(text=expected[i], start=s, end=e))
                last_end = e
            else:
                # tail: spread remaining expected over the rest of the audio
                remaining = n - i
                step = max(0.05, (audio_dur - last_end) / remaining)
                out.append(TimedWord(text=expected[i],
                                     start=last_end,
                                     end=min(audio_dur, last_end + step)))
                last_end = min(audio_dur, last_end + step)
    return out


def _proportional_fallback(words: list[str], wav_path: Path) -> List[TimedWord]:
    import torchaudio as ta
    info = ta.info(str(wav_path))
    duration = info.num_frames / info.sample_rate
    if not words:
        return []
    step = duration / len(words)
    return [TimedWord(text=w, start=i * step, end=(i + 1) * step) for i, w in enumerate(words)]
