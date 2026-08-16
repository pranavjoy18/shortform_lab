"""Tests for the skill dependency graph (topological order) and presets."""

from dataclasses import dataclass
from typing import ClassVar

import pytest

from shortform_lab.config import load_style_config
from shortform_lab.presets import preset_from_style
from shortform_lab.skills.caption import CaptionSkill
from shortform_lab.skills.graph import topological_order
from shortform_lab.skills.hook import HookSkill
from shortform_lab.skills.overlay import OverlaySkill
from shortform_lab.skills.punchin import PunchInSkill
from shortform_lab.skills.tighten import TightenSilenceSkill


@dataclass(frozen=True)
class _FakeSkill:
    name: str
    reads: frozenset
    writes: frozenset

    def apply(self, tl, ctx, *, span=None):
        return tl


# --------------------------------------------------------------------------- #
# Topological order
# --------------------------------------------------------------------------- #
def test_writer_is_ordered_before_reader():
    writer = _FakeSkill("writer", frozenset(), frozenset({"spine"}))
    reader = _FakeSkill("reader", frozenset({"spine"}), frozenset({"captions"}))
    # Even given reader-first, the writer must come out first.
    ordered = topological_order([reader, writer])
    assert [s.name for s in ordered] == ["writer", "reader"]


def test_independent_skills_keep_input_order_stably():
    a = _FakeSkill("a", frozenset({"transcript"}), frozenset({"hook"}))
    b = _FakeSkill("b", frozenset({"transcript"}), frozenset({"captions"}))
    c = _FakeSkill("c", frozenset({"transcript"}), frozenset({"punch_ins"}))
    assert [s.name for s in topological_order([a, b, c])] == ["a", "b", "c"]


def test_cycle_is_rejected():
    x = _FakeSkill("x", frozenset({"b"}), frozenset({"a"}))
    y = _FakeSkill("y", frozenset({"a"}), frozenset({"b"}))
    with pytest.raises(ValueError, match="cyclic"):
        topological_order([x, y])


def test_real_skills_put_tighten_first():
    skills = [PunchInSkill(), CaptionSkill(), TightenSilenceSkill(max_silence_ms=350, pad_ms=100), HookSkill()]
    ordered = topological_order(skills)
    # tighten writes the spine, which every other skill reads -> it leads.
    assert ordered[0].name == "tighten_silence"


# --------------------------------------------------------------------------- #
# Presets
# --------------------------------------------------------------------------- #
def test_preset_gates_disabled_features():
    style = load_style_config("bold_creator")  # hook + overlays off, tighten off
    names = {s.name for s in preset_from_style(style).skills}
    assert "hook" not in names
    assert "overlay" not in names
    assert "tighten_silence" not in names
    assert "add_captions" in names
    assert "punch_in" in names


def test_preset_hook_enabled_without_debug_is_still_omitted():
    # The deterministic hook is purely extractive (never real hook copy), so
    # style.hook.enabled alone isn't enough — it only renders under --debug.
    style = load_style_config("bold_creator")
    style.hook.enabled = True
    names = {s.name for s in preset_from_style(style).skills}
    assert "hook" not in names


def test_preset_includes_enabled_features():
    style = load_style_config("bold_creator")
    style.hook.enabled = True
    style.visuals.overlays_enabled = True
    style.tighten.enabled = True
    names = {s.name for s in preset_from_style(style, debug=True).skills}
    assert {"tighten_silence", "hook", "add_captions", "overlay", "punch_in"} <= names


def test_preset_tighten_flag_overrides_style():
    style = load_style_config("bold_creator")
    style.tighten.enabled = True
    names = {s.name for s in preset_from_style(style, tighten=False).skills}
    assert "tighten_silence" not in names  # forced off regardless of the style


def test_preset_orders_topologically_with_tighten_leading():
    style = load_style_config("word_pop")
    style.tighten.enabled = True
    style.hook.enabled = True
    ordered = topological_order(preset_from_style(style, debug=True).skills)
    names = [s.name for s in ordered]
    assert names[0] == "tighten_silence"
    # segment_beats reads spine so it follows tighten; decoration skills follow beats.
    assert names == ["tighten_silence", "segment_beats", "hook", "add_captions", "punch_in"]
