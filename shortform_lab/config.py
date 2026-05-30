"""Load and validate YAML style configs.

Style configs live in ``configs/styles/<name>.yaml`` and define all creative
behavior (export size, captions, hook, visuals). Loading goes through the
``StyleConfig`` Pydantic model, so a malformed config fails fast with a clear
validation error rather than surfacing as a confusing render bug later.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import StyleConfig

# configs/ sits at the repo root, a sibling of the shortform_lab package.
STYLES_DIR = Path(__file__).resolve().parent.parent / "configs" / "styles"


def available_styles(styles_dir: Path | None = None) -> list[str]:
    """Return the names of all style configs discoverable on disk."""
    directory = styles_dir or STYLES_DIR
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def load_style_config(style_name: str, styles_dir: Path | None = None) -> StyleConfig:
    """Load and validate the style config named ``style_name``.

    Raises ``FileNotFoundError`` if the style does not exist, and
    ``pydantic.ValidationError`` if the YAML is structurally invalid.
    """
    directory = styles_dir or STYLES_DIR
    path = directory / f"{style_name}.yaml"
    if not path.is_file():
        known = ", ".join(available_styles(directory)) or "(none found)"
        raise FileNotFoundError(
            f"Style '{style_name}' not found at {path}. Available styles: {known}"
        )

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Style config {path} must be a YAML mapping, got {type(raw).__name__}")

    return StyleConfig.model_validate(raw)
