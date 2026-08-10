"""Tests for the agent layer and the AgenticOrchestrator.

All LLM calls use FakeClient — the suite runs fully offline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shortform_lab.agent import Agent, Context, FakeClient, Tool
from shortform_lab.config import load_style_config
from shortform_lab.intent import ContentAnalysis, CreativeIntent, PlanCritique, QuestionSet
from shortform_lab.llm_orchestrator import AgenticOrchestrator
from shortform_lab.timeline import MediaRef
from shortform_lab.toolbox import SkillPlan
from shortform_lab.transcribe import load_transcript
from shortform_lab.workers import make_analyst, make_clarifier, make_critic, make_planner

FIXTURE = Path(__file__).parent / "fixtures" / "transcript_sample.json"


def _transcript():
    return load_transcript(FIXTURE)


def _source(duration_ms: int = 12000):
    return MediaRef(id="spine", path="source.mp4", kind="video", duration_ms=duration_ms, has_audio=True)


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #
def test_context_with_merges_immutably():
    c1 = Context().with_(a="hello")
    c2 = c1.with_(b="world")
    assert "a" in c1.items and "b" not in c1.items
    assert "a" in c2.items and "b" in c2.items


def test_context_render_produces_headed_sections():
    rendered = Context().with_(beats="hook 0→1800ms", intent="energetic").render()
    assert "### beats" in rendered
    assert "### intent" in rendered


def test_context_stringify_handles_pydantic_model():
    from shortform_lab.models import ColorGrade
    ctx = Context().with_(grade=ColorGrade(saturation=1.4))
    assert "saturation" in ctx.items["grade"]


# --------------------------------------------------------------------------- #
# FakeClient + Agent.invoke basics
# --------------------------------------------------------------------------- #
def test_agent_invoke_returns_text():
    agent = Agent("t", "be helpful", FakeClient('{"msg": "hi"}'))
    result = agent.invoke("hello")
    assert result.text == '{"msg": "hi"}'
    assert result.parsed is None  # no output_schema


def test_agent_invoke_parses_structured_output():
    from pydantic import BaseModel

    class Out(BaseModel):
        value: int

    agent = Agent("t", "return json", FakeClient('{"value": 42}'), output_schema=Out)
    result = agent.invoke("go")
    assert isinstance(result.parsed, Out)
    assert result.parsed.value == 42


def test_agent_self_repair_on_bad_json():
    """First call returns garbage; repair call returns valid JSON."""
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int

    calls = []

    def dynamic(system, messages):
        calls.append(len(messages))
        return '{"x": 7}' if len(calls) > 1 else "oops not json"

    agent = Agent("t", "json", FakeClient(dynamic), output_schema=Out)
    result = agent.invoke("go")
    assert result.parsed is not None and result.parsed.x == 7
    assert len(calls) == 2  # initial + one repair


def test_agent_parsed_is_none_when_repair_also_fails():
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int

    agent = Agent("t", "json", FakeClient("still broken"), output_schema=Out)
    result = agent.invoke("go")
    assert result.parsed is None


def test_agent_history_is_threaded_through_messages():
    history = [{"role": "user", "content": "previous"}, {"role": "assistant", "content": "ok"}]
    seen = []

    def capture(system, messages):
        seen.extend(messages)
        return "done"

    agent = Agent("t", "be helpful", FakeClient(capture))
    agent.invoke("new task", history=history)
    assert seen[0]["content"] == "previous"  # history preserved
    assert seen[-1]["content"] == "new task"


def test_agent_tool_catalog_appears_in_system_prompt():
    from pydantic import BaseModel

    class Params(BaseModel):
        count: int

    tool = Tool("my_tool", "does a thing", Params)
    seen_system = []

    def capture(system, messages):
        seen_system.append(system)
        return "done"

    agent = Agent("t", "use tools", FakeClient(capture), tools=(tool,))
    agent.invoke("go")
    assert "my_tool" in seen_system[0]
    assert "does a thing" in seen_system[0]


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #
def test_make_clarifier_returns_question_set():
    qs = QuestionSet(questions=[
        {"key": "vibe", "text": "What vibe?", "choices": ["energetic", "calm"]}
    ])
    client = FakeClient(qs.model_dump_json())
    agent = make_clarifier(client)
    result = agent.invoke("ask questions", Context().with_(transcript_summary="..."))
    assert isinstance(result.parsed, QuestionSet)
    assert result.parsed.questions[0].key == "vibe"


def test_make_analyst_returns_content_analysis():
    analysis = ContentAnalysis(
        topic="productivity tips",
        arc="hook → problem → tips → CTA",
        energy_profile="starts high, dips in the middle, picks up for CTA",
        emphasis_moments=["tip 1 is the hook", "tip 3 is the payoff"],
        recommended_look="punchy",
    )
    client = FakeClient(analysis.model_dump_json())
    agent = make_analyst(client)
    result = agent.invoke("analyse", Context())
    assert isinstance(result.parsed, ContentAnalysis)
    assert result.parsed.recommended_look == "punchy"


def test_make_planner_returns_skill_plan():
    plan = SkillPlan(
        skills=[{"name": "add_captions", "params": {}}, {"name": "punch_in", "params": {"count": 2}}],
        reason="captions + rhythm",
    )
    client = FakeClient(plan.model_dump_json())
    agent = make_planner(client)
    result = agent.invoke("plan", Context())
    assert isinstance(result.parsed, SkillPlan)
    assert any(s.name == "add_captions" for s in result.parsed.skills)


def test_make_critic_returns_plan_critique():
    critique = PlanCritique(approved=True, taste_issues=[], fixes=[])
    client = FakeClient(critique.model_dump_json())
    agent = make_critic(client)
    result = agent.invoke("critique", Context())
    assert isinstance(result.parsed, PlanCritique)
    assert result.parsed.approved is True


# --------------------------------------------------------------------------- #
# AgenticOrchestrator (non-interactive, fully offline)
# --------------------------------------------------------------------------- #
def _agentic_client(plans_approved: bool = True) -> FakeClient:
    """Returns a FakeClient that routes responses by the agent's name in the system."""
    analysis = ContentAnalysis(
        topic="demo topic", arc="hook→payoff", energy_profile="high throughout",
        emphasis_moments=["moment 1"], recommended_look="punchy",
    ).model_dump_json()

    plan = SkillPlan(
        skills=[{"name": "add_captions", "params": {}}, {"name": "punch_in", "params": {"count": 1}}],
        reason="clean captions + zoom",
    ).model_dump_json()

    critique = PlanCritique(approved=plans_approved, taste_issues=[], fixes=[]).model_dump_json()

    def route(system: str, messages):
        if "Analyst" in system or "energy_profile" in system:
            return analysis
        if "Critic" in system or "taste" in system.lower():
            return critique
        return plan  # Planner

    return FakeClient(route)


