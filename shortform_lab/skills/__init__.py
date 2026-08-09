"""Composable operations on the ``Timeline`` (see ``docs/architecture.md``).

Every feature becomes a ``Skill``: a media-pure transform that reads/writes named
parts of the Timeline and never touches FFmpeg. This package is the modular
substrate the migration is built on. Each skill module also exposes the pure
builder it wraps (``build_captions``, ``build_hook`, ...), the single home of that
feature's logic, shared by the deterministic and LLM paths.
"""

from .base import Context, Skill
from .caption import CaptionSkill, build_captions
from .color import ColorGradeSkill, build_color_grade
from .hook import HookSkill, build_hook
from .overlay import OverlaySkill, build_overlays
from .punchin import PunchInSkill, build_punch_ins
from .tighten import TightenSilenceSkill

__all__ = [
    "Context",
    "Skill",
    "CaptionSkill",
    "ColorGradeSkill",
    "HookSkill",
    "OverlaySkill",
    "PunchInSkill",
    "TightenSilenceSkill",
    "build_captions",
    "build_color_grade",
    "build_hook",
    "build_overlays",
    "build_punch_ins",
]
