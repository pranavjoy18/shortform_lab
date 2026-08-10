# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`shortform_lab` is a **local talking-head video enhancer**: it takes one short raw clip of a person speaking to camera and produces a polished vertical (9:16) short — hook, captions, simple overlays, punch-in zooms. Everything runs locally and is file-based. (The `pyproject.toml` project name is still `autoshorts`, a leftover from an earlier pivot; the actual package is `shortform_lab`.)

## Commands

```bash
uv sync                      # install (deterministic/offline path works with this alone)
uv sync --extra openai       # add OpenAI Whisper transcription + LLM planning
uv run pytest                # run the test suite
uv run pytest tests/test_planner.py::test_name   # run a single test

# Run the pipeline:
python -m shortform_lab.cli process input.mp4 --style bold_creator
python -m shortform_lab.cli process input.mp4 --style bold_creator --transcript t.json   # offline, no API
python -m shortform_lab.cli process input.mp4 --style bold_creator --use-llm             # LLM planner
```

Requires **FFmpeg** (`ffmpeg` and `ffprobe`) on PATH. The pipeline checks for this up front and fails loudly. FFmpeg-dependent tests generate a tiny clip on the fly (`tests/conftest.py`) and skip automatically when FFmpeg is absent.

## Smoke testing

> **Keep this section current.** Whenever you add or change a feature, update the
> commands/expectations below so there is always a copy-paste way to manually
> verify the latest behavior. This is the first thing to reach for after a change.

Run the whole pipeline offline (no API) on a throwaway clip and eyeball the result:

```bash
# 1. Make a throwaway clip. Use 16:9 to exercise the letterbox bars; for a real
#    check use one of your own clips instead.
ffmpeg -y -f lavfi -i "testsrc=size=1280x720:rate=30:duration=12" \
       -f lavfi -i "sine=frequency=220:duration=12" -shortest -pix_fmt yuv420p /tmp/smoke.mp4

# 2. Run the pipeline, composing the axes you touched. (uv run so deps resolve.)
uv run python -m shortform_lab.cli process /tmp/smoke.mp4 \
  --style word_pop --layout letterbox --tighten \
  --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/smoke_out

# 3. Eyeball a frame at a time where your feature is visible (Read the PNG).
ffmpeg -y -ss 0.5 -i /tmp/smoke_out/final.mp4 -frames:v 1 /tmp/smoke_frame.png

# 4. Inspect the artifacts.
cat /tmp/smoke_out/edit_plan.json   # keep_ranges (source-time), captions, export_layout
cat /tmp/smoke_out/review.md        # human summary incl. tightening
```

Knobs to compose: `--style {bold_creator,clean_captions,word_pop,karaoke,one_word,reels_letterbox}`,
`--layout {fill,letterbox}`, `--tighten/--no-tighten`, `--color {vivid,punchy,cinematic,soft,bright,mono}`. Notes:
- **Tightening needs gaps to do anything**: the bundled `transcript_sample.json`
  is contiguous, so `--tighten` is a no-op on it — pass a transcript with silent
  gaps between segments to see cuts (confirm `final.mp4` is shorter + `keep_ranges`).
- **Filler-word removal** (`tighten.remove_fillers: true` in a style, or the LLM's
  `tighten_silence` param) cuts `um`/`uh`-type words from the video *and* word-level
  captions. It needs **word timings** (real Whisper or the words fixture), so it is a
  no-op on segment-only transcripts. Verify with a transcript whose `words` include a
  filler: `keep_ranges` should carve out the filler span and no caption word is `um`.
- **True word-level caption timing** comes from `uv sync --extra openai` + omitting
  `--transcript` (real Whisper word timestamps); the offline path synthesizes
  even-spaced word timings instead.
- Letterbox + word styles: confirm captions sit on the video/bar seam.
- **The hook and overlay cards are off by default** (no banner, no quote/text
  cards). Enable per style: `hook: {enabled: true, ...}` /
  `visuals: {overlays_enabled: true, ...}`. When off they are absent from the plan
  entirely (gated in `preset_from_style`), so `edit_plan.json`, the echo counts, and
  the video all agree. In letterbox the hook (when on) stays fully inside the top bar.
- **`--use-llm`** routes through `LLMOrchestrator`: the model picks skills from the
  toolbox (it may enable the hook, drop tightening, change punch-in count, etc.) and
  **falls back to the deterministic plan on any failure** (no/invalid API key, bad
  JSON, unknown skill), writing `planner_error.txt`. So a `--use-llm` run with no key
  still produces a video — check `edit_plan.json`'s `reason` (free text = real LLM
  call; the deterministic template = it fell back) and whether `planner_error.txt` exists.

