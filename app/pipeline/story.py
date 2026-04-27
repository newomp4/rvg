"""Parse stories with inline color tags.

Input format (case-insensitive tag names):
    "I got into a {red}car crash{/red} yesterday and {green}threw up{/green}."

Output:
    plain text:    "I got into a car crash yesterday and threw up."
    word colors:   [("I", default), ("got", default), ..., ("car", red), ("crash", red), ...]

Tags are line-friendly and forgiving: unknown tags fall back to the default
color, unclosed tags propagate to end of story, nesting takes the inner color.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
import re

from app.config import COLOR_PALETTE, DEFAULT_CAPTION_COLOR

TAG_RE = re.compile(r"\{(/?)([a-zA-Z_][a-zA-Z0-9_]*)\}")
WORD_RE = re.compile(r"\S+")


@dataclass
class Word:
    text: str
    color: str           # hex string, e.g. "#ff3b30"


def parse_story(raw: str) -> tuple[str, List[Word]]:
    """Returns (plain_text, list_of_Word). plain_text is suitable for TTS."""
    color_stack: list[str] = [DEFAULT_CAPTION_COLOR]
    plain_parts: list[str] = []
    pos = 0

    # First pass: build plain text with tags removed, but remember a color
    # for every character span so we can assign words their colors after.
    char_colors: list[str] = []
    for m in TAG_RE.finditer(raw):
        # text before the tag inherits the current top of stack
        chunk = raw[pos:m.start()]
        plain_parts.append(chunk)
        char_colors.extend([color_stack[-1]] * len(chunk))

        is_close = bool(m.group(1))
        name = m.group(2).lower()
        if is_close:
            if len(color_stack) > 1:
                color_stack.pop()
        else:
            color_stack.append(COLOR_PALETTE.get(name, DEFAULT_CAPTION_COLOR))
        pos = m.end()

    # tail
    tail = raw[pos:]
    plain_parts.append(tail)
    char_colors.extend([color_stack[-1]] * len(tail))

    plain = "".join(plain_parts)

    # Now walk word matches in plain and pick the *most common* color across
    # the word's characters (handles the rare case of a tag landing mid-word).
    words: list[Word] = []
    for wm in WORD_RE.finditer(plain):
        span = char_colors[wm.start():wm.end()]
        color = max(set(span), key=span.count) if span else DEFAULT_CAPTION_COLOR
        words.append(Word(text=wm.group(0), color=color))

    return plain, words


def strip_for_tts(plain: str) -> str:
    """Light cleanup for TTS — collapse whitespace, keep punctuation."""
    return re.sub(r"\s+", " ", plain).strip()
