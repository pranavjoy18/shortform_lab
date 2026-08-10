"""Produce an inspectable ``EditPlan`` from a transcript and a style config.

Both planning paths now run through the **Timeline** substrate and the coherence
layer, then adapt to the ``EditPlan`` the renderer consumes:

- **deterministic** (`use_llm=False`): `DeterministicOrchestrator` composes the
  style's preset of skills.
- **LLM** (`use_llm=True`): `LLMOrchestrator` asks an LLM to pick skills from the
  toolbox, validated and coherence-gated, with the deterministic orchestrator as a
  guaranteed fallback.

`DeterministicPlanner` is kept as a thin `EditPlan`-returning shim (used directly by
tests). `build_captions` is re-exported from ``skills.caption`` for backward compat.
"""

from __future__ import annotations

from pathlib import Path

from .llm_orchestrator import AgenticOrchestrator, LLMOrchestrator
from .models import EditPlan, StyleConfig, Transcript
from .orchestrator import DeterministicOrchestrator
from .skills.caption import build_captions
from .timeline import MediaRef, Timeline, timeline_to_editplan

__all__ = ["DeterministicPlanner", "build_captions", "plan_edits", "plan_timeline"]

_SPINE_ID = "spine"


def _spine_source(source_video: str, duration_ms: int | None, transcript: Transcript) -> MediaRef:
    """The talking-head clip as the Timeline's spine source."""
    return MediaRef(
        id=_SPINE_ID,
        path=source_video,
        kind="video",
        duration_ms=duration_ms or transcript.duration_ms,
        has_audio=True,
    )


class DeterministicPlanner:
    """A no-API planner: compose the style's preset into a Timeline, adapt to an ``EditPlan``.

    Kept as a stable entry point for tests. It does *not* tighten — historically
    tightening happened in ``plan_edits``; the orchestrator owns it there now.
    """

    def plan(self, transcript: Transcript, style: StyleConfig, *, source_video: str) -> EditPlan:
        source = _spine_source(source_video, transcript.duration_ms, transcript)
        tl = DeterministicOrchestrator().plan_timeline(transcript, style, source, tighten=False)
        return timeline_to_editplan(tl)


def plan_timeline(
    transcript: Transcript,
    style: StyleConfig,
    *,
    source_video: str,
    use_llm: bool = False,
    interactive: bool = False,
    model: str = "gpt-4o-mini",
    error_path: Path | None = None,
    source_duration_ms: int | None = None,
) -> Timeline:
    """Build a coherence-validated ``Timeline`` via the deterministic or LLM orchestrator.

    ``use_llm=True, interactive=False`` → ``AgenticOrchestrator`` silent path
        (analyse + reflection loop, no user Q&A).
    ``use_llm=True, interactive=True`` → ``AgenticOrchestrator`` with CLI interview
        (Clarifier asks questions, user answers, then analyse + reflection).
    ``use_llm=False`` → ``DeterministicOrchestrator`` (no API calls).

    All LLM paths fall back to the deterministic orchestrator on any failure.
    """
    source = _spine_source(source_video, source_duration_ms, transcript)
    if use_llm:
        return AgenticOrchestrator(model=model).plan_timeline(
            transcript, style, source,
            interactive=interactive,
            error_path=error_path,
        )
    return DeterministicOrchestrator().plan_timeline(transcript, style, source, tighten=True)


def plan_edits(
    transcript: Transcript,
    style: StyleConfig,
    *,
    source_video: str,
    use_llm: bool = False,
    interactive: bool = False,
    model: str = "gpt-4o-mini",
    error_path: Path | None = None,
    source_duration_ms: int | None = None,
) -> EditPlan:
    """Plan edits and adapt the resulting Timeline to an ``EditPlan`` (back-compat)."""
    return timeline_to_editplan(
        plan_timeline(
            transcript,
            style,
            source_video=source_video,
            use_llm=use_llm,
            interactive=interactive,
            model=model,
            error_path=error_path,
            source_duration_ms=source_duration_ms,
        )
    )
