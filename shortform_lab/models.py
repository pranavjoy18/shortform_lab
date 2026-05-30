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
class TranscriptSegment(BaseModel):
    """One timestamped span of speech."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str

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
    """One on-screen caption line, sentence-level for the first version."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str


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


class EditPlan(BaseModel):
    """The full inspectable plan that the renderer turns into a video."""

    source_video: str
    hook: Hook
    captions: list[CaptionCue]
    overlays: list[VisualOverlay] = Field(default_factory=list)
    punch_ins: list[PunchIn] = Field(default_factory=list)
    export_width: int = Field(gt=0)
    export_height: int = Field(gt=0)
    export_fps: int = Field(gt=0)
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


class HookSettings(BaseModel):
    duration_ms: int = Field(gt=0)
    max_words: int = Field(gt=0)


class CaptionSettings(BaseModel):
    mode: Literal["sentence", "word"] = "sentence"
    font_size: int = Field(gt=0)
    max_chars_per_line: int = Field(gt=0)
    position: Literal["top", "center", "bottom"] = "bottom"


class VisualSettings(BaseModel):
    punch_in_count: int = Field(ge=0)
    text_card_count: int = Field(ge=0)
    allow_stock_broll: bool = False


class AudioSettings(BaseModel):
    normalize: bool = True


class StyleConfig(BaseModel):
    """A named creative style loaded from ``configs/styles/<name>.yaml``."""

    name: str
    export: ExportSettings
    hook: HookSettings
    captions: CaptionSettings
    visuals: VisualSettings
    audio: AudioSettings = Field(default_factory=AudioSettings)
