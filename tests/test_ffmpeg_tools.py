"""Tests for the FFmpeg utility wrappers."""

from pathlib import Path

import pytest

from shortform_lab.ffmpeg_tools import (
    FFmpegError,
    ensure_ffmpeg_available,
    extract_audio,
    probe_video,
)
from tests.conftest import ffmpeg_required


@ffmpeg_required
def test_ensure_ffmpeg_available_passes():
    ensure_ffmpeg_available()  # should not raise on a machine with ffmpeg


@ffmpeg_required
def test_probe_video_reports_dimensions_and_audio(sample_video: Path):
    info = probe_video(sample_video)
    assert info.width == 640
    assert info.height == 360
    assert info.has_audio is True
    assert 2500 <= info.duration_ms <= 3500
    assert 29 <= info.fps <= 31


def test_probe_missing_file_raises():
    with pytest.raises(FFmpegError):
        probe_video(Path("/nonexistent/video.mp4"))


@ffmpeg_required
def test_extract_audio_writes_wav(sample_video: Path, tmp_path: Path):
    out = tmp_path / "audio.wav"
    result = extract_audio(sample_video, out)
    assert result == out
    assert out.is_file()
    assert out.stat().st_size > 0


def test_extract_audio_missing_source_raises(tmp_path: Path):
    with pytest.raises(FFmpegError):
        extract_audio(Path("/nonexistent/in.mp4"), tmp_path / "out.wav")
