"""CLI entry point: apply to a single job URL.

Usage:
    uv run python scripts/apply_one.py <job_url>
    uv run python scripts/apply_one.py <job_url> --profile path/to/user_profile.json
    uv run python scripts/apply_one.py <job_url> --headless

This is Phase 1's only surface. It loads the profile, constructs the LLM
provider, runs the browser-use agent, and prints a short summary plus the
path to the artifact directory.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running as `python scripts/apply_one.py` without installing the package.
_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import typer  # noqa: E402

from autoapply.agent.artifacts import new_bundle  # noqa: E402
from autoapply.agent.runner import run_apply  # noqa: E402
from autoapply.config import get_settings  # noqa: E402
from autoapply.domain.profile import load_profile  # noqa: E402
from autoapply.llm.provider import build_provider  # noqa: E402


app = typer.Typer(add_completion=False, help="Apply to a single job URL via the LLM-driven agent.")


@app.command()
def apply(
    job_url: str = typer.Argument(..., help="Full job URL (apply page or job description)."),
    profile: Path | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Path to user_profile.json. Defaults to USER_PROFILE_PATH env or ./user_profile.json.",
    ),
    headless: bool | None = typer.Option(
        None,
        "--headless/--headed",
        help="Override HEADLESS from .env. Default is headed (visible browser).",
    ),
    max_steps: int | None = typer.Option(
        None,
        "--max-steps",
        help="Override agent step cap. Default from AGENT_MAX_STEPS.",
    ),
    submit_threshold: int | None = typer.Option(
        None,
        "--submit-threshold",
        help="Override confidence threshold for auto-submit (0-100).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build task prompt and print it, but don't launch the agent.",
    ),
) -> None:
    """Run one job application."""
    settings = get_settings()
    if profile is not None:
        settings.user_profile_path = profile.expanduser().resolve()
    if headless is not None:
        settings.headless = headless
    if max_steps is not None:
        settings.agent_max_steps = max_steps
    if submit_threshold is not None:
        settings.submit_threshold = submit_threshold

    # Load the profile now so we fail fast on bad paths/JSON.
    try:
        profile_obj = load_profile(settings.user_profile_path)
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    bundle = new_bundle(settings.artifacts_dir, job_url)

    if dry_run:
        from autoapply.agent.task_builder import TaskPromptInputs, build_task_prompt

        task = build_task_prompt(
            TaskPromptInputs(
                job_url=job_url,
                profile=profile_obj,
                submit_threshold=settings.submit_threshold,
            )
        )
        bundle.save_text("task_prompt.md", task)
        bundle.save_manifest()
        typer.echo(task)
        typer.secho(f"\n[dry-run] artifacts: {bundle.storage.root / bundle.job_id}", fg=typer.colors.CYAN)
        return

    # Build the LLM provider (will raise early if keys are missing).
    llm_provider = build_provider(settings)

    result = asyncio.run(
        run_apply(
            job_url=job_url,
            profile=profile_obj,
            settings=settings,
            llm_provider=llm_provider,
            bundle=bundle,
        )
    )

    _print_summary(result, bundle)

    # Exit code signals outcome to shell callers / CI.
    if result.outcome == "submitted":
        raise typer.Exit(code=0)
    if result.outcome == "stopped_for_review":
        raise typer.Exit(code=10)
    if result.outcome == "auth_required":
        raise typer.Exit(code=11)
    if result.outcome == "captcha":
        raise typer.Exit(code=12)
    if result.outcome == "job_expired":
        raise typer.Exit(code=13)
    raise typer.Exit(code=1)


def _print_summary(result, bundle) -> None:
    artifact_dir = Path(str(bundle.storage.root)) / bundle.job_id
    typer.echo("")
    typer.secho("=== Auto-Apply result ===", fg=typer.colors.BRIGHT_WHITE, bold=True)
    color = {
        "submitted": typer.colors.GREEN,
        "stopped_for_review": typer.colors.YELLOW,
        "auth_required": typer.colors.YELLOW,
        "captcha": typer.colors.YELLOW,
        "job_expired": typer.colors.CYAN,
        "error": typer.colors.RED,
    }.get(result.outcome, typer.colors.WHITE)
    typer.secho(f"outcome:       {result.outcome}", fg=color, bold=True)
    typer.echo(f"confidence:    {result.confidence}")
    typer.echo(f"submitted:     {result.submit_clicked}")
    typer.echo(f"steps:         {result.step_count}")
    typer.echo(f"final URL:     {result.final_url}")
    if result.blockers:
        typer.secho(f"blockers:      {'; '.join(result.blockers)}", fg=typer.colors.YELLOW)
    if result.error:
        typer.secho(f"error:         {result.error}", fg=typer.colors.RED)
    typer.echo(f"fields filled: {len(result.fields_filled)}  skipped: {len(result.skipped_fields)}")
    typer.echo(f"artifacts:     {artifact_dir}")
    if result.confidence_breakdown:
        typer.echo("confidence breakdown:")
        for k, v in result.confidence_breakdown.items():
            typer.echo(f"  {v:+d}  {k}")
    typer.echo("")


def main() -> None:
    """Entry point for console_scripts."""
    app()


if __name__ == "__main__":
    main()
