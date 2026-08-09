"""``color_grade`` skill: set a global color grade on the Timeline.

The grade maps to FFmpeg's ``eq`` filter (brightness/contrast/saturation/gamma).
``LOOKS`` are named presets so a style or the LLM can say "cinematic" instead of
tuning four numbers; explicit params override a look's values. Color is a global
effect — it depends on nothing — so the skill reads nothing and writes ``color``.

This is the first feature rendered straight from the Timeline (no EditPlan field);
see ``render.py``'s ``timeline`` parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import ColorGrade, GradeSpan, TimeRange
from ..timeline import Timeline
from .base import Context

# Named grades, all expressible with `eq` so they work on any FFmpeg build.
LOOKS: dict[str, ColorGrade] = {
    "vivid": ColorGrade(contrast=1.15, saturation=1.4),
    "punchy": ColorGrade(brightness=0.02, contrast=1.25, saturation=1.25),
    "cinematic": ColorGrade(brightness=-0.03, contrast=1.2, saturation=0.85, gamma=1.05),
    "soft": ColorGrade(brightness=0.04, contrast=0.92, saturation=0.95, gamma=1.05),
    "bright": ColorGrade(brightness=0.08, saturation=1.05),
    "mono": ColorGrade(saturation=0.0),
}


def build_color_grade(
    *,
    look: str | None = None,
    brightness: float | None = None,
    contrast: float | None = None,
    saturation: float | None = None,
    gamma: float | None = None,
) -> ColorGrade:
    """Resolve a grade from an optional named ``look`` plus explicit overrides.

    Raises ``ValueError`` for an unknown look name (caught by the LLM fallback).
    """
    if look is not None and look not in LOOKS:
        raise ValueError(f"unknown color look {look!r}; valid looks: {sorted(LOOKS)}")
    base = LOOKS[look] if look is not None else ColorGrade()
    return base.model_copy(
        update={
            k: v
            for k, v in {
                "brightness": brightness,
                "contrast": contrast,
                "saturation": saturation,
                "gamma": gamma,
            }.items()
            if v is not None
        }
    )


@dataclass(frozen=True)
class ColorGradeSkill:
    """Set the Timeline's global (ambient) color grade."""

    grade: ColorGrade

    name: ClassVar[str] = "color_grade"
    reads: ClassVar[frozenset[str]] = frozenset()
    writes: ClassVar[frozenset[str]] = frozenset({"color"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(update={"color": self.grade})


@dataclass(frozen=True)
class BeatColorGradeSkill:
    """Override the color grade for a single beat's time window.

    Reads ``beats`` (written by ``BeatSegmenterSkill``) and appends one
    ``GradeSpan`` scoped to the first beat matching ``beat_role``. If no
    such beat exists the Timeline is returned unchanged (graceful no-op) so
    a missing beat never crashes the pipeline.

    Multiple ``BeatColorGradeSkill`` instances for different roles compose
    cleanly — the coherence layer will reject overlapping spans.
    """

    beat_role: str
    grade: ColorGrade

    name: ClassVar[str] = "beat_color_grade"
    reads: ClassVar[frozenset[str]] = frozenset({"beats"})
    writes: ClassVar[frozenset[str]] = frozenset({"grade_spans"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        beat = next((b for b in tl.beats if b.role == self.beat_role), None)
        if beat is None:
            return tl
        new_span = GradeSpan(
            grade=self.grade,
            start_ms=beat.start_ms,
            end_ms=beat.end_ms,
            beat_id=beat.id,
        )
        return tl.model_copy(update={"grade_spans": tl.grade_spans + [new_span]})
