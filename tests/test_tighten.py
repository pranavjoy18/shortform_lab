"""Tests for silence/pause compression: cut-list computation and time remapping."""

from shortform_lab.models import TimeRange, Transcript, TranscriptSegment, WordTiming
from shortform_lab.tighten import compute_keep_ranges, remap_ms, tighten_transcript


def _seg(start, end, text="word", words=None):
    return TranscriptSegment(start_ms=start, end_ms=end, text=text, words=words or [])


def test_compute_keep_ranges_trims_long_gap():
    t = Transcript(segments=[_seg(0, 1000), _seg(3000, 4000)])
    ranges = compute_keep_ranges(t, max_silence_ms=350, pad_ms=100, source_duration_ms=4000)
    assert [(r.start_ms, r.end_ms) for r in ranges] == [(0, 1100), (2900, 4000)]


def test_compute_keep_ranges_merges_short_gap_and_trims_trailing():
    # 200ms gap (< max_silence) merges into one phrase; trailing silence dropped.
    t = Transcript(segments=[_seg(0, 1000), _seg(1200, 2000)])
    ranges = compute_keep_ranges(t, max_silence_ms=350, pad_ms=100, source_duration_ms=5000)
    assert [(r.start_ms, r.end_ms) for r in ranges] == [(0, 2100)]


def test_compute_keep_ranges_empty_when_nothing_to_cut():
    t = Transcript(segments=[_seg(0, 5000)])
    assert compute_keep_ranges(t, max_silence_ms=350, pad_ms=100, source_duration_ms=5000) == []


def test_compute_keep_ranges_uses_word_spans_when_present():
    words = [WordTiming(start_ms=3000, end_ms=3500, text="hi"),
             WordTiming(start_ms=3500, end_ms=4000, text="there")]
    t = Transcript(segments=[_seg(0, 1000), _seg(3000, 4000, words=words)])
    ranges = compute_keep_ranges(t, max_silence_ms=350, pad_ms=0, source_duration_ms=4000)
    assert [(r.start_ms, r.end_ms) for r in ranges] == [(0, 1000), (3000, 4000)]


def test_remap_ms_accumulates_and_detects_cuts():
    ranges = [TimeRange(start_ms=0, end_ms=1000), TimeRange(start_ms=3000, end_ms=4000)]
    assert remap_ms(0, ranges) == 0
    assert remap_ms(500, ranges) == 500
    assert remap_ms(1000, ranges) == 1000
    assert remap_ms(2000, ranges) is None       # inside the cut gap
    assert remap_ms(3000, ranges) == 1000        # second range continues from 1000
    assert remap_ms(3500, ranges) == 1500
    assert remap_ms(5000, ranges) is None        # past the last kept range


def test_tighten_transcript_remaps_to_output_time():
    words = [WordTiming(start_ms=3000, end_ms=3500, text="hi"),
             WordTiming(start_ms=3500, end_ms=4000, text="there")]
    t = Transcript(segments=[_seg(0, 1000, "first"), _seg(3000, 4000, "second", words)])
    ranges = compute_keep_ranges(t, max_silence_ms=350, pad_ms=0, source_duration_ms=4000)
    tt = tighten_transcript(t, ranges)

    assert (tt.segments[0].start_ms, tt.segments[0].end_ms) == (0, 1000)
    # The 2s gap is gone: the second segment now follows immediately.
    assert (tt.segments[1].start_ms, tt.segments[1].end_ms) == (1000, 2000)
    assert [w.text for w in tt.segments[1].words] == ["hi", "there"]
    assert tt.segments[1].words[0].start_ms == 1000
    assert tt.segments[1].words[1].end_ms == 2000


def test_tighten_transcript_identity_without_ranges():
    t = Transcript(segments=[_seg(0, 1000)])
    assert tighten_transcript(t, []) is t
