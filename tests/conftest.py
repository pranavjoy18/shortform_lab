"""Shared test fixtures."""

import shutil
import subprocess
from pathlib import Path

import pytest

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate a tiny 3s 640x360 test clip with a tone, via FFmpeg.

    Skips the whole test if FFmpeg is unavailable so the suite stays runnable
    on machines without it.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out
