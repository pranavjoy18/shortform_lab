"""Deterministic anchor resolution: string anchors → output-time ``TimeRange``s.

An anchor is a short declarative string that names a point or span on the
output Timeline. Anchors let the LLM (or a preset) express intent without
emitting timestamps; deterministic code resolves them here after the structural
phase (beat segmentation) has run.

Supported formats
-----------------
  ``beat:<role>``
      The full span of the first Beat whose role matches. Roles are the literals
      in ``BeatRole``: hook / setup / payoff / cta / bridge.

  ``boundary:<role_a>|<role_b>``
      A short (100 ms) window straddling the cut-point between the last beat of
      role ``<role_a>`` and the first beat of role ``<role_b>``. Useful for
      placing transitions exactly at beat boundaries.
"""

from __future__ import annotations

from .models import TimeRange
from .timeline import Timeline

_BOUNDARY_HALF_MS = 50  # half-width of the boundary window → 100 ms total


class AnchorError(ValueError):
    """Raised when an anchor string is malformed or has no match in the Timeline."""


def resolve_anchor(anchor: str, tl: Timeline) -> TimeRange:
    """Resolve *anchor* to an output-time ``TimeRange``.

    Raises :class:`AnchorError` for an unknown format or missing beat.
    """
    if anchor.startswith("beat:"):
        return _resolve_beat(anchor[len("beat:"):], tl)
    if anchor.startswith("boundary:"):
        return _resolve_boundary(anchor[len("boundary:"):], tl)
    raise AnchorError(
        f"Unknown anchor format {anchor!r}. "
        "Supported: 'beat:<role>', 'boundary:<role_a>|<role_b>'"
    )


def _resolve_beat(role: str, tl: Timeline) -> TimeRange:
    for beat in tl.beats:
        if beat.role == role:
            return TimeRange(start_ms=beat.start_ms, end_ms=beat.end_ms)
    raise AnchorError(
        f"No beat with role {role!r} in Timeline. "
        f"Available roles: {[b.role for b in tl.beats]}"
    )


def _resolve_boundary(spec: str, tl: Timeline) -> TimeRange:
    parts = spec.split("|", 1)
    if len(parts) != 2:
        raise AnchorError(
            f"Boundary anchor must be '<role_a>|<role_b>', got {spec!r}"
        )
    role_a, role_b = parts

    last_a = next((b for b in reversed(tl.beats) if b.role == role_a), None)
    first_b = next((b for b in tl.beats if b.role == role_b), None)
    if last_a is None:
        raise AnchorError(f"No beat with role {role_a!r} for boundary anchor")
    if first_b is None:
        raise AnchorError(f"No beat with role {role_b!r} for boundary anchor")

    mid = (last_a.end_ms + first_b.start_ms) // 2
    return TimeRange(
        start_ms=max(0, mid - _BOUNDARY_HALF_MS),
        end_ms=mid + _BOUNDARY_HALF_MS,
    )
