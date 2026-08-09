"""``overlay`` skill: quote/text cards from the strongest transcript lines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import StyleConfig, TimeRange, Transcript, VisualOverlay
from ..timeline import Timeline
from .base import Context


def build_overlays(
    transcript: Transcript, style: StyleConfig, *, count: int | None = None
) -> list[VisualOverlay]:
    """Quote cards on the longest non-opening lines, in chronological order.

    ``count`` overrides ``style.visuals.text_card_count`` when given.
    """
    count = style.visuals.text_card_count if count is None else count
    if count <= 0 or len(transcript.segments) <= 1:
        return []
    # Skip the first segment (it feeds the hook); rank the rest by length and take
    # the strongest few, then restore chronological order.
    candidates = sorted(transcript.segments[1:], key=lambda s: len(s.text), reverse=True)[:count]
    candidates.sort(key=lambda s: s.start_ms)
    return [
        VisualOverlay(
            kind="quote_card",
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            text=_shorten(s.text, max_chars=60),
            placement="center",
        )
        for s in candidates
    ]


def _shorten(text: str, *, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


@dataclass(frozen=True)
class OverlaySkill:
    """Add quote-card overlays. Gated by ``style.visuals.overlays_enabled``."""

    count: int | None = None

    name: ClassVar[str] = "overlay"
    # Output-time element -> depends on the spine (cut list); see CaptionSkill.
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"overlays"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(
            update={"overlays": build_overlays(ctx.transcript, ctx.style, count=self.count)}
        )
