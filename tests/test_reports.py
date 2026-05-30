"""Tests for review.md generation."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.models import CaptionCue, EditPlan, Hook, PunchIn, VisualOverlay
from shortform_lab.reports import write_review


def _plan() -> EditPlan:
    return EditPlan(
        source_video="source.mp4",
        hook=Hook(text="Stop quitting early", start_ms=0, end_ms=2800),
        captions=[
            CaptionCue(start_ms=0, end_ms=1500, text="One"),
            CaptionCue(start_ms=1500, end_ms=3000, text="Two"),
        ],
        overlays=[VisualOverlay(kind="quote_card", start_ms=1000, end_ms=2000, text="Big idea")],
        punch_ins=[PunchIn(start_ms=2000, end_ms=3000, zoom=1.2)],
        export_width=1080,
        export_height=1920,
        export_fps=30,
        reason="Lead with the strongest line.",
    )


def test_write_review_includes_key_fields(tmp_path: Path):
    style = load_style_config("bold_creator")
    out = tmp_path / "review.md"
    write_review(
        _plan(), style,
        source_name="raw.mp4",
        final_path=tmp_path / "final.mp4",
        out_path=out,
        used_llm=False,
    )
    text = out.read_text()
    assert "raw.mp4" in text
    assert "bold_creator" in text
    assert "Stop quitting early" in text       # hook
    assert "Big idea" in text                  # overlay
    assert "2 caption cue(s)" in text          # caption count
    assert "Lead with the strongest line." in text  # reason
    assert "deterministic planner" in text
    assert "Known limitations" in text


def test_write_review_marks_llm_fallback(tmp_path: Path):
    style = load_style_config("bold_creator")
    out = tmp_path / "review.md"
    write_review(
        _plan(), style,
        source_name="raw.mp4",
        final_path=tmp_path / "final.mp4",
        out_path=out,
        used_llm=True,
        llm_failed=True,
    )
    text = out.read_text()
    assert "planner_error.txt" in text
