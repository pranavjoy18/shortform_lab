"""Data contracts for the interactive planning workflow.

Two interview layers run before planning:

  1. ``SetupInterview`` (deterministic) — asks about style, layout, color grade,
     tightening, and capitalization. Returns ``SetupChoices``; the CLI applies
     those to the ``StyleConfig`` before anything else runs.

  2. LLM Clarifier → ``QuestionSet`` → ``CliIO.ask()`` — asks about creative
     intent (tone, pacing, emphasis). Returns ``CreativeIntent``.

After both interviews ``CliIO.acknowledge()`` prints a confirmation before the
LLM planning phases start, so the user knows the Q&A phase is over.

All LLM-produced types (``QuestionSet``, ``ContentAnalysis``, ``PlanCritique``)
are Pydantic models validated through the agent's parse-or-repair path.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Style and look descriptions (one line each, shown in the setup interview)
# --------------------------------------------------------------------------- #
STYLE_DESCRIPTIONS: dict[str, str] = {
    "bold_creator":    "Bold sentence captions that pop — high-energy creator look",
    "clean_captions":  "Minimal sentence captions — clean, professional, content-forward",
    "word_pop":        "Word-by-word animated captions — highlights each spoken word",
    "karaoke":         "Color sweeps across the line as each word is spoken",
    "one_word":        "One large word at a time, centered — maximum visual punch",
    "reels_letterbox": "Letterbox layout with word captions — cinematic black-bars look",
    "viral_creator":   "High-energy word captions with vivid color grade and fast cuts",
}

LOOK_DESCRIPTIONS: dict[str, str] = {
    "vivid":     "boosted saturation and contrast — pops on mobile",
    "punchy":    "slightly brighter with more contrast — energetic",
    "cinematic": "desaturated, slightly darker — film-like and moody",
    "soft":      "gentle, slightly lower contrast — calm and approachable",
    "bright":    "lifted brightness — airy and light",
    "mono":      "black and white — stark and editorial",
}


# --------------------------------------------------------------------------- #
# Setup interview — deterministic, no LLM
# --------------------------------------------------------------------------- #
@dataclass
class SetupChoices:
    """Resolved user choices from the setup interview."""
    style_name: str
    layout: str              # "fill" | "letterbox"
    color_look: str | None   # named look or None
    tighten: bool
    uppercase: bool          # default True


class SetupInterview:
    """Deterministic pre-planning interview: style, layout, color, caps.

    These are configuration choices that drive deterministic code — the LLM
    Clarifier asks about creative intent afterward. Keeping them separate means
    the LLM never needs to know about YAML style names or codec settings.
    """

    def run(self, available_styles: list[str]) -> SetupChoices:
        """Ask about structure and pure preferences — NOT color.

        Color grade is content-aware (it can vary per beat) so it belongs to the
        LLM Clarifier, which has the beat map and can ask nuanced questions like
        "cinematic overall, mono when the serious topics come up." Asking for a
        single fixed color choice here would prevent that kind of expression.
        """
        io = CliIO()

        # --- Style ---
        style_choices = [
            f"{n}  —  {STYLE_DESCRIPTIONS.get(n, n)}" for n in available_styles
        ]
        style_answers = io.ask(
            [Question(key="style", text="Which caption style would you like?", choices=style_choices)],
            preamble="=== Configure your edit ===",
        )
        style_name = style_answers["style"].split(" — ")[0].split("  —  ")[0].strip()
        if style_name not in available_styles:
            style_name = available_styles[0]

        # --- Layout ---
        layout_choices = [
            "fill  —  crop the source to cover the full 9:16 frame (portrait footage)",
            "letterbox  —  fit the whole source with black bars (landscape footage)",
        ]
        layout_answers = io.ask(
            [Question(key="layout", text="How should the video fill the frame?", choices=layout_choices)]
        )
        layout = "letterbox" if layout_answers["layout"].startswith("letterbox") else "fill"

        # --- Silence tightening ---
        tighten_choices = [
            "yes  —  cut long pauses and silences (recommended for talking-head clips)",
            "no   —  keep the pacing exactly as recorded",
        ]
        tighten_answers = io.ask(
            [Question(key="tighten", text="Remove long silences and pauses?", choices=tighten_choices)]
        )
        tighten = tighten_answers["tighten"].startswith("yes")

        # --- Capitalization (default ALL CAPS) ---
        caps_choices = [
            "ALL CAPS (default)  —  bold, attention-grabbing, suits most short-form styles",
            "Sentence case  —  natural, conversational, easier to read quickly",
        ]
        caps_answers = io.ask(
            [Question(
                key="uppercase",
                text="Caption and hook text style? (default: ALL CAPS)",
                choices=caps_choices,
            )]
        )
        uppercase = not caps_answers["uppercase"].startswith("Sentence")

        return SetupChoices(
            style_name=style_name,
            layout=layout,
            color_look=None,   # intentionally absent — Clarifier handles color
            tighten=tighten,
            uppercase=uppercase,
        )


# --------------------------------------------------------------------------- #
# Clarifier outputs (LLM-produced)
# --------------------------------------------------------------------------- #
class Question(BaseModel):
    """One interview question, optionally with pre-defined choices.

    ``choices=None`` means free-text answer. When choices are present the CLI
    accepts either the number, the full choice string, or the text before the
    first ' — ' separator so users can type the key quickly.
    """

    key: str
    text: str
    choices: list[str] | None = None


class QuestionSet(BaseModel):
    """The Clarifier's output: a short list of focused questions."""

    questions: list[Question] = Field(min_length=1, max_length=5)
    preamble: str | None = None


