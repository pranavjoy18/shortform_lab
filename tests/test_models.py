"""Tests for the core data models."""

import pytest
from pydantic import ValidationError

from shortform_lab.models import (
    CaptionCue,
    EditPlan,
    Hook,
    PunchIn,
    Transcript,
    TranscriptSegment,
    VisualOverlay,
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


def test_punch_in_zoom_bounds():
    PunchIn(start_ms=0, end_ms=1000, zoom=1.2)
    with pytest.raises(ValidationError):
        PunchIn(start_ms=0, end_ms=1000, zoom=1.0)  # must be > 1
    with pytest.raises(ValidationError):
        PunchIn(start_ms=0, end_ms=1000, zoom=5.0)  # over cap


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
