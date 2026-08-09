"""``lower_third`` skill: add a name/title banner in the lower frame.

A lower-third is a broadcast convention: a semi-transparent text bar in the
bottom-left corner identifying the speaker. Here it shows a primary line
(creator name) and an optional subtitle (handle or title).

``LowerThirdSkill`` places the banner at the start of the first non-hook beat
(so it doesn't stack on the hook overlay). When beats are absent it defaults
to the first 3 seconds of the output. Duration is capped to 4 seconds to
keep it from overstaying its welcome.

Text comes from skill params (LLM path) or falls back to
``style.visuals.lower_third_text`` (deterministic / YAML path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..models import LowerThird, TimeRange
from ..timeline import Timeline
from .base import Context

_DEFAULT_DURATION_MS = 3_500
_MAX_DURATION_MS = 4_000


def build_lower_third(
    tl: Timeline,
    ctx: Context,
    *,
    text: str,
    subtext: str | None = None,
) -> LowerThird:
    """Compute the output-time window for the lower-third banner.

    Placed at the first non-hook beat (usually setup), or [0, 3.5s] if
    no beats exist. Duration is capped at 4 seconds.
    """
    start_ms = 0
    end_ms = _DEFAULT_DURATION_MS

    for beat in tl.beats:
        if beat.role != "hook":
            start_ms = beat.start_ms
            end_ms = min(start_ms + _DEFAULT_DURATION_MS, beat.end_ms, start_ms + _MAX_DURATION_MS)
            break

    return LowerThird(text=text, subtext=subtext, start_ms=start_ms, end_ms=end_ms)


@dataclass(frozen=True)
class LowerThirdSkill:
    """Place a name/title banner on the output.

    ``text`` and ``subtext`` can be set explicitly (LLM path) or left as
    ``None`` to fall back to ``style.visuals.lower_third_text/sub``.
    """

    text: str | None = None
    subtext: str | None = None

    name: ClassVar[str] = "lower_third"
    reads: ClassVar[frozenset[str]] = frozenset({"beats"})
    writes: ClassVar[frozenset[str]] = frozenset({"lower_third"})

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        resolved_text = self.text or ctx.style.visuals.lower_third_text
        if not resolved_text:
            return tl  # no text configured — graceful no-op
        resolved_sub = self.subtext or ctx.style.visuals.lower_third_sub
        lt = build_lower_third(tl, ctx, text=resolved_text, subtext=resolved_sub)
        return tl.model_copy(update={"lower_third": lt})
