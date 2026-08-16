"""The toolbox: the curated set of skills an LLM orchestrator may use.

A toolbox is the agent's function-calling surface (``docs/architecture.md`` §3):
each entry pairs a skill with a strict params schema and a human description. The
LLM returns a :class:`SkillPlan` — *which* skills to run and *what* params — never
timestamps and never the export settings (those stay deterministic). The plan is
validated against these schemas before any skill is built, so a malformed model
response is rejected and the orchestrator falls back deterministically.

This is the one place that maps an LLM's structured choice onto real skills; the
deterministic preset (`presets.py`) is the no-LLM analogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, Field

from .models import StyleConfig
from .skills.base import Skill
from .skills.beat import BeatSegmenterSkill
from .skills.caption import CaptionSkill
from .skills.color import LOOKS, BeatColorGradeSkill, ColorGradeSkill, build_color_grade
from .skills.hook import HookSkill
from .skills.lower_third import LowerThirdSkill
from .skills.overlay import OverlaySkill
from .skills.punchin import PunchInSkill
from .skills.tighten import TightenSilenceSkill
from .skills.transition import TransitionSkill


# --------------------------------------------------------------------------- #
# Per-skill parameter schemas (what the LLM may set)
# --------------------------------------------------------------------------- #
class TightenParams(BaseModel):
    max_silence_ms: int = Field(default=350, gt=0, description="Longest silent gap to keep, in ms.")
    pad_ms: int = Field(default=100, ge=0, description="Breathing room kept around each phrase, in ms.")
    remove_fillers: bool = Field(default=False, description="Also cut filler words like 'um'/'uh' (needs word timings).")


class HookParams(BaseModel):
    text: str | None = Field(
        default=None, max_length=80,
        description=(
            "The hook copy itself, written by you — a short, punchy tease for the "
            "first ~2-3 seconds. Do NOT copy the transcript's opening sentence "
            "verbatim: the caption track will already show that sentence at the "
            "same time, so a hook that repeats it is dead screen space, not a hook. "
            "Write a distinct line that creates curiosity about where the clip is "
            "going (a question, a stat, a contrarian claim) — 4-8 words."
        ),
    )
    max_words: int | None = Field(default=None, gt=0, description="Word cap for the hook overlay.")


class CaptionParams(BaseModel):
    # Caption text/timing is never the LLM's call — always the transcript. This is
    # the one exception: which (if any) already-correct words get accent-colored
    # in "active_word" rendering. Deterministically this is always empty (no
    # emphasis) — it exists so the generative path can be selective instead of
    # mechanically highlighting every single word as it's spoken.
    emphasize: list[str] | None = Field(
        default=None,
        description=(
            "Exact words/short phrases (verbatim substrings of the transcript) to "
            "accent-highlight as they're spoken — only for styles whose "
            "word_animation is 'active_word'; ignored otherwise. Be selective: "
            "highlighting every word defeats the point. Only flag words that are "
            "genuinely load-bearing (a number, a name, the punchline) — most "
            "clips need few or none. Omit or leave empty for no emphasis at all, "
            "which is the right choice more often than not."
        ),
    )


class OverlayParams(BaseModel):
    count: int | None = Field(default=None, ge=0, description="Number of quote/text cards.")


class PunchInParams(BaseModel):
    count: int | None = Field(default=None, ge=0, description="Number of punch-in zoom moments.")


class ColorParams(BaseModel):
    look: str | None = Field(
        default=None, description=f"Named grade preset, one of: {sorted(LOOKS)}."
    )
    brightness: float | None = Field(default=None, ge=-1.0, le=1.0)
    contrast: float | None = Field(default=None, ge=0.0, le=3.0)
    saturation: float | None = Field(default=None, ge=0.0, le=3.0)
    gamma: float | None = Field(default=None, ge=0.1, le=10.0)


class BeatColorParams(BaseModel):
    beat_role: str = Field(
        description="Which beat to grade. One of: hook, setup, payoff, cta."
    )
    look: str | None = Field(
        default=None, description=f"Named grade preset, one of: {sorted(LOOKS)}."
    )
    brightness: float | None = Field(default=None, ge=-1.0, le=1.0)
    contrast: float | None = Field(default=None, ge=0.0, le=3.0)
    saturation: float | None = Field(default=None, ge=0.0, le=3.0)
    gamma: float | None = Field(default=None, ge=0.1, le=10.0)


_TRANSITION_TYPES = ["fade", "dissolve", "wipeleft", "wiperight", "fadeblack", "fadewhite", "pixelize", "radial"]


class TransitionParams(BaseModel):
    transition_type: str = Field(
        default="fade",
        description=f"FFmpeg xfade transition type. One of: {_TRANSITION_TYPES}.",
    )
    duration_ms: int = Field(
        default=300, gt=0,
        description="Cross-fade window in ms. Both adjacent clips must be longer than this.",
    )


class LowerThirdParams(BaseModel):
    text: str = Field(description="Primary name/title line shown in the lower-left corner.")
    subtext: str | None = Field(default=None, description="Optional smaller line below (e.g. handle or role).")


@dataclass(frozen=True)
class ToolSpec:
    params_model: type[BaseModel]
    factory: Callable[[BaseModel, StyleConfig], Skill]
    description: str


TOOLBOX: dict[str, ToolSpec] = {
    "tighten_silence": ToolSpec(
        TightenParams,
        lambda p, style: TightenSilenceSkill(
            max_silence_ms=p.max_silence_ms, pad_ms=p.pad_ms, remove_fillers=p.remove_fillers
        ),
        "Remove silent pauses / dead air (and optionally filler words) by cutting the timeline.",
    ),
    "hook": ToolSpec(
        HookParams,
        lambda p, style: HookSkill(max_words=p.max_words, text=p.text),
        "Add a punchy opening text overlay you write yourself — a curiosity tease, "
        "not a repeat of the transcript (captions already cover that).",
    ),
    "add_captions": ToolSpec(
        CaptionParams,
        lambda p, style: CaptionSkill(emphasize=tuple(p.emphasize or ())),
        "Burn in captions covering the speech (recommended on almost every short). "
        "Optionally flag a few words for accent-highlight emphasis — sparingly.",
    ),
    "overlay": ToolSpec(
        OverlayParams,
        lambda p, style: OverlaySkill(count=p.count),
        "Add quote/text cards on the strongest lines.",
    ),
    "punch_in": ToolSpec(
        PunchInParams,
        lambda p, style: PunchInSkill(count=p.count),
        "Add punch-in zoom moments for visual rhythm.",
    ),
    "color_grade": ToolSpec(
        ColorParams,
        lambda p, style: ColorGradeSkill(
            build_color_grade(
                look=p.look,
                brightness=p.brightness,
                contrast=p.contrast,
                saturation=p.saturation,
                gamma=p.gamma,
            )
        ),
        "Apply a global (ambient) color grade across the whole clip.",
    ),
    "beat_color_grade": ToolSpec(
        BeatColorParams,
        lambda p, style: BeatColorGradeSkill(
            beat_role=p.beat_role,
            grade=build_color_grade(
                look=p.look,
                brightness=p.brightness,
                contrast=p.contrast,
                saturation=p.saturation,
                gamma=p.gamma,
            ),
        ),
        (
            "Override the color grade for a specific beat (hook/setup/payoff/cta). "
            "Use with color_grade to set an ambient base and override individual beats. "
            "Multiple beat_color_grade entries for different roles compose cleanly."
        ),
    ),
    "add_transitions": ToolSpec(
        TransitionParams,
        lambda p, style: TransitionSkill(
            transition_type=p.transition_type,
            duration_ms=p.duration_ms,
        ),
        (
            "Add visual transitions (fade, dissolve, wipe…) at every cut point in the timeline. "
            "Only effective when the timeline has been tightened (cuts exist). "
            "Use 'fade' for a clean look, 'dissolve' for cinematic feel, 'wipeleft' for energy."
        ),
    ),
    "lower_third": ToolSpec(
        LowerThirdParams,
        lambda p, style: LowerThirdSkill(text=p.text, subtext=p.subtext),
        (
            "Add a broadcast-style name/title banner in the lower-left corner of the frame. "
            "Use for creator name, handle, or role. Appears at the first non-hook beat."
        ),
    ),
}


# --------------------------------------------------------------------------- #
# The LLM's structured output
# --------------------------------------------------------------------------- #
class SkillChoice(BaseModel):
    name: str
    params: dict = Field(default_factory=dict)


class SkillPlan(BaseModel):
    skills: list[SkillChoice]
    reason: str | None = None


def build_skill(name: str, params: dict, style: StyleConfig) -> Skill:
    """Validate ``params`` against the named tool's schema and build the skill.

    Raises ``ValueError`` for an unknown skill and ``pydantic.ValidationError`` for
    bad params — both caught by the orchestrator, which then falls back.
    """
    spec = TOOLBOX.get(name)
    if spec is None:
        raise ValueError(f"unknown skill {name!r}; valid skills: {sorted(TOOLBOX)}")
    validated = spec.params_model.model_validate(params or {})
    return spec.factory(validated, style)


def skills_from_plan(plan: SkillPlan, style: StyleConfig) -> list[Skill]:
    """Turn a validated :class:`SkillPlan` into concrete skills."""
    return [build_skill(choice.name, choice.params, style) for choice in plan.skills]


def toolbox_catalog() -> list[dict]:
    """A JSON-serializable description of the toolbox for the LLM prompt."""
    catalog: list[dict] = []
    for name, spec in TOOLBOX.items():
        props = spec.params_model.model_json_schema().get("properties", {})
        catalog.append({"name": name, "description": spec.description, "params": props})
    return catalog
