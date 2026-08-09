"""``punch_in`` skill: spread zoom moments across the clip for visual rhythm."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import PunchIn, StyleConfig, TimeRange, Transcript
from ..timeline import Timeline
from .base import Context


def build_punch_ins(
    transcript: Transcript, style: StyleConfig, *, count: int | None = None
) -> list[PunchIn]:
    """Spread punch-ins across the back half, biased to the middle (open/close stay wide).

    ``count`` overrides ``style.visuals.punch_in_count`` when given.
    """
    count = style.visuals.punch_in_count if count is None else count
    segments = transcript.segments
    if count <= 0 or not segments:
        return []
    chosen: list[PunchIn] = []
    n = len(segments)
    for i in range(count):
        idx = min(n - 1, (n // 2) + i)
        seg = segments[idx]
        chosen.append(PunchIn(start_ms=seg.start_ms, end_ms=seg.end_ms, zoom=1.2))
    # De-duplicate by start_ms in case the clip is very short.
    seen: set[int] = set()
    return [p for p in chosen if not (p.start_ms in seen or seen.add(p.start_ms))]


@dataclass(frozen=True)
class PunchInSkill:
    """Add punch-in zoom effects (count per the style unless overridden; may be empty)."""

    count: int | None = None

    name: ClassVar[str] = "punch_in"
    # Output-time element -> depends on the spine (cut list); see CaptionSkill.
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"punch_ins"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(
            update={"punch_ins": build_punch_ins(ctx.transcript, ctx.style, count=self.count)}
        )
