"""LLM-backed orchestrators: legacy single-shot and new agentic.

Two orchestrators, same external interface (``plan_timeline``):

``LLMOrchestrator`` (legacy)
    One LLM call → SkillPlan → Timeline. Kept intact so existing tests that
    mock ``_call_llm`` continue to work unchanged.

``AgenticOrchestrator`` (new)
    Workflow-of-agents pattern:
      Phase 0 — structural (deterministic): tighten + beat segmentation
      Phase 1 — optional clarification: Clarifier proposes questions, CLI asks user
      Phase 2 — content analysis: Analyst reads the clip
      Phase 3 — reflection loop (plan-space only, ≤2 rounds):
                 Planner proposes SkillPlan → trial compose (no FFmpeg) →
                 coherence check + Critic taste review → Planner revises if needed
      Phase 4 — final compose: structural + chosen skills → Timeline

    The LLM never emits timestamps or export settings. Any failure in any phase
    falls back to the deterministic orchestrator — the video always ships.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .agent import Context, FakeClient, LLMClient, OpenAIClient
from .coherence import check_timeline
from .intent import CliIO, ContentAnalysis, CreativeIntent, PlanCritique, QuestionSet
from .models import StyleConfig, Transcript
from .orchestrator import DeterministicOrchestrator, compose_timeline
from .presets import preset_from_style
from .skills.beat import BeatSegmenterSkill
from .skills.tighten import TightenSilenceSkill
from .timeline import MediaRef, Timeline, timeline_from_spine
from .toolbox import SkillPlan, skills_from_plan, toolbox_catalog
from .workers import make_analyst, make_clarifier, make_critic, make_planner


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _format_beats(beats) -> str:
    if not beats:
        return "No beats detected."
    lines = [
        f"  {b.role:8s}  {b.start_ms:5d}ms → {b.end_ms:5d}ms  energy={b.energy:.1f}"
        for b in beats
    ]
    return "Beat map (output time, post-tighten):\n" + "\n".join(lines)


def _transcript_summary(transcript: Transcript, max_words: int = 150) -> str:
    words = transcript.full_text.split()
    excerpt = " ".join(words[:max_words])
    return excerpt + ("…" if len(words) > max_words else "")


def _structural_skills(style: StyleConfig, *, tighten: bool = True) -> list:
    """Phase 0 skills: always deterministic, always run first."""
    skills = []
    if tighten and style.tighten.enabled:
        skills.append(
            TightenSilenceSkill(
                max_silence_ms=style.tighten.max_silence_ms,
                pad_ms=style.tighten.pad_ms,
                remove_fillers=style.tighten.remove_fillers,
            )
        )
    skills.append(BeatSegmenterSkill())
    return skills


def _trial_compose(structural_skills, plan: SkillPlan, style, transcript, source) -> tuple[Timeline | None, list[str]]:
    """Run compose_timeline cheaply (no FFmpeg) for the reflection loop."""
    from .skills.graph import topological_order
    try:
        all_skills = list(structural_skills) + skills_from_plan(plan, style)
        tl = compose_timeline(all_skills, transcript, style, source)
        warnings = [str(v) for v in check_timeline(tl) if v.severity == "warning"]
        return tl, warnings
    except Exception as exc:
        return None, [str(exc)]


# --------------------------------------------------------------------------- #
# Legacy LLMOrchestrator — one-shot, existing tests monkeypatch _call_llm
# --------------------------------------------------------------------------- #
ORCHESTRATOR_SYSTEM_PROMPT = """\
You are an editor for short vertical talking-head videos. You decide HOW to edit by \
choosing from a fixed toolbox of skills — you do NOT write timestamps, captions text, \
or export settings; deterministic code computes those.

Return ONLY JSON of the form:
  {{"skills": [{{"name": "<skill>", "params": {{...}}}}, ...], "reason": "<short why>"}}

Rules:
- Use only skills from the provided toolbox; use only the params each skill allows.
- Almost always include "add_captions" — captions carry short-form video.
- Choose skills that genuinely improve THIS clip; do not pad the list.
- Order does not matter; dependencies are resolved automatically.

