"""Tests for deterministic planning and LLM fallback behavior."""

from pathlib import Path

from shortform_lab.config import load_style_config
from shortform_lab.models import EditPlan, Transcript, TranscriptSegment
from shortform_lab.planner import DeterministicPlanner, build_captions, plan_edits
from shortform_lab.transcribe import load_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"
WORDS_FIXTURE = Path(__file__).parent / "fixtures" / "transcript_words.json"


def _transcript():
    return load_transcript(FIXTURE)


def _words_transcript():
    return load_transcript(WORDS_FIXTURE)


def test_deterministic_plan_structure():
    style = load_style_config("bold_creator")
    style.hook.enabled = True          # hook/overlays are off by default; enable
    style.visuals.overlays_enabled = True  # to exercise the full plan structure
    # debug=True: the deterministic hook is extractive-only and gated behind
    # --debug (real hook copy is the generative path's job).
    plan = DeterministicPlanner().plan(_transcript(), style, source_video="source.mp4", debug=True)

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


def test_disabled_style_omits_hook_and_overlays():
    # bold_creator ships with hook + overlays off: they are absent from the plan
    # entirely (gated in the planner), not merely skipped at render time.
    style = load_style_config("bold_creator")
    plan = DeterministicPlanner().plan(_transcript(), style, source_video="source.mp4")
    assert plan.hook is None
    assert plan.overlays == []
    assert plan.punch_ins  # punch-ins are unaffected


def test_overlay_and_punchin_timestamps_exist_in_transcript():
    style = load_style_config("bold_creator")
    style.visuals.overlays_enabled = True
    t = _transcript()
    plan = DeterministicPlanner().plan(t, style, source_video="source.mp4")

    starts = {s.start_ms for s in t.segments}
    assert plan.overlays  # enabled, so present
    for overlay in plan.overlays:
        assert overlay.start_ms in starts
    for punch in plan.punch_ins:
        assert punch.start_ms in starts
        assert punch.zoom > 1.0


def test_clean_captions_style_yields_fewer_visuals():
    style = load_style_config("clean_captions")
    style.visuals.overlays_enabled = True
    plan = DeterministicPlanner().plan(_transcript(), style, source_video="source.mp4")
    assert len(plan.overlays) <= 1
    assert len(plan.punch_ins) <= 1


async def test_plan_edits_defaults_to_deterministic():
    style = load_style_config("bold_creator")
    plan = await plan_edits(_transcript(), style, source_video="source.mp4")
    assert isinstance(plan, EditPlan)


def test_build_captions_word_mode_uses_real_word_timings():
    style = load_style_config("word_pop")  # active_word, group of 4
    cues = build_captions(_words_transcript(), style)
    # First segment has 4 words -> one group of 4; second has 3 -> one group of 3.
    assert len(cues) == 2
    assert [w.text for w in cues[0].words] == ["Most", "people", "quit", "early"]
    assert cues[0].start_ms == 0 and cues[0].end_ms == 1600


def test_build_captions_synthesizes_words_when_absent():
    style = load_style_config("word_pop")
    # The sample fixture is segment-only; word timings must be synthesized.
    cues = build_captions(_transcript(), style)
    assert cues, "expected word-group cues"
    for cue in cues:
        assert cue.words, "each word-mode cue must carry word timings"
        # Synthesized words stay within their cue span and keep order.
        assert cue.words[0].start_ms >= cue.start_ms
        assert cue.words[-1].end_ms <= cue.end_ms
    # Groups never exceed the configured size.
    assert max(len(c.words) for c in cues) <= style.captions.max_words_per_group


def test_build_captions_one_word_yields_single_word_cues():
    style = load_style_config("one_word")
    cues = build_captions(_words_transcript(), style)
    assert all(len(c.words) == 1 for c in cues)
    assert [c.text for c in cues] == ["Most", "people", "quit", "early",
                                      "Consistency", "beats", "intensity"]


def test_build_captions_sentence_mode_has_no_words():
    style = load_style_config("bold_creator")
    cues = build_captions(_transcript(), style)
    assert len(cues) == 5
    assert all(c.words == [] for c in cues)


def test_deterministic_word_style_plan_carries_word_cues():
    style = load_style_config("word_pop")
    plan = DeterministicPlanner().plan(_words_transcript(), style, source_video="source.mp4")
    assert all(c.words for c in plan.captions)


async def test_plan_edits_tightens_when_enabled():
    style = load_style_config("bold_creator")
    style.tighten.enabled = True
    # Two phrases with a 3s dead-air gap between them.
    t = Transcript(segments=[
        TranscriptSegment(start_ms=0, end_ms=1000, text="Hello there friend."),
        TranscriptSegment(start_ms=4000, end_ms=5000, text="Welcome back everyone."),
    ])
    plan = await plan_edits(t, style, source_video="s.mp4", source_duration_ms=5000)
    assert plan.keep_ranges, "tightening should produce a cut list"
    # Captions are in output time: they start at 0 and the gap is compressed.
    assert plan.captions[0].start_ms == 0
    assert plan.captions[-1].start_ms < 4000


async def test_plan_edits_no_tighten_leaves_keep_ranges_empty():
    style = load_style_config("bold_creator")  # tighten disabled by default
    plan = await plan_edits(_transcript(), style, source_video="s.mp4")
    assert plan.keep_ranges == []


def test_plan_pins_export_layout_from_style():
    fill = DeterministicPlanner().plan(
        _transcript(), load_style_config("bold_creator"), source_video="s.mp4"
    )
    box = DeterministicPlanner().plan(
        _transcript(), load_style_config("reels_letterbox"), source_video="s.mp4"
    )
    assert fill.export_layout == "fill"
    assert box.export_layout == "letterbox"


async def test_plan_edits_llm_falls_back_to_deterministic(tmp_path: Path, monkeypatch):
    """When the LLM call blows up, plan_edits still returns a valid plan and logs."""
    style = load_style_config("bold_creator")

    def boom(self, *args, **kwargs):
        raise RuntimeError("no api key")

    # use_llm=True routes through AgenticOrchestrator (LLMOrchestrator is the
    # legacy one-shot class, no longer wired to plan_timeline/plan_edits) — patch
    # the class actually used, deterministically, instead of relying on an
    # incidental real failure (no API key / no `openai` package) to trigger the
    # fallback path.
    monkeypatch.setattr("shortform_lab.llm_orchestrator.AgenticOrchestrator._run", boom)
    error_path = tmp_path / "planner_error.txt"
    plan = await plan_edits(
        _transcript(), style, source_video="source.mp4", use_llm=True, error_path=error_path
    )

    assert isinstance(plan, EditPlan)
    assert error_path.is_file()
    assert error_path.read_text()  # any non-empty error message is fine