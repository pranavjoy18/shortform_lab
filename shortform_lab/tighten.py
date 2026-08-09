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

# Conservative disfluencies removed when filler-word removal is on. Kept small on
# purpose: words like "like"/"so"/"actually" are too often meaningful to cut.
DEFAULT_FILLER_WORDS = frozenset(
    {"um", "uh", "umm", "uhh", "uhm", "er", "err", "erm", "ah", "eh", "hmm", "mm", "mhm"}
)


def compute_keep_ranges(
    transcript: Transcript,
    *,
    max_silence_ms: int,
    pad_ms: int,
    source_duration_ms: int,
    remove_fillers: bool = False,
    filler_words: frozenset[str] = DEFAULT_FILLER_WORDS,
) -> list[TimeRange]:
    """Return the source spans to keep after compressing silences (and fillers).

    Speech spans (word-level when available, else segment-level) separated by a
    gap of at most ``max_silence_ms`` are merged into one phrase; each phrase is
    padded by ``pad_ms`` and the long gaps between phrases — plus excess
    leading/trailing silence — are dropped. When ``remove_fillers`` is set, the
    source spans of filler words (e.g. "um", "uh") are then *cut out* of the kept
    ranges — even short ones a natural-pause merge would otherwise keep. This
    needs word timings; on a segment-only transcript it is a no-op. Returns ``[]``
    when nothing meaningful would be cut (the identity / no-cut case).
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

    # Carve filler-word spans out of the kept ranges (may split a range in two).
    if remove_fillers:
        ranges = _subtract_spans(ranges, _filler_spans(transcript, filler_words))

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

    With no ``keep_ranges`` the transcript is returned unchanged. **Words that fall
    entirely in a cut are dropped** (so filler words removed from the video also
    vanish from word-level captions); surviving words are remapped. A segment whose
    words are all dropped is removed. Segment timestamps are clamped to the nearest
    kept boundary; when a segment had words, its text is rebuilt from the survivors
    so caption text stays in sync (segment-only text cannot be scrubbed).
    """
    if not keep_ranges:
        return transcript

    new_segments: list[TranscriptSegment] = []
    for seg in transcript.segments:
        start = _remap_clamped(seg.start_ms, keep_ranges)
        end = max(start, _remap_clamped(seg.end_ms, keep_ranges))

        if seg.words:
            kept = [w for w in seg.words if _word_survives(w, keep_ranges)]
            if not kept:
                continue  # whole segment landed in cuts
            words = [
                WordTiming(
                    start_ms=_remap_clamped(w.start_ms, keep_ranges),
                    end_ms=max(
                        _remap_clamped(w.start_ms, keep_ranges),
                        _remap_clamped(w.end_ms, keep_ranges),
                    ),
                    text=w.text,
                )
                for w in kept
            ]
            # If any words were dropped, rebuild text from survivors; else keep original.
            text = seg.text if len(kept) == len(seg.words) else " ".join(w.text for w in kept)
            new_segments.append(TranscriptSegment(start_ms=start, end_ms=end, text=text, words=words))
        else:
            new_segments.append(TranscriptSegment(start_ms=start, end_ms=end, text=seg.text))
    return Transcript(language=transcript.language, segments=new_segments)


def _word_survives(w: WordTiming, keep_ranges: list[TimeRange]) -> bool:
    """A word survives iff it has positive overlap with some kept range.

    Endpoint mapping alone is not enough: a word cut out exactly between two kept
    ranges (e.g. a filler whose bounds coincide with the cut) has both endpoints
    land on kept boundaries yet zero kept duration.
    """
    return any(
        min(w.end_ms, r.end_ms) > max(w.start_ms, r.start_ms) for r in keep_ranges
    )


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


def _normalize_token(text: str) -> str:
    """Lowercased word with surrounding punctuation stripped, for filler matching."""
    return text.strip().strip(".,!?;:\"'…-").lower()


def _filler_spans(transcript: Transcript, filler_words: frozenset[str]) -> list[tuple[int, int]]:
    """Source spans of every word whose normalized text is a filler word."""
    return [
        (w.start_ms, w.end_ms)
        for seg in transcript.segments
        for w in seg.words
        if _normalize_token(w.text) in filler_words
    ]


def _subtract_spans(ranges: list[TimeRange], cuts: list[tuple[int, int]]) -> list[TimeRange]:
    """Carve ``cuts`` out of ``ranges``; a cut inside a range splits it in two."""
    if not cuts:
        return ranges
    cuts = sorted(cuts)
    out: list[TimeRange] = []
    for r in ranges:
        pieces = [(r.start_ms, r.end_ms)]
        for cs, ce in cuts:
            next_pieces: list[tuple[int, int]] = []
            for ps, pe in pieces:
                if ce <= ps or cs >= pe:  # no overlap
                    next_pieces.append((ps, pe))
                    continue
                if cs > ps:
                    next_pieces.append((ps, cs))  # kept part before the cut
                if ce < pe:
                    next_pieces.append((ce, pe))  # kept part after the cut
            pieces = next_pieces
        out.extend(TimeRange(start_ms=ps, end_ms=pe) for ps, pe in pieces if pe > ps)
    return out
