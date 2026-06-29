"""browser-use Agent wrapper — the core of Phase 1.

Responsibilities:
1. Build the task prompt from the candidate profile + job URL.
2. Construct and run a `browser_use.Agent` bound to a vision-capable chat model.
3. Capture screenshots, the step trace, and the agent's final structured output
   into the artifact bundle.
4. Parse the final JSON-per-contract response into a typed `RunResult` the CLI
   can print.

Tested against browser-use 0.11.x. API surface confirmed:
  - Agent(task, llm, browser_profile, available_file_paths, use_vision, ...)
  - agent.run(max_steps) → AgentHistoryList
  - history.final_result() → str | None
  - history.number_of_steps() → int
  - history.screenshot_paths() → list[str | None]
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoapply.agent.artifacts import ArtifactBundle
from autoapply.agent.task_builder import TaskPromptInputs, build_task_prompt
from autoapply.config import Settings
from autoapply.domain.profile import Profile
from autoapply.llm.provider import LLMProvider


VALID_OUTCOMES = {
    "submitted",
    "stopped_for_review",
    "auth_required",
    "captcha",
    "job_expired",
    "error",
}


@dataclass
class RunResult:
    """What the runner returns to the CLI + persists as result.json."""

    job_url: str
    job_id: str
    outcome: str = "error"
    confidence: int = 0
    confidence_breakdown: dict[str, int] = field(default_factory=dict)
    fields_filled: dict[str, Any] = field(default_factory=dict)
    skipped_fields: list[dict[str, Any]] = field(default_factory=list)
    submit_clicked: bool = False
    confirmation_text: str = ""
    final_url: str = ""
    blockers: list[str] = field(default_factory=list)
    notes: str = ""
    raw_agent_output: str = ""
    step_count: int = 0
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- JSON parsing ----------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_agent_output(text: str) -> dict[str, Any] | None:
    """Extracts the final JSON object from the agent's last message.

    Agents often wrap JSON in prose or markdown fences; we try multiple
    strategies before giving up.
    """
    if not text:
        return None
    text = text.strip()
    # Strip common markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # 1. Whole string
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2. Largest {...} block
    candidates = _JSON_BLOCK_RE.findall(text)
    candidates.sort(key=len, reverse=True)
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def _safe_int(x: Any) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _merge_output(result: RunResult, parsed: dict[str, Any]) -> None:
    outcome = str(parsed.get("outcome", "")).strip().lower()
    if outcome in VALID_OUTCOMES:
        result.outcome = outcome
    if (v := _safe_int(parsed.get("confidence"))) is not None:
        result.confidence = v
    breakdown = parsed.get("confidence_breakdown") or {}
    if isinstance(breakdown, dict):
        result.confidence_breakdown = {
            str(k): int(v)
            for k, v in breakdown.items()
            if _safe_int(v) is not None
        }
    fields = parsed.get("fields_filled") or {}
    if isinstance(fields, dict):
        result.fields_filled = {str(k): v for k, v in fields.items()}
    skipped = parsed.get("skipped_fields") or []
    if isinstance(skipped, list):
        result.skipped_fields = [s for s in skipped if isinstance(s, dict)]
    result.submit_clicked = bool(parsed.get("submit_clicked"))
    result.confirmation_text = str(parsed.get("confirmation_text", ""))[:4000]
    result.final_url = str(parsed.get("final_url", ""))
    blockers = parsed.get("blockers") or []
    if isinstance(blockers, list):
        result.blockers = [str(b) for b in blockers]
    result.notes = str(parsed.get("notes", ""))[:4000]


# --- Runner ----------------------------------------------------------------


async def run_apply(
    *,
    job_url: str,
    profile: Profile,
    settings: Settings,
    llm_provider: LLMProvider,
    bundle: ArtifactBundle,
) -> RunResult:
    """Runs a single job application end-to-end.

    Phase 1 scope: no retries, no JD analyzer, no drafts. Build prompt,
    launch browser-use agent, parse output, save artifacts.
    """
    started = datetime.now(timezone.utc)
    result = RunResult(
        job_url=job_url,
        job_id=bundle.job_id,
        outcome="error",
        started_at=started.isoformat(),
    )

    # 1. Build the task prompt
    task = build_task_prompt(
        TaskPromptInputs(
            job_url=job_url,
            profile=profile,
            submit_threshold=settings.submit_threshold,
        )
    )
    bundle.save_text("task_prompt.md", task)

    # 2. Import browser-use lazily
    try:
        from browser_use import Agent  # type: ignore
        from browser_use.browser.profile import BrowserProfile  # type: ignore
    except ImportError as e:
        result.error = (
            "browser-use is not installed. Run: uv sync\n"
            f"Underlying error: {e}"
        )
        _finalise(result, bundle)
        return result

    # 3. Build LLM
    try:
        chat_model = llm_provider.chat_model()
    except Exception as e:
        result.error = f"Failed to construct LLM provider: {e}"
        _finalise(result, bundle)
        return result

    # 4. Configure browser profile
    browser_profile = BrowserProfile(
        headless=settings.headless,
        # Disable sandbox inside Docker/CI; fine on macOS too.
        chromium_sandbox=False,
    )

    # 5. Tell the agent which files it may upload (the resume).
    available_files: list[str] = []
    resume_path = profile.resume_absolute_path()
    if resume_path:
        available_files = [resume_path]

    # 6. Vision: disable for providers that don't support image input (DeepSeek).
    use_vision = llm_provider.name != "deepseek"

    # 7. Construct Agent
    try:
        agent = Agent(
            task=task,
            llm=chat_model,
            browser_profile=browser_profile,
            use_vision=use_vision,
            available_file_paths=available_files if available_files else None,
        )
    except Exception as e:
        result.error = f"Failed to construct Agent: {e}"
        _finalise(result, bundle)
        return result

    # 8. Run
    try:
        history = await agent.run(max_steps=settings.agent_max_steps)
    except Exception as e:
        result.error = f"Agent.run() raised: {e!r}"
        _finalise(result, bundle)
        return result

    # 9. Extract structured output
    final_text: str = history.final_result() or ""
    result.raw_agent_output = final_text
    bundle.save_text("agent_final.txt", final_text)

    parsed = _parse_agent_output(final_text)
    if parsed:
        _merge_output(result, parsed)
    else:
        # Agent didn't return structured JSON per contract — treat as review.
        result.outcome = "stopped_for_review"
        result.blockers.append(
            "agent did not return JSON per output contract — see agent_final.txt"
        )

    # 10. Step count & artifacts
    result.step_count = history.number_of_steps()
    _save_screenshots(bundle, history)
    _save_trace(bundle, history)

    _finalise(result, bundle)
    return result


# --- Helpers ---------------------------------------------------------------

def _finalise(result: RunResult, bundle: ArtifactBundle) -> None:
    result.finished_at = datetime.now(timezone.utc).isoformat()
    bundle.save_json("result.json", result.as_dict())
    bundle.save_manifest()


def _save_screenshots(bundle: ArtifactBundle, history: Any) -> None:
    """Persist step screenshots from the agent history to the artifact bundle."""
    try:
        paths = history.screenshot_paths()
    except Exception:
        return
    if not paths:
        return
    for i, path in enumerate(paths):
        if not path:
            continue
        try:
            p = Path(path)
            if p.exists():
                bundle.save_screenshot(f"step_{i:03d}.png", p.read_bytes())
        except Exception:
            # Never let artifact saving break a run.
            pass


def _save_trace(bundle: ArtifactBundle, history: Any) -> None:
    """Dump a compact per-step trace for debugging."""
    try:
        actions = history.action_history() if callable(getattr(history, "action_history", None)) else []
        results_list = history.action_results() if callable(getattr(history, "action_results", None)) else []
        urls = history.urls() if callable(getattr(history, "urls", None)) else []
        thoughts = history.model_thoughts() if callable(getattr(history, "model_thoughts", None)) else []
    except Exception:
        actions, results_list, urls, thoughts = [], [], [], []

    steps = []
    n = max(len(actions), len(results_list), len(urls), len(thoughts))
    for i in range(n):
        entry: dict[str, Any] = {"step": i}
        if i < len(urls) and urls[i]:
            entry["url"] = str(urls[i])
        if i < len(thoughts) and thoughts[i]:
            entry["thought"] = str(thoughts[i])[:1000]
        if i < len(actions) and actions[i] is not None:
            entry["action"] = _to_jsonable(actions[i])
        if i < len(results_list) and results_list[i] is not None:
            entry["result"] = str(results_list[i])[:500]
        steps.append(entry)

    bundle.save_json("trace.json", {"steps": steps, "total_steps": history.number_of_steps()})


def _to_jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)
