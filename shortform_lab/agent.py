"""Core agent primitives for the agentic planning layer.

The single ``Agent`` class covers every worker (Clarifier, Analyst, Planner,
Critic): they differ only in ``instructions``, ``output_schema``, and bound
``tools``. Composition over inheritance — no subclasses, only factory
functions that configure instances (see ``workers.py``).

Key design decisions:
  - ``Context`` is injected at ``invoke()`` time, not baked into the prompt,
    so the same ``Agent`` is reusable across calls and across tests.
  - ``LLMClient`` is a Protocol → ``FakeClient`` makes every agent testable
    offline, matching the project's offline-first ethos.
  - Tools bound to the Planner are a MENU (``callable=None``): the model
    selects via structured output; deterministic code executes. The safety
    property "agents decide; deterministic code executes and verifies" holds.
  - One bounded self-repair attempt on JSON parse failure — never an infinite
    loop, never silently swallows the error (``parsed=None`` signals failure).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol, TypedDict

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Wire protocol
# --------------------------------------------------------------------------- #
class Msg(TypedDict):
    role: str     # "user" | "assistant" | "system"
    content: str


def user(content: str) -> Msg:
    return {"role": "user", "content": content}


def assistant(content: str) -> Msg:
    return {"role": "assistant", "content": content}


# --------------------------------------------------------------------------- #
# LLMClient protocol (the one seam to the API)
# --------------------------------------------------------------------------- #
class LLMClient(Protocol):
    """The single seam between the agent layer and the LLM API.

    A ``FakeClient`` implements this for offline tests; ``OpenAIClient`` is the
    real implementation. The agent layer depends only on this protocol, so
    swapping providers or going offline requires no changes above this line.
    """

    def complete(
        self,
        system: str,
        messages: list[Msg],
        *,
        schema: type[BaseModel] | None = None,
    ) -> str:
        ...


@dataclass
class OpenAIClient:
    """Real LLM client. Lazy-imports openai so the offline path stays clean."""

    model: str = "gpt-4o-mini"

    def complete(
        self,
        system: str,
        messages: list[Msg],
        *,
        schema: type[BaseModel] | None = None,
    ) -> str:
        from openai import OpenAI  # lazy; optional extra

        fmt = {"type": "json_object"} if schema is not None else None
        resp = OpenAI().chat.completions.create(
            model=self.model,
            response_format=fmt,
            messages=[{"role": "system", "content": system}] + list(messages),
        )
        return resp.choices[0].message.content or ""


@dataclass
class FakeClient:
    """Canned-response client for offline tests.

    ``response`` can be a plain string (returned for every call) or a callable
    that receives ``(system, messages)`` and returns a string, enabling
    dynamic fakes that vary per call.
    """

    response: str | Callable[[str, list[Msg]], str]

    def complete(
        self,
        system: str,
        messages: list[Msg],
        *,
        schema: type[BaseModel] | None = None,
    ) -> str:
        if callable(self.response):
            return self.response(system, messages)
        return self.response


# --------------------------------------------------------------------------- #
# Context — immutable, composable facts injected at invoke() time
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Context:
    """An immutable bag of named facts injected into an agent at call time.

    Context is NOT baked into the system prompt (the agent's role is static and
    reusable). It is rendered as a structured block appended to the user turn so
    the model sees it as call-time data, not permanent instructions.

    ``with_`` returns a new Context with extra keys merged in, so context
    accumulates across orchestration phases without mutating the original.
    """

    items: dict[str, str] = field(default_factory=dict)

    def with_(self, **kv: Any) -> "Context":
        """Return a new Context with ``kv`` merged in (values are stringified)."""
        return Context({**self.items, **{k: _stringify(v) for k, v in kv.items()}})

    def render(self) -> str:
        """Render to a markdown-style block the model can parse at a glance."""
        if not self.items:
            return ""
        return "\n\n".join(f"### {k}\n{v}" for k, v in self.items.items())


def _stringify(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, BaseModel):
        return v.model_dump_json(indent=2)
    if isinstance(v, list):
        return json.dumps(
            [x.model_dump() if isinstance(x, BaseModel) else x for x in v],
            default=str, indent=2,
        )
    return str(v)


# --------------------------------------------------------------------------- #
# Tool — a capability bound to an agent
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tool:
    """A capability declaration bound to an agent.

    For the Planner, tools are a MENU surfaced in the system prompt and
    selected via structured JSON output (``callable=None`` — the model never
    *runs* a tool directly). The safety property is preserved: deterministic
    code executes and verifies; the model only picks.

    A ``callable`` may be set for future agents that need real function calling
    (e.g. a search tool), but today all tools in the Planner are menu-only.
    """

    name: str
    description: str
    params_schema: type[BaseModel]
    callable: Callable[..., Any] | None = None


# --------------------------------------------------------------------------- #
# AgentResult
# --------------------------------------------------------------------------- #
@dataclass
class AgentResult:
    text: str
    parsed: BaseModel | None          # populated when output_schema is set and parse succeeded
    messages: list[Msg]               # full turn history including this response (for multi-turn)


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
@dataclass
class Agent:
    """A configured LLM unit: role (instructions) + optional output schema + bound tools.

    The same class for every worker — Clarifier / Analyst / Planner / Critic
    differ only in configuration. ``client`` is injected so tests swap in
    ``FakeClient`` without touching the agent logic.
    """

    name: str
    instructions: str                          # static system prompt (role + rules)
    client: LLMClient
    output_schema: type[BaseModel] | None = None
    tools: tuple[Tool, ...] = ()

    def invoke(
        self,
        task: str,
        context: Context | None = None,
        *,
        history: list[Msg] | None = None,
    ) -> AgentResult:
        """Run one LLM turn, optionally with prior history for multi-turn conversations.

        When ``output_schema`` is set, attempts JSON parse + Pydantic validation.
        On failure, sends exactly one self-repair message and tries again. If
        the repair also fails, ``result.parsed`` is ``None`` — the caller decides
        how to handle the degraded result (the orchestrator falls back).
        """
        system = self._render_system()
        user_content = (context.render() + "\n\n" + task) if context else task
        msgs: list[Msg] = list(history or []) + [user(user_content)]

        raw = self.client.complete(system, msgs, schema=self.output_schema)
        parsed, raw, msgs = self._parse_with_repair(raw, system, msgs)
        full_history = msgs + [assistant(raw)]
        return AgentResult(text=raw, parsed=parsed, messages=full_history)

    # ------------------------------------------------------------------ #
    def _render_system(self) -> str:
        if not self.tools:
            return self.instructions
        catalog = json.dumps(
            [
                {
                    "name": t.name,
                    "description": t.description,
                    "params": t.params_schema.model_json_schema().get("properties", {}),
                }
                for t in self.tools
            ],
            indent=2,
        )
        return f"{self.instructions}\n\nAvailable tools (select via structured output):\n{catalog}"

    def _parse_with_repair(
        self,
        raw: str,
        system: str,
        msgs: list[Msg],
    ) -> tuple[BaseModel | None, str, list[Msg]]:
        if self.output_schema is None:
            return None, raw, msgs

        cleaned = _strip_fences(raw)
        try:
            return self.output_schema.model_validate_json(cleaned), raw, msgs
        except Exception as first_err:
            # One bounded self-repair: show the model what failed.
            repair_prompt = (
                f"Your response failed validation: {first_err}\n\n"
                "Return ONLY valid JSON that satisfies the schema. No prose, no fences."
            )
            repair_msgs: list[Msg] = msgs + [assistant(raw), user(repair_prompt)]
            raw2 = self.client.complete(system, repair_msgs, schema=self.output_schema)
            try:
                return self.output_schema.model_validate_json(_strip_fences(raw2)), raw2, repair_msgs
            except Exception:
                return None, raw2, repair_msgs  # caller handles None


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return m.group(1) if m else text