# --------------------------------------------------------------------------- #
# User's answers (collected by CliIO)
# --------------------------------------------------------------------------- #
class CreativeIntent(BaseModel):
    """The creator's stated preferences, keyed by the question's ``key``."""

    answers: dict[str, str]

    def render(self) -> str:
        return "\n".join(f"- {k}: {v}" for k, v in self.answers.items())


# --------------------------------------------------------------------------- #
# Analyst output (LLM-produced)
# --------------------------------------------------------------------------- #
class ContentAnalysis(BaseModel):
    topic: str
    arc: str
    energy_profile: str
    emphasis_moments: list[str]
    recommended_look: str | None = None

    def render(self) -> str:
        lines = [
            f"Topic: {self.topic}",
            f"Arc: {self.arc}",
            f"Energy: {self.energy_profile}",
        ]
        if self.emphasis_moments:
            lines.append("Emphasis moments:\n" + "\n".join(f"  - {m}" for m in self.emphasis_moments))
        if self.recommended_look:
            lines.append(f"Suggested color look: {self.recommended_look}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Critic output (LLM-produced)
# --------------------------------------------------------------------------- #
class PlanCritique(BaseModel):
    approved: bool
    taste_issues: list[str] = Field(default_factory=list)
    fixes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# CLI I/O — deterministic, no LLM
# --------------------------------------------------------------------------- #
class CliIO:
    """Renders questions to the terminal and collects typed answers.

    Accepts: the choice number, the full choice string, or the text before the
    first ' — ' or '  —  ' separator (so users can type "bold_creator" to match
    "bold_creator  —  Bold sentence captions...").
    """

    def ask(self, questions: list[Question], preamble: str | None = None) -> dict[str, str]:
        import typer

        if preamble:
            typer.echo(f"\n{preamble}")

        answers: dict[str, str] = {}
        for q in questions:
            typer.echo(f"\n{q.text}")
            if q.choices:
                for i, choice in enumerate(q.choices, 1):
                    typer.echo(f"  {i}. {choice}")
                while True:
                    raw = typer.prompt("Your choice (number or text)").strip()
                    matched = self._match_choice(raw, q.choices)
                    if matched is not None:
                        answers[q.key] = matched
                        break
                    typer.echo(f"  Enter a number 1–{len(q.choices)} or one of the options above.")
            else:
                answers[q.key] = typer.prompt("Your answer").strip()

        return answers

    def acknowledge(self) -> None:
        import typer
        typer.echo(
            "\n✓ Got it! Analyzing your clip and building the edit plan"
            " — this may take a moment...\n"
        )

    @staticmethod
    def _match_choice(raw: str, choices: list[str]) -> str | None:
        """Return the matched choice string or None."""
        # 1. Numeric index
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        # 2. Exact full string
        if raw in choices:
            return raw
        # 3. Key prefix before ' — ' or '  —  ' (case-insensitive)
        raw_lower = raw.lower()
        for choice in choices:
            key = choice.split("  —  ")[0].split(" — ")[0].strip().lower()
            if raw_lower == key:
                return choice
        return None
