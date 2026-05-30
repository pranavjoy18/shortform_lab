"""Tests for transcript loading and the provided-transcript adapter."""

from pathlib import Path

import pytest

from shortform_lab.models import Transcript
from shortform_lab.transcribe import ProvidedTranscriptTranscriber, load_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"


def test_load_transcript_ms_schema():
    t = load_transcript(FIXTURE)
    assert isinstance(t, Transcript)
    assert t.language == "en"
    assert len(t.segments) == 5
    assert t.segments[0].start_ms == 0
    assert "quit way too early" in t.segments[0].text


def test_load_transcript_seconds_schema(tmp_path: Path):
    p = tmp_path / "secs.json"
    p.write_text(
        '{"language": "en", "segments": ['
        '{"start": 0.0, "end": 1.5, "text": "Hello"},'
        '{"start": 1.5, "end": 3.0, "text": "World"}]}',
        encoding="utf-8",
    )
    t = load_transcript(p)
    assert t.segments[0].end_ms == 1500
    assert t.segments[1].start_ms == 1500


def test_load_transcript_skips_empty_segments(tmp_path: Path):
    p = tmp_path / "empty.json"
    p.write_text(
        '{"segments": ['
        '{"start_ms": 0, "end_ms": 1000, "text": "  "},'
        '{"start_ms": 1000, "end_ms": 2000, "text": "kept"}]}',
        encoding="utf-8",
    )
    t = load_transcript(p)
    assert len(t.segments) == 1
    assert t.segments[0].text == "kept"


def test_load_transcript_no_segments_raises(tmp_path: Path):
    p = tmp_path / "none.json"
    p.write_text('{"segments": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_transcript(p)


def test_provided_transcriber_returns_file_contents():
    transcriber = ProvidedTranscriptTranscriber(FIXTURE)
    t = transcriber.transcribe(Path("ignored.mp4"), work_dir=Path("."))
    assert len(t.segments) == 5
