"""Turn audio into a timestamped ``Transcript``.

Transcription is behind a small ``Transcriber`` protocol so providers are
swappable. Two are included:

- ``ProvidedTranscriptTranscriber`` reads an existing transcript JSON file. This
  lets the whole pipeline run with zero API cost during early development
  (``--transcript path/to/transcript.json``).
- ``OpenAITranscriber`` calls OpenAI's Whisper-family endpoint. It is optional
  and only imported when used, so the package works without the ``openai`` extra.

A tolerant ``load_transcript`` reader accepts both this project's
``{start_ms, end_ms, text}`` shape and the looser ``{start, end, text}`` (seconds)
shape produced by tools like yt-dlp, so provided files don't all have to match
one exact schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .ffmpeg_tools import extract_audio
from .models import Transcript, TranscriptSegment, WordTiming


class Transcriber(Protocol):
    """Anything that can turn a video into a ``Transcript``."""

    def transcribe(self, video_path: Path, *, work_dir: Path) -> Transcript:
        ...


def load_transcript(path: Path) -> Transcript:
    """Load a transcript JSON file, tolerating ms- or second-based timestamps."""
    if not path.is_file():
        raise FileNotFoundError(f"Transcript file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    language = data.get("language", "en")
    segments_raw = data.get("segments", [])

    segments: list[TranscriptSegment] = []
    for seg in segments_raw:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start_ms, end_ms = _coerce_times(seg)
        words = _coerce_words(seg.get("words") or [])
        segments.append(
            TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text, words=words)
        )

    if not segments:
        raise ValueError(f"Transcript {path} contains no usable segments")

    return Transcript(language=language, segments=segments)


def _coerce_times(seg: dict) -> tuple[int, int]:
    """Extract (start_ms, end_ms) from either ms or seconds fields."""
    if "start_ms" in seg and "end_ms" in seg:
        return int(seg["start_ms"]), int(seg["end_ms"])
    if "start" in seg and "end" in seg:
        # Seconds (possibly fractional) -> milliseconds.
        return int(round(float(seg["start"]) * 1000)), int(round(float(seg["end"]) * 1000))
    raise ValueError(f"Segment is missing timestamps: {seg!r}")


def _coerce_words(words_raw: list) -> list[WordTiming]:
    """Parse an optional per-segment word list, tolerating ms/seconds shapes.

    Accepts ``{start_ms,end_ms,text}`` or the looser ``{start,end,word|text}``
    (seconds) shape some tools emit. Words missing text or timestamps are skipped.
    """
    words: list[WordTiming] = []
    for w in words_raw:
        text = (w.get("text") or w.get("word") or "").strip()
        if not text:
            continue
        try:
            start_ms, end_ms = _coerce_times(w)
        except ValueError:
            continue
        words.append(WordTiming(start_ms=start_ms, end_ms=end_ms, text=text))
    return words


class ProvidedTranscriptTranscriber:
    """A 'transcriber' that simply returns a transcript already on disk."""

    def __init__(self, transcript_path: Path):
        self.transcript_path = transcript_path

    def transcribe(self, video_path: Path, *, work_dir: Path) -> Transcript:
        # video_path/work_dir are unused: the transcript already exists.
        return load_transcript(self.transcript_path)


class OpenAITranscriber:
    """Transcribe via OpenAI's Whisper-family API.

    Requires the optional ``openai`` extra and an ``OPENAI_API_KEY``. Imported
    lazily so the package works without it.
    """

    def __init__(self, model: str = "whisper-1", *, normalize: bool = False):
        self.model = model
        self.normalize = normalize

    def transcribe(self, video_path: Path, *, work_dir: Path) -> Transcript:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "OpenAI transcription requires the 'openai' extra: uv sync --extra openai"
            ) from exc

        audio_path = work_dir / "audio.wav"
        extract_audio(video_path, audio_path, normalize=self.normalize)

        client = OpenAI()
        with audio_path.open("rb") as fh:
            response = client.audio.transcriptions.create(
                model=self.model,
                file=fh,
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"],
            )

        all_words = [
            WordTiming(
                start_ms=int(round(float(w.start) * 1000)),
                end_ms=int(round(float(w.end) * 1000)),
                text=text,
            )
            for w in getattr(response, "words", []) or []
            if (text := (getattr(w, "word", "") or "").strip())
        ]

        segments: list[TranscriptSegment] = []
        for seg in getattr(response, "segments", []) or []:
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            start_ms = int(round(float(seg.start) * 1000))
            end_ms = int(round(float(seg.end) * 1000))
            # Bucket each word into the segment whose span contains its start.
            seg_words = [w for w in all_words if start_ms <= w.start_ms < end_ms]
            segments.append(
                TranscriptSegment(start_ms=start_ms, end_ms=end_ms, text=text, words=seg_words)
            )
        if not segments:
            raise RuntimeError("OpenAI transcription returned no segments")

        language = getattr(response, "language", "en") or "en"
        return Transcript(language=language, segments=segments)