- **Color grading** (`--color cinematic`, a `color.look` in a style, or the LLM's
  `color_grade` skill) maps to FFmpeg `eq`. It lands in `timeline.json` (NOT
  `edit_plan.json` — it's rendered Timeline-direct). Verify by eyeballing a frame:
  `--color mono` is grayscale; captions keep their colors (grade precedes burn-in).

Always run `uv run pytest` too — but the smoke test is what catches "renders but
looks wrong" issues the unit tests can't.

## Architecture

> **Target architecture (migration in progress):** a modular **Timeline + Skills +
> orchestrator** design — every feature is a composable skill; an orchestrator edits
> one core Timeline; a deterministic coherence layer validates before render. See
> [`docs/architecture.md`](docs/architecture.md) (status: **Accepted**, "Model C").
> The principle — *agents decide; deterministic code executes and verifies* — is the
> generalization of the `EditPlan` → validate → render flow.
> **Migration milestones 1–4 are done.** **Both** planning paths now run through the
> substrate — `Timeline`/`Clip`/`MediaRef` (`timeline.py`), feature `skills/`, a
> topo-ordered **preset** (`presets.py` + `skills/graph.py`), the
> `DeterministicOrchestrator` / `LLMOrchestrator` (`orchestrator.py` /
> `llm_orchestrator.py`), and a deterministic **coherence layer** (`coherence.py`)
> that gates every Timeline — then `timeline_to_editplan` adapts to the same
> `EditPlan` the renderer consumes for the original tracks. The LLM picks skills from
> a **toolbox** (`toolbox.py`); it never emits timestamps or export settings.
> **Render-from-Timeline migration has begun**: the **color grade** is the first
> track the renderer reads straight from the `Timeline` (via `render_video(timeline=)`),
> not the `EditPlan` — new media/effect tracks follow this path, shrinking `EditPlan`
> over time. Segment-parallel subagents (milestone 5) remain deliberately **dropped**.

The pipeline (`cli.py` `process`) is a linear sequence that writes every intermediate artifact into a fresh per-run folder `output/<date>-<slug>-<uuid>/`: copy source → `probe_video` + `extract_audio` → transcribe → `plan_edits` → `render_video` → `write_review`. Stages communicate through Pydantic models, not loose dicts. `plan_edits` (deterministic path) builds a `Timeline` via the orchestrator — which tightens silence, then runs the feature skills — and adapts it to an `EditPlan`.

**Time model with cutting:** `EditPlan.keep_ranges` is the cut list — *source-time* spans kept in the output (empty = render the whole source uncut). Every *other* plan field (hook, captions, overlays, punch-ins) is in *output time* (the tightened timeline). On the Timeline these are the spine track (sequential, source-time → `keep_ranges`) vs. the floating tracks (output-time). Tightening happens once during planning: the orchestrator runs `TightenSilenceSkill` (rewriting the spine) then remaps the transcript to output time so the floating skills land there automatically — staying in sync with `final.mp4` with no per-render remapping. (The LLM path still tightens in `plan_edits` itself, via `tighten.py`, then stamps `keep_ranges`.)

Two design decisions drive everything and should be preserved when extending the code:

1. **`EditPlan` (in `models.py`) is the contract between planning and rendering.** The `Timeline` adapter emits a validated `EditPlan`; the renderer consumes *only* what's in it, and `edit_plan.json` can be hand-edited and re-rendered. Crucially, **the LLM never produces an `EditPlan` or any timestamps** — it only picks skills/params from the toolbox; deterministic code computes cuts/timings, and the coherence layer validates the whole Timeline *before* it becomes FFmpeg. When adding a creative feature now, the path is: add a **skill** that writes a `Timeline` track (+ a toolbox entry if the LLM should control it), surface it through `timeline_to_editplan`, and extend `EditPlan` + the renderer if the on-screen element is new. (The `Timeline` is the richer contract; `EditPlan` remains the renderer's input via the adapter.)

2. **Creative behavior lives in YAML styles, never in code.** `StyleConfig` (loaded/validated by `config.py` from `configs/styles/<name>.yaml`) controls export size, hook length, caption mode/animation/position, layout, and how many punch-ins/text-cards to produce. The renderer hardcodes no creator style. New styles are new YAML files, discovered automatically by `available_styles`. **Layout is a separate axis from the caption look** (`export.layout`: `fill` crops to cover, `letterbox` fits + pads black bars with captions in the bars), overridable per run via `--layout` so any caption style composes with either layout — no N×M style files.

### Module map

