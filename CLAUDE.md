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

Knobs to compose: `--style {bold_creator,clean_captions,word_pop,karaoke,one_word,reels_letterbox,viral_creator}`,
`--layout {fill,letterbox}`, `--tighten/--no-tighten`, `--color {vivid,punchy,cinematic,soft,bright,mono}`. Notes:
- **`--style` has no hardcoded default any more.** Omit it and `cli.py`'s
  `_prompt_style_choice` asks interactively: a numbered list of `available_styles()`
  each with its one-line `STYLE_DESCRIPTIONS` (`intent.py`), plus a final "Surprise
  me" entry that picks `random.choice(available)`. This exists so `--use-llm` never
  silently inherits an arbitrary style the user never chose — the LLM only ever picks
  *skills/params inside* an already-resolved style's toolbox (fonts/animations/layout
  all come from the style, not the model), so there was no principled basis for it to
  guess a style either; a human (or the random pick) has to name one. `--interactive`'s
  full `SetupInterview` already asks this same style question (plus layout/tighten/caps),
  so `_prompt_style_choice` never runs there — and (pre-existing behavior, unchanged)
  `--interactive` always re-asks via the interview even if `--style` was also passed,
  since the interview's answer is what gets used. Outside `--interactive`, passing
  `--style` skips the new prompt entirely. Verify:
  ```bash
  uv run python -c "
  from typer.testing import CliRunner
  from shortform_lab import cli
  r = CliRunner().invoke(cli.app, ['process', '/tmp/smoke.mp4', '--transcript',
      'tests/fixtures/transcript_sample.json', '--output-dir', '/tmp/prompt_out'],
      input='surprise me\n')
  print(r.output)"
  ```
  should print the numbered list + descriptions, echo which style the surprise pick
  landed on, then proceed exactly as if that style had been passed via `--style`.
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
- **Letterbox cropping is flexible, not a hardcoded fit-the-whole-source /
  half-and-half split.** `render._letterbox_scale` picks a scale anywhere
  between "fit" (whole source, zero crop, can leave huge bars) and "fill"
  (zero bars, can crop most of the frame away), targeting
  `style.export.letterbox_min_content` (default `0.72` — about how much of a
  9:16 frame a Reels-style UI leaves uncovered) as the video band's share of
  the canvas height, capped so it never crops more than 40% of the scaled
  frame's width away (`_LETTERBOX_MAX_WIDTH_CROP`) — an extreme aspect
  mismatch (e.g. ultrawide) degrades to bigger bars rather than cropping the
  subject out. It's resolution-independent (driven by aspect ratio, not
  absolute pixel size) and only trades crop for bars when the source is wider
  than the canvas (a source already taller than 9:16 falls back to plain fit —
  nothing to trade). `render_video` always knows the source size for
  letterbox (it probes it), so this is always live in real runs;
  `build_video_filter` only falls back to the old fit-and-pad when called
  without `source_size` (unit tests). Verify:
  ```bash
  ffmpeg -y -f lavfi -i "testsrc=size=1280x720:rate=30:duration=6" \
         -f lavfi -i "sine=frequency=220:duration=6" -shortest -pix_fmt yuv420p /tmp/wide_lb.mp4
  uv run python -m shortform_lab.cli process /tmp/wide_lb.mp4 --style reels_letterbox \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/wide_lb_out
  ffmpeg -y -ss 0.5 -i /tmp/wide_lb_out/final.mp4 -frames:v 1 /tmp/wide_lb_frame.png
  ```
  Eyeball `/tmp/wide_lb_frame.png`: the video band should visibly cover more
  than half the frame height (not the old ~32%-band/68%-bars split a plain fit
  gives a 16:9 source), with the sides cropped in rather than the whole frame
  shrunk down. Raise/lower `letterbox_min_content` in a style's `export:`
  block to make bars smaller/larger; `edit_plan.json`'s `export_layout` stays
  `"letterbox"` either way — only the FFmpeg-side crop amount changes.
