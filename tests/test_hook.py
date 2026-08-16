"""Tests for the hook skill: extractive fallback, generative text, and the
deterministic-path --debug gate."""

from __future__ import annotations

from shortform_lab.config import load_style_config
from shortform_lab.coherence import check_timeline
from shortform_lab.models import CaptionCue, ExportSettings, Hook, Transcript, TranscriptSegment
from shortform_lab.presets import preset_from_style
from shortform_lab.skills.hook import _clause_safe_truncate, build_hook
from shortform_lab.timeline import Clip, MediaRef, Timeline
from shortform_lab.toolbox import build_skill


def _style():
    style = load_style_config("bold_creator")
    style.hook.duration_ms = 2500
    style.hook.max_words = 8
    style.captions.uppercase = False  # explicit baseline case; see test_build_hook_uppercases_when_style_does
    return style


def _transcript(text="Most people quit way too early on their goals about consistency"):
    return Transcript(segments=[TranscriptSegment(start_ms=0, end_ms=4000, text=text)])


# --------------------------------------------------------------------------- #
# _clause_safe_truncate
# --------------------------------------------------------------------------- #
def test_clause_safe_truncate_backs_off_dangling_word():
    # A blind 8-word cut of this sentence lands on "their" (a dangling pronoun);
    # backing off one more word lands on "on" (a dangling preposition) too.
    text = "Most people quit way too early on their goals"
    out = _clause_safe_truncate(text, max_words=8)
    assert out == "Most people quit way too early"


def test_clause_safe_truncate_respects_min_words_floor():
    out = _clause_safe_truncate("a a a a a a", max_words=8, min_words=3)
    assert len(out.split()) >= 3


def test_clause_safe_truncate_strips_trailing_punctuation():
    out = _clause_safe_truncate("Consistency beats intensity.", max_words=8)
    assert not out.endswith(".")


# --------------------------------------------------------------------------- #
# build_hook
# --------------------------------------------------------------------------- #
def test_build_hook_extractive_default_uses_first_segment():
    style = _style()
    hook = build_hook(_transcript(), style)
    assert hook.start_ms == 0
    assert len(hook.text.split()) <= style.hook.max_words
    # No dangling connective at the end.
    assert hook.text.split()[-1].lower() not in {"on", "their", "the", "a"}


def test_build_hook_prefers_llm_authored_text():
    style = _style()
    hook = build_hook(_transcript(), style, text="Wait for the twist at the end")
    assert hook.text.startswith("Wait for the twist")
    assert "Most people" not in hook.text


def test_build_hook_word_caps_llm_text_too():
    style = _style()
    style.hook.max_words = 4
    hook = build_hook(_transcript(), style, text="This is a much longer hook than the budget allows")
    assert len(hook.text.split()) <= 4


def test_build_hook_uppercases_when_style_does():
    style = _style()
    style.captions.uppercase = True
    hook = build_hook(_transcript(), style, text="short punchy line")
    assert hook.text == hook.text.upper()


# --------------------------------------------------------------------------- #
# Deterministic-path gating (--debug)
# --------------------------------------------------------------------------- #
def test_deterministic_preset_never_includes_hook_without_debug():
    style = load_style_config("viral_creator")  # the one style shipping hook.enabled: true
    assert style.hook.enabled is True
    names = {s.name for s in preset_from_style(style).skills}
    assert "hook" not in names


def test_deterministic_preset_includes_hook_only_with_debug():
    style = load_style_config("viral_creator")
    names = {s.name for s in preset_from_style(style, debug=True).skills}
    assert "hook" in names


# --------------------------------------------------------------------------- #
# toolbox wiring: the LLM's hook_text reaches HookSkill
# --------------------------------------------------------------------------- #
def test_toolbox_hook_text_param_reaches_skill():
    style = _style()
    skill = build_skill("hook", {"text": "Three habits nobody tells you about"}, style)
    assert skill.text == "Three habits nobody tells you about"


# --------------------------------------------------------------------------- #
# Coherence: hook duplicating the caption under it
# --------------------------------------------------------------------------- #
def _tl_with_hook_and_caption(hook_text: str, caption_text: str) -> Timeline:
    export = ExportSettings(width=1080, height=1920, fps=30)
    src = MediaRef(id="v", path="s.mp4", duration_ms=5000)
    spine = [Clip(source_id="v", source_in_ms=0, duration_ms=4000)]
    tl = Timeline(sources=[src], spine_source_id="v", spine=spine, export=export)
    tl = tl.model_copy(update={
        "hook": Hook(text=hook_text, start_ms=0, end_ms=2000),
        "captions": [CaptionCue(start_ms=0, end_ms=2000, text=caption_text)],
    })
    return tl


def test_coherence_flags_hook_duplicating_caption():
    tl = _tl_with_hook_and_caption("Most people quit way too early", "Most people quit way too early on their goals.")
    warnings = [v for v in check_timeline(tl) if v.track == "hook" and v.severity == "warning"]
    assert any("duplicates" in w.message for w in warnings)


def test_coherence_does_not_flag_distinct_hook():
    tl = _tl_with_hook_and_caption("Wait for the twist at the end", "Most people quit way too early on their goals.")
    warnings = [v for v in check_timeline(tl) if v.track == "hook" and v.severity == "warning"]
    assert not any("duplicates" in w.message for w in warnings)
