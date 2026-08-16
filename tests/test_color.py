"""Tests for color grading: the grade model, the skill, and the render filter."""

import pytest

from shortform_lab.config import load_style_config
from shortform_lab.models import ColorGrade, EditPlan, Transcript, TranscriptSegment
from shortform_lab.orchestrator import DeterministicOrchestrator
from shortform_lab.presets import preset_from_style
from shortform_lab.render import build_video_filter
from shortform_lab.skills.color import LOOKS, ColorGradeSkill, build_color_grade
from shortform_lab.timeline import MediaRef
from shortform_lab.toolbox import build_skill


def _plan(layout="fill"):
    return EditPlan(source_video="s.mp4", captions=[], export_width=1080,
                    export_height=1920, export_fps=30, export_layout=layout)


# --------------------------------------------------------------------------- #
# Model + builder
# --------------------------------------------------------------------------- #
def test_default_grade_is_identity():
    assert ColorGrade().is_identity()
    assert not ColorGrade(saturation=0.0).is_identity()


def test_build_color_grade_resolves_look_and_overrides():
    assert build_color_grade(look="mono").saturation == 0.0
    # Explicit param overrides the look's value.
    g = build_color_grade(look="vivid", saturation=2.0)
    assert g.saturation == 2.0 and g.contrast == LOOKS["vivid"].contrast


def test_build_color_grade_rejects_unknown_look():
    with pytest.raises(ValueError, match="unknown color look"):
        build_color_grade(look="instagram2014")


def test_grade_bounds_enforced_by_model():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ColorGrade(saturation=99)


# --------------------------------------------------------------------------- #
# Skill
# --------------------------------------------------------------------------- #
def test_color_skill_writes_color_and_reads_nothing():
    skill = ColorGradeSkill(build_color_grade(look="cinematic"))
    assert skill.reads == frozenset()
    assert skill.writes == frozenset({"color"})


# --------------------------------------------------------------------------- #
# Render filter
# --------------------------------------------------------------------------- #
def test_no_color_means_no_eq_stage():
    assert "eq=" not in build_video_filter(_plan(), "captions.ass")
    assert "eq=" not in build_video_filter(_plan(), "captions.ass", color=ColorGrade())  # identity


def test_color_prepends_eq_before_layout():
    vf = build_video_filter(_plan("letterbox"), "captions.ass", color=ColorGrade(saturation=0.0))
    # Grade is the first stage so the letterbox pad's black bars stay pure black.
    assert vf.startswith("eq=")
    assert vf.index("eq=") < vf.index("scale=")
    assert "saturation=0.0000" in vf


# --------------------------------------------------------------------------- #
# Wiring: preset, toolbox, orchestrator
# --------------------------------------------------------------------------- #
def test_preset_adds_color_skill_when_style_sets_look():
    style = load_style_config("bold_creator")
    style.color.look = None  # explicit "no grade" baseline, independent of this style's own default
    assert not any(s.name == "color_grade" for s in preset_from_style(style).skills)
    style.color.look = "vivid"
    assert any(s.name == "color_grade" for s in preset_from_style(style).skills)


def test_toolbox_builds_color_skill():
    skill = build_skill("color_grade", {"look": "punchy"}, load_style_config("bold_creator"))
    assert skill.name == "color_grade"
    assert skill.grade.saturation == LOOKS["punchy"].saturation


def test_orchestrator_sets_color_on_timeline():
    style = load_style_config("bold_creator")
    style.color.look = "cinematic"
    t = Transcript(segments=[TranscriptSegment(start_ms=0, end_ms=2000, text="hello there")])
    source = MediaRef(id="spine", path="s.mp4", kind="video", duration_ms=2000, has_audio=True)
    tl = DeterministicOrchestrator().plan_timeline(t, style, source)
    assert tl.color == LOOKS["cinematic"]