- `timeline.py` — the migration's core domain model (**"Model C"**, `docs/architecture.md`). `MediaRef` (a source asset), `Clip` (a placement of a source span on a track), and `Timeline` (multi-source: a `sources` registry + designated **spine**). The **spine** is a SEQUENTIAL track (positions derived back-to-back) that *is* the cut list — `spine_keep_ranges()` projects it to source-time `keep_ranges`; the **floating** tracks (`hook`/`captions`/`overlays`/`punch_ins`) are output-time. Validators enforce the single-spine Model C constraint and reject reserved fields (`speed`/transitions) until later milestones. `timeline_to_editplan()` is the adapter that drives the unchanged renderer; `timeline_from_spine()` builds the initial uncut state.
- `skills/` — composable, media-pure operations on a `Timeline` (never touch FFmpeg). `base.py`: the `Skill` protocol (`reads`/`writes`/`apply`) + read-only `Context` (transcript, style). `runner.py`: `SkillRunner` (reads-precondition + writes-purity postcondition). `graph.py`: `topological_order`. Each feature module holds its **pure builder** (the single home of that logic, shared by both planners) + a thin `*Skill` wrapper: `caption.py` (`build_captions` — sentence cues or word-group cues with per-word timings; synthesizes even-spaced timings when a segment lacks words; **word-mode captions always come from the transcript, never the LLM**), `hook.py` (`build_hook`), `overlay.py` (`build_overlays` — longest non-opening lines as quote cards), `punchin.py` (`build_punch_ins`), `tighten.py` (`TightenSilenceSkill`, a thin adapter over `tighten.compute_keep_ranges` that rewrites the spine), `color.py` (`ColorGradeSkill` + `build_color_grade` + named `LOOKS`; writes the Timeline's `color` field, read nothing). The floating skills declare a read on `spine` so the graph orders them after `tighten`.
- `orchestrator.py` — `compose_timeline(skills, …)` is the **shared engine** for both orchestrators: topo-order the skills, run each via a `SkillRunner`, remap the transcript to output time whenever a skill rewrites the spine, record `reason` in `meta`, and gate through `validate_timeline` (warnings → `meta["coherence_warnings"]`). `DeterministicOrchestrator.plan_timeline()` is the no-LLM strategy: its skill set is `preset_from_style`. The LLM strategy (`llm_orchestrator.py`) reuses the same engine — only the *source of the skill set* differs.
- `presets.py` — a **preset** is the composed skill pipeline (milestone 3): `preset_from_style` turns a `StyleConfig` into a gated *set* of skills (`tighten`/`hook`/`overlay` included only when their style flag is on; the `tighten=` arg can force tightening off for the legacy `DeterministicPlanner` path). **Feature gating lives here.** The listed order is only the stable tiebreak — the real run order comes from the graph. This is the seam where an explicit YAML skill list or an LLM-chosen set plugs in later.
- `skills/graph.py` — `topological_order(skills)`: orders skills so every writer of a resource precedes its readers, from the `reads`/`writes` sets (stable on input order; raises on a cycle). The floating skills (`add_captions`/`hook`/`overlay`/`punch_in`) declare a read on `spine` because their output-time positions depend on the cut list, so this sort places `tighten` (which writes `spine`) first automatically.
- `coherence.py` — the deterministic validation pass run **before render** (`docs/architecture.md` milestone 2). `check_timeline` returns `Violation`s with two severities: **error** (inverted/overlapping caption ranges, words outside their cue, spine out of order — `validate_timeline` raises `CoherenceError`) vs **warning** (elements past the output duration — renderable, surfaced not rejected). This is `EditPlan`'s Pydantic validation grown cross-track; the floating element types (`CaptionCue`/`Hook`/`PunchIn`/`VisualOverlay`) carry no order validation themselves, so this pass is where it lives. `skills/runner.py`'s `SkillRunner` enforces per-skill reads (precondition) + writes-purity (a skill only changes the tracks it declares) postconditions, tracking availability for ordering.
- `planner.py` — `plan_edits` entry point. Both paths build a coherence-validated `Timeline` and adapt to an `EditPlan`: deterministic → `DeterministicOrchestrator`; `use_llm` → `LLMOrchestrator`. `DeterministicPlanner` remains as a thin `EditPlan`-returning shim (used directly by tests; it does not tighten). `build_captions` is re-exported here from `skills.caption` for backward compatibility.
- `toolbox.py` — the LLM's function-calling surface (milestone 4): each skill paired with a strict Pydantic params schema (`TightenParams`/`HookParams`/…) + a description. `SkillPlan`/`SkillChoice` is the LLM's structured output (which skills + what params — **never** timestamps or export settings); `build_skill`/`skills_from_plan` validate it and build skills; `toolbox_catalog` describes it for the prompt. The deterministic analogue is `presets.py`.
- `llm_orchestrator.py` — `LLMOrchestrator.plan_timeline()`: asks an LLM (lazy `openai` import) to pick skills from the toolbox, validates the `SkillPlan`, composes it via the shared `compose_timeline`, and **falls back to `DeterministicOrchestrator` on any failure** (raw response → `planner_error.txt`). A single agent edits the whole clip; no subagents. Because the LLM only chooses skills/params, it cannot corrupt timings or export dims (a safety win over the old EditPlan-JSON approach).
- `tighten.py` — pure (no-FFmpeg) silence/pause + **filler-word** compression: `compute_keep_ranges` (merges speech spans across short gaps, pads phrases, drops long gaps; with `remove_fillers=True` also carves out filler-word spans — `um`/`uh`/… from `DEFAULT_FILLER_WORDS` — needs word timings; returns `[]` when nothing to cut), `remap_ms` (source→output time, `None` in a cut), `tighten_transcript` (remaps onto the tightened timeline and **drops words cut out of the video** by positive-overlap test, so removed fillers also leave word-level captions; rebuilds a segment's text from survivors). All wrapped by the single spine-writing `TightenSilenceSkill`. Still future: retake/duplicate removal (more `keep_ranges`).
- `render.py` — thin FFmpeg translation layer. All on-screen text (hook, captions, overlays) is written into a **single ASS subtitle file** (`captions.ass`) so the filtergraph stays small: optional **color grade** (`eq`, applied *first* so it never tints the captions burned in later, and so letterbox black bars added by `pad` stay pure black) → layout stage → optional time-gated punch-in zoom → `subtitles` burn-in. `render_video` takes an optional `timeline=` — the **first track read straight from the Timeline rather than the EditPlan** (the color grade): `build_video_filter(..., color=)` reads `timeline.color`. When `timeline` is `None` (or the grade is identity) the render is byte-identical to before. When `plan.keep_ranges` is set, `build_concat_filtergraph` builds a `-filter_complex` that `trim`/`atrim`s each kept span, `concat`s them, then runs the same vf chain on the result (loudnorm moves into the graph); empty `keep_ranges` keeps the plain `-vf` path. The layout stage is the only difference between layouts (`fill` = scale-increase + `crop`; `letterbox` = scale-decrease + `pad` black bars); both yield a full WxH canvas, so punch-in and burn-in code is shared. FFmpeg runs with cwd set to the run folder so the subtitle filter can use a bare filename (avoids filter-path escaping). **Only the first punch-in is rendered**; the rest stay in the plan for inspection. In letterbox, `render_video` probes the source to compute bar heights (`_letterbox_bars`); captions sit *on* the video/bar seam (slight overlap onto the video) while the hook — when present — stays fully inside the top bar. **The hook (`style.hook.enabled`) and overlay cards (`style.visuals.overlays_enabled`) are gated upstream (the orchestrator, and the LLM planner) and off by default**: when disabled they are simply absent from the plan (`plan.hook is None`, `plan.overlays == []`), and the renderer draws exactly what the plan holds. Re-enable per style with no code change. A caption cue carrying `words` animates per the style's `captions.word_animation` (`active_word` per-word highlight events, `karaoke` `\kf` sweep, `one_word` centered `WordBig`); a cue without `words` renders as one static line. Override tags (`\c`, `\kf`) are assembled around already-escaped word text so `_escape_ass` doesn't clobber them.
- `transcribe.py` — `Transcriber` protocol with `ProvidedTranscriptTranscriber` (reads JSON, zero cost) and `OpenAITranscriber` (lazy-imported, optional extra; requests word + segment granularity and buckets words into their segment). `load_transcript` tolerantly accepts both `{start_ms,end_ms}` and seconds-based `{start,end}` shapes, including an optional per-segment `words` array in either shape.
- `ffmpeg_tools.py` — `subprocess.run` wrappers (always arg lists, never shell strings). `probe_video` returns the handful of facts the pipeline needs; raises `FFmpegError` early.
- `models.py` — all data contracts. **Timestamps are integer milliseconds throughout** the system; do not introduce float-seconds in stored shapes.

## Conventions

- **After any feature or behavior change, update the "Smoke testing" section above** so it always reflects how to manually verify the latest behavior.
- The package lives at the repo root with no build step; `pyproject.toml` puts the root on the pytest import path rather than requiring an editable install.
- OpenAI usage (transcription and planning) is always behind a lazy import + optional `openai` extra, so the offline path never depends on it. Preserve this when adding API features.
- `OPENAI_API_KEY` is read from `.env` via `load_dotenv()` (see `.env.example`).
