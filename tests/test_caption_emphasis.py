"""Tests for word-level caption emphasis: the generative-path-only, opt-in
accent-highlight flag (never touches caption text/timing)."""

from __future__ import annotations

from shortform_lab.config import load_style_config
from shortform_lab.models import Transcript, TranscriptSegment
from shortform_lab.skills.caption import build_captions
from shortform_lab.toolbox import build_skill


def _word_pop_style():
    return load_style_config("word_pop")  # word mode, active_word


def _transcript():
    return Transcript(segments=[
        TranscriptSegment(start_ms=0, end_ms=4000, text="Reinforcement learning has one key concept"),
    ])


def test_no_emphasize_marks_nothing():
    cues = build_captions(_transcript(), _word_pop_style())
    all_words = [w for cue in cues for w in cue.words]
    assert all_words  # word mode always carries word timings
    assert not any(w.emphasize for w in all_words)


def test_emphasize_marks_matching_word_case_and_punctuation_insensitive():
    cues = build_captions(_transcript(), _word_pop_style(), emphasize=("Concept",))
    all_words = [w for cue in cues for w in cue.words]
    emphasized = [w.text for w in all_words if w.emphasize]
    assert emphasized == ["concept"]


def test_emphasize_marks_multi_word_phrase():
    cues = build_captions(_transcript(), _word_pop_style(), emphasize=("key concept",))
    all_words = [w for cue in cues for w in cue.words]
    emphasized = [w.text for w in all_words if w.emphasize]
    assert emphasized == ["key", "concept"]


def test_emphasize_ignores_non_matching_phrase_without_crashing():
    cues = build_captions(_transcript(), _word_pop_style(), emphasize=("nonexistent phrase",))
    all_words = [w for cue in cues for w in cue.words]
    assert not any(w.emphasize for w in all_words)


def test_emphasize_is_noop_in_sentence_mode():
    style = load_style_config("bold_creator")  # sentence mode
    cues = build_captions(_transcript(), style, emphasize=("concept",))
    assert all(cue.words == [] for cue in cues)


def test_toolbox_emphasize_param_reaches_skill():
    style = _word_pop_style()
    skill = build_skill("add_captions", {"emphasize": ["concept", "key concept"]}, style)
    assert skill.emphasize == ("concept", "key concept")


def test_toolbox_emphasize_defaults_to_empty():
    style = _word_pop_style()
    skill = build_skill("add_captions", {}, style)
    assert skill.emphasize == ()
