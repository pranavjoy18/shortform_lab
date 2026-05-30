"""Silence/pause compression: turn a transcript into a cut list and remap time.

These are pure functions (no FFmpeg) so they are fully testable offline. The
pipeline calls them *before* planning: ``compute_keep_ranges`` decides which
source spans to keep, and ``tighten_transcript`` remaps the transcript onto the
tightened (output) timeline. The planner then builds the edit plan from that
tightened transcript, so every plan element comes out in output time while the
``keep_ranges`` (source spans) describe the cut for the renderer.

Only silence is removed here — no word or sentence is altered. Filler-word and
retake removal are future tiers that would add more entries to ``keep_ranges``.
"""

from __future__ import annotations

from .models import TimeRange, Transcript, TranscriptSegment, WordTiming


def compute_keep_ranges(
    transcript: Transcript,
    *,
    max_silence_ms: int,
    pad_ms: int,
    source_duration_ms: int,
) -> list[TimeRange]:
    """Return the source spans to keep after compressing long silences.

    Speech spans (word-level when available, else segment-level) separated by a
    gap of at most ``max_silence_ms`` are merged into one phrase; each phrase is
    padded by ``pad_ms`` and the long gaps between phrases — plus excess
    leading/trailing silence — are dropped. Returns ``[]`` when nothing
    meaningful would be cut (the identity / no-cut case).
    """
    spans = _speech_spans(transcript)
    if not spans:
        return []
    duration = max(source_duration_ms, spans[-1][1])

    # Merge spans separated by short gaps (natural pauses stay intact).
    merged: list[list[int]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start - merged[-1][1] <= max_silence_ms:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # Pad each phrase, clamp to the clip, and coalesce any ranges the padding
    # pushed into overlap.
    ranges: list[TimeRange] = []
    for start, end in merged:
        ps = max(0, start - pad_ms)
        pe = min(duration, end + pad_ms)
        if ranges and ps <= ranges[-1].end_ms:
            ranges[-1] = TimeRange(start_ms=ranges[-1].start_ms, end_ms=max(ranges[-1].end_ms, pe))
        else:
            ranges.append(TimeRange(start_ms=ps, end_ms=pe))

    # No-op if the kept content already spans the whole clip as one range.
    if len(ranges) == 1 and ranges[0].start_ms == 0 and ranges[0].end_ms >= duration:
        return []
    return ranges


def remap_ms(source_ms: int, keep_ranges: list[TimeRange]) -> int | None:
    """Map a source timestamp to output time, or ``None`` if it lands in a cut.

    Output time is the summed duration of earlier kept ranges plus the offset
    within the containing range.
    """
    acc = 0
    for r in keep_ranges:
        if source_ms < r.start_ms:
            return None
        if source_ms <= r.end_ms:
            return acc + (source_ms - r.start_ms)
        acc += r.duration_ms
    return None


def tighten_transcript(transcript: Transcript, keep_ranges: list[TimeRange]) -> Transcript:
    """Remap a transcript (segments and words) onto the tightened output timeline.

    With no ``keep_ranges`` the transcript is returned unchanged. Timestamps that
    fall in a cut are clamped to the nearest kept boundary; by construction all
    speech lies inside a kept range, so this only matters defensively.
    """
    if not keep_ranges:
        return transcript

    new_segments: list[TranscriptSegment] = []
    for seg in transcript.segments:
        start = _remap_clamped(seg.start_ms, keep_ranges)
        end = max(start, _remap_clamped(seg.end_ms, keep_ranges))
        words = [
            WordTiming(
                start_ms=_remap_clamped(w.start_ms, keep_ranges),
                end_ms=max(_remap_clamped(w.start_ms, keep_ranges), _remap_clamped(w.end_ms, keep_ranges)),
                text=w.text,
            )
            for w in seg.words
        ]
        new_segments.append(
            TranscriptSegment(start_ms=start, end_ms=end, text=seg.text, words=words)
        )
    return Transcript(language=transcript.language, segments=new_segments)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _speech_spans(transcript: Transcript) -> list[tuple[int, int]]:
    """Speech spans to keep: per segment, its words when present else the segment."""
    spans: list[tuple[int, int]] = []
    for seg in transcript.segments:
        if seg.words:
            spans.extend((w.start_ms, w.end_ms) for w in seg.words)
        else:
            spans.append((seg.start_ms, seg.end_ms))
    return sorted(spans)


def _remap_clamped(source_ms: int, keep_ranges: list[TimeRange]) -> int:
    """Like ``remap_ms`` but clamps cut-region timestamps to the nearest kept edge."""
    acc = 0
    for r in keep_ranges:
        if source_ms < r.start_ms:
            return acc  # in a gap before this range -> its output start
        if source_ms <= r.end_ms:
            return acc + (source_ms - r.start_ms)
        acc += r.duration_ms
    return acc  # past the last kept range -> total kept duration
