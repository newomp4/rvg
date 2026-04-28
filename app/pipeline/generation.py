"""Story + title generation via the Claude Code CLI.

Why CLI and not the Anthropic SDK? Because the user is already running
Claude Code locally — shelling out to `claude -p` reuses their existing
auth/billing instead of asking them to provision a separate API key.

The functions read style references from a Reddit-stories markdown file
in the project root (any *.md whose name contains "story", "stories",
"script", or "reference"). The file is inlined into the prompt so the
generator picks up the user's preferred voice and structure.
"""
from __future__ import annotations
from pathlib import Path
import subprocess
import shutil

from app.config import PROJECT_ROOT


class GenError(RuntimeError):
    pass


# ---------------------------------------------------------------- references

_REF_PATTERNS = ("*stor*.md", "*script*.md", "*reference*.md")


def _find_references() -> str:
    """Return the contents of the first matching references doc, or ''."""
    seen: set[Path] = set()
    for pat in _REF_PATTERNS:
        for path in PROJECT_ROOT.glob(pat):
            if path.name.lower() in {"readme.md", "claude.md"}:
                continue
            if path in seen:
                continue
            seen.add(path)
            try:
                return path.read_text()
            except Exception:
                continue
    return ""


# ---------------------------------------------------------------- claude CLI

def _run_claude(prompt: str, *, timeout: int = 180) -> str:
    """Run `claude -p` with the given prompt, return stdout."""
    if not shutil.which("claude"):
        raise GenError(
            "The `claude` CLI isn't on your PATH. Install Claude Code from "
            "https://claude.com/code, then relaunch the app.")
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise GenError(f"Claude Code didn't reply within {timeout}s.")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "no output").strip()
        raise GenError(f"claude -p exited {proc.returncode}: {msg[:500]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise GenError("Claude Code returned empty output.")
    return out


def _strip_quotes(s: str) -> str:
    s = s.strip()
    for q in ('"', "'", "“", "”", "‘", "’"):
        if s.startswith(q) and s.endswith(q) and len(s) > 1:
            s = s[1:-1].strip()
    return s


# ---------------------------------------------------------------- public API

_TITLE_RULES = (
    "Style guide for the title:\n"
    "- 50 to 100 characters.\n"
    "- First-person, lowercase except proper nouns and the leading acronym.\n"
    "- Start with a Reddit-style hook: \"AITA for\", \"AITAH for\", "
    "\"TIFU by\", \"WIBTA if\", \"how did\", \"what's the\", or a punchy "
    "first-person setup.\n"
    "- Hooky and dramatic, set up a question, do not reveal the ending.\n"
    "- Match the actual story; do not fabricate facts."
)


def generate_title(story: str) -> str:
    """Generate a Reddit-style title for `story`. One line, no quotes."""
    story = (story or "").strip()
    if not story:
        raise GenError("story is empty — write something first")

    refs = _find_references()
    parts = []
    if refs:
        parts.append(
            "Here are reference titles in the exact style I want, drawn from "
            "high-performing posts:\n\n" + refs[:60_000])
    parts.append(_TITLE_RULES)
    parts.append(
        "Write ONE title for the story below. Output ONLY the title — "
        "no preamble, no quotes, no explanation, no trailing newline.\n\n"
        "STORY:\n" + story[:8000])

    return _strip_quotes(_run_claude("\n\n".join(parts)))


_STORY_RULES = (
    "Style guide for the story:\n"
    "- First-person past tense, conversational, no chapter breaks.\n"
    "- 180 to 260 words (about 60 to 90 seconds spoken).\n"
    "- Strong hook in the first sentence.\n"
    "- Vivid, surprising, and hooky — Reddit-r/AITA / r/TIFU energy.\n"
    "- Plausible everyday situation, escalating tension, satisfying ending.\n"
    "- Plain prose; do NOT include color tags or markdown.\n"
    "- Do not include the title; the story body only."
)


def generate_story(prompt_hint: str = "") -> str:
    """Generate a fresh Reddit-style story body. `prompt_hint` is an
    optional nudge ('story about a wedding gone wrong')."""
    refs = _find_references()
    parts = []
    if refs:
        parts.append(
            "Here are reference stories in the exact style I want:\n\n"
            + refs[:80_000])
    parts.append(_STORY_RULES)
    if prompt_hint.strip():
        parts.append(f"Theme / hint: {prompt_hint.strip()}")
    parts.append(
        "Write ONE original story now. Output ONLY the story body — "
        "no title, no preamble, no quotes, no explanation, no trailing notes.")
    return _strip_quotes(_run_claude("\n\n".join(parts)))
