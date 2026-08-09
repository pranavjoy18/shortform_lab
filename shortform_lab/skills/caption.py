"""``add_captions`` skill + the shared caption builder.

``build_captions`` is the single source of caption logic, shared by the
deterministic and LLM paths (word-mode captions must come from the transcript,
never the model). It lives here, in the skills package, as the home of caption
behavior; ``planner`` re-exports it for backward compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import CaptionCue, StyleConfig, TimeRange, Transcript, TranscriptSegment, WordTiming
from ..timeline import Timeline
from .base import Context


def build_captions(transcript: Transcript, style: StyleConfig) -> list[CaptionCue]:
    """Turn a transcript into caption cues according to the style's caption mode.

    Sentence mode yields one cue per segment with no word timings. Word mode
    yields short word *groups*, each cue carrying the per-word timings the
    renderer animates. When a segment lacks word timings (e.g. a segment-only
    transcript), even-spaced timings are synthesized so the word look still works.
    """
    if style.captions.mode == "sentence":
        return [
            CaptionCue(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text.strip())
            for s in transcript.segments
        ]

    group_size = (
        1 if style.captions.word_animation == "one_word" else style.captions.max_words_per_group
    )
    cues: list[CaptionCue] = []
    for seg in transcript.segments:
        words = seg.words or _synthesize_words(seg)
        for group in _chunk(words, group_size):
            cues.append(
                CaptionCue(
                    start_ms=group[0].start_ms,
                    end_ms=group[-1].end_ms,
                    text=" ".join(w.text for w in group),
                    words=list(group),
                )
            )
    return cues


def _synthesize_words(seg: TranscriptSegment) -> list[WordTiming]:
    """Spread a segment's words evenly across its span (timing fallback)."""
    tokens = seg.text.split()
    if not tokens:
        return []
    span = max(seg.end_ms - seg.start_ms, len(tokens))
    per = span / len(tokens)
    words: list[WordTiming] = []
    for i, tok in enumerate(tokens):
        start = seg.start_ms + int(round(i * per))
        end = seg.end_ms if i == len(tokens) - 1 else seg.start_ms + int(round((i + 1) * per))
        words.append(WordTiming(start_ms=start, end_ms=max(end, start), text=tok))
    return words


def _chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


@dataclass(frozen=True)
class CaptionSkill:
    """Build caption cues from the (output-time) transcript per the style."""

    name: ClassVar[str] = "add_captions"
    # Reads the spine too: caption cues are in output time, which depends on the
    # cut list — so this must run after any skill that rewrites the spine.
    reads: ClassVar[frozenset[str]] = frozenset({"transcript", "spine"})
    writes: ClassVar[frozenset[str]] = frozenset({"captions"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        return tl.model_copy(update={"captions": build_captions(ctx.transcript, ctx.style)})
