"""Tests for deterministic planning and LLM fallback behavior."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.models import EditPlan
from shortform_lab.planner import DeterministicPlanner, LLMPlanner, plan_edits
from shortform_lab.transcribe import load_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"


def _transcript():
    return load_transcript(FIXTURE)


def test_deterministic_plan_structure():
    style = load_style_config("bold_creator")
    plan = DeterministicPlanner().plan(_transcript(), style, source_video="source.mp4")

    assert isinstance(plan, EditPlan)
    assert plan.export_width == style.export.width
    assert plan.export_fps == style.export.fps

    # Hook respects max_words and starts at 0.
    assert plan.hook.start_ms == 0
    assert len(plan.hook.text.split()) <= style.hook.max_words

    # Sentence-level captions: one per transcript segment.
    assert len(plan.captions) == 5

    # Visual counts follow the style.
    assert len(plan.overlays) <= style.visuals.text_card_count
    assert len(plan.punch_ins) <= style.visuals.punch_in_count
    assert plan.reason


def test_overlay_and_punchin_timestamps_exist_in_transcript():
    style = load_style_config("bold_creator")
    t = _transcript()
    plan = DeterministicPlanner().plan(t, style, source_video="source.mp4")

    starts = {s.start_ms for s in t.segments}
    for overlay in plan.overlays:
        assert overlay.start_ms in starts
    for punch in plan.punch_ins:
        assert punch.start_ms in starts
        assert punch.zoom > 1.0


def test_clean_captions_style_yields_fewer_visuals():
    style = load_style_config("clean_captions")
    plan = DeterministicPlanner().plan(_transcript(), style, source_video="source.mp4")
    assert len(plan.overlays) <= 1
    assert len(plan.punch_ins) <= 1


def test_plan_edits_defaults_to_deterministic():
    style = load_style_config("bold_creator")
    plan = plan_edits(_transcript(), style, source_video="source.mp4")
    assert isinstance(plan, EditPlan)


def test_llm_planner_falls_back_and_writes_error(tmp_path: Path, monkeypatch):
    style = load_style_config("bold_creator")
    planner = LLMPlanner()

    # Force the LLM call to blow up; the planner must fall back deterministically.
    def boom(*args, **kwargs):
        raise RuntimeError("no api key")

    monkeypatch.setattr(planner, "_call_llm", boom)
    error_path = tmp_path / "planner_error.txt"
    plan = planner.plan(_transcript(), style, source_video="source.mp4", error_path=error_path)

    assert isinstance(plan, EditPlan)
    assert error_path.is_file()
    assert "LLM planning failed" in error_path.read_text()


def test_llm_planner_parses_valid_json(tmp_path: Path, monkeypatch):
    style = load_style_config("bold_creator")
    planner = LLMPlanner()

    fake = (
        '{"source_video": "x", "hook": {"text": "Stop quitting early", '
        '"start_ms": 0, "end_ms": 2800}, '
        '"captions": [{"start_ms": 0, "end_ms": 2600, "text": "Most people quit early."}], '
        '"overlays": [], "punch_ins": [{"start_ms": 6400, "end_ms": 10200, "zoom": 1.2}], '
        '"export_width": 999, "export_height": 999, "export_fps": 99, '
        '"reason": "lead strong"}'
    )
    monkeypatch.setattr(planner, "_call_llm", lambda *a, **k: fake)
    plan = planner.plan(_transcript(), style, source_video="source.mp4")

    assert plan.hook.text == "Stop quitting early"
    # Export dims are pinned to the style, not whatever the model returned.
    assert plan.export_width == style.export.width
    assert plan.export_fps == style.export.fps
    assert plan.source_video == "source.mp4"
