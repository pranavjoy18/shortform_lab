"""Tests for Batch 1+2+3: fonts, animation, transitions, lower-third."""

from __future__ import annotations

from pathlib import Path

import pytest

from shortform_lab.coherence import check_timeline
from shortform_lab.config import load_style_config
from shortform_lab.models import (
    CaptionCue,
    EditPlan,
    LowerThird,
    TimeRange,
    TransitionInfo,
)
from shortform_lab.render import (
    _animation_prefix,
    build_concat_filtergraph,
    write_captions_ass,
)
from shortform_lab.skills.lower_third import LowerThirdSkill, build_lower_third
from shortform_lab.skills.transition import TransitionSkill, build_transitions
from shortform_lab.timeline import timeline_from_spine, timeline_to_editplan
from shortform_lab.toolbox import build_skill

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_media_ref():
    from shortform_lab.timeline import MediaRef
    return MediaRef(id="v", path="source.mp4", duration_ms=10_000)


def _make_spine_tl(n_clips: int = 3, clip_ms: int = 3000):
    """Build a Timeline with n_clips equal-length spine clips."""
    from shortform_lab.models import ExportSettings
    from shortform_lab.timeline import Clip, Timeline
    src = _make_media_ref()
    spine = [
        Clip(source_id="v", source_in_ms=i * clip_ms, duration_ms=clip_ms)
        for i in range(n_clips)
    ]
    export = ExportSettings(width=1080, height=1920, fps=30)
    return Timeline(sources=[src], spine_source_id="v", spine=spine, export=export)


def _make_context(style_name="bold_creator"):
    from shortform_lab.models import Transcript, TranscriptSegment
    from shortform_lab.skills.base import Context
    style = load_style_config(style_name)
    transcript = Transcript(segments=[
        TranscriptSegment(start_ms=0, end_ms=5000, text="Hello world"),
        TranscriptSegment(start_ms=6000, end_ms=9000, text="This is a test"),
    ])
    return Context(transcript=transcript, style=style)


# --------------------------------------------------------------------------- #
# 1. Font + animation_in
# --------------------------------------------------------------------------- #

def test_viral_creator_style_loads():
    style = load_style_config("viral_creator")
    assert style.captions.font_family == "Anton"
    assert style.captions.animation_in == "fly_up"
    assert style.transitions.enabled is True
    assert style.transitions.transition_type == "fade"


def test_ass_uses_style_font(tmp_path: Path):
    style = load_style_config("viral_creator")
    plan = EditPlan(
        source_video="s.mp4",
        captions=[CaptionCue(start_ms=0, end_ms=2000, text="Hello")],
        export_width=1080, export_height=1920, export_fps=30,
    )
    content = write_captions_ass(plan, style, tmp_path / "c.ass").read_text()
    assert "Anton" in content
    assert "FreeSans" not in content


def test_ass_default_font_when_not_set(tmp_path: Path):
    # A style whose CaptionSettings never sets font_family falls back to the
    # Pydantic default (a bundled font, not the old system "FreeSans").
    from shortform_lab.models import CaptionSettings, ExportSettings, HookSettings, StyleConfig, VisualSettings

    style = StyleConfig(
        name="no_font_set",
        export=ExportSettings(width=1080, height=1920, fps=30),
        hook=HookSettings(duration_ms=2000, max_words=6),
        captions=CaptionSettings(font_size=54, max_chars_per_line=28),
        visuals=VisualSettings(punch_in_count=0, text_card_count=0),
    )
    plan = EditPlan(
        source_video="s.mp4",
        captions=[CaptionCue(start_ms=0, end_ms=1000, text="Test")],
        export_width=1080, export_height=1920, export_fps=30,
    )
    content = write_captions_ass(plan, style, tmp_path / "c.ass").read_text()
    assert "Poppins ExtraBold" in content
    assert "FreeSans" not in content


def test_animation_prefix_none():
    assert _animation_prefix("none", 1080, 1920, 2, 160) == ""


def test_animation_prefix_fade():
    prefix = _animation_prefix("fade", 1080, 1920, 2, 160)
    assert r"\fad(200,100)" in prefix


def test_animation_prefix_fly_up_bottom():
    prefix = _animation_prefix("fly_up", 1080, 1920, 2, 160)
    assert r"\move(" in prefix
    # y_end for bottom align (an2): h - margin_v = 1920 - 160 = 1760
    # y_start: 1760 + 40 = 1800
    assert "1760" in prefix and "1800" in prefix


def test_animation_prefix_fly_up_top():
    prefix = _animation_prefix("fly_up", 1080, 1920, 8, 160)
    # y_end for top align (an8): margin_v = 160
    # y_start: 160 - 40 = 120
    assert "160" in prefix and "120" in prefix


