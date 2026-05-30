"""Produce an inspectable ``EditPlan`` from a transcript and a style config.

Two planners share one ``plan_edits`` entry point:

- ``DeterministicPlanner`` needs no API. It derives a hook from the opening
  line, turns transcript segments into sentence-level captions, adds quote-card
  overlays and punch-ins according to the style's visual settings. It is the
  always-available fallback so the prototype produces an ``edit_plan.json`` even
  when the LLM call fails.
- ``LLMPlanner`` asks an LLM for the creative choices as JSON, then validates
  that JSON against ``EditPlan`` before it is ever trusted. On any failure it
  saves the raw response to ``planner_error.txt`` and falls back to the
  deterministic planner.

The LLM only ever returns structured JSON; it never renders video.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import (
    CaptionCue,
    EditPlan,
    Hook,
    PunchIn,
    StyleConfig,
    Transcript,
    VisualOverlay,
)


# --------------------------------------------------------------------------- #
# Deterministic planner
# --------------------------------------------------------------------------- #
class DeterministicPlanner:
    """A no-API planner that derives a sensible plan from transcript + style."""

    def plan(self, transcript: Transcript, style: StyleConfig, *, source_video: str) -> EditPlan:
        segments = transcript.segments
        if not segments:
            raise ValueError("Cannot plan edits from an empty transcript")

        hook = self._make_hook(transcript, style)
        captions = [
            CaptionCue(start_ms=s.start_ms, end_ms=s.end_ms, text=s.text.strip())
            for s in segments
        ]
        overlays = self._make_overlays(transcript, style)
        punch_ins = self._make_punch_ins(transcript, style)

        return EditPlan(
            source_video=source_video,
            hook=hook,
            captions=captions,
            overlays=overlays,
            punch_ins=punch_ins,
            export_width=style.export.width,
            export_height=style.export.height,
            export_fps=style.export.fps,
            reason=(
                "Deterministic plan: hook drawn from the opening line, "
                "sentence-level captions from the transcript, "
                f"{len(overlays)} quote card(s) on the longest lines, and "
                f"{len(punch_ins)} punch-in(s) for visual rhythm."
            ),
        )

    def _make_hook(self, transcript: Transcript, style: StyleConfig) -> Hook:
        first = transcript.segments[0].text.strip()
        words = first.split()
        max_words = style.hook.max_words
        text = " ".join(words[:max_words])
        # Drop a trailing period from the truncated hook; keep ? and ! for punch.
        text = text.rstrip(".,;:")
        end_ms = min(style.hook.duration_ms, transcript.duration_ms or style.hook.duration_ms)
        return Hook(text=text, start_ms=0, end_ms=end_ms)

    def _make_overlays(self, transcript: Transcript, style: StyleConfig) -> list[VisualOverlay]:
        count = style.visuals.text_card_count
        if count <= 0 or len(transcript.segments) <= 1:
            return []
        # Skip the first segment (it feeds the hook); rank the rest by length and
        # take the strongest few, then restore chronological order.
        candidates = sorted(
            transcript.segments[1:],
            key=lambda s: len(s.text),
            reverse=True,
        )[:count]
        candidates.sort(key=lambda s: s.start_ms)
        return [
            VisualOverlay(
                kind="quote_card",
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                text=_shorten(s.text, max_chars=60),
                placement="center",
            )
            for s in candidates
        ]

    def _make_punch_ins(self, transcript: Transcript, style: StyleConfig) -> list[PunchIn]:
        count = style.visuals.punch_in_count
        segments = transcript.segments
        if count <= 0 or not segments:
            return []
        # Spread punch-ins evenly across the back half of the clip, biased to the
        # middle so the open and close stay wide.
        chosen: list[PunchIn] = []
        n = len(segments)
        for i in range(count):
            idx = min(n - 1, (n // 2) + i)
            seg = segments[idx]
            chosen.append(PunchIn(start_ms=seg.start_ms, end_ms=seg.end_ms, zoom=1.2))
        # De-duplicate by start_ms in case the clip is very short.
        seen: set[int] = set()
        unique = [p for p in chosen if not (p.start_ms in seen or seen.add(p.start_ms))]
        return unique


# --------------------------------------------------------------------------- #
# LLM planner
# --------------------------------------------------------------------------- #
PLANNER_SYSTEM_PROMPT = """\
You are an editor for short vertical talking-head videos. You produce ONLY a \
structured edit plan as JSON. You never write prose outside the JSON.

