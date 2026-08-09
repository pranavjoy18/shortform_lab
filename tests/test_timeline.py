"""Tests for the Model C ``Timeline`` and the ``tighten`` skill port.

The load-bearing assertion is *behavior equivalence*: running the
``TightenSilenceSkill`` on a spine Timeline and projecting it back with
``spine_keep_ranges`` yields exactly the ``keep_ranges`` the current planner path
produces via ``compute_keep_ranges`` — so the migration changes structure, not
output.
"""

import pytest

from shortform_lab.models import ExportSettings, Transcript, TranscriptSegment
from shortform_lab.skills import Context, Skill, TightenSilenceSkill
from shortform_lab.tighten import compute_keep_ranges
from shortform_lab.timeline import Clip, MediaRef, Timeline, timeline_from_spine


def _export():
    return ExportSettings(width=1080, height=1920, fps=30)


def _spine_source(duration_ms=4000):
    return MediaRef(id="spine", path="source.mp4", kind="video",
                    width=1280, height=720, duration_ms=duration_ms,
                    fps=30, has_audio=True)


def _seg(start, end, text="word"):
    return TranscriptSegment(start_ms=start, end_ms=end, text=text)


def _style():
    # A minimal style is only needed to satisfy Context; the tighten skill takes
    # its params directly, so we reuse the loaded default style.
    from shortform_lab.config import load_style_config

    return load_style_config("bold_creator")


# --------------------------------------------------------------------------- #
# Timeline model + validators
# --------------------------------------------------------------------------- #
def test_timeline_from_spine_is_uncut_identity():
    tl = timeline_from_spine(_spine_source(4000), _export())
    assert len(tl.spine) == 1
    # A single full-span clip reports the identity (uncut) case.
    assert tl.spine_keep_ranges() == []
    assert tl.output_duration_ms == 4000


def test_spine_keep_ranges_projects_cut_clips():
    src = _spine_source(4000)
    spine = [
        Clip(source_id="spine", source_in_ms=0, duration_ms=1100),
        Clip(source_id="spine", source_in_ms=2900, duration_ms=1100),
    ]
    tl = Timeline(sources=[src], spine_source_id="spine", spine=spine, export=_export())
    assert [(r.start_ms, r.end_ms) for r in tl.spine_keep_ranges()] == [(0, 1100), (2900, 4000)]
    assert tl.output_duration_ms == 2200


def test_unknown_spine_source_rejected():
    with pytest.raises(ValueError, match="not in sources"):
        Timeline(sources=[_spine_source()], spine_source_id="nope", export=_export())


def test_overlapping_spine_clips_rejected():
    src = _spine_source(4000)
    spine = [
        Clip(source_id="spine", source_in_ms=0, duration_ms=1500),
        Clip(source_id="spine", source_in_ms=1000, duration_ms=500),  # overlaps the first
    ]
    with pytest.raises(ValueError, match="non-overlapping"):
        Timeline(sources=[src], spine_source_id="spine", spine=spine, export=_export())


def test_spine_clip_past_source_duration_rejected():
    src = _spine_source(4000)
    spine = [Clip(source_id="spine", source_in_ms=3000, duration_ms=2000)]  # ends at 5000
    with pytest.raises(ValueError, match="past the source duration"):
        Timeline(sources=[src], spine_source_id="spine", spine=spine, export=_export())


def test_spine_clip_with_explicit_position_rejected():
    src = _spine_source(4000)
    spine = [Clip(source_id="spine", source_in_ms=0, duration_ms=4000, timeline_start_ms=0)]
    with pytest.raises(ValueError, match="sequential"):
        Timeline(sources=[src], spine_source_id="spine", spine=spine, export=_export())


def test_reserved_clip_fields_rejected():
    with pytest.raises(ValueError, match="reserved"):
        Clip(source_id="spine", duration_ms=1000, speed=1.5)
    # transition_in/out are now unlocked (TransitionSkill uses them); only speed stays reserved.
    c = Clip(source_id="spine", duration_ms=1000, transition_in="dissolve")
    assert c.transition_in == "dissolve"


# --------------------------------------------------------------------------- #
# Skill protocol + tighten port
# --------------------------------------------------------------------------- #
def test_tighten_skill_conforms_to_protocol():
    skill = TightenSilenceSkill(max_silence_ms=350, pad_ms=100)
    assert isinstance(skill, Skill)
    assert skill.reads == frozenset({"transcript", "spine"})
    assert skill.writes == frozenset({"spine"})


def test_tighten_skill_matches_planner_keep_ranges():
    # The same gapped transcript the planner would tighten.
    transcript = Transcript(segments=[_seg(0, 1000), _seg(3000, 4000)])
    expected = compute_keep_ranges(transcript, max_silence_ms=350, pad_ms=100, source_duration_ms=4000)

    tl = timeline_from_spine(_spine_source(4000), _export())
    ctx = Context(transcript=transcript, style=_style())
    out = TightenSilenceSkill(max_silence_ms=350, pad_ms=100).apply(tl, ctx)

    assert out.spine_keep_ranges() == expected
    assert [(r.start_ms, r.end_ms) for r in out.spine_keep_ranges()] == [(0, 1100), (2900, 4000)]


def test_tighten_skill_is_noop_without_gaps():
    transcript = Transcript(segments=[_seg(0, 4000)])
    tl = timeline_from_spine(_spine_source(4000), _export())
    ctx = Context(transcript=transcript, style=_style())
    out = TightenSilenceSkill(max_silence_ms=350, pad_ms=100).apply(tl, ctx)

    # Nothing to cut: spine unchanged, still the uncut identity.
    assert out.spine == tl.spine
    assert out.spine_keep_ranges() == []
