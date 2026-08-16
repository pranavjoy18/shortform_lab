"""Presets: a composed skill pipeline derived from a style.

A **preset** is the milestone-3 generalization of "a style drives a fixed planner
sequence" (``docs/architecture.md``). It is a *set* of instantiated, gated skills;
the orchestrator topologically orders them from their reads/writes graph and runs
them. The order they are listed here is only the stable tiebreak among skills with
no dependency between them — it is not the execution contract.

Today the single preset is derived from a ``StyleConfig`` (feature flags + params),
so existing YAML styles keep working unchanged. This is the seam where an explicit
YAML skill list, or an LLM-chosen skill set, plugs in later.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import StyleConfig
from .skills.base import Skill
from .skills.beat import BeatSegmenterSkill
from .skills.caption import CaptionSkill
from .skills.color import ColorGradeSkill, build_color_grade
from .skills.hook import HookSkill
from .skills.lower_third import LowerThirdSkill
from .skills.overlay import OverlaySkill
from .skills.punchin import PunchInSkill
from .skills.tighten import TightenSilenceSkill
from .skills.transition import TransitionSkill


@dataclass(frozen=True)
class Preset:
    """A named, gated set of skills to compose onto a Timeline."""

    name: str
    skills: tuple[Skill, ...]


def preset_from_style(style: StyleConfig, *, tighten: bool = True, debug: bool = False) -> Preset:
    """Build the deterministic preset for a style.

    Feature gating happens here (not in the renderer): a disabled feature's skill
    is simply omitted, so it is absent from the resulting Timeline. ``tighten`` can
    force silence compression off regardless of the style (used by the legacy
    ``DeterministicPlanner`` path, which tightens elsewhere).

    The hook is additionally gated on ``debug``: the deterministic hook is purely
    extractive (opening line, word-capped — see ``skills/hook.py``), never a real
    written hook, so it never ships to real users. It renders only under
    ``--debug``, for inspecting the extractive fallback itself. The generative
    (LLM/agentic) path is unaffected — it writes real hook copy and is gated by
    the LLM's own skill choice, not this flag.
    """
    skills: list[Skill] = []
    if tighten and style.tighten.enabled:
        skills.append(
            TightenSilenceSkill(
                max_silence_ms=style.tighten.max_silence_ms,
                pad_ms=style.tighten.pad_ms,
                remove_fillers=style.tighten.remove_fillers,
            )
        )
    # Phase 0: always detect beats so decoration skills have anchors available.
    skills.append(BeatSegmenterSkill())
    if style.hook.enabled and debug:
        skills.append(HookSkill())
    skills.append(CaptionSkill())
    if style.visuals.overlays_enabled:
        skills.append(OverlaySkill())
    skills.append(PunchInSkill())
    if style.color.look is not None:
        skills.append(ColorGradeSkill(build_color_grade(look=style.color.look)))
    if style.transitions.enabled:
        skills.append(
            TransitionSkill(
                transition_type=style.transitions.transition_type,
                duration_ms=style.transitions.duration_ms,
            )
        )
    if style.visuals.lower_third_enabled and style.visuals.lower_third_text:
        skills.append(LowerThirdSkill())
    return Preset(name=style.name, skills=tuple(skills))
