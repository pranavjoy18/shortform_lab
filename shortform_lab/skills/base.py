"""The ``Skill`` protocol and the ``Context`` skills read from.

A skill is a pure-with-respect-to-media transform on a ``Timeline``: it declares
what it ``reads`` and ``writes`` (so an orchestrator can topologically order
skills and detect missing preconditions) and returns a new ``Timeline``. Skills
never call FFmpeg — that stays the renderer's sole job. The heavy deterministic
computation lives in plain functions (e.g. ``tighten.compute_keep_ranges``); a
skill is the thin adapter that applies it to the Timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..models import StyleConfig, TimeRange, Transcript
from ..timeline import Timeline


@dataclass(frozen=True)
class Context:
    """Read-only inputs a skill may consult beyond the Timeline itself.

    Holds the artifacts skills derive edits from (the transcript, the active
    style). It grows as skills need more; keeping it a single object means adding
    an input does not change every skill signature.
    """

    transcript: Transcript
    style: StyleConfig


@runtime_checkable
class Skill(Protocol):
    """A composable edit on the Timeline.

    ``reads``/``writes`` name Timeline parts (e.g. ``"transcript"``, ``"spine"``,
    ``"captions"``) for ordering and precondition checks. ``apply`` returns a new
    Timeline; an optional ``span`` scopes the edit (unused while subagents are out
    of scope, but kept so the signature is stable).
    """

    name: str
    reads: frozenset[str]
    writes: frozenset[str]

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        ...
