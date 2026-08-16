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

import os
import textwrap
from dataclasses import dataclass
from pathlib import Path

from .models import CaptionCue, CaptionSettings, ColorGrade, EditPlan, GradeSpan, StyleConfig
from .timeline import Timeline

# Bundled caption fonts (assets/fonts/, a sibling of configs/) — real family
# names embedded in each file's name table, verified via its TrueType 'name'
# table (nameID 1). Passed to FFmpeg's ``subtitles`` filter as ``fontsdir`` so
# libass finds them without any system font install, keeping renders identical
# across machines. All OFL-licensed (license text bundled alongside).
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# Fallback font when a style doesn't specify one — a real bundled weight
# (not a synthetic bold), so captions look like a picked typeface, not a
# generic system default.
DEFAULT_FONT = "Poppins ExtraBold"

# ASS alignment uses numpad positions: 2=bottom-center, 5=middle-center, 8=top-center,
# 1=bottom-left (used for lower-third placement).
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
    """Write all burn-in text (hook, captions, overlays, lower-third) to one ASS file.

    In ``letterbox`` layout, ``source_size`` (the raw source WxH) lets captions
    straddle the video/bar seam and the hook sit in the top bar.
    """
    w, h = plan.export_width, plan.export_height
    font = style.captions.font_family or DEFAULT_FONT
    cap_size = style.captions.font_size
    cap_align = _ALIGN[style.captions.position]
    hook_size = int(round(cap_size * 1.3))
    card_size = int(round(cap_size * 0.95))
    word_big_size = int(round(cap_size * 1.6))
    lt_size = int(round(cap_size * 0.85))
    lt_sub_size = int(round(cap_size * 0.60))
    margin_v = max(80, h // 12)
    accent = _hex_to_ass(style.captions.highlight_color)

    cap_align_eff = cap_align
    cap_margin_v = margin_v
    hook_margin_v = margin_v
    if plan.export_layout == "letterbox" and source_size is not None:
        top_bar, band_h, _bottom_bar = _letterbox_bars(w, h, *source_size)
        band_bottom = top_bar + band_h
        overlap = cap_size // 3
        if cap_align == _ALIGN["bottom"]:
            cap_align_eff = _ALIGN["top"]
            cap_margin_v = max(0, band_bottom - overlap)
        elif cap_align == _ALIGN["top"]:
            cap_align_eff = _ALIGN["bottom"]
            cap_margin_v = max(0, (h - top_bar) - overlap)
        hook_margin_v = max(40, (top_bar - hook_size) // 2)

    # Lower-third sits in the bottom-left corner at a fixed margin from bottom.
    lt_margin_v = max(120, h // 8)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{cap_size},&H00FFFFFF,&H00000000,&H64000000,0,1,2,1,{cap_align_eff},60,60,{cap_margin_v},1
Style: Hook,{font},{hook_size},&H00FFFFFF,&H00000000,&H64000000,0,1,2,1,8,80,80,{hook_margin_v},1
Style: Card,{font},{card_size},&H00FFFFFF,&H00000000,&HAA000000,0,3,2,0,5,80,80,0,1
Style: WordBig,{font},{word_big_size},{accent},&H00000000,&HAA000000,0,3,3,1,5,80,80,0,1
Style: LowerThird,{font},{lt_size},&H00FFFFFF,&H00000000,&HBB000000,0,1,2,1,1,60,60,{lt_margin_v},1
Style: LowerThirdSub,{font},{lt_sub_size},&HCCFFFFFF,&H00000000,&HBB000000,0,1,1,0,1,60,60,{lt_margin_v + lt_size + 8},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Compute caption animation prefix once — same across all sentence cues.
    anim_in = style.captions.animation_in
    cap_anim = _animation_prefix(anim_in, w, h, cap_align_eff, cap_margin_v)

    lines: list[str] = []

    if plan.hook is not None:
        lines.append(
            _event("Hook", plan.hook.start_ms, plan.hook.end_ms, plan.hook.text,
                   max_chars=style.captions.max_chars_per_line)
        )

    for overlay in plan.overlays:
        if not overlay.text:
            continue
        align = _ALIGN.get(overlay.placement, 5)
        lines.append(
            _event("Card", overlay.start_ms, overlay.end_ms, overlay.text,
                   max_chars=style.captions.max_chars_per_line, align_override=align)
        )

    base = "&H00FFFFFF"
    for cue in plan.captions:
        if cue.words:
            lines.extend(
                _word_events(cue, style.captions, base=base, accent=accent,
                             animation_prefix=cap_anim if anim_in == "fade" else "")
            )
        else:
            lines.append(
                _event("Caption", cue.start_ms, cue.end_ms, cue.text,
                       max_chars=style.captions.max_chars_per_line,
                       animation_prefix=cap_anim)
            )

    if plan.lower_third is not None:
        lt = plan.lower_third
        lines.append(
            _event("LowerThird", lt.start_ms, lt.end_ms, lt.text,
                   max_chars=40, animation_prefix=r"{\fad(250,200)}")
        )
        if lt.subtext:
            lines.append(
                _event("LowerThirdSub", lt.start_ms, lt.end_ms, lt.subtext,
                       max_chars=50, animation_prefix=r"{\fad(300,200)}")
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def _eq_filter(color: ColorGrade, *, enable: str | None = None) -> str:
    """FFmpeg ``eq`` stage for a color grade, with optional time-gate expression."""
    f = (
        f"eq=contrast={color.contrast:.4f}:brightness={color.brightness:.4f}"
        f":saturation={color.saturation:.4f}:gamma={color.gamma:.4f}"
    )
    if enable:
        f += f":enable='{enable}'"
    return f


def build_video_filter(
    plan: EditPlan,
    captions_filename: str,
    *,
    color: ColorGrade | None = None,
    grade_spans: list[GradeSpan] | None = None,
    fontsdir: str | None = None,
) -> str:
    """Build the ``-vf`` chain: color grade, layout stage, optional punch-in, burn-in.

    Both layouts produce a full ``WxH`` canvas, so punch-in and the subtitle
    burn-in are identical between them — only the layout stage differs. Color
    grading is applied *first*, before the layout — so in letterbox the black
    bars added by ``pad`` stay pure black.

    When ``grade_spans`` are present they override the ambient ``color`` during
    their output-time windows using FFmpeg's ``enable=`` expression on ``eq``.
    The ambient grade fills the gaps (everywhere the spans are NOT active).
    Falls back to the simple global ``eq`` when no spans are present.
    """
    w, h = plan.export_width, plan.export_height
    parts: list[str] = []

    active_spans = [gs for gs in (grade_spans or []) if not gs.grade.is_identity()]

    if active_spans:
        # Build one enable-expression per span for output time (seconds).
        span_clauses = [
            f"between(t,{gs.start_ms / 1000:.3f},{gs.end_ms / 1000:.3f})"
            for gs in active_spans
        ]
        span_union = "+".join(span_clauses)
        # Ambient grade applies everywhere EXCEPT the span windows.
        if color is not None and not color.is_identity():
            parts.append(_eq_filter(color, enable=f"not({span_union})"))
        # Each span overrides with its own grade during its window.
        for gs, clause in zip(active_spans, span_clauses):
            parts.append(_eq_filter(gs.grade, enable=clause))
    elif color is not None and not color.is_identity():
        parts.append(_eq_filter(color))
    if plan.export_layout == "letterbox":
        # Fit the whole source, then pad black bars to the 9:16 canvas. FFmpeg
        # expressions center the scaled video, so no source dims are needed here.
        parts += [
            f"scale={w}:{h}:force_original_aspect_ratio=decrease",
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]
    else:
        # Fill the 9:16 frame: scale to cover, then center-crop to exact size.
        parts += [
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

    sub = f"subtitles={captions_filename}"
    if fontsdir is not None:
        # Points libass at the bundled fonts (assets/fonts/) so caption fonts
        # resolve identically on every machine, with no system font install.
        sub += f":fontsdir={fontsdir}"
    parts.append(sub)
    parts.append("format=yuv420p")
    return ",".join(parts)


def _audio_filter(*, normalize: bool, denoise: bool) -> str | None:
    """Build the audio filter chain string, or ``None`` when no filters are needed.

    Order: denoise first so loudnorm does not amplify the noise floor.
    ``afftdn`` params: nf=-25 (noise floor threshold), nr=10 (10 dB reduction),
    nt=w (white-noise type — broadband hiss/AC/fans).
    """
    parts: list[str] = []
    if denoise:
        parts.append("afftdn=nf=-25:nr=10:nt=w")
    if normalize:
        parts.append("loudnorm")
    return ",".join(parts) if parts else None


def build_concat_filtergraph(
    plan: EditPlan, vf_chain: str, *, has_audio: bool, normalize: bool, denoise: bool = False
) -> tuple[str, str | None]:
    """Build the ``-filter_complex`` that cuts to ``plan.keep_ranges`` then styles.

    When ``plan.transitions`` is populated (one entry per cut boundary), adjacent
    clips are joined with FFmpeg's ``xfade``/``acrossfade`` filters instead of
    a hard ``concat``. Otherwise falls back to plain ``concat`` (existing behaviour).

    xfade offset formula: offset_i = sum(D_j - T_j for j in 0..i-1), where D_j is
    clip j's duration and T_j is the transition duration at boundary j. This makes
    the cross-fade window start exactly when clip i-1 has T_i-1 seconds remaining.
    """
    n = len(plan.keep_ranges)
    parts: list[str] = []
    for i, r in enumerate(plan.keep_ranges):
        s, e = r.start_ms / 1000.0, r.end_ms / 1000.0
        parts.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")

    use_xfade = bool(plan.transitions) and len(plan.transitions) == n - 1

    if use_xfade:
        durations_s = [(r.end_ms - r.start_ms) / 1000.0 for r in plan.keep_ranges]
        offset = 0.0
        cur_v, cur_a = "[v0]", ("[a0]" if has_audio else None)
        for i in range(1, n):
            tr = plan.transitions[i - 1]
            td = tr.duration_ms / 1000.0
            offset += durations_s[i - 1] - td
            out_v = f"[xt{i}]" if i < n - 1 else "[vcat]"
            parts.append(
                f"{cur_v}[v{i}]xfade=transition={tr.transition_type}"
                f":duration={td:.3f}:offset={offset:.3f}{out_v}"
            )
            cur_v = out_v
            if has_audio and cur_a is not None:
                out_a = f"[at{i}]" if i < n - 1 else "[acat]"
                parts.append(f"{cur_a}[a{i}]acrossfade=d={td:.3f}:c1=tri:c2=tri{out_a}")
                cur_a = out_a
        v_label, a_label = cur_v, cur_a
    else:
        if has_audio:
            inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
            parts.append(f"{inputs}concat=n={n}:v=1:a=1[vcat][acat]")
            a_label: str | None = "[acat]"
        else:
            inputs = "".join(f"[v{i}]" for i in range(n))
            parts.append(f"{inputs}concat=n={n}:v=1:a=0[vcat]")
            a_label = None
        v_label = "[vcat]"

    parts.append(f"{v_label}{vf_chain}[vout]")

    audio_label: str | None = None
    if has_audio and a_label is not None:
        af = _audio_filter(normalize=normalize, denoise=denoise)
        if af:
            parts.append(f"{a_label}{af}[aout]")
            audio_label = "[aout]"
        else:
            audio_label = a_label

    return ";".join(parts), audio_label


def render_video(
    plan: EditPlan,
    input_path: Path,
    output_path: Path,
    style: StyleConfig,
    *,
    has_audio: bool,
    work_dir: Path | None = None,
    timeline: Timeline | None = None,
) -> RenderResult:
    """Render ``final.mp4`` from ``input_path`` according to ``plan``.

    Captions are written next to the output as ``captions.ass``. FFmpeg runs with
    its working directory set to that folder so the ``subtitles`` filter can use a
    bare filename and avoid filter-path escaping issues.

    ``timeline`` carries tracks read straight from the Timeline rather than the
    ``EditPlan`` (today: the color grade); when ``None`` the render is exactly as
    before.
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

    color = timeline.color if timeline is not None else None
    grade_spans = timeline.grade_spans if timeline is not None else []
    # Relative path (not absolute) for the same reason as the bare captions
    # filename: keeps the filtergraph argument free of characters (":" on
    # Windows drive letters) that collide with FFmpeg's filter-option syntax.
    fontsdir = os.path.relpath(FONTS_DIR, work_dir).replace(os.sep, "/")
    vf = build_video_filter(plan, captions_path.name, color=color, grade_spans=grade_spans, fontsdir=fontsdir)

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
        # result. Audio filters move into the graph since -af can't coexist with
        # a mapped filter_complex audio output.
        graph, audio_label = build_concat_filtergraph(
            plan, vf, has_audio=has_audio,
            normalize=style.audio.normalize, denoise=style.audio.denoise,
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
            af = _audio_filter(normalize=style.audio.normalize, denoise=style.audio.denoise)
            if af:
                cmd += ["-af", af]
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


def _animation_prefix(animation_in: str, w: int, h: int, align: int, margin_v: int) -> str:
    """Build the ASS override tag(s) that animate a caption on entry.

    ``fly_up`` uses ``\\move`` from a position 40px below/above the resting anchor
    to the resting anchor over 250ms, combined with a tail fade-out. The anchor
    position depends on the alignment (an2=bottom, an8=top, an5=center). ``fade``
    is a simple opacity fade-in / fade-out via ``\\fad``. ``none`` returns "".
    """
    if animation_in == "fade":
        return r"{\fad(200,100)}"
    if animation_in == "fly_up":
        x = w // 2
        if align == 2:  # bottom-center: anchor at bottom of text box
            y_end = h - margin_v
            y_start = y_end + 40
        elif align == 8:  # top-center: anchor at top of text box, drop in from above
            y_end = margin_v
            y_start = y_end - 40
        else:  # center
            y_end = h // 2
            y_start = y_end + 30
        return "{" + f"\\move({x},{y_start},{x},{y_end},0,250)\\fad(0,100)" + "}"
    return ""


def _event(
    style: str,
    start_ms: int,
    end_ms: int,
    text: str,
    *,
    max_chars: int,
    align_override: int | None = None,
    animation_prefix: str = "",
) -> str:
    wrapped = "\\N".join(textwrap.wrap(text.strip(), width=max_chars)) or text.strip()
    wrapped = _escape_ass(wrapped)
    an_tag = f"{{\\an{align_override}}}" if align_override is not None else ""
    return _dialogue(style, start_ms, end_ms, an_tag + animation_prefix + wrapped)


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


def _clean_word(text: str, *, uppercase: bool) -> str:
    """Strip, optionally upper-case, and neutralise braces in one word's text."""
    t = text.strip()
    if uppercase:
        t = t.upper()
    return _escape_ass(t)


def _word_events(
    cue: CaptionCue,
    settings: CaptionSettings,
    *,
    base: str,
    accent: str,
    animation_prefix: str = "",
) -> list[str]:
    """Render one caption cue's words per the configured ``word_animation``.

    ``animation_prefix`` (fade only — fly_up is skipped for word events since each
    word group is its own event and per-word fly-ins look disjointed).
    """
    upper = settings.uppercase
    if settings.word_animation == "karaoke":
        return [_karaoke_event(cue, base=base, accent=accent, uppercase=upper,
                               animation_prefix=animation_prefix)]
    if settings.word_animation == "one_word":
        text = animation_prefix + _clean_word(cue.text, uppercase=upper)
        return [_dialogue("WordBig", cue.start_ms, cue.end_ms, text)]
    return _active_word_events(cue, base=base, accent=accent, uppercase=upper)


def _active_word_events(cue: CaptionCue, *, base: str, accent: str, uppercase: bool) -> list[str]:
    """Keep the whole group on screen, recolouring the current word per event.

    Only recolours a word during its own span if that word is flagged
    ``emphasize`` (generative-path-only, cosmetic — see ``skills/caption.py``).
    An unflagged word never gets the accent, so a cue with no emphasized words
    renders as identical plain-text events throughout — no highlight cycling
    through words just because they happen to be the one being spoken.
    """
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
            parts.append(f"{{\\c{accent}}}{tok}{{\\c{base}}}" if j == i and w.emphasize else tok)
        events.append(_dialogue("Caption", start, end, " ".join(parts)))
    return events


def _karaoke_event(
    cue: CaptionCue, *, base: str, accent: str, uppercase: bool, animation_prefix: str = ""
) -> str:
    """One static line; ``\\kf`` sweeps the accent colour across words over time."""
    words = cue.words
    parts = [animation_prefix + f"{{\\1c{accent}\\2c{base}}}"]
    for i, w in enumerate(words):
        end = words[i + 1].start_ms if i + 1 < len(words) else cue.end_ms
        dur_cs = max(1, (end - w.start_ms) // 10)
        parts.append(f"{{\\kf{dur_cs}}}{_clean_word(w.text, uppercase=uppercase)} ")
    return _dialogue("Caption", cue.start_ms, cue.end_ms, "".join(parts))