def test_agentic_orchestrator_produces_valid_timeline():
    style = load_style_config("bold_creator")
    orc = AgenticOrchestrator(client=_agentic_client())
    tl = orc.plan_timeline(_transcript(), style, _source(_transcript().duration_ms))
    assert tl.captions
    assert tl.beats  # structural phase always runs


def test_agentic_orchestrator_reflection_loop_revises_when_not_approved():
    """When critic rejects, planner is called again for revision."""
    style = load_style_config("bold_creator")
    call_log: list[str] = []

    plan = SkillPlan(
        skills=[{"name": "add_captions", "params": {}}],
        reason="revised",
    ).model_dump_json()
    analysis = ContentAnalysis(
        topic="t", arc="a", energy_profile="e", emphasis_moments=[]
    ).model_dump_json()

    reject = PlanCritique(approved=False, taste_issues=["too bare"], fixes=["add punch_in"]).model_dump_json()
    approve = PlanCritique(approved=True).model_dump_json()

    critique_calls = [0]

    def route(system: str, messages):
        if "energy_profile" in system:
            return analysis
        if "taste" in system.lower() or "Critic" in system:
            critique_calls[0] += 1
            return reject if critique_calls[0] == 1 else approve
        call_log.append("planner")
        return plan

    orc = AgenticOrchestrator(client=FakeClient(route))
    tl = orc.plan_timeline(_transcript(), style, _source(_transcript().duration_ms))
    assert tl.captions
    assert len(call_log) >= 2  # initial plan + at least one revision


def test_agentic_orchestrator_falls_back_on_total_failure():
    """If the planner returns unparseable JSON the fallback still produces a video."""
    style = load_style_config("bold_creator")
    orc = AgenticOrchestrator(client=FakeClient("not json at all"))
    tl = orc.plan_timeline(_transcript(), style, _source(_transcript().duration_ms))
    assert tl.captions  # deterministic fallback


def test_agentic_orchestrator_writes_error_file_on_failure(tmp_path):
    style = load_style_config("bold_creator")
    orc = AgenticOrchestrator(client=FakeClient("broken"))
    err = tmp_path / "err.txt"
    orc.plan_timeline(_transcript(), style, _source(_transcript().duration_ms), error_path=err)
    assert err.is_file()
