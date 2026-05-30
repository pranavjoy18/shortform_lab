"""Command-line entrypoint that runs the full local pipeline.

    python -m shortform_lab.cli process input.mp4 --style bold_creator
    python -m shortform_lab.cli process input.mp4 --style bold_creator --transcript t.json

Each run creates a unique ``output/<date>-<slug>/`` folder and writes the source
copy, extracted audio, transcript, edit plan, final video, and review report.
The pipeline runs fully offline with ``--transcript`` and the deterministic
planner; ``--use-llm`` opts into the OpenAI planner (which still falls back).
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import date
from pathlib import Path

import typer
from dotenv import load_dotenv

from .config import load_style_config
from .ffmpeg_tools import ensure_ffmpeg_available, extract_audio, probe_video
from .models import Transcript
from .planner import plan_edits
from .reports import write_review
from .render import render_video
from .transcribe import OpenAITranscriber, load_transcript

app = typer.Typer(add_completion=False, help="Local talking-head video enhancer.")

OUTPUT_ROOT = Path("output")


@app.callback()
def _main() -> None:
    """Keep ``process`` as an explicit subcommand (Typer otherwise collapses a
    single-command app, which would swallow the ``process`` token as a filename)."""


@app.command()
def process(
    input_video: Path = typer.Argument(..., exists=True, dir_okay=False, help="Raw talking-head clip (mp4/mov)."),
    style: str = typer.Option("bold_creator", "--style", "-s", help="Style config name from configs/styles/."),
    layout: str | None = typer.Option(None, "--layout", help="Override layout: 'fill' (crop) or 'letterbox' (fit + black bars)."),
    tighten: bool | None = typer.Option(None, "--tighten/--no-tighten", help="Override silence/pause compression (default: per style)."),
    transcript: Path | None = typer.Option(None, "--transcript", "-t", help="Existing transcript JSON (skips transcription)."),
    use_llm: bool = typer.Option(False, "--use-llm", help="Use the OpenAI edit planner (falls back deterministically)."),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="Override the auto-generated run folder."),
) -> None:
    """Process one clip into a polished vertical short."""
    load_dotenv()
    ensure_ffmpeg_available()

    cfg = load_style_config(style)
    # Layout is an axis independent of the caption style, so it can be overridden
    # at the CLI to compose any caption style with either layout.
    if layout is not None:
        if layout not in ("fill", "letterbox"):
            raise typer.BadParameter("--layout must be 'fill' or 'letterbox'.")
        cfg.export.layout = layout
    if tighten is not None:
        cfg.tighten.enabled = tighten

    run_dir = output_dir or (OUTPUT_ROOT / _run_slug(input_video))
    run_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"→ Output folder: {run_dir}")

    # 1. Copy source.
    source_copy = run_dir / "source.mp4"
    shutil.copy2(input_video, source_copy)

    # 2. Probe + extract audio.
    info = probe_video(source_copy)
    typer.echo(f"→ Probed: {info.width}x{info.height}, {info.duration_ms/1000:.1f}s, "
               f"audio={info.has_audio}, layout={cfg.export.layout}")
    audio_path = run_dir / "audio.wav"
    if info.has_audio:
        extract_audio(source_copy, audio_path, normalize=cfg.audio.normalize)

    # 3. Transcribe (or load provided transcript).
    transcript_obj = _resolve_transcript(transcript, source_copy, run_dir, info.has_audio)
    (run_dir / "transcript.json").write_text(
        transcript_obj.model_dump_json(indent=2), encoding="utf-8"
    )
    typer.echo(f"→ Transcript: {len(transcript_obj.segments)} segment(s)")

    # 4. Plan edits.
    error_path = run_dir / "planner_error.txt"
    plan = plan_edits(
        transcript_obj, cfg,
        source_video=source_copy.name,
        use_llm=use_llm,
        error_path=error_path,
        source_duration_ms=info.duration_ms,
    )
    llm_failed = use_llm and error_path.is_file()
    (run_dir / "edit_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    if plan.keep_ranges:
        kept_ms = sum(r.duration_ms for r in plan.keep_ranges)
        typer.echo(f"→ Tightened: kept {kept_ms/1000:.1f}s of {info.duration_ms/1000:.1f}s "
                   f"in {len(plan.keep_ranges)} span(s)")
    hook_part = f'hook="{plan.hook.text}"' if plan.hook else "hook=off"
    typer.echo(f"→ Edit plan: {hook_part}, "
               f"{len(plan.captions)} captions, {len(plan.overlays)} overlays, "
               f"{len(plan.punch_ins)} punch-ins")

    # 5. Render.
    final_path = run_dir / "final.mp4"
    render_video(plan, source_copy, final_path, cfg, has_audio=info.has_audio, work_dir=run_dir)
    typer.echo(f"→ Rendered: {final_path}")

    # 6. Review report.
    write_review(
        plan, cfg,
        source_name=input_video.name,
        final_path=final_path,
        out_path=run_dir / "review.md",
        used_llm=use_llm,
        llm_failed=llm_failed,
        source_duration_ms=info.duration_ms,
    )
    typer.echo(f"✓ Done. See {run_dir / 'review.md'} and {final_path}")


def _resolve_transcript(
    transcript: Path | None, source: Path, run_dir: Path, has_audio: bool
) -> Transcript:
    if transcript is not None:
        return load_transcript(transcript)
    if not has_audio:
        raise typer.BadParameter(
            "Source has no audio track and no --transcript was provided."
        )
    typer.echo("→ No transcript provided; transcribing with OpenAI Whisper...")
    return OpenAITranscriber(normalize=False).transcribe(source, work_dir=run_dir)


def _run_slug(input_video: Path) -> str:
    """Build a unique, sortable run folder name: ``<date>-<name>-<short-uuid>``."""
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", input_video.stem).strip("-").lower() or "clip"
    return f"{date.today().isoformat()}-{stem}-{uuid.uuid4().hex[:6]}"


if __name__ == "__main__":
    app()