def test_ass_fade_animation_in_captions(tmp_path: Path):
    style = load_style_config("bold_creator")
    style.captions.animation_in = "fade"
    plan = EditPlan(
        source_video="s.mp4",
        captions=[CaptionCue(start_ms=0, end_ms=2000, text="Test caption")],
        export_width=1080, export_height=1920, export_fps=30,
    )
    content = write_captions_ass(plan, style, tmp_path / "c.ass").read_text()
    assert r"\fad(200,100)" in content


# --------------------------------------------------------------------------- #
# 2. Transitions
# --------------------------------------------------------------------------- #

def test_build_transitions_single_clip():
    tl = _make_spine_tl(n_clips=1)
    result = build_transitions(tl)
    assert result == []


def test_build_transitions_three_clips():
    tl = _make_spine_tl(n_clips=3)
    result = build_transitions(tl, transition_type="dissolve", duration_ms=400)
    assert len(result) == 2
    assert all(t.transition_type == "dissolve" for t in result)
    assert all(t.duration_ms == 400 for t in result)


def test_transition_skill_writes_transitions():
    tl = _make_spine_tl(n_clips=3)
    ctx = _make_context()
    skill = TransitionSkill(transition_type="fade", duration_ms=300)
    out = skill.apply(tl, ctx)
    assert len(out.transitions) == 2
    assert out.transitions[0].transition_type == "fade"


def test_transition_skill_noop_on_single_clip():
    tl = _make_spine_tl(n_clips=1)
    ctx = _make_context()
    skill = TransitionSkill()
    out = skill.apply(tl, ctx)
    assert out.transitions == []


def test_timeline_to_editplan_includes_transitions():
    tl = _make_spine_tl(n_clips=2)
    tl = tl.model_copy(update={"transitions": [TransitionInfo(transition_type="fade")]})
    plan = timeline_to_editplan(tl)
    assert len(plan.transitions) == 1
    assert plan.transitions[0].transition_type == "fade"


def test_transitions_dropped_for_uncut_timeline():
    """Transitions on an uncut (single full-span spine) timeline must not reach EditPlan."""
    from shortform_lab.models import ExportSettings
    src = _make_media_ref()
    tl = timeline_from_spine(src, ExportSettings(width=1080, height=1920, fps=30))
    tl = tl.model_copy(update={"transitions": [TransitionInfo()]})
    plan = timeline_to_editplan(tl)
    assert plan.transitions == []


def test_xfade_filtergraph_uses_xfade_filter():
    plan = EditPlan(
        source_video="s.mp4",
        captions=[],
        keep_ranges=[TimeRange(start_ms=0, end_ms=2000), TimeRange(start_ms=3000, end_ms=5000)],
        transitions=[TransitionInfo(transition_type="dissolve", duration_ms=300)],
        export_width=1080, export_height=1920, export_fps=30,
    )
    graph, audio = build_concat_filtergraph(plan, "scale=1080:1920[vout2]",
                                            has_audio=False, normalize=False)
    assert "xfade=transition=dissolve" in graph
    assert "concat" not in graph


def test_xfade_filtergraph_offset_calculation():
    """First xfade offset = clip0_duration - transition_duration."""
    plan = EditPlan(
        source_video="s.mp4",
        captions=[],
        keep_ranges=[TimeRange(start_ms=0, end_ms=2000), TimeRange(start_ms=3000, end_ms=5000)],
        transitions=[TransitionInfo(transition_type="fade", duration_ms=300)],
        export_width=1080, export_height=1920, export_fps=30,
    )
    graph, _ = build_concat_filtergraph(plan, "scale=1[out]", has_audio=False, normalize=False)
    # clip0 = 2000ms = 2.0s, transition = 300ms = 0.3s → offset = 1.7
    assert "offset=1.700" in graph


def test_fallback_to_concat_when_transitions_mismatch():
    """If transitions count doesn't match N-1, fall back to hard concat."""
    plan = EditPlan(
        source_video="s.mp4",
        captions=[],
        keep_ranges=[
            TimeRange(start_ms=0, end_ms=1000),
            TimeRange(start_ms=2000, end_ms=3000),
            TimeRange(start_ms=4000, end_ms=5000),
        ],
        transitions=[TransitionInfo()],  # only 1 for 2 gaps → mismatch → concat
        export_width=1080, export_height=1920, export_fps=30,
    )
    graph, _ = build_concat_filtergraph(plan, "scale=1[out]", has_audio=False, normalize=False)
    assert "concat=n=3" in graph
    assert "xfade" not in graph


def test_coherence_rejects_transition_count_mismatch():
    tl = _make_spine_tl(n_clips=3)
    # 3 clips → 2 gaps, but only 1 transition → mismatch
    tl = tl.model_copy(update={"transitions": [TransitionInfo()]})
    violations = [v for v in check_timeline(tl) if v.track == "transitions"]
    assert any("count" in v.message for v in violations)


