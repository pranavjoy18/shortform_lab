"""``add_transitions`` skill: annotate spine cut-points with xfade transitions.

``TransitionSkill`` writes one ``TransitionInfo`` per gap between spine clips.
The renderer threads these through FFmpeg's ``xfade`` filter so each cut becomes
a visual transition (fade, dissolve, wipe…) instead of a hard cut.

The skill is a no-op when the spine has fewer than two clips (no cuts, nothing
to transition). It runs after any spine-writing skills (tighten) because it
declares a read on ``spine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import TimeRange, TransitionInfo, TransitionType
from ..timeline import Timeline
from .base import Context


def build_transitions(
    tl: Timeline,
    *,
    transition_type: TransitionType = "fade",
    duration_ms: int = 300,
) -> list[TransitionInfo]:
    """Build one TransitionInfo per gap in the spine (N-1 for N clips)."""
    n = len(tl.spine)
    if n < 2:
        return []
    return [
        TransitionInfo(transition_type=transition_type, duration_ms=duration_ms)
        for _ in range(n - 1)
    ]


@dataclass(frozen=True)
class TransitionSkill:
    """Place a visual transition at every cut point in the spine.

    ``transition_type`` is any FFmpeg xfade name (fade/dissolve/wipeleft/…).
    ``duration_ms`` is the crossfade window; both adjacent clips must be at least
    this long or the coherence layer will flag it.
    """

    transition_type: TransitionType = "fade"
    duration_ms: int = 300

    name: ClassVar[str] = "add_transitions"
    reads: ClassVar[frozenset[str]] = frozenset({"spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"transitions"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        new_transitions = build_transitions(
            tl,
            transition_type=self.transition_type,
            duration_ms=self.duration_ms,
        )
        return tl.model_copy(update={"transitions": new_transitions})
