"""Process-wide concurrency caps for the engine's two I/O boundaries.

FFmpeg work is CPU-bound (the subprocess saturates a core); OpenAI calls are
network-rate-bound. These are different resources with different natural
limits, so they get separate semaphores rather than one shared cap — capping
them together would either starve API throughput or oversubscribe the CPU.

Both are module-level singletons, safe to construct outside a running loop
(``asyncio.Semaphore`` no longer binds to a loop at construction time as of
the Python version this project requires, >=3.12).
"""

from __future__ import annotations

import asyncio
import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


FFMPEG_CONCURRENCY = _env_int("SHORTFORM_LAB_FFMPEG_CONCURRENCY", 4)
OPENAI_CONCURRENCY = _env_int("SHORTFORM_LAB_OPENAI_CONCURRENCY", 8)

ffmpeg_semaphore = asyncio.Semaphore(FFMPEG_CONCURRENCY)
openai_semaphore = asyncio.Semaphore(OPENAI_CONCURRENCY)