def test_coherence_rejects_transition_longer_than_clip():
    tl = _make_spine_tl(n_clips=2, clip_ms=500)
    # transition_duration > clip_ms
    tl = tl.model_copy(update={"transitions": [TransitionInfo(duration_ms=600)]})
    violations = [v for v in check_timeline(tl) if v.track == "transitions"]
    assert violations


# --------------------------------------------------------------------------- #
# 3. Lower third
# --------------------------------------------------------------------------- #

def test_lower_third_skill_places_banner():
    tl = _make_spine_tl()
    from shortform_lab.models import Beat
    tl = tl.model_copy(update={"beats": [
        Beat(id="b0", start_ms=0, end_ms=3000, role="hook"),
        Beat(id="b1", start_ms=3000, end_ms=7000, role="setup"),
    ]})
    ctx = _make_context()
    skill = LowerThirdSkill(text="Pranav | Engineer")
    out = skill.apply(tl, ctx)
    assert out.lower_third is not None
    assert out.lower_third.text == "Pranav | Engineer"
    # Should be placed at setup beat, not hook
    assert out.lower_third.start_ms == 3000


def test_lower_third_skill_noop_without_text():
    tl = _make_spine_tl()
    ctx = _make_context()
    skill = LowerThirdSkill()  # no text, no style config text
    out = skill.apply(tl, ctx)
    assert out.lower_third is None


def test_lower_third_from_style_config():
    tl = _make_spine_tl()
    ctx = _make_context()
    ctx.style.visuals.lower_third_text = "Ada Lovelace"
    ctx.style.visuals.lower_third_sub = "@ada"
    skill = LowerThirdSkill()
    out = skill.apply(tl, ctx)
    assert out.lower_third is not None
    assert out.lower_third.text == "Ada Lovelace"
    assert out.lower_third.subtext == "@ada"


def test_lower_third_renders_in_ass(tmp_path: Path):
    style = load_style_config("bold_creator")
    plan = EditPlan(
        source_video="s.mp4",
        captions=[],
        lower_third=LowerThird(text="Pranav", subtext="@pranav", start_ms=0, end_ms=3000),
        export_width=1080, export_height=1920, export_fps=30,
    )
    content = write_captions_ass(plan, style, tmp_path / "c.ass").read_text()
    assert "Pranav" in content
    assert "@pranav" in content
    assert "LowerThird" in content
    assert "LowerThirdSub" in content


def test_lower_third_absent_renders_nothing(tmp_path: Path):
    style = load_style_config("bold_creator")
    plan = EditPlan(
        source_video="s.mp4",
        captions=[CaptionCue(start_ms=0, end_ms=1000, text="Caption")],
        export_width=1080, export_height=1920, export_fps=30,
    )
    content = write_captions_ass(plan, style, tmp_path / "c.ass").read_text()
    assert "LowerThird" not in content.split("[Events]")[-1].strip() or \
           "LowerThird" in content.split("[V4+ Styles]")[-1].split("[Events]")[0]


def test_model_rejects_inverted_lower_third():
    with pytest.raises(Exception, match="precedes"):
        LowerThird(text="X", start_ms=5000, end_ms=3000)


def test_coherence_warns_lower_third_past_output():
    tl = _make_spine_tl(n_clips=1, clip_ms=2000)
    tl = tl.model_copy(update={"lower_third": LowerThird(text="X", start_ms=0, end_ms=9000)})
    violations = [v for v in check_timeline(tl) if v.track == "lower_third"]
    assert any(v.severity == "warning" for v in violations)


def test_editplan_carries_lower_third():
    tl = _make_spine_tl()
    tl = tl.model_copy(update={"lower_third": LowerThird(text="Me", start_ms=0, end_ms=2000)})
    plan = timeline_to_editplan(tl)
    assert plan.lower_third is not None
    assert plan.lower_third.text == "Me"


# --------------------------------------------------------------------------- #
# 4. Toolbox integration
# --------------------------------------------------------------------------- #

def test_toolbox_add_transitions_skill():
    style = load_style_config("bold_creator")
    skill = build_skill("add_transitions", {"transition_type": "dissolve", "duration_ms": 400}, style)
    assert isinstance(skill, TransitionSkill)
    assert skill.transition_type == "dissolve"
    assert skill.duration_ms == 400


def test_toolbox_lower_third_skill():
    style = load_style_config("bold_creator")
    skill = build_skill("lower_third", {"text": "Ada", "subtext": "@ada"}, style)
    assert isinstance(skill, LowerThirdSkill)
    assert skill.text == "Ada"
    assert skill.subtext == "@ada"
