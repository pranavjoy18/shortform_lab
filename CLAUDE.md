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
`--layout {fill,letterbox}`, `--tighten/--no-tighten`. Notes:
- **Tightening needs gaps to do anything**: the bundled `transcript_sample.json`
  is contiguous, so `--tighten` is a no-op on it — pass a transcript with silent
  gaps between segments to see cuts (confirm `final.mp4` is shorter + `keep_ranges`).
- **True word-level caption timing** comes from `uv sync --extra openai` + omitting
  `--transcript` (real Whisper word timestamps); the offline path synthesizes
  even-spaced word timings instead.
- Letterbox + word styles: confirm captions sit on the video/bar seam.
- **The hook and overlay cards are off by default** (no banner, no quote/text
  cards). Enable per style: `hook: {enabled: true, ...}` /
  `visuals: {overlays_enabled: true, ...}`. When off they are absent from the plan
  entirely (gated in the planner), so `edit_plan.json`, the echo counts, and the
  video all agree. In letterbox the hook (when on) stays fully inside the top bar.

Always run `uv run pytest` too — but the smoke test is what catches "renders but
looks wrong" issues the unit tests can't.

## Architecture

The pipeline (`cli.py` `process`) is a linear sequence that writes every intermediate artifact into a fresh per-run folder `output/<date>-<slug>-<uuid>/`: copy source → `probe_video` + `extract_audio` → transcribe → `plan_edits` (which tightens silence first when enabled) → `render_video` → `write_review`. Stages communicate through Pydantic models, not loose dicts.

**Time model with cutting:** `EditPlan.keep_ranges` is the cut list — *source-time* spans kept in the output (empty = render the whole source uncut). Every *other* plan field (hook, captions, overlays, punch-ins) is in *output time* (the tightened timeline). Tightening happens once in `plan_edits` before planning: `tighten.py` computes `keep_ranges` and remaps the transcript to output time, then the planner builds the plan from that tightened transcript — so plan elements come out in output time automatically and stay in sync with `final.mp4`, with no per-render remapping.

Two design decisions drive everything and should be preserved when extending the code:

1. **`EditPlan` (in `models.py`) is the contract between planning and rendering.** The planner emits a validated `EditPlan`; the renderer consumes *only* what's in it. This means an LLM's JSON response is validated against a strict schema (timestamp ordering, `zoom` in `(1.0, 3.0]`, etc.) *before* it ever becomes FFmpeg commands, and `edit_plan.json` can be hand-edited and re-rendered. When adding a creative feature, extend `EditPlan` first, then teach both the planner and renderer about it.

2. **Creative behavior lives in YAML styles, never in code.** `StyleConfig` (loaded/validated by `config.py` from `configs/styles/<name>.yaml`) controls export size, hook length, caption mode/animation/position, layout, and how many punch-ins/text-cards to produce. The renderer hardcodes no creator style. New styles are new YAML files, discovered automatically by `available_styles`. **Layout is a separate axis from the caption look** (`export.layout`: `fill` crops to cover, `letterbox` fits + pads black bars with captions in the bars), overridable per run via `--layout` so any caption style composes with either layout — no N×M style files.

### Module map

- `planner.py` — `plan_edits` dispatches to `DeterministicPlanner` (no API; derives hook from opening line, captions via the shared `build_captions`, ranks longest segments for quote cards, spreads punch-ins) or `LLMPlanner`. **`LLMPlanner` always falls back to the deterministic planner on any failure**, writing the raw response to `planner_error.txt`. It pins export dims/source from the style regardless of what the model returns. The LLM only ever emits structured JSON — it never touches video. **Caption building is shared, not the LLM's job**: `build_captions` produces sentence cues (`mode: sentence`) or word-group cues carrying per-word timings (`mode: word`); in word mode the LLM's captions are discarded and rebuilt from the transcript, since per-word ms must come from transcription, not the model. When a segment lacks word timings, `build_captions` synthesizes even-spaced ones so the word look works on segment-only transcripts.
- `tighten.py` — pure (no-FFmpeg) silence/pause compression: `compute_keep_ranges` (merges speech spans across short gaps, pads phrases, drops long gaps; returns `[]` when nothing to cut), `remap_ms` (source→output time, `None` in a cut), `tighten_transcript` (remaps a transcript onto the tightened timeline). v1 cuts only silence; filler-word/retake removal would add more `keep_ranges`.
- `render.py` — thin FFmpeg translation layer. All on-screen text (hook, captions, overlays) is written into a **single ASS subtitle file** (`captions.ass`) so the filtergraph stays small: a layout stage → optional time-gated punch-in zoom → `subtitles` burn-in. When `plan.keep_ranges` is set, `build_concat_filtergraph` builds a `-filter_complex` that `trim`/`atrim`s each kept span, `concat`s them, then runs the same vf chain on the result (loudnorm moves into the graph); empty `keep_ranges` keeps the plain `-vf` path. The layout stage is the only difference between layouts (`fill` = scale-increase + `crop`; `letterbox` = scale-decrease + `pad` black bars); both yield a full WxH canvas, so punch-in and burn-in code is shared. FFmpeg runs with cwd set to the run folder so the subtitle filter can use a bare filename (avoids filter-path escaping). **Only the first punch-in is rendered**; the rest stay in the plan for inspection. In letterbox, `render_video` probes the source to compute bar heights (`_letterbox_bars`); captions sit *on* the video/bar seam (slight overlap onto the video) while the hook — when present — stays fully inside the top bar. **The hook (`style.hook.enabled`) and overlay cards (`style.visuals.overlays_enabled`) are gated in the *planner* and off by default**: when disabled they are simply absent from the plan (`plan.hook is None`, `plan.overlays == []`), and the renderer draws exactly what the plan holds. Re-enable per style with no code change. A caption cue carrying `words` animates per the style's `captions.word_animation` (`active_word` per-word highlight events, `karaoke` `\kf` sweep, `one_word` centered `WordBig`); a cue without `words` renders as one static line. Override tags (`\c`, `\kf`) are assembled around already-escaped word text so `_escape_ass` doesn't clobber them.
- `transcribe.py` — `Transcriber` protocol with `ProvidedTranscriptTranscriber` (reads JSON, zero cost) and `OpenAITranscriber` (lazy-imported, optional extra; requests word + segment granularity and buckets words into their segment). `load_transcript` tolerantly accepts both `{start_ms,end_ms}` and seconds-based `{start,end}` shapes, including an optional per-segment `words` array in either shape.
- `ffmpeg_tools.py` — `subprocess.run` wrappers (always arg lists, never shell strings). `probe_video` returns the handful of facts the pipeline needs; raises `FFmpegError` early.
- `models.py` — all data contracts. **Timestamps are integer milliseconds throughout** the system; do not introduce float-seconds in stored shapes.

## Conventions

- **After any feature or behavior change, update the "Smoke testing" section above** so it always reflects how to manually verify the latest behavior.
- The package lives at the repo root with no build step; `pyproject.toml` puts the root on the pytest import path rather than requiring an editable install.
- OpenAI usage (transcription and planning) is always behind a lazy import + optional `openai` extra, so the offline path never depends on it. Preserve this when adding API features.
- `OPENAI_API_KEY` is read from `.env` via `load_dotenv()` (see `.env.example`).
