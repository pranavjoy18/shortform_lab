"""Composing a preset/skill-set into a finished, validated ``Timeline``.

``compose_timeline`` is the shared engine for both orchestrators (deterministic
and LLM): given a set of skills, it topologically orders them from the reads/writes
graph, runs each through a ``SkillRunner`` (pre/postcondition checks), remaps the
transcript to output time whenever a skill rewrites the spine, and gates the result
through the coherence layer before returning.

`DeterministicOrchestrator` is the no-LLM strategy: its skill set is a preset
derived from the style. The LLM strategy lives in ``llm_orchestrator.py`` and reuses
``compose_timeline`` — the only difference is *where the skill set comes from*.
"""

from __future__ import annotations

from collections.abc import Sequence

from .coherence import check_timeline, validate_timeline
from .models import StyleConfig, Transcript
from .presets import preset_from_style
from .skills.base import Context, Skill
from .skills.graph import topological_order
from .skills.runner import SkillRunner
from .tighten import tighten_transcript
from .timeline import MediaRef, Timeline, timeline_from_spine

# Resources available before any skill runs (see SkillRunner / the reads graph).
_INITIAL_AVAILABLE = frozenset({"transcript", "spine", "export", "source"})

_DEFAULT_REASON = (
    "Deterministic plan: hook drawn from the opening line, "
    "sentence-level captions from the transcript, "
    "{n_overlays} quote card(s) on the longest lines, and "
    "{n_punch_ins} punch-in(s) for visual rhythm."
)


def compose_timeline(
    skills: Sequence[Skill],
    transcript: Transcript,
    style: StyleConfig,
    source: MediaRef,
    *,
    reason: str | None = None,
) -> Timeline:
    """Topo-order and run ``skills`` onto a fresh Timeline, then validate it.

    ``reason`` (e.g. an LLM's explanation) is recorded in ``meta``; when ``None`` a
    deterministic summary is generated. Raises on an empty transcript, a cyclic
    skill graph, a skill contract violation, or a coherence error.
    """
    if not transcript.segments:
        raise ValueError("Cannot plan edits from an empty transcript")

    ordered = topological_order(skills)
    tl = timeline_from_spine(source, style.export)
    ctx = Context(transcript=transcript, style=style)
    runner = SkillRunner(available=set(_INITIAL_AVAILABLE))

    for skill in ordered:
        tl = runner.run(skill, tl, ctx)
        # A skill that rewrote the spine (the cut list) changed the timebase:
        # refresh the output-time transcript for the skills that read `spine`.
        if "spine" in skill.writes:
            ctx = Context(
                transcript=tighten_transcript(transcript, tl.spine_keep_ranges()),
                style=style,
            )

    meta: dict[str, object] = {
        **tl.meta,
        "reason": reason
        or _DEFAULT_REASON.format(n_overlays=len(tl.overlays), n_punch_ins=len(tl.punch_ins)),
    }
    warnings = [str(vio) for vio in check_timeline(tl) if vio.severity == "warning"]
    if warnings:
        meta["coherence_warnings"] = warnings
    tl = tl.model_copy(update={"meta": meta})

    # Gate: never return (and so never render) an incoherent Timeline.
    return validate_timeline(tl)


class DeterministicOrchestrator:
    """The no-LLM orchestrator: compose the style's preset onto a Timeline."""

    def plan_timeline(
        self,
        transcript: Transcript,
        style: StyleConfig,
        source: MediaRef,
        *,
        tighten: bool = True,
        debug: bool = False,
    ) -> Timeline:
        preset = preset_from_style(style, tighten=tighten, debug=debug)
        return compose_timeline(preset.skills, transcript, style, source)
