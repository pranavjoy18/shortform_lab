# Architecture: Timeline + Skills + Orchestration

**Status:** Accepted (ADR). Direction locked 2026-06-04; **milestone 1 in
progress**. The current pipeline (`cli.py` → `plan_edits` → `EditPlan` →
`render_video`) keeps working untouched while we build the substrate alongside
it.

**Locked decisions (2026-06-04):**
- **Multi-source data model = "Model C"** — the `Timeline` types are designed
  multi-source from day one (`sources: list[MediaRef]`, clips that name their
  `source_id`), but we *operate* with a single contiguous talking-head **spine**
  for now, enforced by the coherence layer. Future features (b-roll, images,
  audio-bearing inserts) relax a constraint rather than rewrite the model.
- **Spine audio is the timebase** — captions, tightening, and output time pin to
  the spine source's audio; video tracks cut away above it (b-roll/images cover
  *picture* while the speaker keeps talking). This is the primary editing mode and
  preserves today's clean output-time ↔ source-time map. See Layer 1.
- **Single agentic loop, not segment-parallel subagents** — for ~90s talking-head
  clips, multi-agent fan-out is too token-expensive and unnecessary. We build
  milestones 1–4 (substrate + a single orchestrator loop) and **drop milestone 5**
  until a long/multi-scene workload justifies it.

## Why

`shortform_lab` started as a linear pipeline. Each feature (captions, layout,
tighten, hook, overlays) was added by editing `planner.py` + `render.py` + the
config. That doesn't scale: features tangle together, ordering is implicit, and
there's no clean place to add "agentic" decision-making.

We want to think of it as a real **video editor**: one core **Timeline**, edited
by **skills** (each feature is a skill; fundamental skills compose into bigger
ones), driven by an **orchestrator** that can plan work and optionally delegate
timeline segments to parallel subagents, with a **deterministic coherence layer**
that validates every edit before it renders.

## The load-bearing principle

> **Agents decide; deterministic code executes and verifies.**
> Orchestrator and subagents only ever propose **edits to the Timeline**. They
> never touch pixels or FFmpeg. A single deterministic renderer turns the
> *validated* Timeline into video. A deterministic coherence layer gates every
> proposed edit before it is accepted.

This is the existing `EditPlan` → validate → render flow generalized. It keeps the
system inspectable, testable, reproducible, and cheap to fall back to. It is the
non-negotiable invariant of this architecture.

## Layers

### 1. Timeline — the core domain model (evolves `EditPlan`)

One time base: **integer milliseconds, output time** (the tightened timeline).
**Multi-source (Model C):** a `sources` registry of media, with one designated
**spine** — the talking-head clip whose audio defines the timebase. Every clip
names the source it draws from; today the spine is the only video source, enforced
by the coherence layer.

```
Timeline
  sources:        list[MediaRef]   # id, path, kind{video|image|audio}, w, h, duration_ms, fps, has_audio, label
  spine_source_id: str             # the talking-head source whose AUDIO is the timebase
  tracks:
    spine:    list[Clip]    # SEQUENTIAL base video/audio: the cut list. Positions derived
                            #   back-to-back; empty = whole spine uncut. (today's keep_ranges)
    reframe:  list[Clip]    # how source fills 9:16 over time (fill/letterbox; future: track-to-speaker)
    captions: list[CaptionCue]  # FLOATING (explicit output-time); may carry per-word timings
    overlays: list[Clip]    # FLOATING: cards / lower-thirds / b-roll / images
    effects:  list[Clip]    # FLOATING: punch-in/zoom and future effects
    audio:    list[Clip]    # FLOATING: music bed, ducking, normalize
  export:   ExportSettings  # size, fps, layout default
  meta:     dict            # provenance: which skills ran, with what params

Clip: source_id, source_in_ms, duration_ms, timeline_start_ms?, track,
      speed=1.0, transition_in?, transition_out?   # speed/transition reserved; coherence rejects non-default
```

