"""Tests for auto-detecting fill/letterbox layout from source resolution.

These are decision-logic tests: the full ``process()`` precedence-chain checks
monkeypatch out ``probe_video``/``render_video``/``ensure_ffmpeg_available`` so
they run without a real FFmpeg install or a real video file (a canned
``VideoInfo`` stands in for the probe), which is cheaper than the
``ffmpeg_required`` fixture pattern for testing pure wiring logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from shortform_lab import cli
from shortform_lab.config import style_explicitly_sets_layout
from shortform_lab.ffmpeg_tools import VideoInfo

runner = CliRunner()


# --- _auto_layout -----------------------------------------------------------


def test_auto_layout_landscape_is_letterbox():
    assert cli._auto_layout(1280, 720) == "letterbox"


def test_auto_layout_portrait_is_fill():
    assert cli._auto_layout(1080, 1920) == "fill"


def test_auto_layout_square_ties_to_fill():
    assert cli._auto_layout(1000, 1000) == "fill"


# --- style_explicitly_sets_layout -------------------------------------------


def test_style_explicitly_sets_layout_true_for_reels_letterbox():
    assert style_explicitly_sets_layout("reels_letterbox") is True


def test_style_explicitly_sets_layout_false_for_bold_creator():
    assert style_explicitly_sets_layout("bold_creator") is False


def test_style_explicitly_sets_layout_false_when_no_export_section(tmp_path: Path):
    p = tmp_path / "bare.yaml"
    p.write_text("name: bare\n", encoding="utf-8")
    assert style_explicitly_sets_layout("bare", styles_dir=tmp_path) is False


# --- full precedence chain via the CLI, without a real video ---------------


@pytest.fixture()
def fake_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Stub out everything FFmpeg-shaped so ``process()`` runs on a canned probe."""
    probed: dict[str, VideoInfo] = {}

    async def fake_probe_video(path: Path) -> VideoInfo:
        return probed["info"]

    async def fake_extract_audio(*args, **kwargs):
        raise AssertionError("extract_audio should not run when has_audio=False")

    async def fake_render_video(plan, source, dest, style, *, has_audio, work_dir, timeline=None):
        Path(dest).write_bytes(b"fake")

    monkeypatch.setattr(cli, "ensure_ffmpeg_available", lambda: None)
    monkeypatch.setattr(cli, "probe_video", fake_probe_video)
    monkeypatch.setattr(cli, "extract_audio", fake_extract_audio)
    monkeypatch.setattr(cli, "render_video", fake_render_video)

    def set_probe(width: int, height: int) -> None:
        probed["info"] = VideoInfo(
            width=width, height=height, duration_ms=5000, fps=30.0, has_audio=False
        )

    return set_probe


def _run(tmp_path: Path, *, style: str, extra_args: list[str] | None = None) -> dict:
    input_video = tmp_path / "input.mp4"
    input_video.write_bytes(b"not a real video")
    out_dir = tmp_path / "out"
    args = [
        "process", str(input_video),
        "--style", style,
        "--transcript", "tests/fixtures/transcript_sample.json",
        "--output-dir", str(out_dir),
        *(extra_args or []),
    ]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    return json.loads((out_dir / "edit_plan.json").read_text(encoding="utf-8"))


def test_auto_detect_picks_letterbox_for_landscape_when_style_silent(fake_pipeline, tmp_path: Path):
    fake_pipeline(1280, 720)
    plan = _run(tmp_path, style="bold_creator")
    assert plan["export_layout"] == "letterbox"


def test_auto_detect_picks_fill_for_portrait_when_style_silent(fake_pipeline, tmp_path: Path):
    fake_pipeline(1080, 1920)
    plan = _run(tmp_path, style="bold_creator")
    assert plan["export_layout"] == "fill"


def test_style_explicit_letterbox_wins_over_landscape_autodetect(fake_pipeline, tmp_path: Path):
    fake_pipeline(1280, 720)
    plan = _run(tmp_path, style="reels_letterbox")
    assert plan["export_layout"] == "letterbox"


def test_style_explicit_letterbox_unaffected_by_portrait_source(fake_pipeline, tmp_path: Path):
    fake_pipeline(1080, 1920)
    plan = _run(tmp_path, style="reels_letterbox")
    assert plan["export_layout"] == "letterbox"


def test_cli_flag_overrides_autodetect_on_silent_style(fake_pipeline, tmp_path: Path):
    fake_pipeline(1280, 720)  # would auto-detect to letterbox
    plan = _run(tmp_path, style="bold_creator", extra_args=["--layout", "fill"])
    assert plan["export_layout"] == "fill"


def test_cli_flag_overrides_styles_own_explicit_layout(fake_pipeline, tmp_path: Path):
    fake_pipeline(1280, 720)
    plan = _run(tmp_path, style="reels_letterbox", extra_args=["--layout", "fill"])
    assert plan["export_layout"] == "fill"