Toolbox:
{toolbox}"""


class LLMOrchestrator:
    """Plan a Timeline via an LLM skill choice; fall back deterministically."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self._fallback = DeterministicOrchestrator()
        self._last_raw = ""

    def plan_timeline(
        self,
        transcript: Transcript,
        style: StyleConfig,
        source: MediaRef,
        *,
        error_path: Path | None = None,
    ) -> Timeline:
        if not transcript.segments:
            raise ValueError("Cannot plan edits from an empty transcript")
        try:
            raw = self._call_llm(transcript, style)
            plan = self._parse_plan(raw)
            skills = skills_from_plan(plan, style)
            return compose_timeline(skills, transcript, style, source, reason=plan.reason)
        except Exception as exc:
            if error_path is not None:
                detail = f"LLM orchestration failed: {exc}\n\nRaw response:\n{self._last_raw}"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(detail, encoding="utf-8")
            return self._fallback.plan_timeline(transcript, style, source)

    def _call_llm(self, transcript: Transcript, style: StyleConfig) -> str:
        from openai import OpenAI

        system = ORCHESTRATOR_SYSTEM_PROMPT.format(
            toolbox=json.dumps(toolbox_catalog(), ensure_ascii=False, indent=2)
        )
        user = self._build_user_prompt(transcript, style)
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

    def _build_user_prompt(self, transcript: Transcript, style: StyleConfig) -> str:
        segs = [{"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text} for s in transcript.segments]
        context = {
            "style": style.name,
            "caption_mode": style.captions.mode,
            "style_defaults": {
                "tighten_enabled": style.tighten.enabled,
                "hook_enabled": style.hook.enabled,
                "overlays_enabled": style.visuals.overlays_enabled,
                "punch_in_count": style.visuals.punch_in_count,
            },
        }
        return (
            f"Style context: {json.dumps(context, ensure_ascii=False)}\n\n"
            f"Transcript segments (ms): {json.dumps(segs, ensure_ascii=False)}\n\n"
            "Choose the skills to apply."
        )

    def _parse_plan(self, raw: str) -> SkillPlan:
        return SkillPlan.model_validate_json(_strip_fences(raw))


# --------------------------------------------------------------------------- #
# AgenticOrchestrator — workflow-of-agents
# --------------------------------------------------------------------------- #
class AgenticOrchestrator:
    """Workflow-of-agents orchestrator: clarify → analyse → plan (with reflection).

    Control flow is plain Python; agents are stateless LLM workers injected
    via ``LLMClient``. Any failure in any phase falls back to the deterministic
    orchestrator so a video always ships.
    """

    def __init__(self, client: LLMClient | None = None, model: str = "gpt-4o-mini"):
        _client: LLMClient = client or OpenAIClient(model=model)
        self._clarifier = make_clarifier(_client)
        self._analyst   = make_analyst(_client)
        self._planner   = make_planner(_client)
        self._critic    = make_critic(_client)
        self._fallback  = DeterministicOrchestrator()
        self._last_error: str = ""

    def plan_timeline(
        self,
        transcript: Transcript,
        style: StyleConfig,
        source: MediaRef,
        *,
        interactive: bool = False,
        error_path: Path | None = None,
    ) -> Timeline:
        if not transcript.segments:
            raise ValueError("Cannot plan edits from an empty transcript")
        try:
            return self._run(transcript, style, source, interactive=interactive)
        except Exception as exc:
            self._last_error = str(exc)
            if error_path is not None:
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(
                    f"AgenticOrchestrator failed: {exc}\n\nDetail:\n{self._last_error}",
                    encoding="utf-8",
                )
            return self._fallback.plan_timeline(transcript, style, source)

    def _run(
        self,
        transcript: Transcript,
        style: StyleConfig,
        source: MediaRef,
        *,
        interactive: bool,
    ) -> Timeline:
        # ----------------------------------------------------------------- #
        # Phase 0: structural (deterministic) — tighten + beat segmentation
        # ----------------------------------------------------------------- #
        structural = _structural_skills(style)
        tl0 = compose_timeline(structural, transcript, style, source)

        # ----------------------------------------------------------------- #
        # Build base context that all agents share
        # ----------------------------------------------------------------- #
        ctx = Context().with_(
            transcript_summary=_transcript_summary(transcript),
            beats=_format_beats(tl0.beats),
            toolbox=json.dumps(toolbox_catalog(), indent=2),
            style_name=style.name,
            caption_mode=style.captions.mode,
            word_animation=style.captions.word_animation,
        )

        # ----------------------------------------------------------------- #
        # Phase 1: optional clarification (interactive only)
        # ----------------------------------------------------------------- #
        if interactive:
            ctx = self._clarify(ctx, transcript)

        # ----------------------------------------------------------------- #
        # Phase 2: content analysis
        # ----------------------------------------------------------------- #
        ctx = self._analyse(ctx)

        # ----------------------------------------------------------------- #
        # Phase 3: plan in reflection loop (plan-space only, ≤ 2 rounds)
        # ----------------------------------------------------------------- #
        plan = self._plan_with_reflection(ctx, structural, style, transcript, source)

        # ----------------------------------------------------------------- #
        # Phase 4: final compose (structural + decoration)
        # ----------------------------------------------------------------- #
        decoration = skills_from_plan(plan, style)
        return compose_timeline(
            structural + decoration,
            transcript, style, source,
            reason=plan.reason,
        )

    def _clarify(self, ctx: Context, transcript: Transcript) -> Context:
        """Ask the Clarifier what to ask, run the CLI interview, then acknowledge."""
        result = self._clarifier.invoke("Ask what you need to edit this clip well.", ctx)
        qset: QuestionSet | None = result.parsed
        io = CliIO()
        if qset is None:
            io.acknowledge()
            return ctx
        answers = io.ask(qset.questions, preamble=qset.preamble)
        io.acknowledge()   # Q&A is over; planning begins
        intent = CreativeIntent(answers=answers)
        return ctx.with_(intent=intent.render())

    def _analyse(self, ctx: Context) -> Context:
        """Run the Analyst; add its output to context (best-effort)."""
        result = self._analyst.invoke("Analyze this clip for editing cues.", ctx)
        analysis: ContentAnalysis | None = result.parsed
        if analysis is None:
            return ctx
        return ctx.with_(analysis=analysis.render())

    def _plan_with_reflection(
        self,
        ctx: Context,
        structural: list,
        style: StyleConfig,
        transcript: Transcript,
        source: MediaRef,
        max_rounds: int = 2,
    ) -> SkillPlan:
        """Evaluator-optimizer loop in plan-space.

        Planner proposes → trial compose (no FFmpeg) → coherence check + Critic
        taste review → Planner revises if not approved. Bounded to max_rounds;
        last plan is used regardless. Coherence gate still runs downstream.
        """
        result = self._planner.invoke("Produce the edit plan for this clip.", ctx)
        plan: SkillPlan | None = result.parsed

        if plan is None:
            raise ValueError("Planner returned unparseable output")

        for round_num in range(max_rounds):
            tl_trial, violations = _trial_compose(structural, plan, style, transcript, source)

            # Hard coherence errors → always revise.
            errors = [v for v in violations if "error" in v.lower()]

            critique_ctx = ctx.with_(
                proposed_plan=plan.model_dump_json(indent=2),
                coherence_violations=json.dumps(violations, indent=2),
            )
            critique_result = self._critic.invoke(
                "Review this plan. Approve or provide concrete fixes.", critique_ctx
            )
            critique: PlanCritique | None = critique_result.parsed

            if critique is None:
                break  # critic failed; use current plan

            if critique.approved and not errors:
                break  # good plan — exit the loop

            # Build revision context with feedback.
            fixes_text = "\n".join(f"- {f}" for f in (critique.fixes or []))
            issues_text = "\n".join(f"- {i}" for i in (critique.taste_issues or []))
            feedback = (
                f"The plan was NOT approved.\n\n"
                f"Taste issues:\n{issues_text or '(none)'}\n\n"
                f"Requested fixes:\n{fixes_text or '(none)'}\n\n"
                f"Coherence violations:\n{json.dumps(violations, indent=2)}"
            )
            revision_result = self._planner.invoke(
                "Revise the plan to address the feedback.",
                ctx.with_(feedback=feedback, previous_plan=plan.model_dump_json(indent=2)),
            )
            revised: SkillPlan | None = revision_result.parsed
            if revised is not None:
                plan = revised

        return plan


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text
