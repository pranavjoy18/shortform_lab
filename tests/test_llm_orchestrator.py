"""Tests for the LLM orchestrator and the toolbox it drives.

The LLM call is always mocked (offline); these verify that a structured skill plan
is turned into a coherence-validated Timeline, and that any bad plan falls back to
the deterministic orchestrator.
"""

from pathlib import Path

import pytest

from shortform_lab.config import load_style_config
from shortform_lab.llm_orchestrator import LLMOrchestrator
from shortform_lab.models import Transcript, TranscriptSegment
from shortform_lab.timeline import MediaRef
from shortform_lab.toolbox import SkillPlan, build_skill, skills_from_plan, toolbox_catalog
from shortform_lab.transcribe import load_transcript

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"


def _transcript():
    return load_transcript(FIXTURE)


def _source(duration_ms):
    return MediaRef(id="spine", path="source.mp4", kind="video",
                    duration_ms=duration_ms, has_audio=True)


async def _plan(orch, transcript, style, **kw):
    return await orch.plan_timeline(transcript, style, _source(transcript.duration_ms), **kw)


# --------------------------------------------------------------------------- #
# Toolbox
# --------------------------------------------------------------------------- #
def test_toolbox_catalog_lists_skills_with_params():
    names = {entry["name"] for entry in toolbox_catalog()}
    assert {"tighten_silence", "hook", "add_captions", "overlay", "punch_in"} <= names


def test_build_skill_validates_params_and_rejects_unknown():
    skill = build_skill("punch_in", {"count": 3}, load_style_config("bold_creator"))
    assert skill.name == "punch_in" and skill.count == 3
    with pytest.raises(ValueError, match="unknown skill"):
        build_skill("nope", {}, load_style_config("bold_creator"))


def test_build_skill_rejects_bad_params():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        build_skill("tighten_silence", {"max_silence_ms": -5}, load_style_config("bold_creator"))


# --------------------------------------------------------------------------- #
# LLM orchestrator (mocked call)
# --------------------------------------------------------------------------- #
async def test_llm_plan_builds_timeline_from_chosen_skills(monkeypatch):
    style = load_style_config("bold_creator")  # hook off by default; LLM enables it
    fake = (
        '{"skills": ['
        '{"name": "tighten_silence", "params": {"max_silence_ms": 300, "pad_ms": 80}}, '
        '{"name": "hook", "params": {"max_words": 5}}, '
        '{"name": "add_captions"}, '
        '{"name": "punch_in", "params": {"count": 1}}], '
        '"reason": "tight open with a hook"}'
    )
    async def fake_call(self, t, s):
        return fake

    monkeypatch.setattr(LLMOrchestrator, "_call_llm", fake_call)

    tl = await _plan(LLMOrchestrator(), _transcript(), style)
    assert tl.hook is not None                      # LLM chose to add a hook
    assert tl.captions
    assert len(tl.punch_ins) == 1                   # LLM-overridden count
    assert tl.meta["reason"] == "tight open with a hook"


async def test_llm_plan_omitting_a_skill_leaves_that_track_empty(monkeypatch):
    style = load_style_config("bold_creator")
    fake = '{"skills": [{"name": "add_captions"}], "reason": "captions only"}'

    async def fake_call(self, t, s):
        return fake

    monkeypatch.setattr(LLMOrchestrator, "_call_llm", fake_call)

    tl = await _plan(LLMOrchestrator(), _transcript(), style)
    assert tl.captions
    assert tl.hook is None
    assert tl.overlays == []
    assert tl.punch_ins == []


async def test_llm_word_mode_captions_come_from_transcript(monkeypatch):
    # The LLM only *chooses* add_captions; the word-level timing is built
    # deterministically from the transcript, never from the model.
    style = load_style_config("word_pop")
    fake = '{"skills": [{"name": "add_captions"}], "reason": "words"}'

    async def fake_call(self, t, s):
        return fake

    monkeypatch.setattr(LLMOrchestrator, "_call_llm", fake_call)

    words = load_transcript(Path(__file__).parent / "fixtures" / "transcript_words.json")
    tl = await LLMOrchestrator().plan_timeline(words, style, _source(words.duration_ms))
    assert all(c.words for c in tl.captions)
    assert tl.captions[0].words[0].text == "Most"


async def test_llm_unknown_skill_falls_back(monkeypatch, tmp_path):
    style = load_style_config("bold_creator")

    async def fake_call(self, t, s):
        return '{"skills": [{"name": "make_it_viral"}]}'

    monkeypatch.setattr(LLMOrchestrator, "_call_llm", fake_call)
    error_path = tmp_path / "err.txt"
    tl = await _plan(LLMOrchestrator(), _transcript(), style, error_path=error_path)
    # Fell back to the deterministic preset (bold_creator: captions + punch-ins).
    assert tl.captions
    assert error_path.is_file()
    assert "unknown skill" in error_path.read_text()


async def test_llm_malformed_json_falls_back(monkeypatch):
    style = load_style_config("bold_creator")

    async def fake_call(self, t, s):
        return "not json at all"

    monkeypatch.setattr(LLMOrchestrator, "_call_llm", fake_call)
    tl = await _plan(LLMOrchestrator(), _transcript(), style)
    assert tl.captions  # deterministic fallback still produced a plan
