"""Turn an ``EditPlan`` into ``final.mp4`` via FFmpeg.

The renderer is deliberately a thin translation layer: it reads only what the
plan contains and emits FFmpeg arguments. All on-screen text (captions, the
hook, and quote-card overlays) is written into a single ASS subtitle file, so
the FFmpeg filtergraph stays small — a fill-crop to 9:16, an optional time-gated
punch-in zoom, then the ``subtitles`` burn-in.

Per the plan, only the first punch-in is rendered; any additional punch-ins
remain in ``edit_plan.json`` for inspection and future use.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from .models import EditPlan, StyleConfig

# libass resolves font names through fontconfig and substitutes a fallback if
# the exact family is missing, so this is a sensible, widely-available default.
DEFAULT_FONT = "FreeSans"

# ASS alignment uses numpad positions: 2=bottom-center, 5=middle-center, 8=top-center.
_ALIGN = {"bottom": 2, "center": 5, "top": 8}


@dataclass
class RenderResult:
    output_path: Path
    captions_path: Path


def write_captions_ass(plan: EditPlan, style: StyleConfig, path: Path) -> Path:
    """Write all burn-in text (hook, captions, overlays) to one ASS file."""
    w, h = plan.export_width, plan.export_height
    cap_size = style.captions.font_size
    cap_align = _ALIGN[style.captions.position]
    # The hook reads as a punchy banner; cards sit mid-frame like simple b-roll.
    hook_size = int(round(cap_size * 1.15))
    card_size = int(round(cap_size * 0.95))
    margin_v = max(80, h // 12)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{DEFAULT_FONT},{cap_size},&H00FFFFFF,&H00000000,&H64000000,1,1,3,1,{cap_align},60,60,{margin_v},1
Style: Hook,{DEFAULT_FONT},{hook_size},&H0000FFFF,&H00000000,&HB4000000,1,3,2,0,8,60,60,{margin_v},1
Style: Card,{DEFAULT_FONT},{card_size},&H00FFFFFF,&H00000000,&HAA000000,1,3,2,0,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []

    # Hook first (top banner over the opening seconds).
    lines.append(
        _event("Hook", plan.hook.start_ms, plan.hook.end_ms, plan.hook.text,
                max_chars=style.captions.max_chars_per_line)
    )

    # Quote/text cards (simulated b-roll, centered).
    for overlay in plan.overlays:
        if not overlay.text:
            continue
        align = _ALIGN.get(overlay.placement, 5)
        lines.append(
            _event("Card", overlay.start_ms, overlay.end_ms, overlay.text,
                    max_chars=style.captions.max_chars_per_line, align_override=align)
        )

    # Captions (bottom).
    for cue in plan.captions:
        lines.append(
            _event("Caption", cue.start_ms, cue.end_ms, cue.text,
                    max_chars=style.captions.max_chars_per_line)
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_video_filter(plan: EditPlan, captions_filename: str) -> str:
    """Build the ``-vf`` chain: fill-crop, optional punch-in, then subtitle burn-in."""
    w, h = plan.export_width, plan.export_height
    parts = [
        # Fill the 9:16 frame: scale to cover, then center-crop to exact size.
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}",
    ]

    # Only the first punch-in is rendered (others stay in the plan for inspection).
    if plan.punch_ins:
        punch = plan.punch_ins[0]
        s = punch.start_ms / 1000.0
        e = punch.end_ms / 1000.0
        z = punch.zoom
        # Time-gated zoom: crop a centered region of size (frame / zoom) during the
        # window, full frame otherwise, then scale back to the fixed export size.
        zf = f"if(between(t,{s:.3f},{e:.3f}),{z:.3f},1)"
        parts.append(
            f"crop=w='iw/({zf})':h='ih/({zf})':x='(iw-ow)/2':y='(ih-oh)/2'"
        )
        parts.append(f"scale={w}:{h}")

    parts.append(f"subtitles={captions_filename}")
    parts.append("format=yuv420p")
    return ",".join(parts)


def render_video(
    plan: EditPlan,
    input_path: Path,
    output_path: Path,
    style: StyleConfig,
    *,
    has_audio: bool,
    work_dir: Path | None = None,
) -> RenderResult:
    """Render ``final.mp4`` from ``input_path`` according to ``plan``.

    Captions are written next to the output as ``captions.ass``. FFmpeg runs with
    its working directory set to that folder so the ``subtitles`` filter can use a
    bare filename and avoid filter-path escaping issues.
    """
    work_dir = work_dir or output_path.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    captions_path = work_dir / "captions.ass"
    write_captions_ass(plan, style, captions_path)

    vf = build_video_filter(plan, captions_path.name)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path.resolve()),
        "-vf", vf,
        "-r", str(plan.export_fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
        if style.audio.normalize:
            cmd += ["-af", "loudnorm"]
    else:
        cmd += ["-an"]
    cmd += [str(output_path.resolve())]

    # Run inside work_dir so the bare captions filename resolves for the filter.
    _run_in(cmd, cwd=work_dir)

    if not output_path.is_file():
        raise RuntimeError(f"Render produced no file at {output_path}")
    return RenderResult(output_path=output_path, captions_path=captions_path)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _run_in(cmd: list[str], *, cwd: Path):
    import subprocess

    from .ffmpeg_tools import FFmpegError

    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=str(cwd))
    except FileNotFoundError as exc:
        raise FFmpegError(f"Command not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise FFmpegError(f"Render failed: exit {exc.returncode}\n{stderr}") from exc


def _ass_time(ms: int) -> str:
    """Format milliseconds as ASS ``H:MM:SS.cc`` (centiseconds)."""
    cs = ms // 10
    s, cs = divmod(cs, 100)
    m, s = divmod(s, 60)
    hgt, m = divmod(m, 60)
    return f"{hgt}:{m:02d}:{s:02d}.{cs:02d}"


def _event(
    style: str,
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    max_chars: int,
    align_override: int | None = None,
) -> str:
    wrapped = "\\N".join(textwrap.wrap(text.strip(), width=max_chars)) or text.strip()
    wrapped = _escape_ass(wrapped)
    prefix = f"{{\\an{align_override}}}" if align_override is not None else ""
    return (
        f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},{style},,0,0,0,,{prefix}{wrapped}"
    )


def _escape_ass(text: str) -> str:
    # Keep our intentional \N line breaks; neutralize stray braces that would be
    # read as ASS override blocks.
    return text.replace("{", "(").replace("}", ")")
