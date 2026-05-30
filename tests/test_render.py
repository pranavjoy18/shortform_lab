"""Tests for caption file generation, the filter chain, and a real render."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.ffmpeg_tools import probe_video
from shortform_lab.models import (
    CaptionCue,
    EditPlan,
    Hook,
    PunchIn,
    VisualOverlay,
)
from shortform_lab.render import build_video_filter, render_video, write_captions_ass
from tests.conftest import ffmpeg_required


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