- **Letterbox font size scales with how much the crop shows.** When
  `_letterbox_scale` picks a scale above plain `fit_scale` (less bar, more of
  the source visible — see above), the subject reads larger on screen too, so
  a fixed caption `font_size` would look proportionally smaller. `write_captions_ass`
  scales `cap_size` (and everything derived from it — hook/card/word/lower-third
  sizes and their contrast-box padding, see below) by `actual_scale / fit_scale`,
  so a source that crops in a lot gets proportionally bigger captions, and a
  source that needed no crop (already close to 9:16) is unaffected (`ratio == 1.0`).
  Verify: `grep 'Style: Caption,' /tmp/wide_lb_out/captions.ass` — the `Fontsize`
  field (3rd) should read well above the style's configured `font_size` (e.g.
  `reels_letterbox`'s `56` renders as `93` for the 16:9 `/tmp/wide_lb.mp4` fixture
  above, since that source hits the ~1.67x crop-safety-cap ratio); a near-9:16
  source should render at (or very near) the configured size unchanged.
- **Caption/hook/card text gets a translucent dark contrast box behind it**
  (`BorderStyle=3` in the ASS style, not just an outline), sized to the text's
  own line rather than a full-width banner. On the black letterbox bar it's
  visually indistinguishable from the bar (no visible box); over bright/light
  video content it guarantees a dark backing so white text never blends in —
  no per-frame background analysis needed, applies uniformly to every style
  since it lives in `write_captions_ass`, not per-style YAML. Padding scales
  with each element's own font size (`cap_pad`/`hook_pad`/`word_big_pad`).
  Verify: render `word_pop` or `bold_creator` over a bright/white background
  clip and eyeball a frame — captions should sit on a visible dark box rather
  than blending into the background:
  ```bash
  ffmpeg -y -f lavfi -i "color=c=white:size=1080x1920:rate=30:duration=6" \
         -f lavfi -i "sine=frequency=220:duration=6" -shortest -pix_fmt yuv420p /tmp/white_bg.mp4
  uv run python -m shortform_lab.cli process /tmp/white_bg.mp4 --style bold_creator \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/white_bg_out
  ffmpeg -y -ss 0.5 -i /tmp/white_bg_out/final.mp4 -frames:v 1 /tmp/white_bg_frame.png
  ```
- **`captions.uppercase` now actually applies to sentence-mode captions, the
  hook, and overlay cards** (previously only word-mode paths — `active_word`/
  `karaoke`/`one_word` — respected it; sentence-mode `Caption`/`Hook`/`Card`
  events silently ignored the flag). Verify: a style with `uppercase: true` and
  `captions.mode: sentence` should render its hook/caption/card text upper-cased
  in `captions.ass`.
- **`bold_creator` now ships uppercase + a fade-in + a `punchy` color grade**
  (`configs/styles/bold_creator.yaml`) instead of flat, static, ungraded
  sentence captions — the "looks really ass" default. Still sentence-mode
  captions with hook/overlays/tighten off by default (deliberately unchanged —
  several tests, e.g. `test_preset_gates_disabled_features`, rely on
  `bold_creator` being the canonical "gated features off" fixture, so don't
  flip those flags in this style without updating those tests). Verify:
  `uv run python -m shortform_lab.cli process /tmp/smoke.mp4 --style bold_creator --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/bold_creator_out`
  → `edit_plan.json`'s echo line should show `color=punchy`, and
  `captions.ass` should show `Style: Caption` uppercase text and
  `{\fad(200,100)}` prefixes on caption events.
