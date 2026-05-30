"""Tests for the core data models."""

import pytest
from pydantic import ValidationError

from shortform_lab.models import (
    CaptionCue,
    EditPlan,
    Hook,
    PunchIn,
    TimeRange,
    Transcript,
    TranscriptSegment,
    VisualOverlay,
    WordTiming,
)


def test_transcript_helpers():
    t = Transcript(
        segments=[
            TranscriptSegment(start_ms=0, end_ms=1000, text="Hello there."),
            TranscriptSegment(start_ms=1000, end_ms=2500, text="This is a test."),
        ]
    )
    assert t.full_text == "Hello there. This is a test."
    assert t.duration_ms == 2500
    assert t.language == "en"


def test_segment_rejects_reversed_times():
    with pytest.raises(ValidationError):
        TranscriptSegment(start_ms=2000, end_ms=1000, text="bad")


def test_word_timing_rejects_reversed_times():
    WordTiming(start_ms=0, end_ms=400, text="hi")
    with pytest.raises(ValidationError):
        WordTiming(start_ms=400, end_ms=0, text="bad")


def test_segment_and_cue_carry_words():
    words = [WordTiming(start_ms=0, end_ms=400, text="Most")]
    seg = TranscriptSegment(start_ms=0, end_ms=400, text="Most", words=words)
    cue = CaptionCue(start_ms=0, end_ms=400, text="Most", words=words)
    assert seg.words[0].text == "Most"
    assert cue.words[0].text == "Most"
    # Defaults to empty (sentence mode) when omitted.
    assert CaptionCue(start_ms=0, end_ms=400, text="Most").words == []


def test_punch_in_zoom_bounds():
    PunchIn(start_ms=0, end_ms=1000, zoom=1.2)
    with pytest.raises(ValidationError):
        PunchIn(start_ms=0, end_ms=1000, zoom=1.0)  # must be > 1
    with pytest.raises(ValidationError):
        PunchIn(start_ms=0, end_ms=1000, zoom=5.0)  # over cap


def test_time_range_validation_and_duration():
    r = TimeRange(start_ms=1000, end_ms=2500)
    assert r.duration_ms == 1500
    with pytest.raises(ValidationError):
        TimeRange(start_ms=2000, end_ms=1000)


def test_edit_plan_keep_ranges_defaults_empty():
    plan = EditPlan(
        source_video="s.mp4",
        hook=Hook(text="hi", start_ms=0, end_ms=1000),
        captions=[CaptionCue(start_ms=0, end_ms=1000, text="hi")],
        export_width=1080, export_height=1920, export_fps=30,
    )
    assert plan.keep_ranges == []


def test_overlay_placement_validated():
    VisualOverlay(kind="text_card", start_ms=0, end_ms=1000, text="hi", placement="top")
    with pytest.raises(ValidationError):
        VisualOverlay(kind="text_card", start_ms=0, end_ms=1000, placement="diagonal")


def test_edit_plan_roundtrips_json():
    plan = EditPlan(
        source_video="source.mp4",
        hook=Hook(text="Watch this", start_ms=0, end_ms=2800),
        captions=[CaptionCue(start_ms=0, end_ms=1500, text="Hello world")],
        overlays=[VisualOverlay(kind="quote_card", start_ms=3000, end_ms=5000, text="Key idea")],
        punch_ins=[PunchIn(start_ms=4000, end_ms=6000, zoom=1.3)],
        export_width=1080,
        export_height=1920,
        export_fps=30,
        reason="Lead with the strongest line.",
    )
    dumped = plan.model_dump_json()
    restored = EditPlan.model_validate_json(dumped)
    assert restored == plan
