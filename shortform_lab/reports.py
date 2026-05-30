"""Write a human-readable ``review.md`` so creative choices are inspectable.

The report mirrors the contents of ``edit_plan.json`` in prose: which style was
used, the hook, the overlays, how many captions, where the file landed, and the
planner's stated reason. It also lists the known limitations of the current
prototype so a reviewer reads the output with the right expectations.
"""

from __future__ import annotations

from pathlib import Path

from .models import EditPlan, StyleConfig

KNOWN_LIMITATIONS = [
    "Only the first punch-in is rendered; any others are kept in edit_plan.json.",
    "Fill layout reframes with a static center-crop, so off-center speakers may be cropped.",
    "Overlays are simulated b-roll (text/quote cards), not real stock footage.",
    "Tightening removes only silence/pauses — not filler words or repeated takes.",
]


def write_review(
    plan: EditPlan,
    style: StyleConfig,
    *,
    source_name: str,
    final_path: Path,
    out_path: Path,
    used_llm: bool,
    llm_failed: bool = False,
    source_duration_ms: int | None = None,
) -> Path:
    """Write ``review.md`` summarizing the run and return its path."""
    overlay_lines = (
        "\n".join(
            f"- `{o.kind}` ({o.placement}, {_secs(o.start_ms)}–{_secs(o.end_ms)}): "
            f"{o.text or '(no text)'}"
            for o in plan.overlays
        )
        or "- (none)"
    )

    planner_label = "LLM planner" if used_llm else "deterministic planner"
    if used_llm and llm_failed:
        planner_label = "deterministic planner (LLM call failed — see planner_error.txt)"

    if plan.keep_ranges:
        kept_ms = sum(r.duration_ms for r in plan.keep_ranges)
        cuts = len(plan.keep_ranges) - 1
        src = f" of {_secs(source_duration_ms)}" if source_duration_ms else ""
        tightening = (
            f"- Kept {_secs(kept_ms)}{src} across {len(plan.keep_ranges)} span(s) "
            f"({cuts} cut{'s' if cuts != 1 else ''})."
        )
    else:
        tightening = "- Off (no cuts; full source rendered)."

    if plan.hook is not None:
        hook_block = (
            f"> {plan.hook.text}\n\n"
            f"Shown {_secs(plan.hook.start_ms)}–{_secs(plan.hook.end_ms)}."
        )
    else:
        hook_block = "_No hook (disabled for this style)._"

    limitations = "\n".join(f"- {item}" for item in KNOWN_LIMITATIONS)

    md = f"""# Review

## Source
- File: `{source_name}`
- Style: `{style.name}`
- Planner: {planner_label}
- Export: {plan.export_width}x{plan.export_height} @ {plan.export_fps}fps

## Hook
{hook_block}

## Overlays
{overlay_lines}

## Captions
- {len(plan.captions)} caption cue(s) ({style.captions.mode}-level).

## Tightening
{tightening}

## Punch-ins
- {len(plan.punch_ins)} planned; the first is rendered.

## Edit reason
{plan.reason or "_No reason provided by the planner._"}

## Output
- Final video: `{final_path}`

## Known limitations (this run)
{limitations}
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    return out_path


def _secs(ms: int) -> str:
    return f"{ms / 1000:.1f}s"
