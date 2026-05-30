# shortform_lab

A **local talking-head enhancer**. Give it one short raw talking-head clip
(10–60s, mp4/mov, one person speaking to camera) and it produces a polished
vertical short-form video with a hook, readable captions, simple overlays, and
punch-in zooms.

This is a local content-polish engine, not a long-form clip finder. Everything
runs locally and is file-based. The pipeline produces an inspectable
`edit_plan.json` first, then renders `final.mp4` from that plan — so every
creative decision can be reviewed and replaced.

## Pipeline

```text
input video
  -> validate and probe media
  -> extract audio
  -> transcribe speech
  -> create edit plan
  -> render captions and overlays
  -> export final video
  -> write review report
```

## Requirements

- Python 3.12+
- [FFmpeg](https://ffmpeg.org/) (`ffmpeg` and `ffprobe` on your `PATH`)

## Install

```bash
uv sync
# optional: enable OpenAI transcription + LLM planning
uv sync --extra openai
cp .env.example .env   # then fill in OPENAI_API_KEY if using OpenAI
```

## Usage

```bash
python -m shortform_lab.cli process input.mp4 --style bold_creator
```

With a pre-existing transcript (runs with no API cost):

```bash
python -m shortform_lab.cli process input.mp4 --style bold_creator --transcript transcript.json
```

Each run creates a unique output folder:

```text
output/2026-05-29-<slug>/
  source.mp4
  audio.wav
  transcript.json
  edit_plan.json
  final.mp4
  review.md
```

## Styles

Creative behavior lives in YAML configs under `configs/styles/`, not in code.
Two ship by default: `bold_creator` and `clean_captions`. Copy one to make your
own — the renderer never hardcodes a creator style.

## Tests

```bash
uv run pytest
```

FFmpeg-dependent tests generate a tiny clip on the fly and skip automatically if
FFmpeg is not installed.

## Manual acceptance test

```bash
python -m shortform_lab.cli process samples/talking_head.mp4 --style bold_creator
```

Then open `output/<project_id>/final.mp4` and score it by eye:

```text
Would I post this?                          yes/no
Are captions readable?                      yes/no
Does the hook make sense?                   yes/no
Does the video feel better than the raw input?  yes/no
```

**Success criteria:** the generated video should be clearly more postable than
the raw input, even if it is not yet polished enough for a paid product.
