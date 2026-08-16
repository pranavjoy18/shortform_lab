"""The coherence layer: deterministic validation of a ``Timeline`` before render.

This is ``EditPlan``'s Pydantic validation grown into a cross-track, cross-segment
pass (``docs/architecture.md``, milestone 2). The principle is *agents decide;
deterministic code executes and verifies* — so every proposed Timeline (whether
built by the deterministic orchestrator or, later, edited by an LLM) is gated here
before it can become FFmpeg.

Two severities:
- **error** — a structural breakage that must never render (inverted ranges,
  overlapping caption cues, words outside their cue, spine clips out of order).
  ``validate_timeline`` raises ``CoherenceError`` on any of these.
- **warning** — incoherent but renderable, surfaced for inspection rather than
  rejected (e.g. captions that run past the end of the video — common when a
  provided transcript is longer than the clip).

Structural spine invariants (single source, sorted, in-bounds) are already enforced
by ``Timeline``'s own model validator at construction; they are re-checked here so
this pass is the single gate the renderer can trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .timeline import Timeline

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Violation:
    track: str
    message: str
    severity: Severity = "error"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.track}: {self.message}"


class CoherenceError(ValueError):
    """Raised when a Timeline has error-severity coherence violations."""

    def __init__(self, violations: list[Violation]):
        self.violations = list(violations)
        body = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(f"Timeline failed coherence checks:\n{body}")


def check_timeline(tl: Timeline) -> list[Violation]:
    """Return all coherence violations (errors and warnings); empty == coherent."""
    out_ms = tl.output_duration_ms
    v: list[Violation] = []
    v += _check_spine(tl)
    v += _check_beats(tl, out_ms)
    v += _check_grade_spans(tl, out_ms)
    v += _check_captions(tl, out_ms)
    v += _check_hook(tl, out_ms)
    v += _check_overlays(tl, out_ms)
    v += _check_effects(tl, out_ms)
    v += _check_transitions(tl)
    v += _check_lower_third(tl, out_ms)
    return v


def validate_timeline(tl: Timeline) -> Timeline:
    """Raise ``CoherenceError`` if the Timeline has any error-severity violation.

    Warnings pass through silently (read them via :func:`check_timeline`). Returns
    the Timeline unchanged so callers can use it inline.
    """
    errors = [vio for vio in check_timeline(tl) if vio.severity == "error"]
    if errors:
        raise CoherenceError(errors)
    return tl


# --------------------------------------------------------------------------- #
# Per-track checks
# --------------------------------------------------------------------------- #
def _check_spine(tl: Timeline) -> list[Violation]:
    v: list[Violation] = []
    prev_end: int | None = None
    for i, clip in enumerate(tl.spine):
        if clip.source_id != tl.spine_source_id:
            v.append(Violation("spine", f"clip {i} references {clip.source_id!r}, not the spine source"))
        if prev_end is not None and clip.source_in_ms < prev_end:
            v.append(Violation("spine", f"clip {i} overlaps the previous clip in source time"))
        prev_end = clip.source_end_ms
    dur = tl.spine_source.duration_ms
    if dur is not None and prev_end is not None and prev_end > dur:
        v.append(Violation("spine", f"spine extends to {prev_end}ms past source duration {dur}ms"))
    return v


def _check_beats(tl: Timeline, out_ms: int) -> list[Violation]:
    v: list[Violation] = []
    prev_end: int | None = None
    for i, beat in enumerate(tl.beats):
        if beat.end_ms < beat.start_ms:
            v.append(Violation("beats", f"beat {i} ({beat.id!r}) start {beat.start_ms} > end {beat.end_ms}"))
        if prev_end is not None and beat.start_ms < prev_end:
            v.append(Violation("beats", f"beat {i} ({beat.id!r}) overlaps the previous beat"))
        if beat.end_ms > out_ms:
            v.append(Violation("beats", f"beat {beat.id!r} ends at {beat.end_ms}ms past output {out_ms}ms", "warning"))
        prev_end = beat.end_ms
    return v


def _check_grade_spans(tl: Timeline, out_ms: int) -> list[Violation]:
    v: list[Violation] = []
    beat_ids = {b.id for b in tl.beats}
    prev_end: int | None = None
    for i, gs in enumerate(sorted(tl.grade_spans, key=lambda g: g.start_ms)):
        if gs.end_ms < gs.start_ms:
            v.append(Violation("grade_spans", f"span {i} start {gs.start_ms} > end {gs.end_ms}"))
        if prev_end is not None and gs.start_ms < prev_end:
            v.append(Violation("grade_spans", f"grade span {i} overlaps previous span (start {gs.start_ms} < {prev_end})"))
        if gs.end_ms > out_ms:
            v.append(Violation("grade_spans", f"grade span {i} ends at {gs.end_ms}ms past output {out_ms}ms", "warning"))
        if gs.beat_id is not None and gs.beat_id not in beat_ids:
            v.append(Violation("grade_spans", f"span {i} references unknown beat_id {gs.beat_id!r}"))
        prev_end = gs.end_ms
    return v


def _check_captions(tl: Timeline, out_ms: int) -> list[Violation]:
    v: list[Violation] = []
    prev_end: int | None = None
    for i, cue in enumerate(tl.captions):
        if cue.start_ms > cue.end_ms:
            v.append(Violation("captions", f"cue {i} start {cue.start_ms} > end {cue.end_ms}"))
        if prev_end is not None and cue.start_ms < prev_end:
            v.append(Violation("captions", f"cue {i} overlaps the previous cue (start {cue.start_ms} < {prev_end})"))
        if cue.end_ms > out_ms:
            v.append(Violation("captions", f"cue {i} ends at {cue.end_ms}ms past output {out_ms}ms", "warning"))
        prev_end = cue.end_ms

        # Per-word timings must stay ordered and within their cue.
        wprev: int | None = None
        for j, w in enumerate(cue.words):
            if w.start_ms < cue.start_ms or w.end_ms > cue.end_ms:
                v.append(Violation("captions", f"cue {i} word {j} {w.text!r} falls outside its cue bounds"))
            if wprev is not None and w.start_ms < wprev:
                v.append(Violation("captions", f"cue {i} word {j} {w.text!r} is out of order"))
            wprev = w.end_ms
    return v


def _check_hook(tl: Timeline, out_ms: int) -> list[Violation]:
    if tl.hook is None:
        return []
    v: list[Violation] = []
    if tl.hook.start_ms > tl.hook.end_ms:
        v.append(Violation("hook", f"start {tl.hook.start_ms} > end {tl.hook.end_ms}"))
    if tl.hook.end_ms > out_ms:
        v.append(Violation("hook", f"ends at {tl.hook.end_ms}ms past output {out_ms}ms", "warning"))

    # A hook that just repeats the caption playing under it wastes the slot — it
    # should tease, not duplicate. Compare word-normalized text against every
    # caption cue overlapping the hook's time window.
    hook_words = _normalize_words(tl.hook.text)
    if hook_words:
        for cue in tl.captions:
            overlaps = cue.start_ms < tl.hook.end_ms and cue.end_ms > tl.hook.start_ms
            if not overlaps:
                continue
            cue_words = _normalize_words(cue.text)
            if hook_words == cue_words or (cue_words[: len(hook_words)] == hook_words):
                v.append(Violation(
                    "hook",
                    f"text duplicates the caption playing under it ({tl.hook.text!r} vs {cue.text!r})",
                    "warning",
                ))
                break
    return v


def _normalize_words(text: str) -> list[str]:
    return [w.strip(".,;:!?").lower() for w in text.split()]


def _check_overlays(tl: Timeline, out_ms: int) -> list[Violation]:
    v: list[Violation] = []
    for i, o in enumerate(tl.overlays):
        if o.start_ms > o.end_ms:
            v.append(Violation("overlays", f"overlay {i} start {o.start_ms} > end {o.end_ms}"))
        if o.end_ms > out_ms:
            v.append(Violation("overlays", f"overlay {i} ends at {o.end_ms}ms past output {out_ms}ms", "warning"))
    return v


def _check_effects(tl: Timeline, out_ms: int) -> list[Violation]:
    v: list[Violation] = []
    for i, p in enumerate(tl.punch_ins):
        if p.start_ms > p.end_ms:
            v.append(Violation("effects", f"punch-in {i} start {p.start_ms} > end {p.end_ms}"))
        if p.end_ms > out_ms:
            v.append(Violation("effects", f"punch-in {i} ends at {p.end_ms}ms past output {out_ms}ms", "warning"))
    return v


def _check_transitions(tl: Timeline) -> list[Violation]:
    v: list[Violation] = []
    n_gaps = max(0, len(tl.spine) - 1)
    if tl.transitions and len(tl.transitions) != n_gaps:
        v.append(Violation(
            "transitions",
            f"transitions count ({len(tl.transitions)}) must equal spine gaps ({n_gaps})",
        ))
    for i, tr in enumerate(tl.transitions):
        td = tr.duration_ms
        # Each adjacent pair of clips must be longer than the transition.
        if i < len(tl.spine) and tl.spine[i].duration_ms <= td:
            v.append(Violation(
                "transitions",
                f"transition {i}: clip {i} duration {tl.spine[i].duration_ms}ms "
                f"is not longer than transition {td}ms",
            ))
        if i + 1 < len(tl.spine) and tl.spine[i + 1].duration_ms <= td:
            v.append(Violation(
                "transitions",
                f"transition {i}: clip {i+1} duration {tl.spine[i+1].duration_ms}ms "
                f"is not longer than transition {td}ms",
            ))
    return v


def _check_lower_third(tl: Timeline, out_ms: int) -> list[Violation]:
    lt = tl.lower_third
    if lt is None:
        return []
    v: list[Violation] = []
    if lt.start_ms > lt.end_ms:
        v.append(Violation("lower_third", f"start {lt.start_ms} > end {lt.end_ms}"))
    if lt.end_ms > out_ms:
        v.append(Violation(
            "lower_third", f"ends at {lt.end_ms}ms past output {out_ms}ms", "warning"
        ))
    return v
