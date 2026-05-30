"""Core data contracts for the shortform_lab pipeline.

These Pydantic models define every shape that flows through the system:
transcripts, the edit plan the planner produces, and the style config that
drives creative behavior. Timestamps are integer milliseconds throughout so
nothing depends on float rounding.

The ``EditPlan`` is the contract between planning and rendering: the planner
(deterministic or LLM) emits one, and the renderer consumes only what is in it.
Keeping it a validated schema means an LLM response can be checked before it is
ever turned into FFmpeg commands.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #
class WordTiming(BaseModel):
    """One timestamped word, used to drive word-level animated captions."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _check_order(self) -> "WordTiming":
        if self.end_ms < self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) precedes start_ms ({self.start_ms})")
        return self


class TranscriptSegment(BaseModel):
    """One timestamped span of speech, optionally with per-word timings."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    # Present when the transcriber provides word-level timing; empty otherwise.
    words: list[WordTiming] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_order(self) -> "TranscriptSegment":
        if self.end_ms < self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) precedes start_ms ({self.start_ms})")
        return self


class Transcript(BaseModel):
    """A full transcript: a language tag plus ordered speech segments."""

    language: str = "en"
    segments: list[TranscriptSegment]

    @property
    def full_text(self) -> str:
        return " ".join(seg.text.strip() for seg in self.segments).strip()

    @property
    def duration_ms(self) -> int:
        return self.segments[-1].end_ms if self.segments else 0


# --------------------------------------------------------------------------- #
# Edit plan pieces
# --------------------------------------------------------------------------- #
class Hook(BaseModel):
    """The opening text overlay shown over the first few seconds."""

    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class CaptionCue(BaseModel):
    """One on-screen caption unit.

    In sentence mode this is a full line and ``words`` is empty. In word mode it
    is a short word *group*, and ``words`` carries the per-word timings the
    renderer animates (highlight/karaoke/one-word).
    """

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    words: list[WordTiming] = Field(default_factory=list)


class VisualOverlay(BaseModel):
    """A simulated b-roll element: a text card or quote card.

    ``kind`` is open-ended (e.g. "text_card", "quote_card") so styles can add
    new overlay types without a schema change; ``placement`` is where on the
    frame it sits.
    """

    kind: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str | None = None
    placement: Literal["top", "center", "bottom"] = "center"


class PunchIn(BaseModel):
    """A center-crop zoom over a time range. ``zoom`` is a scale factor > 1."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    zoom: float = Field(gt=1.0, le=3.0)


class TimeRange(BaseModel):
    """A span of *source* time kept in the output (a cut-list entry)."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_order(self) -> "TimeRange":
        if self.end_ms < self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) precedes start_ms ({self.start_ms})")
        return self

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class EditPlan(BaseModel):
    """The full inspectable plan that the renderer turns into a video."""

    source_video: str
    # None when the style disables the hook (gated in the planner, not the renderer).
    hook: Hook | None = None
    captions: list[CaptionCue]
    overlays: list[VisualOverlay] = Field(default_factory=list)
    punch_ins: list[PunchIn] = Field(default_factory=list)
    # Source-time spans kept in the output (the cut list). Empty means the whole
    # source is rendered uncut; all other plan fields are in *output* time.
    keep_ranges: list[TimeRange] = Field(default_factory=list)
    export_width: int = Field(gt=0)
    export_height: int = Field(gt=0)
    export_fps: int = Field(gt=0)
    export_layout: Literal["fill", "letterbox"] = "fill"
    # Optional human-readable explanation of the creative choices, surfaced in
    # review.md. Provided by the LLM planner; the deterministic planner sets it.
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Style config
# --------------------------------------------------------------------------- #
class ExportSettings(BaseModel):
    aspect_ratio: str = "9:16"
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    # How the source fills the frame: "fill" crops to cover the 9:16 frame;
    # "letterbox" fits the whole source and pads black bars (captions then sit
    # in the bars). This is an axis independent of the caption look.
    layout: Literal["fill", "letterbox"] = "fill"


class HookSettings(BaseModel):
    # Off by default: the hook overlay is isolated behind this flag so it can be
    # re-enabled per style later without touching code. When disabled the planner
    # still records the hook in the plan, but the renderer does not burn it in.
    enabled: bool = False
    duration_ms: int = Field(gt=0)
    max_words: int = Field(gt=0)


class CaptionSettings(BaseModel):
    mode: Literal["sentence", "word"] = "sentence"
    font_size: int = Field(gt=0)
    max_chars_per_line: int = Field(gt=0)
    position: Literal["top", "center", "bottom"] = "bottom"
    # Word-mode only: how per-word captions animate.
    #   active_word - keep a small word group on screen, highlight the current word
    #   karaoke     - static group, color sweeps across words as they are spoken
    #   one_word    - one large centered word at a time
    word_animation: Literal["active_word", "karaoke", "one_word"] = "active_word"
    # Accent color for the highlighted/sung word, as #RRGGBB.
    highlight_color: str = "#FFE000"
    # Words per on-screen group for active_word/karaoke (one_word forces 1).
    max_words_per_group: int = Field(gt=0, default=4)
    # Uppercase caption text for a bolder look.
    uppercase: bool = False


class VisualSettings(BaseModel):
    punch_in_count: int = Field(ge=0)
    text_card_count: int = Field(ge=0)
    allow_stock_broll: bool = False
    # Off by default: like the hook, overlay (quote/text) cards are isolated
    # behind this flag. The planner still records them in the plan; the renderer
    # only burns them in when enabled, so they can be re-enabled per style later.
    overlays_enabled: bool = False


class AudioSettings(BaseModel):
    normalize: bool = True


class TighteningSettings(BaseModel):
    """Silence/pause compression. Disabled by default so existing styles are unchanged.

    ``max_silence_ms`` is the longest gap between speech kept intact; longer gaps
    are cut. ``pad_ms`` is the breathing room kept around each retained phrase.
    """

    enabled: bool = False
    max_silence_ms: int = Field(gt=0, default=350)
    pad_ms: int = Field(ge=0, default=100)


class StyleConfig(BaseModel):
    """A named creative style loaded from ``configs/styles/<name>.yaml``."""

    name: str
    export: ExportSettings
    hook: HookSettings
    captions: CaptionSettings
    visuals: VisualSettings
    audio: AudioSettings = Field(default_factory=AudioSettings)
    tighten: TighteningSettings = Field(default_factory=TighteningSettings)
