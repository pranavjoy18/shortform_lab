"""``hook`` skill: derive the opening text overlay from the first line."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import Hook, StyleConfig, TimeRange, Transcript
from ..timeline import Timeline
from .base import Context


def build_hook(transcript: Transcript, style: StyleConfig, *, max_words: int | None = None) -> Hook:
    """The opening line, truncated to a word budget, over the style's duration.

    ``max_words`` overrides ``style.hook.max_words`` when given (an LLM orchestrator
    may set it); ``None`` falls back to the style.
    """
    budget = max_words if max_words is not None else style.hook.max_words
    first = transcript.segments[0].text.strip()
    words = first.split()
    text = " ".join(words[:budget])
    # Drop a trailing period from the truncated hook; keep ? and ! for punch.
    text = text.rstrip(".,;:")
    if style.captions.uppercase:
        text = text.upper()
    end_ms = min(style.hook.duration_ms, transcript.duration_ms or style.hook.duration_ms)
    return Hook(text=text, start_ms=0, end_ms=end_ms)


@dataclass(frozen=True)
class HookSkill:
    """Set the hook overlay. Gated by ``style.hook.enabled`` upstream."""

    max_words: int | None = None

    name: ClassVar[str] = "hook"
    # Output-time element -> depends on the spine (cut list); see CaptionSkill.
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"hook"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(
            update={"hook": build_hook(ctx.transcript, ctx.style, max_words=self.max_words)}
        )
