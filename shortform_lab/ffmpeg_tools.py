"""Thin wrappers around the FFmpeg/ffprobe binaries.

Every call uses ``subprocess.run`` with an explicit argument list (never a shell
string) so paths with spaces and odd characters are safe. The prototype is
expected to fail early and loudly if FFmpeg is missing or a video cannot be
read, rather than producing a broken render much later in the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .concurrency import ffmpeg_semaphore


class FFmpegError(RuntimeError):
    """Raised when FFmpeg is missing or an FFmpeg/ffprobe command fails."""


@dataclass
class VideoInfo:
    """The handful of media facts the pipeline actually needs."""

    width: int
    height: int
    duration_ms: int
    fps: float
    has_audio: bool


def ensure_ffmpeg_available() -> None:
    """Raise ``FFmpegError`` unless both ``ffmpeg`` and ``ffprobe`` are on PATH."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise FFmpegError(
            f"Required tool(s) not found on PATH: {', '.join(missing)}. "
            "Install FFmpeg (https://ffmpeg.org/) and ensure ffmpeg and ffprobe are runnable."
        )


async def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    """Run a command under the ffmpeg concurrency cap, returning its stdout.

    Raises ``FFmpegError`` on failure. Uses ``asyncio.create_subprocess_exec``
    (never a shell string) so the caller's event loop isn't blocked waiting on
    an external process, and so several ffmpeg/ffprobe invocations can be in
    flight at once, bounded by ``ffmpeg_semaphore``.
    """
    async with ffmpeg_semaphore:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd) if cwd is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise FFmpegError(f"Command not found: {cmd[0]}") from exc
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise FFmpegError(
                f"Command failed ({' '.join(cmd[:2])} ...): exit {proc.returncode}\n"
                f"{stderr.decode(errors='replace').strip()}"
            )
        return stdout.decode(errors="replace")


async def probe_video(path: Path) -> VideoInfo:
    """Probe ``path`` with ffprobe and return its core media facts."""
    if not path.is_file():
        raise FFmpegError(f"Video file not found: {path}")

    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    stdout = await _run(cmd)
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"Could not parse ffprobe output for {path}") from exc

    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video_stream is None:
        raise FFmpegError(f"No video stream found in {path}")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    if width <= 0 or height <= 0:
        raise FFmpegError(f"Invalid video dimensions in {path}: {width}x{height}")

    # Duration can live on the format or the stream; prefer whichever is present.
    duration_s = _first_float(
        data.get("format", {}).get("duration"),
        video_stream.get("duration"),
    )
    duration_ms = int(round(duration_s * 1000)) if duration_s is not None else 0

    fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    return VideoInfo(
        width=width,
        height=height,
        duration_ms=duration_ms,
        fps=fps,
        has_audio=has_audio,
    )


async def extract_audio(video_path: Path, audio_path: Path, *, normalize: bool = False) -> Path:
    """Extract a mono 16 kHz WAV from ``video_path`` (good for transcription).

    When ``normalize`` is set, loudness is normalized via the ``loudnorm`` filter.
    Returns the written audio path.
    """
    if not video_path.is_file():
        raise FFmpegError(f"Video file not found: {video_path}")
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",                  # drop video
        "-ac", "1",             # mono
        "-ar", "16000",         # 16 kHz, the rate Whisper-family models expect
    ]
    if normalize:
        cmd += ["-af", "loudnorm"]
    cmd += [str(audio_path)]

    await _run(cmd)
    if not audio_path.is_file():
        raise FFmpegError(f"Audio extraction produced no file at {audio_path}")
    return audio_path


def _first_float(*values: object) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _parse_fps(rate: str | None) -> float:
    """Parse an ffprobe frame-rate string like ``30000/1001`` into a float."""
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0
