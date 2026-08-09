"""The core domain model we are migrating toward: a multi-source ``Timeline``.

This is the "Model C" shape from ``docs/architecture.md``: the types are
multi-source from day one (a ``sources`` registry; every ``Clip`` names the
source it draws from), but we *operate* with a single contiguous talking-head
**spine** for now — enforced by the validators here, which reject what later
milestones will relax (multiple video sources, speed changes, transitions).

Time model:
- One time base: integer milliseconds, **output time** (the tightened timeline).
- The **spine** is a SEQUENTIAL track: its clips are concatenated back-to-back, so
  their output positions are *derived*, not stored. The spine *is* the cut list —
  ``spine_keep_ranges`` projects it back to the source spans the current renderer
  consumes as ``EditPlan.keep_ranges``. An empty spine means "whole source, uncut".
- The **spine source's audio is the timebase**: captions/effects (floating tracks,
  added in later milestones) carry explicit output-time positions against it.

Nothing here is wired into the live pipeline yet (``cli.py`` still uses
``EditPlan``); ``spine_keep_ranges`` is the adapter that lets a ``Timeline`` drive
the existing cut/concat renderer unchanged. See ``skills/`` for the operations
that edit a Timeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .models import (
    Beat,
    CaptionCue,
    ColorGrade,
    EditPlan,
    ExportSettings,
    GradeSpan,
    Hook,
    LowerThird,
    PunchIn,
    TimeRange,
    TransitionInfo,
    VisualOverlay,
)


# --------------------------------------------------------------------------- #
# Media + clips
# --------------------------------------------------------------------------- #
class MediaRef(BaseModel):
    """One source asset the Timeline can draw from.

    ``duration_ms``/``fps`` are ``None`` for stills (an image has no intrinsic
    timebase; a clip placing it chooses its own duration). ``id`` is referenced by
    every ``Clip.source_id``.
    """

    id: str
    path: str
    kind: Literal["video", "image", "audio"] = "video"
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, gt=0)
    has_audio: bool = False
    label: str | None = None


class Clip(BaseModel):
    """A placement of a span of one source onto a track.

    ``timeline_start_ms`` is set on *floating* tracks (explicit output position)
    and left ``None`` on *sequential* tracks (the spine), where positions are
    derived back-to-back. ``speed`` and the ``transition_*`` fields are reserved
    for later milestones; the coherence checks below reject non-default values so
    the type can carry them without the renderer pretending to support them yet.
    """

    source_id: str
    source_in_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(gt=0)
    timeline_start_ms: int | None = Field(default=None, ge=0)
    track: str = "spine"

    # --- reserved for future milestones (rejected until built) --------------- #
    speed: float = Field(default=1.0, gt=0)
    transition_in: str | None = None
    transition_out: str | None = None

    @model_validator(mode="after")
    def _reject_reserved(self) -> "Clip":
        if self.speed != 1.0:
            raise ValueError("Clip.speed != 1.0 is reserved for a future milestone")
        return self

    @property
    def source_end_ms(self) -> int:
        """End of the consumed source span (at 1x speed)."""
        return self.source_in_ms + self.duration_ms

    def source_range(self) -> TimeRange:
        """The source span this clip consumes, as a ``TimeRange``."""
        return TimeRange(start_ms=self.source_in_ms, end_ms=self.source_end_ms)


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #
class Timeline(BaseModel):
    """The multi-source edit state (Model C, single-spine for now).

    Only the spine track is exercised today; ``captions`` is present (reusing the
    existing ``CaptionCue``) so the caption-skill port has a home, and the
    remaining floating tracks (reframe/overlays/effects/audio) arrive with their
    skill ports in milestone 1b.
    """

    sources: list[MediaRef]
    spine_source_id: str
    # SEQUENTIAL base track == the cut list. Empty means the whole spine, uncut.
    spine: list[Clip] = Field(default_factory=list)
    # FLOATING tracks: output-time annotations/effects over the spine. Today these
    # reuse the established element types (validated already); they become richer
    # clip tracks (e.g. b-roll/image overlays) in later milestones.
    hook: Hook | None = None
    captions: list[CaptionCue] = Field(default_factory=list)
    overlays: list[VisualOverlay] = Field(default_factory=list)
    punch_ins: list[PunchIn] = Field(default_factory=list)  # the "effects" track (punch-in only today)
    # A global color grade (None = ungraded). Read by the renderer straight from
    # the Timeline — the first track rendered Timeline-direct rather than via EditPlan.
    color: ColorGrade | None = None
    # Structural skeleton: semantic segments written by BeatSegmenterSkill (Phase 0).
    # Decoration skills declare a read on "beats" so topo-sort places them after.
    beats: list[Beat] = Field(default_factory=list)
    # Per-beat color overrides: each GradeSpan overrides Timeline.color during its
    # window. Multiple spans must not overlap (coherence-checked). Read Timeline-direct
    # by the renderer (like color); not carried through EditPlan.
    grade_spans: list[GradeSpan] = Field(default_factory=list)
    # Visual transitions between adjacent spine clips: transitions[i] applies at the
    # boundary between spine[i] and spine[i+1]. Written by TransitionSkill.
    transitions: list[TransitionInfo] = Field(default_factory=list)
    # Lower-third name/title banner (output-time floating overlay).
    lower_third: LowerThird | None = None
    export: ExportSettings
    meta: dict[str, object] = Field(default_factory=dict)

    # --- validation (the seed of the coherence layer) ----------------------- #
    @model_validator(mode="after")
    def _check_invariants(self) -> "Timeline":
        ids = {s.id for s in self.sources}
        if len(ids) != len(self.sources):
            raise ValueError("MediaRef ids must be unique")
        if self.spine_source_id not in ids:
            raise ValueError(f"spine_source_id {self.spine_source_id!r} is not in sources")

        prev_end: int | None = None
        for clip in self.spine:
            # Model C constraint: the spine is a single video source for now.
            if clip.source_id != self.spine_source_id:
                raise ValueError(
                    "spine clips must reference spine_source_id "
                    "(multiple video sources are a future milestone)"
                )
            if clip.timeline_start_ms is not None:
                raise ValueError("spine clips are sequential; timeline_start_ms must be None")
            if prev_end is not None and clip.source_in_ms < prev_end:
                raise ValueError("spine clips must be sorted and non-overlapping in source time")
            prev_end = clip.source_end_ms

        src = self.source(self.spine_source_id)
        if src.duration_ms is not None and prev_end is not None and prev_end > src.duration_ms:
            raise ValueError("a spine clip extends past the source duration")
        return self

    # --- helpers ------------------------------------------------------------ #
    def source(self, source_id: str) -> MediaRef:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)

    @property
    def spine_source(self) -> MediaRef:
        return self.source(self.spine_source_id)

    def spine_keep_ranges(self) -> list[TimeRange]:
        """Project the spine track back to source spans (``EditPlan.keep_ranges``).

        Returns ``[]`` for the identity case — an empty spine, or a single clip
        covering the whole source — matching the renderer's "empty = uncut"
        convention exactly. Otherwise returns one source span per spine clip.
        """
        if not self.spine:
            return []
        ranges = [c.source_range() for c in self.spine]
        dur = self.spine_source.duration_ms
        if (
            len(ranges) == 1
            and ranges[0].start_ms == 0
            and (dur is None or ranges[0].end_ms >= dur)
        ):
            return []
        return ranges

    @property
    def output_duration_ms(self) -> int:
        """Total output (tightened) duration: the summed spine clip durations."""
        if not self.spine:
            return self.spine_source.duration_ms or 0
        return sum(c.duration_ms for c in self.spine)


def timeline_to_editplan(tl: Timeline) -> EditPlan:
    """Adapter: project a ``Timeline`` onto the ``EditPlan`` the renderer consumes.

    This is the seam that lets the new substrate drive the existing, untouched
    renderer. The spine collapses to ``keep_ranges`` (source time); the floating
    tracks pass straight through (they are already output-time element types). The
    deterministic plan ``reason`` rides along in ``meta``.
    """
    keep_ranges = tl.spine_keep_ranges()
    # Transitions only make sense at cut boundaries; drop them for uncut timelines.
    transitions = tl.transitions if keep_ranges else []
    return EditPlan(
        source_video=tl.spine_source.path,
        hook=tl.hook,
        captions=tl.captions,
        overlays=tl.overlays,
        punch_ins=tl.punch_ins,
        keep_ranges=keep_ranges,
        transitions=transitions,
        lower_third=tl.lower_third,
        export_width=tl.export.width,
        export_height=tl.export.height,
        export_fps=tl.export.fps,
        export_layout=tl.export.layout,
        reason=tl.meta.get("reason"),  # type: ignore[arg-type]
    )


def timeline_from_spine(source: MediaRef, export: ExportSettings) -> Timeline:
    """Build the initial Timeline for a raw clip: one full-span, uncut spine clip.

    This is the starting state skills edit. The single clip spans the whole source
    so ``spine_keep_ranges`` reports the identity (uncut) case until a skill cuts.
    """
    if source.duration_ms is None or source.duration_ms <= 0:
        raise ValueError("spine source needs a positive duration_ms")
    spine = [Clip(source_id=source.id, source_in_ms=0, duration_ms=source.duration_ms)]
    return Timeline(sources=[source], spine_source_id=source.id, spine=spine, export=export)
