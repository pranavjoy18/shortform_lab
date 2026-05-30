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

from .models import CaptionCue, CaptionSettings, EditPlan, StyleConfig

# libass resolves font names through fontconfig and substitutes a fallback if
# the exact family is missing, so this is a sensible, widely-available default.
DEFAULT_FONT = "FreeSans"

# ASS alignment uses numpad positions: 2=bottom-center, 5=middle-center, 8=top-center.
_ALIGN = {"bottom": 2, "center": 5, "top": 8}


@dataclass
class RenderResult:
    output_path: Path
    captions_path: Path


def write_captions_ass(
    plan: EditPlan,
    style: StyleConfig,
    path: Path,
    *,
    source_size: tuple[int, int] | None = None,
) -> Path:
    """Write all burn-in text (hook, captions, overlays) to one ASS file.

    In ``letterbox`` layout, ``source_size`` (the raw source WxH) lets captions
    straddle the video/bar seam (sitting on the boundary, dipping slightly onto
    the video) and the hook sit in the top bar. Without it (or in ``fill`` layout)
    text sits over the video with the default margin.
    """
    w, h = plan.export_width, plan.export_height
    cap_size = style.captions.font_size
    cap_align = _ALIGN[style.captions.position]
    # The hook reads as a bold accent sticker; cards sit mid-frame like b-roll.
    hook_size = int(round(cap_size * 1.3))
    card_size = int(round(cap_size * 0.95))
    # The one-word look needs a large, centered word.
    word_big_size = int(round(cap_size * 1.6))
    margin_v = max(80, h // 12)
    accent = _hex_to_ass(style.captions.highlight_color)
    hook_text_color = _text_on_accent(style.captions.highlight_color)

    # Default (fill, or letterbox without source dims): text over the video.
    cap_align_eff = cap_align
    cap_margin_v = margin_v
    hook_margin_v = margin_v
    if plan.export_layout == "letterbox" and source_size is not None:
        top_bar, band_h, _bottom_bar = _letterbox_bars(w, h, *source_size)
        band_bottom = top_bar + band_h
        # Sit the caption *on* the video/bar seam: it rests on the boundary and
        # dips slightly onto the video, with extra lines growing into the bar.
        overlap = cap_size // 3
        if cap_align == _ALIGN["bottom"]:
            cap_align_eff = _ALIGN["top"]            # grows down into the bottom bar
            cap_margin_v = max(0, band_bottom - overlap)
        elif cap_align == _ALIGN["top"]:
            cap_align_eff = _ALIGN["bottom"]         # grows up into the top bar
            cap_margin_v = max(0, (h - top_bar) - overlap)
        # The hook stays fully inside the top bar (centered), never dipping onto
        # the video, so it can't land on the speaker's head.
        hook_margin_v = max(40, (top_bar - hook_size) // 2)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{DEFAULT_FONT},{cap_size},&H00FFFFFF,&H00000000,&H64000000,1,1,3,1,{cap_align_eff},60,60,{cap_margin_v},1
Style: Hook,{DEFAULT_FONT},{hook_size},{hook_text_color},{accent},&H64000000,1,3,8,3,8,80,80,{hook_margin_v},1
Style: Card,{DEFAULT_FONT},{card_size},&H00FFFFFF,&H00000000,&HAA000000,1,3,2,0,5,80,80,0,1
Style: WordBig,{DEFAULT_FONT},{word_big_size},{accent},&H00000000,&HAA000000,1,3,3,1,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []

    # The renderer draws exactly what the plan contains; the hook/overlay gating
    # lives in the planner (a disabled feature is simply absent from the plan).

    # Hook first (top banner over the opening seconds), if the plan has one.
    if plan.hook is not None:
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

    # Captions (bottom). A cue carrying per-word timings animates per the style's
    # word_animation; a plain cue (sentence mode) renders as one static line.
    base = "&H00FFFFFF"
    for cue in plan.captions:
        if cue.words:
            lines.extend(
                _word_events(cue, style.captions, base=base, accent=accent)
            )
        else:
            lines.append(
                _event("Caption", cue.start_ms, cue.end_ms, cue.text,
                        max_chars=style.captions.max_chars_per_line)
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def build_video_filter(plan: EditPlan, captions_filename: str) -> str:
    """Build the ``-vf`` chain: layout stage, optional punch-in, then subtitle burn-in.

    Both layouts produce a full ``WxH`` canvas, so punch-in and the subtitle
    burn-in are identical between them — only the first stage differs.
    """
    w, h = plan.export_width, plan.export_height
    if plan.export_layout == "letterbox":
        # Fit the whole source, then pad black bars to the 9:16 canvas. FFmpeg
        # expressions center the scaled video, so no source dims are needed here.
        parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]
    else:
        # Fill the 9:16 frame: scale to cover, then center-crop to exact size.
        parts = [
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


def build_concat_filtergraph(
    plan: EditPlan, vf_chain: str, *, has_audio: bool, normalize: bool
) -> tuple[str, str | None]:
    """Build the ``-filter_complex`` that cuts to ``plan.keep_ranges`` then styles.

    Each kept span is trimmed and PTS-reset, the spans are concatenated, and the
    normal vf chain runs on the concatenated video to ``[vout]``. Returns the
    graph and the audio output label to map (``None`` when there is no audio).
    """
    n = len(plan.keep_ranges)
    parts: list[str] = []
    for i, r in enumerate(plan.keep_ranges):
        s, e = r.start_ms / 1000.0, r.end_ms / 1000.0
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")

    if has_audio:
        inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
        parts.append(f"{inputs}concat=n={n}:v=1:a=1[cv][ca]")
    else:
        inputs = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{inputs}concat=n={n}:v=1:a=0[cv]")

    parts.append(f"[cv]{vf_chain}[vout]")

    audio_label: str | None = None
    if has_audio:
        if normalize:
            parts.append("[ca]loudnorm[aout]")
            audio_label = "[aout]"
        else:
            audio_label = "[ca]"

    return ";".join(parts), audio_label


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

    # Letterbox needs the source aspect to place captions inside the black bars.
    source_size: tuple[int, int] | None = None
    if plan.export_layout == "letterbox":
        from .ffmpeg_tools import probe_video

        info = probe_video(input_path)
        source_size = (info.width, info.height)

    captions_path = work_dir / "captions.ass"
    write_captions_ass(plan, style, captions_path, source_size=source_size)

    vf = build_video_filter(plan, captions_path.name)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path.resolve()),
    ]
    video_codec = [
        "-r", str(plan.export_fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
    ]

    if plan.keep_ranges:
        # Cut: trim each kept span, concat, then run the normal vf chain on the
        # result. loudnorm moves into the graph since -af can't coexist with a
        # mapped filter_complex audio output.
        graph, audio_label = build_concat_filtergraph(
            plan, vf, has_audio=has_audio, normalize=style.audio.normalize
        )
        cmd += ["-filter_complex", graph, "-map", "[vout]"]
        if has_audio:
            cmd += ["-map", audio_label]
        cmd += video_codec
        cmd += (["-c:a", "aac", "-b:a", "128k"] if has_audio else ["-an"])
    else:
        cmd += ["-vf", vf]
        cmd += video_codec
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


def _dialogue(style: str, start_ms: int, end_ms: int, text: str) -> str:
    """Format one ASS Dialogue line from already-assembled (escaped) text."""
    return f"Dialogue: 0,{_ass_time(start_ms)},{_ass_time(end_ms)},{style},,0,0,0,,{text}"


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
    return _dialogue(style, start_ms, end_ms, prefix + wrapped)


def _escape_ass(text: str) -> str:
    # Keep our intentional \N line breaks; neutralize stray braces that would be
    # read as ASS override blocks.
    return text.replace("{", "(").replace("}", ")")


# --------------------------------------------------------------------------- #
# Word-level animated captions
# --------------------------------------------------------------------------- #
def _letterbox_bars(export_w: int, export_h: int, src_w: int, src_h: int) -> tuple[int, int, int]:
    """Return ``(top_bar, band_h, bottom_bar)`` heights for a fit-and-pad layout.

    The source is scaled to fit entirely (``min`` scale factor); the leftover
    vertical space becomes the black bars. For a landscape source on a 9:16
    canvas these are the top/bottom bars where captions can live.
    """
    scale = min(export_w / src_w, export_h / src_h)
    band_h = round(src_h * scale)
    top_bar = (export_h - band_h) // 2
    bottom_bar = export_h - band_h - top_bar
    return top_bar, band_h, bottom_bar


def _hex_to_ass(hex_color: str) -> str:
    """Convert ``#RRGGBB`` to an opaque ASS colour ``&H00BBGGRR``."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _text_on_accent(hex_color: str) -> str:
    """Pick a readable ASS text colour (black or white) for text on the accent box."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return "&H00000000"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "&H00000000" if luminance > 140 else "&H00FFFFFF"


def _clean_word(text: str, *, uppercase: bool) -> str:
    """Strip, optionally upper-case, and neutralise braces in one word's text."""
    t = text.strip()
    if uppercase:
        t = t.upper()
    return _escape_ass(t)


def _word_events(
    cue: CaptionCue, settings: CaptionSettings, *, base: str, accent: str
) -> list[str]:
    """Render one caption cue's words per the configured ``word_animation``."""
    upper = settings.uppercase
    if settings.word_animation == "karaoke":
        return [_karaoke_event(cue, base=base, accent=accent, uppercase=upper)]
    if settings.word_animation == "one_word":
        return [
            _dialogue("WordBig", cue.start_ms, cue.end_ms, _clean_word(cue.text, uppercase=upper))
        ]
    return _active_word_events(cue, base=base, accent=accent, uppercase=upper)


def _active_word_events(cue: CaptionCue, *, base: str, accent: str, uppercase: bool) -> list[str]:
    """Keep the whole group on screen, recolouring the current word per event."""
    words = cue.words
    events: list[str] = []
    for i, w in enumerate(words):
        start = w.start_ms
        end = words[i + 1].start_ms if i + 1 < len(words) else cue.end_ms
        if end <= start:  # guard against zero/negative spans
            end = max(w.end_ms, start + 1)
        parts = []
        for j, ww in enumerate(words):
            tok = _clean_word(ww.text, uppercase=uppercase)
            parts.append(f"{{\\c{accent}}}{tok}{{\\c{base}}}" if j == i else tok)
        events.append(_dialogue("Caption", start, end, " ".join(parts)))
    return events


def _karaoke_event(cue: CaptionCue, *, base: str, accent: str, uppercase: bool) -> str:
    """One static line; ``\\kf`` sweeps the accent colour across words over time."""
    words = cue.words
    # Inline primary (sung) = accent, secondary (unsung) = base.
    parts = [f"{{\\1c{accent}\\2c{base}}}"]
    for i, w in enumerate(words):
        end = words[i + 1].start_ms if i + 1 < len(words) else cue.end_ms
        dur_cs = max(1, (end - w.start_ms) // 10)
        parts.append(f"{{\\kf{dur_cs}}}{_clean_word(w.text, uppercase=uppercase)} ")
    return _dialogue("Caption", cue.start_ms, cue.end_ms, "".join(parts))
