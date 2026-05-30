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

## Architecture

The pipeline (`cli.py` `process`) is a linear sequence that writes every intermediate artifact into a fresh per-run folder `output/<date>-<slug>-<uuid>/`: copy source → `probe_video` + `extract_audio` → transcribe → `plan_edits` → `render_video` → `write_review`. Stages communicate through Pydantic models, not loose dicts.

Two design decisions drive everything and should be preserved when extending the code:

1. **`EditPlan` (in `models.py`) is the contract between planning and rendering.** The planner emits a validated `EditPlan`; the renderer consumes *only* what's in it. This means an LLM's JSON response is validated against a strict schema (timestamp ordering, `zoom` in `(1.0, 3.0]`, etc.) *before* it ever becomes FFmpeg commands, and `edit_plan.json` can be hand-edited and re-rendered. When adding a creative feature, extend `EditPlan` first, then teach both the planner and renderer about it.

2. **Creative behavior lives in YAML styles, never in code.** `StyleConfig` (loaded/validated by `config.py` from `configs/styles/<name>.yaml`) controls export size, hook length, caption mode/position, and how many punch-ins/text-cards to produce. The renderer hardcodes no creator style. New styles are new YAML files, discovered automatically by `available_styles`.

### Module map

- `planner.py` — `plan_edits` dispatches to `DeterministicPlanner` (no API; derives hook from opening line, sentence-level captions, ranks longest segments for quote cards, spreads punch-ins) or `LLMPlanner`. **`LLMPlanner` always falls back to the deterministic planner on any failure**, writing the raw response to `planner_error.txt`. It pins export dims/source from the style regardless of what the model returns. The LLM only ever emits structured JSON — it never touches video.
- `render.py` — thin FFmpeg translation layer. All on-screen text (hook, captions, overlays) is written into a **single ASS subtitle file** (`captions.ass`) so the filtergraph stays small: fill-crop to 9:16 → optional time-gated punch-in zoom → `subtitles` burn-in. FFmpeg runs with cwd set to the run folder so the subtitle filter can use a bare filename (avoids filter-path escaping). **Only the first punch-in is rendered**; the rest stay in the plan for inspection.
- `transcribe.py` — `Transcriber` protocol with `ProvidedTranscriptTranscriber` (reads JSON, zero cost) and `OpenAITranscriber` (lazy-imported, optional extra). `load_transcript` tolerantly accepts both `{start_ms,end_ms}` and seconds-based `{start,end}` shapes.
- `ffmpeg_tools.py` — `subprocess.run` wrappers (always arg lists, never shell strings). `probe_video` returns the handful of facts the pipeline needs; raises `FFmpegError` early.
- `models.py` — all data contracts. **Timestamps are integer milliseconds throughout** the system; do not introduce float-seconds in stored shapes.

## Conventions

- The package lives at the repo root with no build step; `pyproject.toml` puts the root on the pytest import path rather than requiring an editable install.
- OpenAI usage (transcription and planning) is always behind a lazy import + optional `openai` extra, so the offline path never depends on it. Preserve this when adding API features.
- `OPENAI_API_KEY` is read from `.env` via `load_dotenv()` (see `.env.example`).