- **Layout auto-detects from source resolution when neither `--layout` nor the
  style YAML pins one**: portrait/square sources (`height >= width`) default to
  `fill`, landscape sources default to `letterbox` (`cli.py`'s `_auto_layout`,
  applied right after `probe_video`). A style with an explicit `export.layout`
  (e.g. `reels_letterbox`) always keeps its own choice regardless of source
  resolution (`config.style_explicitly_sets_layout`); `--layout`/the interactive
  interview's layout answer still win over everything, unchanged. Verify:
  ```bash
  ffmpeg -y -f lavfi -i "testsrc=size=1280x720:rate=30:duration=6" \
         -f lavfi -i "sine=frequency=220:duration=6" -shortest -pix_fmt yuv420p /tmp/wide.mp4
  ffmpeg -y -f lavfi -i "testsrc=size=720x1280:rate=30:duration=6" \
         -f lavfi -i "sine=frequency=220:duration=6" -shortest -pix_fmt yuv420p /tmp/tall.mp4
  # bold_creator never sets export.layout -> should follow the source:
  uv run python -m shortform_lab.cli process /tmp/wide.mp4 --style bold_creator \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/wide_out
  uv run python -m shortform_lab.cli process /tmp/tall.mp4 --style bold_creator \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/tall_out
  grep export_layout /tmp/wide_out/edit_plan.json   # "letterbox"
  grep export_layout /tmp/tall_out/edit_plan.json   # "fill"
  # reels_letterbox always sets export.layout -> stays letterbox either way:
  uv run python -m shortform_lab.cli process /tmp/tall.mp4 --style reels_letterbox \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/tall_lb_out
  grep export_layout /tmp/tall_lb_out/edit_plan.json   # "letterbox"
  # --layout still force-overrides on top of auto-detection:
  uv run python -m shortform_lab.cli process /tmp/wide.mp4 --style bold_creator --layout fill \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/wide_forced_out
  grep export_layout /tmp/wide_forced_out/edit_plan.json   # "fill"
  ```
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
- **`--translate`** (requires `uv sync --extra openai` + omitting `--transcript`;
  ignored when `--transcript` is passed) swaps `OpenAITranscriber` for
  `OpenAITranslator` (`transcribe.py`), which calls OpenAI's Whisper
  **translations** endpoint (`audio.translations.create`, always English
  output, `whisper-1` only) instead of the transcriptions endpoint — so
  non-English speech (e.g. Tamil) renders as English captions. That endpoint
  doesn't accept `timestamp_granularities`/return word timestamps, only
  segment-level text + timing, so the resulting `Transcript`'s segments carry
  no `words`; word-mode caption styles (`word_pop`/`karaoke`/`one_word`/
  `active_word`) fall back to `build_captions`'s even-spaced timing synthesis
  rather than real per-word timestamps (sentence-mode styles are unaffected).
  Verify (needs a real API key and non-English audio — no offline path since
  it's a live translation call):
  ```bash
  uv run python -m shortform_lab.cli process /path/to/tamil_clip.mp4 \
    --style bold_creator --translate --output-dir /tmp/translate_out
  cat /tmp/translate_out/transcript.json   # "language": "en", English "text" fields
  ```

- **Color grading** (`--color cinematic`, a `color.look` in a style, or the LLM's
  `color_grade` skill) maps to FFmpeg `eq`. It lands in `timeline.json` (NOT
  `edit_plan.json` — it's rendered Timeline-direct). Verify by eyeballing a frame:
  `--color mono` is grayscale; captions keep their colors (grade precedes burn-in).
- **The hook only ever ships real, LLM-written copy — never the deterministic
  path's extractive fallback.** `style.hook.enabled: true` (e.g. `viral_creator`)
  is *not* enough on its own: `plan.hook` stays `None` on a plain deterministic
  run of that style. The extractive fallback (opening line, word-capped,
  clause-safe-truncated — see `skills/hook.py`) only renders under `--debug`, for
  inspecting that fallback itself; it never ships. `--use-llm`/`--interactive`
  write real hook copy via the Planner (`HookParams.text`) independent of
  `--debug`, or skip the hook entirely if the Planner judges the clip doesn't need
  one. Verify: `process clip.mp4 --style viral_creator` → `edit_plan.json`'s
  `hook` is `null`; add `--debug` → a hook appears, visually matching the
  caption's font/outline (no more boxed/accent-colored treatment — `Style: Hook`
  and `Style: Caption` in `captions.ass` should share the same
  Bold/BorderStyle/Outline/Shadow columns, differing only in size/position).
- **`active_word` captions never accent-highlight a word by default.** The old
  behavior — mechanically cycling the highlight through every single word as
  it's spoken, regardless of whether that word means anything — is gone. Only
  words a `CaptionParams.emphasize` list (generative path only) matches get
  `WordTiming.emphasize=True` and thus ever get the accent color; the
  deterministic path always passes an empty list, so a plain deterministic run
  of e.g. `word_pop` never shows a highlighted word at all. Verify:
  ```bash
  uv run python -m shortform_lab.cli process /tmp/smoke.mp4 --style word_pop \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/smoke_out
  grep -c 'c&H' /tmp/smoke_out/captions.ass   # 0 — no word ever recolours
  ```
  To see the opposite case (a real emphasis), build a
  `CaptionSkill(emphasize=("consistency", "intensity"))` directly against the
  sample transcript (both words are in segment 2) — the same `grep -c` on the
  resulting `captions.ass` should then be `2`, and eyeballing that segment's
  frame should show exactly those two words in the style's accent color,
  everything else plain.
- **Caption fonts are bundled** (`assets/fonts/`: Poppins ExtraBold, Anton, Bebas
  Neue — see the style YAMLs for which style uses which). Verify the bundled font
  actually resolved (not a silent libass fallback) by re-running the `subtitles`
  filter at verbose log level and checking for a `fontselect: (<family>, ...) ->
  <matched file>` line naming your font, e.g.:
  ```bash
  cd /tmp/smoke_out && ffmpeg -y -i source.mp4 \
    -vf "subtitles=captions.ass:fontsdir=../../../home/pranav/Documents/autoshorts/assets/fonts" \
    -frames:v 1 -loglevel verbose /tmp/f.png 2>&1 | grep fontselect
  ```
  (adjust the `fontsdir` path — it's relative to the run folder). Eyeballing the
  frame is the real check: `captions.ass`'s `[V4+ Styles]` block should show the
  style's `font_family` (not `FreeSans`) and `Bold=0` on every line.
- **The engine's I/O boundary (ffmpeg subprocesses, OpenAI calls) is now async**,
  gated behind two independent `asyncio.Semaphore`s in `concurrency.py`
  (`ffmpeg_semaphore`/`openai_semaphore` — CPU-bound ffmpeg work and
  rate-limited API calls have different natural limits, so they're never
  conflated into one cap). Pure computation (`timeline.py`, `skills/`,
  `coherence.py`, `presets.py`, `orchestrator.py`'s topo-order/compose) stays
  synchronous — only real I/O (`ffmpeg_tools.py`, `render.py`'s ffmpeg
  invocation, `transcribe.py`'s OpenAI calls, `agent.py`'s `OpenAIClient`) is
  `async def`. `cli.py`'s `process` command is unchanged from the outside — it
  still runs as one blocking command; internally it wraps the async pipeline
  in a single `asyncio.run(...)` call. Verify the conversion didn't change
  behavior by re-running a smoke command and confirming the render is
  byte-identical with the ffmpeg cap forced down to serialize everything:
  ```bash
  uv run python -m shortform_lab.cli process /tmp/smoke.mp4 --style word_pop \
    --layout letterbox --tighten --transcript tests/fixtures/transcript_sample.json \
    --output-dir /tmp/async_a
  SHORTFORM_LAB_FFMPEG_CONCURRENCY=1 uv run python -m shortform_lab.cli process /tmp/smoke.mp4 \
    --style word_pop --layout letterbox --tighten \
    --transcript tests/fixtures/transcript_sample.json --output-dir /tmp/async_b
  cmp /tmp/async_a/final.mp4 /tmp/async_b/final.mp4   # identical either way
  ```
  Tune caps via `SHORTFORM_LAB_FFMPEG_CONCURRENCY` (default 4) and
  `SHORTFORM_LAB_OPENAI_CONCURRENCY` (default 8) — relevant once a future
  service runs many pipeline jobs concurrently in one worker process; a single
  CLI run never contends against itself since it only ever has one ffmpeg
  invocation and one OpenAI call in flight at a time.

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

2. **Creative behavior lives in YAML styles, never in code.** `StyleConfig` (loaded/validated by `config.py` from `configs/styles/<name>.yaml`) controls export size, hook length, caption mode/animation/position, layout, and how many punch-ins/text-cards to produce. The renderer hardcodes no creator style. New styles are new YAML files, discovered automatically by `available_styles`. **Layout is a separate axis from the caption look** (`export.layout`: `fill` crops to cover, `letterbox` fits + pads black bars with captions in the bars), overridable per run via `--layout` so any caption style composes with either layout — no N×M style files. When a style doesn't explicitly set `export.layout` and no explicit choice (`--layout` / the interactive interview) is made, `cli.py` picks a smarter default than the bare Pydantic default by probing the source resolution (`_auto_layout`: portrait/square → `fill`, landscape → `letterbox`) — this only changes what the *default* resolves to, never what `--layout` means or its precedence.

### Module map

- `timeline.py` — the migration's core domain model (**"Model C"**, `docs/architecture.md`). `MediaRef` (a source asset), `Clip` (a placement of a source span on a track), and `Timeline` (multi-source: a `sources` registry + designated **spine**). The **spine** is a SEQUENTIAL track (positions derived back-to-back) that *is* the cut list — `spine_keep_ranges()` projects it to source-time `keep_ranges`; the **floating** tracks (`hook`/`captions`/`overlays`/`punch_ins`) are output-time. Validators enforce the single-spine Model C constraint and reject reserved fields (`speed`/transitions) until later milestones. `timeline_to_editplan()` is the adapter that drives the unchanged renderer; `timeline_from_spine()` builds the initial uncut state.
- `skills/` — composable, media-pure operations on a `Timeline` (never touch FFmpeg). `base.py`: the `Skill` protocol (`reads`/`writes`/`apply`) + read-only `Context` (transcript, style). `runner.py`: `SkillRunner` (reads-precondition + writes-purity postcondition). `graph.py`: `topological_order`. Each feature module holds its **pure builder** (the single home of that logic, shared by both planners) + a thin `*Skill` wrapper: `caption.py` (`build_captions` — sentence cues or word-group cues with per-word timings; synthesizes even-spaced timings when a segment lacks words; **word-mode captions always come from the transcript, never the LLM**; an `emphasize` tuple — generative-path-only, empty by default — flags specific already-correct words as `WordTiming.emphasize=True` via `_mark_emphasis`, case/punctuation-tolerant phrase matching that never touches text or timing), `hook.py` (`build_hook` — extractive fallback by default; accepts LLM-authored `text` from the generative path, and always clause-safe-truncates via `_clause_safe_truncate` so a word-capped hook never dangles on an article/preposition/pronoun), `overlay.py` (`build_overlays` — longest non-opening lines as quote cards), `punchin.py` (`build_punch_ins`), `tighten.py` (`TightenSilenceSkill`, a thin adapter over `tighten.compute_keep_ranges` that rewrites the spine), `color.py` (`ColorGradeSkill` + `build_color_grade` + named `LOOKS`; writes the Timeline's `color` field, read nothing). The floating skills declare a read on `spine` so the graph orders them after `tighten`.
- `orchestrator.py` — `compose_timeline(skills, …)` is the **shared engine** for both orchestrators: topo-order the skills, run each via a `SkillRunner`, remap the transcript to output time whenever a skill rewrites the spine, record `reason` in `meta`, and gate through `validate_timeline` (warnings → `meta["coherence_warnings"]`). `DeterministicOrchestrator.plan_timeline()` is the no-LLM strategy: its skill set is `preset_from_style`. The LLM strategy (`llm_orchestrator.py`) reuses the same engine — only the *source of the skill set* differs.
- `presets.py` — a **preset** is the composed skill pipeline (milestone 3): `preset_from_style` turns a `StyleConfig` into a gated *set* of skills (`tighten`/`overlay` included when their style flag is on; the `tighten=` arg can force tightening off for the legacy `DeterministicPlanner` path). **Feature gating lives here.** `hook` is gated on **both** `style.hook.enabled` **and** the `debug=` arg (default `False`) — the deterministic hook is purely extractive (never real copy), so it's testing-only and never ships; `cli.py`'s `--debug` flag is the only thing that sets `debug=True`. The listed order is only the stable tiebreak — the real run order comes from the graph. This is the seam where an explicit YAML skill list or an LLM-chosen set plugs in later.
- `skills/graph.py` — `topological_order(skills)`: orders skills so every writer of a resource precedes its readers, from the `reads`/`writes` sets (stable on input order; raises on a cycle). The floating skills (`add_captions`/`hook`/`overlay`/`punch_in`) declare a read on `spine` because their output-time positions depend on the cut list, so this sort places `tighten` (which writes `spine`) first automatically.
- `coherence.py` — the deterministic validation pass run **before render** (`docs/architecture.md` milestone 2). `check_timeline` returns `Violation`s with two severities: **error** (inverted/overlapping caption ranges, words outside their cue, spine out of order — `validate_timeline` raises `CoherenceError`) vs **warning** (elements past the output duration, or a hook whose text duplicates the caption cue overlapping its time window — both renderable, surfaced not rejected). The hook/caption duplicate check is a taste signal for the Agentic Critic (fed `coherence_violations` each reflection round), not a hard rejection — a duplicate hook still renders, just badly. This is `EditPlan`'s Pydantic validation grown cross-track; the floating element types (`CaptionCue`/`Hook`/`PunchIn`/`VisualOverlay`) carry no order validation themselves, so this pass is where it lives. `skills/runner.py`'s `SkillRunner` enforces per-skill reads (precondition) + writes-purity (a skill only changes the tracks it declares) postconditions, tracking availability for ordering.
- `planner.py` — `plan_edits` entry point. Both paths build a coherence-validated `Timeline` and adapt to an `EditPlan`: deterministic → `DeterministicOrchestrator`; `use_llm` → `LLMOrchestrator`. `DeterministicPlanner` remains as a thin `EditPlan`-returning shim (used directly by tests; it does not tighten). `build_captions` is re-exported here from `skills.caption` for backward compatibility.
- `toolbox.py` — the LLM's function-calling surface (milestone 4): each skill paired with a strict Pydantic params schema (`TightenParams`/`HookParams`/…) + a description. `SkillPlan`/`SkillChoice` is the LLM's structured output (which skills + what params — **never timestamps or export settings, with two deliberate exceptions**: `HookParams.text` is real hook copy the Planner writes itself, and `CaptionParams.emphasize` is a list of words/phrases (verbatim transcript substrings) it may flag for accent-highlight — see `workers.py`'s `_PLANNER_INSTRUCTIONS`, which tells it to tease rather than restate the transcript for the hook, and to leave `emphasize` empty far more often than not (mechanically highlighting every word is exactly the bug this replaced); `build_skill`/`skills_from_plan` validate it and build skills; `toolbox_catalog` describes it for the prompt. The deterministic analogue is `presets.py`.
- `llm_orchestrator.py` — `LLMOrchestrator.plan_timeline()`: asks an LLM (lazy `openai` import) to pick skills from the toolbox, validates the `SkillPlan`, composes it via the shared `compose_timeline`, and **falls back to `DeterministicOrchestrator` on any failure** (raw response → `planner_error.txt`). A single agent edits the whole clip; no subagents. Because the LLM only chooses skills/params, it cannot corrupt timings or export dims (a safety win over the old EditPlan-JSON approach).
- `tighten.py` — pure (no-FFmpeg) silence/pause + **filler-word** compression: `compute_keep_ranges` (merges speech spans across short gaps, pads phrases, drops long gaps; with `remove_fillers=True` also carves out filler-word spans — `um`/`uh`/… from `DEFAULT_FILLER_WORDS` — needs word timings; returns `[]` when nothing to cut), `remap_ms` (source→output time, `None` in a cut), `tighten_transcript` (remaps onto the tightened timeline and **drops words cut out of the video** by positive-overlap test, so removed fillers also leave word-level captions; rebuilds a segment's text from survivors). All wrapped by the single spine-writing `TightenSilenceSkill`. Still future: retake/duplicate removal (more `keep_ranges`).
- `render.py` — thin FFmpeg translation layer. All on-screen text (hook, captions, overlays) is written into a **single ASS subtitle file** (`captions.ass`) so the filtergraph stays small: optional **color grade** (`eq`, applied *first* so it never tints the captions burned in later, and so letterbox black bars added by `pad` stay pure black) → layout stage → optional time-gated punch-in zoom → `subtitles` burn-in. **Caption fonts are bundled, not system-dependent**: `FONTS_DIR` (`assets/fonts/`, OFL-licensed — Poppins ExtraBold, Anton, Bebas Neue) is passed to the `subtitles` filter as `fontsdir=` (a path relative to `work_dir`, same escaping rationale as the bare captions filename below), so libass resolves a style's `font_family` from the bundled `.ttf` first — renders are identical across machines regardless of what's installed system-wide. `DEFAULT_FONT`/`CaptionSettings.font_family` (`models.py`) both default to `"Poppins ExtraBold"`. Every ASS style line sets `Bold=0`: the bundled fonts are real weighted files (ExtraBold/the display faces' natural weight), so libass's synthetic-bold (`Bold=1`) is never invoked — it fake-embolds and looks worse than picking the right weight file. `render_video` takes an optional `timeline=` — the **first track read straight from the Timeline rather than the EditPlan** (the color grade): `build_video_filter(..., color=)` reads `timeline.color`. When `timeline` is `None` (or the grade is identity) the render is byte-identical to before. When `plan.keep_ranges` is set, `build_concat_filtergraph` builds a `-filter_complex` that `trim`/`atrim`s each kept span, `concat`s them, then runs the same vf chain on the result (loudnorm moves into the graph); empty `keep_ranges` keeps the plain `-vf` path. The layout stage is the only difference between layouts (`fill` = scale-increase + `crop`; `letterbox` = scale by `_letterbox_scale` + `pad` + `crop`, a flexible fit-to-fill blend — see the Smoke testing section); both yield a full WxH canvas, so punch-in and burn-in code is shared. FFmpeg runs with cwd set to the run folder so the subtitle filter can use a bare filename (avoids filter-path escaping). **Only the first punch-in is rendered**; the rest stay in the plan for inspection. In letterbox, `render_video` probes the source to compute bar heights (`_letterbox_bars`, sharing the same `_letterbox_scale` the filtergraph uses so captions always match the actual bars); captions sit *on* the video/bar seam (slight overlap onto the video) while the hook — when present — stays fully inside the top bar. **The hook (`style.hook.enabled` + `--debug` — see `presets.py`) and overlay cards (`style.visuals.overlays_enabled`) are gated upstream (the orchestrator, and the LLM planner) and off by default**: when disabled they are simply absent from the plan (`plan.hook is None`, `plan.overlays == []`), and the renderer draws exactly what the plan holds. Re-enable per style with no code change. The `Hook` ASS style reuses `Caption`'s exact Bold/BorderStyle/Outline/Shadow/PrimaryColour/OutlineColour — same visual family as the body captions (just larger, top-positioned), not a separate boxed/accent-colored treatment. A caption cue carrying `words` animates per the style's `captions.word_animation` (`active_word` per-word highlight events — **but only recolours a word during its own span if `WordTiming.emphasize` is set**; an unflagged word (the deterministic default) never gets the accent, so a cue with no emphasized words renders as plain identical-looking events with zero highlight cycling — `karaoke` `\kf` sweep (unaffected by `emphasize`; it's a rhythm effect across all words, not a per-word importance call), `one_word` centered `WordBig`); a cue without `words` renders as one static line. Override tags (`\c`, `\kf`) are assembled around already-escaped word text so `_escape_ass` doesn't clobber them.
- `transcribe.py` — `Transcriber` protocol with `ProvidedTranscriptTranscriber` (reads JSON, zero cost) and `OpenAITranscriber` (lazy-imported, optional extra; requests word + segment granularity and buckets words into their segment). `load_transcript` tolerantly accepts both `{start_ms,end_ms}` and seconds-based `{start,end}` shapes, including an optional per-segment `words` array in either shape.
- `ffmpeg_tools.py` — `subprocess.run` wrappers (always arg lists, never shell strings). `probe_video` returns the handful of facts the pipeline needs; raises `FFmpegError` early.
- `models.py` — all data contracts. **Timestamps are integer milliseconds throughout** the system; do not introduce float-seconds in stored shapes.

## Conventions

- **After any feature or behavior change, update the "Smoke testing" section above** so it always reflects how to manually verify the latest behavior.
- The package lives at the repo root with no build step; `pyproject.toml` puts the root on the pytest import path rather than requiring an editable install.
- OpenAI usage (transcription and planning) is always behind a lazy import + optional `openai` extra, so the offline path never depends on it. Preserve this when adding API features.
- `OPENAI_API_KEY` is read from `.env` via `load_dotenv()` (see `.env.example`).
