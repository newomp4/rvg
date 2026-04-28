"""Reddit-style title generation via Claude Haiku.

Reads ANTHROPIC_API_KEY from the environment. The prompt is tuned for the
storiesandtexts / AITA / TIFU style: first-person, 50–90 chars, hooky,
sets up a question without giving away the punchline.
"""
from __future__ import annotations
import os

MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You write viral Reddit-style story titles for short YouTube videos. "
    "Given a first-person story, output ONE title and nothing else. "
    "No quotes, no preamble, no trailing punctuation beyond a single ? or . "
    "Style guide:\n"
    "- 50 to 90 characters.\n"
    "- First person, past tense (\"I\", \"my\", \"we\").\n"
    "- Start with one of: \"AITA for\", \"TIFU by\", \"WIBTA if\", "
    "\"I [verb]\", \"My [person]\", \"AITAH for\", or a punchy hook.\n"
    "- Hooky and dramatic, set up a question, don't reveal the ending.\n"
    "- Match the actual story content; don't fabricate details.\n"
    "- All lowercase except proper nouns and the leading acronym (AITA etc.)."
)


class TitleError(RuntimeError):
    pass


def generate_title(story: str) -> str:
    """Generate a Reddit-style title from a story body. Raises TitleError
    on missing API key or API failure (UI catches and shows a dialog)."""
    story = (story or "").strip()
    if not story:
        raise TitleError("story is empty — write some text first")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise TitleError(
            "ANTHROPIC_API_KEY is not set. Add it to your shell env, e.g.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...\n"
            "then relaunch the app.")

    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise TitleError(f"anthropic SDK not installed: {e}")

    client = Anthropic(api_key=api_key)
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=_SYSTEM,
            messages=[{"role": "user", "content": story[:6000]}],
        )
    except Exception as e:
        raise TitleError(f"Claude API error: {e}") from e

    # response is a list of content blocks; we expect a single text block
    parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    title = " ".join(parts).strip().strip('"').strip("'").strip()
    if not title:
        raise TitleError("Claude returned an empty title")
    return title
