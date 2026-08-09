"""``tighten_silence`` as a Skill: compress silence by editing the spine track.

This is the first feature ported to the Skill model. It is a thin adapter over
the existing pure ``tighten.compute_keep_ranges`` (no logic is duplicated): it
turns the source spans that function returns into spine clips. The result, run
through ``Timeline.spine_keep_ranges``, is identical to the ``keep_ranges`` the
current planner produces — which the equivalence tests assert.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import TimeRange
from ..tighten import compute_keep_ranges
from ..timeline import Clip, Timeline
from .base import Context


@dataclass(frozen=True)
class TightenSilenceSkill:
    """Drop long silences (and optionally filler words), rewriting the spine.

    Params mirror ``TighteningSettings`` (``max_silence_ms``, ``pad_ms``,
    ``remove_fillers``). When nothing meaningful would be cut, the Timeline is
    returned unchanged (the spine stays a single full-span clip → identity / uncut).
    This stays the only spine-writing skill so the topo graph has no cycle.
    """

    max_silence_ms: int
    pad_ms: int
    remove_fillers: bool = False

    name: ClassVar[str] = "tighten_silence"
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"spine"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        src = tl.spine_source
        duration = src.duration_ms or ctx.transcript.duration_ms
        ranges = compute_keep_ranges(
            ctx.transcript,
            max_silence_ms=self.max_silence_ms,
            pad_ms=self.pad_ms,
            source_duration_ms=duration,
            remove_fillers=self.remove_fillers,
        )
        if not ranges:
            return tl  # nothing to cut: leave the spine as-is (uncut)
        new_spine = [
            Clip(source_id=tl.spine_source_id, source_in_ms=r.start_ms, duration_ms=r.duration_ms)
            for r in ranges
            if r.duration_ms > 0
        ]
        return tl.model_copy(update={"spine": new_spine})
