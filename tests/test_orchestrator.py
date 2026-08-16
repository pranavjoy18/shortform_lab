"""Tests for the deterministic orchestrator and the Timeline -> EditPlan adapter.

These lock the milestone-1b seam: skills compose onto a Timeline, the adapter
projects it to the EditPlan the renderer consumes, and feature gating leaves
disabled features absent from the Timeline (not merely skipped at render).
"""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.models import Transcript, TranscriptSegment
from shortform_lab.orchestrator import DeterministicOrchestrator
from shortform_lab.timeline import MediaRef, timeline_to_editplan
from shortform_lab.transcribe import load_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"


def _transcript():
    return load_transcript(FIXTURE)


def _source(duration_ms):
    return MediaRef(id="spine", path="source.mp4", kind="video",
                    duration_ms=duration_ms, has_audio=True)


def test_orchestrator_builds_timeline_and_adapts_to_editplan():
    style = load_style_config("bold_creator")
    style.hook.enabled = True
    style.visuals.overlays_enabled = True
    t = _transcript()

    # debug=True: the deterministic hook is purely extractive and only renders
    # under --debug (real hook copy comes from the generative path); exercising
    # it here specifically to test that gate + the EditPlan adapter.
    tl = DeterministicOrchestrator().plan_timeline(
        t, style, _source(t.duration_ms), tighten=False, debug=True
    )

    assert tl.hook is not None
    assert len(tl.captions) == 5
    assert tl.overlays
    assert "reason" in tl.meta
    assert tl.spine_keep_ranges() == []  # not tightened

    # The adapter faithfully projects the tracks onto the EditPlan.
    plan = timeline_to_editplan(tl)
    assert plan.hook.text == tl.hook.text
    assert len(plan.captions) == len(tl.captions)
    assert plan.overlays == tl.overlays
    assert plan.keep_ranges == []
    assert plan.reason == tl.meta["reason"]
    assert plan.export_layout == style.export.layout


def test_orchestrator_gating_omits_disabled_features():
    style = load_style_config("bold_creator")  # hook + overlays off by default
    tl = DeterministicOrchestrator().plan_timeline(
        _transcript(), style, _source(_transcript().duration_ms), tighten=False
    )
    assert tl.hook is None
    assert tl.overlays == []
    assert tl.punch_ins  # unaffected


def test_orchestrator_tightens_spine_and_remaps_floating_tracks():
    style = load_style_config("bold_creator")
    style.tighten.enabled = True
    t = Transcript(segments=[
        TranscriptSegment(start_ms=0, end_ms=1000, text="Hello there friend."),
        TranscriptSegment(start_ms=4000, end_ms=5000, text="Welcome back everyone."),
    ])

    tl = DeterministicOrchestrator().plan_timeline(t, style, _source(5000), tighten=True)

    # Spine was cut: at least two clips, and the projection feeds keep_ranges.
    assert len(tl.spine) >= 2
    assert tl.spine_keep_ranges()
    # Floating captions were remapped onto the tightened timeline.
    assert tl.captions[0].start_ms == 0
    assert tl.captions[-1].start_ms < 4000

    plan = timeline_to_editplan(tl)
    assert plan.keep_ranges == tl.spine_keep_ranges()