**Two kinds of track** (both already exist in today's model):
- **Sequential** — ordered clips whose positions are *derived* back-to-back (the
  spine, i.e. today's `keep_ranges`). More hand-editable: no absolute positions to
  renumber. Editing one clip's duration reflows the rest.
- **Floating** — clips carry explicit output-time `timeline_start_ms` (captions,
  overlays, effects — already `start_ms`/`end_ms` today).

**Key unifying insight:** today's `keep_ranges` *is* a degenerate sequential
spine track — a cut-list of source spans concatenated back-to-back is exactly a
list of base-track clips. `tighten` is the skill that emits it; `remap_ms` is the
spine's source↔output time map. "Multi-source" just generalizes the single
implicit source to a clip that names its `source_id`.

Properties to preserve from today's `EditPlan`:
- It is the **single source of truth** the renderer consumes.
- It is **validated** before it becomes FFmpeg.
- `timeline.json` is **hand-editable and re-renderable**.
- Disabled features are **absent from the Timeline** (gated upstream), so the
  JSON, logs, and video always agree.

### 2. Skills — composable operations on the Timeline

Every feature is a skill. A skill is a pure-ish transform that declares what it
needs and what it produces, so skills can be ordered, parallelized, and checked.

```python
class Skill(Protocol):
    name: str
    reads:  frozenset[str]   # e.g. {"transcript"}        - preconditions
    writes: frozenset[str]   # e.g. {"cuts"} / {"captions"} - postconditions

    def apply(self, tl: Timeline, ctx: Context, *, span: TimeRange | None = None) -> Timeline:
        ...
```

- **Fundamental skills** (port existing features): `transcribe`, `tighten_silence`,
  `add_captions`, `reframe` (fill/letterbox), `punch_in`, `hook`, `overlay`.
- **Composed skills** = an ordered set of fundamental skills + params, themselves
  exposing combined `reads`/`writes`.
- Skills are **pure with respect to media**: they edit the Timeline only. They
  never call FFmpeg. This keeps them unit-testable offline (like `tighten.py`).
- The `reads`/`writes` graph lets a planner **topologically order** skills and
  detect missing preconditions (e.g. `add_captions` requires `transcript`).

### 3. Toolboxes

A toolbox is a curated set of skills exposed with JSON schemas — the
function-calling surface an agent may use. Different agents get different
toolboxes (e.g. a "captioning" subagent only sees caption/overlay skills).

### 4. Orchestrator + subagents

The orchestrator reads the Timeline + goal and plans a skill sequence (ordered
from the reads/writes graph) and **runs the sequence itself — a single agentic
loop**. This is the committed target. (Segment-parallel subagents — splitting the
Timeline by span and merging patches — were considered and **deferred**; see
Non-goals and milestone 5.)

The deterministic default pipeline (today's `DeterministicPlanner`) is just an
orchestrator that runs a fixed composed skill with no LLM. The LLM orchestrator
(today's `LLMPlanner`) is one strategy that proposes the skill plan / Timeline
edits — always validated, always able to fall back deterministically.

### 5. Coherence / validation — deterministic ("logical inference")

After any proposed edit — especially merged parallel patches — deterministic
validators enforce Timeline invariants before render:
- timestamps within source/cut bounds; cuts sorted, non-overlapping;
- caption cues monotonic and non-overlapping within a track;
- no double-cut or style discontinuity at span **seams** after a parallel merge;
- every skill's postconditions actually hold; preconditions were met.

Invalid edits are **rejected or auto-repaired, never rendered**. This is
`EditPlan`'s Pydantic validation grown into a cross-track, cross-segment pass.

### Renderer — the single deterministic sink

Exactly one component turns `Timeline` → FFmpeg (today's `render.py`). Skills
never emit FFmpeg; they only edit the Timeline. This preserves the "one place
builds the filtergraph" property and keeps the agentic layers pure.

## Migration map (from today's code)

| Today | Target |
|---|---|
| `EditPlan` (`models.py`) | `Timeline` (multi-track) |
| `tighten.py`, `build_captions`, layout logic, punch-in, hook, overlay | individual **Skills** under `skills/` |
| `StyleConfig` YAML (`configs/styles/`) | **composed skills / presets** (skill list + params) |
| `DeterministicPlanner` | default deterministic **orchestrator** (a composed skill) |
| `LLMPlanner` | ✅ `LLMOrchestrator` — picks skills from a toolbox, composes the Timeline, validated, falls back |
| Pydantic model validators | the **coherence layer** (extended, cross-track/segment) |
| `render.py` `render_video` | the single **Timeline → FFmpeg** sink |
| `--style/--layout/--tighten` flags | preset selection + per-skill param overrides |

## Design invariants (must hold after migration)

- Agents never touch FFmpeg/pixels — they edit the Timeline only.
- The Timeline is validated before render; invalid never renders.
- A fully **deterministic, offline, instant** path remains the default; agentic
  orchestration is opt-in (like `--use-llm` today) and always has a deterministic
  fallback.
- Disabled features are absent from the Timeline (gated in skills/orchestration,
  not the renderer).
- `timeline.json` stays hand-editable and re-renderable.
- Timestamps are integer milliseconds throughout.

## Non-goals / cautions

- **Segment-parallel subagents are out of scope** (decision 2026-06-04). For ~90s
  talking-head clips the parallelism doesn't pay for its token cost; a single
  agentic loop suffices. Revisit only if a long/multi-scene workload appears.
- **Audio-bearing inserts are deferred.** The spine-audio rule means b-roll/images
  cover *picture* only while the speaker keeps talking. A cutaway that interrupts
  and plays its own sound would require pausing/remapping captions across the gap —
  out of scope for now, but the `Clip` type already carries what it would need.
- **Don't let LLMs do deterministic work.** Picking *which* skills and *what*
  params is agentic; computing cuts, caption timings, and filtergraphs is not.
- Cost/latency/nondeterminism: keep agentic runs opt-in and cached/loggable.

## Incremental milestones

1. **Foundational refactor** *(in progress)* — introduce `Timeline` + `Skill`
   protocol; port existing features to skills; keep one deterministic renderer. No
   agents. This is the modularization itself and makes every future feature a
   self-contained skill. *(High value, low risk, no LLM.)*
   - **1a (done):** `Timeline`/`Clip`/`MediaRef` (Model C) + `Skill` protocol;
     `tighten` ported as the first skill emitting the spine cut list; spine →
     `keep_ranges` adapter, with behavior-equivalence tests against the current
     planner. Existing pipeline untouched.
   - **1b:** port the remaining features to skills (`reframe`, `add_captions`,
     `punch_in`, `hook`, `overlay`) and a full `Timeline → EditPlan` adapter so the
     existing renderer consumes a Timeline; then reroute `plan_edits` through skills.
2. **Coherence layer** *(done, 2026-06-04)* — `coherence.py` (`check_timeline`/
   `validate_timeline`, error-vs-warning `Violation`s: inverted/overlapping ranges
   and out-of-bounds words are errors; elements past the output duration are
   warnings) + `skills/runner.py` `SkillRunner` (reads precondition + writes-purity
   postcondition). The orchestrator gates every Timeline through `validate_timeline`
   before returning, stashing warnings in `meta["coherence_warnings"]`.
3. **Deterministic orchestrator** *(done, 2026-06-04)* — a preset (`presets.py`
   `preset_from_style`) is a gated *set* of skills; `skills/graph.py`
   `topological_order` derives the run order from the reads/writes graph (stable
   tiebreak = preset order). The floating skills declare a read on `spine` so the
   sort places `tighten` first; the orchestrator no longer hand-orders skills. The
   chosen preset format is **declarative selection from the style + graph-derived
   order** (resolving the open question in favor of not hand-maintaining an ordered
   YAML list yet). `DeterministicOrchestrator` already replaced the old
   `DeterministicPlanner` body in 1b; this removed the last hardcoded sequencing.
4. **LLM orchestrator (single agentic loop)** *(done, 2026-06-04)* — `toolbox.py`
   exposes the skills with strict param schemas + a `SkillPlan` output shape;
   `llm_orchestrator.py` `LLMOrchestrator` asks the LLM to choose *which* skills and
   *what* params (never timestamps/export), validates the plan against the toolbox,
   composes it via the shared `compose_timeline` (topo-order → run → coherence-gate),
   and **falls back to `DeterministicOrchestrator` on any failure** (raw response →
   `planner_error.txt`). Replaced the old `LLMPlanner`. Runs the sequence itself; no
   subagents. Verified end-to-end against the live API.
5. **~~Segment-parallel subagents~~** — **dropped** (2026-06-04). Reconsider only
   if a long/multi-scene workload demonstrably benefits.

Steps 1–3 are pure software architecture. Step 4 is where the agentic layer arrives.

## Open questions (to resolve before/while building)

- **Transitions are seam objects, not a track.** A dissolve is a relationship
  between two adjacent clips (or clip↔black), and it needs **handle frames** —
  source material *beyond* the cut to dissolve into — which interacts with
  `tighten` (it can't always trim to the exact spoken frame). How are transitions
  represented (edges on a track?) and how does the coherence layer verify handles?
- **Hand-editability of floating positions.** Absolute `timeline_start_ms` is
  awkward to hand-edit (move one clip, renumber the rest). The sequential/floating
  split mitigates it; decide the `timeline.json` ergonomics deliberately.
- **Patch representation**: do skills return a whole new Timeline, or a typed
  diff/patch? (Milestone 1a uses whole-Timeline `model_copy`.) Revisit if/when
  provenance or merges need it.
- ~~**Preset format**~~ — resolved (milestone 3): YAML stays declarative; the skill
  *set* is gated from the style and the *order* is derived from the reads/writes
  graph. An explicit ordered YAML skill list can still come later if needed.
- **Skill params vs style**: where do per-skill params live when an orchestrator
  overrides a preset? (Today params come from the `StyleConfig` inside
  `preset_from_style`; an LLM orchestrator overriding them is milestone 4.)
- ~~**Span boundaries**~~ — moot while subagents are out of scope (milestone 5).
