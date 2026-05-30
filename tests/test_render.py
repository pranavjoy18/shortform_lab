"""Tests for caption file generation, the filter chain, and a real render."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.ffmpeg_tools import probe_video
from shortform_lab.models import (
    CaptionCue,
    EditPlan,
    Hook,
    PunchIn,
    TimeRange,
    VisualOverlay,
    WordTiming,
)
from shortform_lab.render import (
    _letterbox_bars,
    build_concat_filtergraph,
    build_video_filter,
    render_video,
    write_captions_ass,
)
from tests.conftest import ffmpeg_required


def _word_plan() -> EditPlan:
    words = [
        WordTiming(start_ms=0, end_ms=400, text="Most"),
        WordTiming(start_ms=400, end_ms=800, text="people"),
        WordTiming(start_ms=800, end_ms=1200, text="quit"),
    ]
    return EditPlan(
        source_video="source.mp4",
        hook=Hook(text="Watch this now", start_ms=0, end_ms=2500),
        captions=[CaptionCue(start_ms=0, end_ms=1200, text="Most people quit", words=words)],
        export_width=1080,
        export_height=1920,
        export_fps=30,
    )


def _plan() -> EditPlan:
    return EditPlan(
        source_video="source.mp4",
        hook=Hook(text="Watch this now", start_ms=0, end_ms=2500),
        captions=[
            CaptionCue(start_ms=0, end_ms=1500, text="A first caption line that is fairly long."),
            CaptionCue(start_ms=1500, end_ms=2900, text="Second line here."),
        ],
        overlays=[VisualOverlay(kind="quote_card", start_ms=1000, end_ms=2000, text="Key idea", placement="center")],
        punch_ins=[
            PunchIn(start_ms=1000, end_ms=2000, zoom=1.3),
            PunchIn(start_ms=2000, end_ms=2800, zoom=1.2),
        ],
        export_width=1080,
        export_height=1920,
        export_fps=30,
    )


def test_write_captions_ass_contains_all_text(tmp_path: Path):
    # The renderer draws whatever the plan holds; _plan() includes a hook + card.
    style = load_style_config("bold_creator")
    path = write_captions_ass(_plan(), style, tmp_path / "captions.ass")
    content = path.read_text()
    assert "[V4+ Styles]" in content
    assert "PlayResX: 1080" in content
    assert "Watch this now" in content     # hook
    assert "Key idea" in content           # overlay
    assert "Second line here." in content  # caption
    # Three event styles present.
    assert "Hook," in content and "Caption," in content and "Card," in content


def test_render_omits_hook_and_overlays_absent_from_plan(tmp_path: Path):
    # A plan with no hook / no overlays (what a disabled style produces) renders
    # neither — the gating lives in the plan, not the renderer.
    plan = _plan()
    plan.hook = None
    plan.overlays = []
    content = write_captions_ass(plan, load_style_config("bold_creator"), tmp_path / "c.ass").read_text()
    assert "Watch this now" not in content
    assert "Key idea" not in content
    # The style definitions still ship, so re-enabling needs no code.
    assert "Style: Hook," in content and "Style: Card," in content


def test_ass_dialogue_text_has_no_leading_comma(tmp_path: Path):
    """Regression: the [Events] Format must match Dialogue fields exactly, or the
    Text column inherits a stray leading comma from a misaligned Name field."""
    style = load_style_config("bold_creator")
    path = write_captions_ass(_plan(), style, tmp_path / "captions.ass")
    for line in path.read_text().splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Text is everything after the first 9 commas (10 fields precede it).
        text = line.split(",", 9)[-1].lstrip("\\")  # tolerate an {\anN} override
        assert not text.lstrip().startswith(","), f"stray leading comma in: {line}"


def test_build_video_filter_includes_punchin_and_subtitles():
    vf = build_video_filter(_plan(), "captions.ass")
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in vf
    assert "crop=1080:1920" in vf
    assert "subtitles=captions.ass" in vf
    # Only the first punch-in (zoom 1.3) is rendered.
    assert "1.300" in vf
    assert "format=yuv420p" in vf


def test_build_video_filter_without_punchins():
    plan = _plan()
    plan.punch_ins = []
    vf = build_video_filter(plan, "captions.ass")
    assert "between(t" not in vf


def test_build_video_filter_letterbox_fits_and_pads():
    plan = _plan()
    plan.export_layout = "letterbox"
    plan.punch_ins = []
    vf = build_video_filter(plan, "captions.ass")
    assert "force_original_aspect_ratio=decrease" in vf
    assert "pad=1080:1920" in vf
    assert "crop=1080:1920" not in vf            # not the fill path
    assert "subtitles=captions.ass" in vf
    assert "format=yuv420p" in vf


def test_letterbox_bars_for_16x9_source():
    top, band, bottom = _letterbox_bars(1080, 1920, 640, 360)
    assert band == 608                            # 360 * (1080/640)
    assert top == 656 and bottom == 656           # (1920 - 608) / 2
    assert top + band + bottom == 1920


def test_letterbox_anchors_captions_just_below_video(tmp_path: Path):
    style = load_style_config("reels_letterbox")  # letterbox, position: bottom
    plan = _word_plan()
    plan.export_layout = "letterbox"
    box = write_captions_ass(
        plan, style, tmp_path / "box.ass", source_size=(640, 360)
    ).read_text()

    def caption_fields(content: str) -> tuple[int, int]:
        line = next(ln for ln in content.splitlines() if ln.startswith("Style: Caption,"))
        fields = line[len("Style: "):].split(",")
        return int(fields[10]), int(fields[13])  # (Alignment, MarginV)

    align, margin_v = caption_fields(box)
    top_bar, band_h, _ = _letterbox_bars(1080, 1920, 640, 360)
    band_bottom = top_bar + band_h  # 656 + 608 = 1264
    cap_size = style.captions.font_size
    # Captions are top-anchored (8) and sit *on* the seam: the text top is just
    # above the video's bottom edge (slight overlap), not floating in the bar.
    assert align == 8
    assert band_bottom - cap_size <= margin_v < band_bottom
    assert margin_v == band_bottom - cap_size // 3


def test_active_word_emits_per_word_highlight_events(tmp_path: Path):
    style = load_style_config("word_pop")  # active_word, uppercase
    content = write_captions_ass(_word_plan(), style, tmp_path / "c.ass").read_text()
    # One Dialogue per word, each recolouring the active word with the accent.
    dialogues = [ln for ln in content.splitlines() if ln.startswith("Dialogue:")]
    word_events = [ln for ln in dialogues if "\\c&H" in ln]
    assert len(word_events) == 3            # three words -> three events
    assert "&H0000E0FF" in content          # #FFE000 -> ASS accent colour
    assert "MOST" in content                # uppercase applied


def test_karaoke_emits_kf_sweep(tmp_path: Path):
    style = load_style_config("karaoke")
    content = write_captions_ass(_word_plan(), style, tmp_path / "c.ass").read_text()
    assert "\\kf" in content                # karaoke fill timing present
    assert "\\1c" in content and "\\2c" in content  # sung/unsung colours set


def test_one_word_uses_centered_big_style(tmp_path: Path):
    style = load_style_config("one_word")
    content = write_captions_ass(_word_plan(), style, tmp_path / "c.ass").read_text()
    assert "Style: WordBig" in content
    assert ",WordBig,," in content          # the caption renders with WordBig


@ffmpeg_required
def test_render_word_style_produces_video(sample_video: Path, tmp_path: Path):
    style = load_style_config("word_pop")
    out = tmp_path / "final.mp4"
    render_video(_word_plan(), sample_video, out, style, has_audio=True, work_dir=tmp_path)
    info = probe_video(out)
    assert info.width == 1080 and info.height == 1920


def test_concat_filtergraph_trims_and_concats():
    plan = _plan()
    plan.keep_ranges = [TimeRange(start_ms=0, end_ms=1000), TimeRange(start_ms=2000, end_ms=3000)]
    graph, audio = build_concat_filtergraph(
        plan, "scale=1080:1920,subtitles=captions.ass", has_audio=True, normalize=True
    )
    assert "trim=start=0.000:end=1.000" in graph
    assert "atrim=start=2.000:end=3.000" in graph
    assert "concat=n=2:v=1:a=1[cv][ca]" in graph
    assert "[cv]scale=1080:1920,subtitles=captions.ass[vout]" in graph
    assert "loudnorm[aout]" in graph and audio == "[aout]"


def test_concat_filtergraph_without_audio():
    plan = _plan()
    plan.keep_ranges = [TimeRange(start_ms=0, end_ms=1000)]
    graph, audio = build_concat_filtergraph(
        plan, "scale=1,subtitles=c.ass", has_audio=False, normalize=False
    )
    assert "concat=n=1:v=1:a=0[cv]" in graph
    assert "atrim" not in graph
    assert audio is None


@ffmpeg_required
def test_render_with_cuts_shortens_video(sample_video: Path, tmp_path: Path):
    style = load_style_config("bold_creator")
    plan = _plan()
    # Sample is 3s; keep ~1.8s across two spans.
    plan.keep_ranges = [TimeRange(start_ms=0, end_ms=1000), TimeRange(start_ms=2000, end_ms=2800)]
    out = tmp_path / "final.mp4"
    render_video(plan, sample_video, out, style, has_audio=True, work_dir=tmp_path)
    info = probe_video(out)
    assert info.width == 1080 and info.height == 1920
    assert info.duration_ms < 2500  # clearly shorter than the 3s source


@ffmpeg_required
def test_render_letterbox_produces_vertical_video(sample_video: Path, tmp_path: Path):
    style = load_style_config("reels_letterbox")
    plan = _word_plan()
    plan.export_layout = "letterbox"
    out = tmp_path / "final.mp4"
    render_video(plan, sample_video, out, style, has_audio=True, work_dir=tmp_path)
    info = probe_video(out)
    assert info.width == 1080 and info.height == 1920


@ffmpeg_required
def test_render_produces_vertical_video(sample_video: Path, tmp_path: Path):
    style = load_style_config("bold_creator")
    out = tmp_path / "final.mp4"
    result = render_video(
        _plan(), sample_video, out, style, has_audio=True, work_dir=tmp_path
    )
    assert result.output_path.is_file()
    assert result.captions_path.is_file()

    info = probe_video(out)
    assert info.width == 1080
    assert info.height == 1920
    assert info.has_audio is True
