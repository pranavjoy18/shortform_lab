"""Tests for filler-word removal (a capability of the tightening skill)."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.models import Transcript, TranscriptSegment, WordTiming
from shortform_lab.orchestrator import DeterministicOrchestrator
from shortform_lab.tighten import compute_keep_ranges, tighten_transcript
from shortform_lab.timeline import MediaRef


def _w(start, end, text):
    return WordTiming(start_ms=start, end_ms=end, text=text)


def _seg_with_um():
    # "I think um we should go" — 'um' is a filler word spanning 1000-1400.
    words = [
        _w(0, 400, "I"), _w(400, 800, "think"), _w(1000, 1400, "um"),
        _w(1600, 2000, "we"), _w(2000, 2400, "should"), _w(2400, 2800, "go"),
    ]
    return TranscriptSegment(start_ms=0, end_ms=2800, text="I think um we should go", words=words)


def _transcript():
    return Transcript(segments=[_seg_with_um()])


# --------------------------------------------------------------------------- #
# Cut-list level
# --------------------------------------------------------------------------- #
def test_no_filler_removal_keeps_um_span():
    # Without filler removal and no long silence, nothing is cut.
    ranges = compute_keep_ranges(_transcript(), max_silence_ms=350, pad_ms=0,
                                 source_duration_ms=2800, remove_fillers=False)
    assert ranges == []  # identity / uncut


def test_filler_removal_cuts_the_um_span():
    ranges = compute_keep_ranges(_transcript(), max_silence_ms=350, pad_ms=0,
                                 source_duration_ms=2800, remove_fillers=True)
    # The 1000-1400 'um' is carved out, splitting the clip in two.
    assert [(r.start_ms, r.end_ms) for r in ranges] == [(0, 1000), (1400, 2800)]


def test_filler_removal_is_noop_without_word_timings():
    seg = TranscriptSegment(start_ms=0, end_ms=2800, text="I think um we should go")  # no words
    ranges = compute_keep_ranges(Transcript(segments=[seg]), max_silence_ms=350, pad_ms=0,
                                 source_duration_ms=2800, remove_fillers=True)
    assert ranges == []  # can't locate fillers without word timings


# --------------------------------------------------------------------------- #
# Transcript level: the filler word disappears from captions
# --------------------------------------------------------------------------- #
def test_tighten_transcript_drops_the_filler_word():
    ranges = compute_keep_ranges(_transcript(), max_silence_ms=350, pad_ms=0,
                                 source_duration_ms=2800, remove_fillers=True)
    tt = tighten_transcript(_transcript(), ranges)
    texts = [w.text for w in tt.segments[0].words]
    assert "um" not in texts
    assert texts == ["I", "think", "we", "should", "go"]
    # Surviving words are remapped to the tightened timeline (the 'um' span is gone).
    assert tt.segments[0].words[2].text == "we"
    # kept[0:1000] (1000ms) then [1400:2800]; 'we' at source 1600 -> 1000 + (1600-1400) = 1200.
    assert tt.segments[0].words[2].start_ms == 1200
    # Segment text was rebuilt from survivors (filler scrubbed).
    assert "um" not in tt.segments[0].text


# --------------------------------------------------------------------------- #
# End to end through the orchestrator
# --------------------------------------------------------------------------- #
def test_orchestrator_filler_removal_via_style():
    style = load_style_config("word_pop")
    style.tighten.enabled = True
    style.tighten.remove_fillers = True
    source = MediaRef(id="spine", path="s.mp4", kind="video", duration_ms=2800, has_audio=True)

    tl = DeterministicOrchestrator().plan_timeline(_transcript(), style, source)
    assert tl.spine_keep_ranges()  # the um span was cut
    # No caption cue contains the filler word.
    assert all("um" not in (w.text for w in cue.words) for cue in tl.captions)