Rules you must obey:
- Keep the original meaning. Do not invent claims or facts.
- The hook must be under {max_words} words and grab attention honestly.
- Captions must cover the speech and only lightly clean filler words; no meaning changes.
- Provide {text_card_count} short text/quote card overlay idea(s); keep each text short.
- Provide {punch_in_count} punch-in moment(s) using a zoom between 1.05 and 1.5.
- Use ONLY timestamps that appear in the provided transcript segments.
- Output must be valid JSON matching the requested schema, nothing else."""


class LLMPlanner:
    """Plan via an LLM, validate strictly, and fall back deterministically."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._fallback = DeterministicPlanner()

    def plan(
        self,
        transcript: Transcript,
        style: StyleConfig,
        *,
        source_video: str,
        error_path: Path | None = None,
    ) -> EditPlan:
        try:
            raw = self._call_llm(transcript, style, source_video=source_video)
            plan = self._parse_plan(raw, style, source_video=source_video)
            return plan
        except Exception as exc:  # noqa: BLE001 - any failure must fall back
            if error_path is not None:
                detail = f"LLM planning failed: {exc}\n\nRaw response:\n{getattr(self, '_last_raw', '')}"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(detail, encoding="utf-8")
            return self._fallback.plan(transcript, style, source_video=source_video)

    def _call_llm(self, transcript: Transcript, style: StyleConfig, *, source_video: str) -> str:
        from openai import OpenAI  # lazy import; optional extra

        system = PLANNER_SYSTEM_PROMPT.format(
            max_words=style.hook.max_words,
            text_card_count=style.visuals.text_card_count,
            punch_in_count=style.visuals.punch_in_count,
        )
        user = self._build_user_prompt(transcript, style, source_video=source_video)

        client = OpenAI()
        response = client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        raw = response.choices[0].message.content or ""
        self._last_raw = raw
        return raw

    def _build_user_prompt(
        self, transcript: Transcript, style: StyleConfig, *, source_video: str
    ) -> str:
        segs = [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
            for s in transcript.segments
        ]
        schema = {
            "source_video": source_video,
            "hook": {"text": "str", "start_ms": 0, "end_ms": style.hook.duration_ms},
            "captions": [{"start_ms": "int", "end_ms": "int", "text": "str"}],
            "overlays": [
                {"kind": "quote_card", "start_ms": "int", "end_ms": "int", "text": "str", "placement": "center"}
            ],
            "punch_ins": [{"start_ms": "int", "end_ms": "int", "zoom": 1.2}],
            "export_width": style.export.width,
            "export_height": style.export.height,
            "export_fps": style.export.fps,
            "reason": "str (short explanation of your edit)",
        }
        return (
            "Transcript segments (timestamps in milliseconds):\n"
            f"{json.dumps(segs, ensure_ascii=False)}\n\n"
            "Return JSON matching exactly this shape (replace the placeholder types "
            "with real values):\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )

    def _parse_plan(self, raw: str, style: StyleConfig, *, source_video: str) -> EditPlan:
        text = _strip_code_fences(raw)
        data = json.loads(text)
        # The renderer relies on export dims matching the chosen style, so pin
        # them regardless of what the model echoed back.
        data["source_video"] = source_video
        data["export_width"] = style.export.width
        data["export_height"] = style.export.height
        data["export_fps"] = style.export.fps
        return EditPlan.model_validate(data)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def plan_edits(
    transcript: Transcript,
    style: StyleConfig,
    *,
    source_video: str,
    use_llm: bool = False,
    model: str = "gpt-4o-mini",
    error_path: Path | None = None,
) -> EditPlan:
    """Plan edits, using the LLM if requested and falling back deterministically."""
    if use_llm:
        return LLMPlanner(model=model).plan(
            transcript, style, source_video=source_video, error_path=error_path
        )
    return DeterministicPlanner().plan(transcript, style, source_video=source_video)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _shorten(text: str, *, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` fences an LLM may wrap its JSON in."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1) if fence else text
