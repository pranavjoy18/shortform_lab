"""Tests for style config loading and validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from shortform_lab.config import available_styles, load_style_config


def test_both_shipped_styles_load():
    for name in ("bold_creator", "clean_captions"):
        cfg = load_style_config(name)
        assert cfg.name == name
        assert cfg.export.width > 0 and cfg.export.height > 0
        assert cfg.export.fps > 0
        assert cfg.captions.font_size > 0
        assert cfg.hook.max_words > 0
        assert cfg.visuals.punch_in_count >= 0


def test_available_styles_lists_shipped_configs():
    styles = available_styles()
    assert "bold_creator" in styles
    assert "clean_captions" in styles


def test_missing_style_raises_with_helpful_message():
    with pytest.raises(FileNotFoundError) as exc:
        load_style_config("does_not_exist")
    assert "Available styles" in str(exc.value)


def test_invalid_config_fails_validation(tmp_path: Path):
    bad = tmp_path / "broken.yaml"
    # Missing required export/hook/captions/visuals sections.
    bad.write_text("name: broken\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_style_config("broken", styles_dir=tmp_path)


def test_negative_dimensions_rejected(tmp_path: Path):
    bad = tmp_path / "neg.yaml"
    bad.write_text(
        "name: neg\n"
        "export: {aspect_ratio: '9:16', width: -1, height: 1920, fps: 30}\n"
        "hook: {duration_ms: 2000, max_words: 8}\n"
        "captions: {mode: sentence, font_size: 64, max_chars_per_line: 28, position: bottom}\n"
        "visuals: {punch_in_count: 1, text_card_count: 1, allow_stock_broll: false}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_style_config("neg", styles_dir=tmp_path)
