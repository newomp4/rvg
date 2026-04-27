"""Edge TTS wrapper.

edge-tts streams two kinds of events while it synthesizes:
  - audio chunks (bytes), which we concatenate to an mp3
  - WordBoundary events, which give us start time + duration per spoken word
    in units of 100-nanoseconds (i.e. divide by 1e7 to get seconds)

We use the WordBoundary events as the canonical word-timing source, which is
much cheaper and more accurate than running a forced aligner like Whisper.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List
import asyncio

import edge_tts


@dataclass
class TimedWord:
    text: str          # the word string as the TTS pronounced it (may differ slightly from input)
    start: float       # seconds, in *original* audio (before silence removal)
    end: float


async def _synthesize_async(text: str, voice: str, rate: str, out_mp3: Path) -> List[TimedWord]:
    # boundary='WordBoundary' is required since edge-tts ≥7 — the default
    # changed to SentenceBoundary, which is too coarse for per-word captions.
    com = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    timed: list[TimedWord] = []
    with open(out_mp3, "wb") as f:
        async for chunk in com.stream():
            t = chunk.get("type")
            if t == "audio":
                f.write(chunk["data"])
            elif t == "WordBoundary":
                start = chunk["offset"] / 1e7
                end = start + chunk["duration"] / 1e7
                timed.append(TimedWord(text=chunk["text"], start=start, end=end))
    return timed


def synthesize(text: str, voice: str, rate: str, out_mp3: Path) -> List[TimedWord]:
    """Synthesize `text` to `out_mp3`. Returns word timings in seconds."""
    return asyncio.run(_synthesize_async(text, voice, rate, out_mp3))


# Curated subset of high-quality en-US voices — full list is huge, this
# covers the natural-sounding ones that work well for narration.
PRESET_VOICES: list[tuple[str, str]] = [
    ("Andrew (US, male, warm)",     "en-US-AndrewMultilingualNeural"),
    ("Brian (US, male, casual)",    "en-US-BrianMultilingualNeural"),
    ("Christopher (US, male, low)", "en-US-ChristopherNeural"),
    ("Eric (US, male, calm)",       "en-US-EricNeural"),
    ("Guy (US, male, narration)",   "en-US-GuyNeural"),
    ("Aria (US, female, warm)",     "en-US-AriaNeural"),
    ("Ava (US, female, casual)",    "en-US-AvaMultilingualNeural"),
    ("Emma (US, female, energetic)","en-US-EmmaMultilingualNeural"),
    ("Jenny (US, female, warm)",    "en-US-JennyNeural"),
    ("Michelle (US, female, calm)", "en-US-MichelleNeural"),
    ("Ryan (UK, male, deep)",       "en-GB-RyanNeural"),
    ("Sonia (UK, female, clear)",   "en-GB-SoniaNeural"),
]
