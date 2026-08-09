"""``segment_beats`` skill: detect structural beats on the output Timeline.

A beat is a semantic segment (hook / setup / payoff / cta) that gives
decoration skills an anchor to attach localised effects to. This is Phase 0
of the two-phase planning model: structure first, then decoration.

The segmenter uses a two-pass heuristic:

1. Scan the final 30 % of transcript segments for CTA keyword patterns
   ("subscribe", "follow", "link in bio", etc.) to locate the cta beat.
2. Divide the remaining output duration into hook / setup / payoff with
   proportional thresholds tuned for 30-90 s talking-head shorts.

The result is intentionally deterministic — the beat map is the stable
substrate the LLM decoration phase attaches effects to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from ..models import Beat, TimeRange, Transcript
from ..timeline import Timeline
from .base import Context

_CTA_PATTERN = re.compile(
    r"\b(subscribe|follow|like|comment|check.?out|link.?in.?bio|notify|share|tag)\b",
    re.IGNORECASE,
)

# Proportional thresholds for 4-beat structure (fraction of output duration).
_HOOK_END_FRAC = 0.15
_SETUP_END_FRAC = 0.45
_PAYOFF_END_FRAC = 0.85

_ENERGY: dict[str, float] = {
    "hook": 0.9,
    "setup": 0.4,
    "payoff": 0.7,
    "cta": 0.5,
}


@dataclass(frozen=True)
class BeatSegmenterSkill:
    """Detect structural beats and write them to the Timeline.

    Cheap and purely additive (no FFmpeg, no API). Every preset includes it so
    decoration skills that declare ``reads={"beats"}`` always have anchors
    available. If the output duration is zero the beats list stays empty.
    """

    name: ClassVar[str] = "segment_beats"
    reads: ClassVar[frozenset[str]] = frozenset({"spine", "transcript"})
    writes: ClassVar[frozenset[str]] = frozenset({"beats"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        dur = tl.output_duration_ms
        if dur <= 0 or not ctx.transcript.segments:
            return tl
        return tl.model_copy(update={"beats": _detect_beats(ctx.transcript, dur)})


def _detect_beats(transcript: Transcript, dur_ms: int) -> list[Beat]:
    cta_start_ms: int | None = None
    cutoff = dur_ms * 0.70
    for seg in reversed(transcript.segments):
        if seg.start_ms < cutoff:
            break
        if _CTA_PATTERN.search(seg.text):
            cta_start_ms = seg.start_ms
            break

    if cta_start_ms is None:
        cta_start_ms = round(dur_ms * _PAYOFF_END_FRAC)

    hook_end = round(dur_ms * _HOOK_END_FRAC)
    setup_end = round(dur_ms * _SETUP_END_FRAC)

    raw: list[Beat] = [
        Beat(id="hook_0",   start_ms=0,            end_ms=hook_end,       role="hook",   energy=_ENERGY["hook"]),
        Beat(id="setup_0",  start_ms=hook_end,     end_ms=setup_end,      role="setup",  energy=_ENERGY["setup"]),
        Beat(id="payoff_0", start_ms=setup_end,    end_ms=cta_start_ms,   role="payoff", energy=_ENERGY["payoff"]),
        Beat(id="cta_0",    start_ms=cta_start_ms, end_ms=dur_ms,         role="cta",    energy=_ENERGY["cta"]),
    ]
    return [b for b in raw if b.end_ms > b.start_ms]
