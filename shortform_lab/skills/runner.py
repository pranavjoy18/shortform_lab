"""Validated skill application: enforce reads/writes pre- and post-conditions.

A ``SkillRunner`` is the disciplined way to apply skills. It tracks which Timeline
parts have been *produced* so far and checks, for each skill:

- **precondition** — every name in ``skill.reads`` is already available (catches
  running a skill before its input exists; the basis for ordering skills from the
  reads/writes graph in later milestones).
- **postcondition** — the skill changed *only* the tracks it declared in
  ``skill.writes`` (a purity check: a captions skill must not quietly rewrite the
  spine). The declared writes then become available to downstream skills.

Timeline-level coherence (ordering, bounds, overlaps) is a separate pass; see
``coherence.validate_timeline``.
"""

from __future__ import annotations

from ..coherence import CoherenceError, Violation
from ..models import TimeRange
from ..timeline import Timeline
from .base import Context, Skill

# Timeline fields a skill may legitimately write, keyed as in reads/writes.
TRACK_KEYS = frozenset({
    "spine", "captions", "hook", "overlays", "punch_ins",
    "color", "beats", "grade_spans", "transitions", "lower_third",
})


class SkillRunner:
    """Apply skills with precondition/postcondition checks, tracking availability."""

    def __init__(self, available: set[str]):
        self.available = set(available)

    def run(self, skill: Skill, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        missing = set(skill.reads) - self.available
        if missing:
            raise CoherenceError([
                Violation("skill", f"{skill.name!r} reads unmet precondition(s): {sorted(missing)}")
            ])

        before = {k: getattr(tl, k) for k in TRACK_KEYS}
        out = skill.apply(tl, ctx, span=span)
        changed = {k for k in TRACK_KEYS if getattr(out, k) != before[k]}
        undeclared = changed - set(skill.writes)
        if undeclared:
            raise CoherenceError([
                Violation("skill", f"{skill.name!r} wrote undeclared track(s): {sorted(undeclared)}")
            ])

        self.available |= set(skill.writes)
        return out
