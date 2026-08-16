"""``hook`` skill: the opening text overlay.

Deterministically this is extractive (the first line, word-capped) — a safe but
uninspired fallback, reachable only via ``--debug`` (see ``presets.py``). The
generative path (``AgenticOrchestrator``) is expected to supply ``text``: an
LLM-authored line that teases the clip rather than repeating it verbatim, since a
hook and the caption playing under it must never say the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import Hook, StyleConfig, TimeRange, Transcript
from ..timeline import Timeline
from .base import Context

# Words that almost always require a following word — trimming a truncated hook
# down to one of these reads as an unfinished thought ("...on their"), so the
# truncator backs up past them instead. Not real NLP, just a blunt safety net.
_DANGLING_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "with",
    "for", "is", "are", "was", "were", "that", "this", "which", "who", "who's",
    "their", "his", "her", "its", "my", "your", "our", "so", "because", "if", "as",
})


def _clause_safe_truncate(text: str, max_words: int, *, min_words: int = 3) -> str:
    """Cut ``text`` to at most ``max_words``, then back up off any dangling word.

    Blind word-count truncation regularly lands on an article/preposition/pronoun
    and leaves a dangling fragment ("...too early on their"). This backs off one
    word at a time (down to ``min_words``) until the last word isn't one of those,
    so a truncated hook still reads as a complete-feeling phrase.
    """
    words = text.split()[:max_words]
    while len(words) > min_words and words[-1].lower().strip(".,;:!?") in _DANGLING_WORDS:
        words.pop()
    return " ".join(words).rstrip(".,;:")


def build_hook(
    transcript: Transcript,
    style: StyleConfig,
    *,
    max_words: int | None = None,
    text: str | None = None,
) -> Hook:
    """Build the hook overlay.

    ``text`` is LLM-authored hook copy (the generative path); when given, it is
    used verbatim (word-capped as a safety net) instead of the transcript. ``None``
    falls back to the extractive default: the opening line, word-capped. ``max_words``
    overrides ``style.hook.max_words`` when given.
    """
    budget = max_words if max_words is not None else style.hook.max_words
    source = text.strip() if text is not None else transcript.segments[0].text.strip()
    out = _clause_safe_truncate(source, budget)
    if style.captions.uppercase:
        out = out.upper()
    end_ms = min(style.hook.duration_ms, transcript.duration_ms or style.hook.duration_ms)
    return Hook(text=out, start_ms=0, end_ms=end_ms)


@dataclass(frozen=True)
class HookSkill:
    """Set the hook overlay. Gated by ``style.hook.enabled`` (+ ``--debug``) upstream
    in the deterministic preset; the generative path may enable it unconditionally.
    """

    max_words: int | None = None
    text: str | None = None

    name: ClassVar[str] = "hook"
    # Output-time element -> depends on the spine (cut list); see CaptionSkill.
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"hook"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(
            update={"hook": build_hook(
                ctx.transcript, ctx.style, max_words=self.max_words, text=self.text
            )}
        )
