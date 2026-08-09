"""Tests for the coherence layer and the skill pre/postcondition runner."""

import pytest

from shortform_lab.coherence import CoherenceError, check_timeline, validate_timeline
from shortform_lab.models import (
    CaptionCue,
    ExportSettings,
    Hook,
    PunchIn,
    Transcript,
    TranscriptSegment,
    WordTiming,
)
from shortform_lab.skills.base import Context
from shortform_lab.skills.caption import CaptionSkill
from shortform_lab.skills.runner import SkillRunner
from shortform_lab.timeline import Clip, MediaRef, Timeline, timeline_from_spine


def _export():
    return ExportSettings(width=1080, height=1920, fps=30)


def _source(duration_ms=10000):
    return MediaRef(id="spine", path="source.mp4", kind="video",
                    duration_ms=duration_ms, has_audio=True)


def _tl(**tracks):
    return Timeline(sources=[_source()], spine_source_id="spine", export=_export(), **tracks)


def _style():
    from shortform_lab.config import load_style_config
    return load_style_config("bold_creator")


# --------------------------------------------------------------------------- #
# Timeline coherence
# --------------------------------------------------------------------------- #
def test_clean_timeline_has_no_violations():
    tl = timeline_from_spine(_source(10000), _export()).model_copy(update={
        "captions": [CaptionCue(start_ms=0, end_ms=1000, text="a"),
                     CaptionCue(start_ms=1000, end_ms=2000, text="b")],  # touching is fine
    })
    assert check_timeline(tl) == []
    assert validate_timeline(tl) is tl


def test_overlapping_captions_is_an_error():
    tl = _tl(captions=[
        CaptionCue(start_ms=0, end_ms=1500, text="a"),
        CaptionCue(start_ms=1000, end_ms=2000, text="b"),  # starts before 'a' ends
    ])
    with pytest.raises(CoherenceError, match="overlaps the previous cue"):
        validate_timeline(tl)


def test_inverted_caption_range_is_an_error():
    tl = _tl(captions=[CaptionCue(start_ms=2000, end_ms=1000, text="bad")])
    with pytest.raises(CoherenceError, match="start 2000 > end 1000"):
        validate_timeline(tl)


def test_word_outside_cue_bounds_is_an_error():
    tl = _tl(captions=[
        CaptionCue(start_ms=0, end_ms=1000, text="hi there", words=[
            WordTiming(start_ms=0, end_ms=500, text="hi"),
            WordTiming(start_ms=500, end_ms=2000, text="there"),  # past the cue end
        ]),
    ])
    with pytest.raises(CoherenceError, match="outside its cue bounds"):
        validate_timeline(tl)


def test_inverted_hook_and_punchin_are_errors():
    with pytest.raises(CoherenceError, match="hook"):
        validate_timeline(_tl(hook=Hook(text="x", start_ms=900, end_ms=100)))
    with pytest.raises(CoherenceError, match="punch-in"):
        validate_timeline(_tl(punch_ins=[PunchIn(start_ms=900, end_ms=100, zoom=1.2)]))


def test_captions_past_output_is_a_warning_not_an_error():
    # Output is the 10s spine; a 12s caption is incoherent but renderable.
    tl = _tl(captions=[CaptionCue(start_ms=0, end_ms=12000, text="too long")])
    violations = check_timeline(tl)
    assert any(v.severity == "warning" and v.track == "captions" for v in violations)
    assert not any(v.severity == "error" for v in violations)
    # validate_timeline only raises on errors, so this passes.
    assert validate_timeline(tl) is tl


# --------------------------------------------------------------------------- #
# Skill runner pre/postconditions
# --------------------------------------------------------------------------- #
def test_runner_rejects_unmet_precondition():
    tl = timeline_from_spine(_source(), _export())
    ctx = Context(transcript=Transcript(segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="hi")]),
                  style=_style())
    runner = SkillRunner(available={"spine"})  # 'transcript' deliberately absent
    with pytest.raises(CoherenceError, match="unmet precondition"):
        runner.run(CaptionSkill(), tl, ctx)


def test_runner_passes_when_precondition_met_and_tracks_writes():
    tl = timeline_from_spine(_source(), _export())
    ctx = Context(transcript=Transcript(segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="hi")]),
                  style=_style())
    runner = SkillRunner(available={"transcript", "spine"})
    out = runner.run(CaptionSkill(), tl, ctx)
    assert out.captions
    assert "captions" in runner.available


def test_runner_rejects_skill_that_writes_undeclared_track():
    """A skill must only change the tracks it declares in ``writes``."""
    from dataclasses import dataclass
    from typing import ClassVar
    from shortform_lab.models import TimeRange
    from shortform_lab.timeline import Timeline

    @dataclass(frozen=True)
    class RogueSkill:
        name: ClassVar[str] = "rogue"
        reads: ClassVar[frozenset] = frozenset()
        writes: ClassVar[frozenset] = frozenset({"captions"})  # claims captions...

        def apply(self, tl: Timeline, ctx, *, span: TimeRange | None = None) -> Timeline:
            # ...but actually rewrites the hook.
            return tl.model_copy(update={"hook": Hook(text="sneaky", start_ms=0, end_ms=500)})

    tl = timeline_from_spine(_source(), _export())
    ctx = Context(transcript=Transcript(segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="hi")]),
                  style=_style())
    with pytest.raises(CoherenceError, match="undeclared track"):
        SkillRunner(available=set()).run(RogueSkill(), tl, ctx)
