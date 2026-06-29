"""
Tabashir Auto-Apply Integration
================================
يربط داتابيز تبشير مع Auto-Apply لتقديم تلقائي على الوظائف.

Usage:
    uv run python tabashir_auto_apply.py <client_id> <job_url> <job_title> [location]

Example:
    uv run python tabashir_auto_apply.py 123 https://company.com/apply "Software Engineer" "Dubai, UAE"
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

import paramiko
import psycopg2
import psycopg2.extras

_ROOT = Path(__file__).resolve().parent

# Dedicated Chrome profile for the bot — log in once (GUI "Setup Login"), sessions
# (Bayt/LinkedIn/Google) persist here so every apply starts already authenticated.
CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BOT_PROFILE_DIR = _ROOT / "bot_chrome_profile"

_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from autoapply.agent.artifacts import new_bundle
from autoapply.agent.runner import run_apply
from autoapply.config import get_settings
from autoapply.domain.profile import load_profile
from autoapply.llm.provider import build_provider

# ── Database ────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host": "77.243.85.225",
    "port": 5432,
    "database": "tabashir",
    "user": "postgres",
    "password": "tabashir2025",
}

# ── SSH / CV Storage ─────────────────────────────────────────────────────────
SSH_HOST = "77.243.85.225"
SSH_USER = "root"
SSH_PASS = "THai@ae2026-27"
CV_REMOTE_DIR = "/var/www/AI_Job_Matching_and_Apply/CVs"


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_client(client_id: int) -> dict:
    conn = psycopg2.connect(**DB_CONFIG, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, email, major, skills, keywords, degree, gpa,
               phone_number, location, nationality, gender, filename, cv_link,
               jobs_to_apply_number
        FROM clients WHERE id = %s
        """,
        (client_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"Client {client_id} not found in database")
    return dict(row)


def record_application(
    client_id: int,
    job_title: str,
    job_url: str,
    location: str,
    status: str,
    failure_reason: str = "",
    screenshot_path: str = "",
) -> None:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(MAX(no), 0) + 1 FROM manual_applications WHERE client_id = %s",
        (client_id,),
    )
    next_no = cur.fetchone()[0]
    images = json.dumps([screenshot_path] if screenshot_path else [])
    cur.execute(
        """
        INSERT INTO manual_applications
          (client_id, no, location, app_date, job_title, job_link,
           status, failure_reason, images, applied_by, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            client_id, next_no, location, date.today(),
            job_title, job_url, status, failure_reason,
            images, "AUTO", datetime.now(),
        ),
    )
    conn.commit()
    conn.close()
    print(f"Recorded: {job_title} -> {status}")


def decrement_quota(client_id: int) -> None:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE clients
        SET jobs_to_apply_number = jobs_to_apply_number - 1
        WHERE id = %s AND jobs_to_apply_number > 0
        """,
        (client_id,),
    )
    conn.commit()
    conn.close()


# ── CV Download ──────────────────────────────────────────────────────────────

def download_cv(filename: str, dest_dir: Path) -> Path | None:
    if not filename:
        return None
    pdf_name = filename.replace(".docx", ".pdf")
    local = dest_dir / pdf_name
    if local.exists():
        return local
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=15)
        sftp = ssh.open_sftp()
        # Try PDF first, then original
        for remote_name in [pdf_name, filename]:
            try:
                sftp.get(f"{CV_REMOTE_DIR}/{remote_name}", str(dest_dir / remote_name))
                sftp.close(); ssh.close()
                return dest_dir / remote_name
            except FileNotFoundError:
                continue
        sftp.close(); ssh.close()
    except Exception as e:
        print(f"Warning: Could not download CV — {e}")
    return None


# ── Profile Builder ──────────────────────────────────────────────────────────

def build_profile(client: dict, cv_path: Path | None) -> dict:
    name_parts = (client["name"] or "").strip().split(" ", 1)
    first = name_parts[0] if name_parts else ""
    last = name_parts[1] if len(name_parts) > 1 else ""
    skills_text = (client["skills"] or "")[:300]
    keywords_text = (client["keywords"] or "")[:200]
    major = client["major"] or ""
    degree = client["degree"] or ""

    return {
        "first_name": first,
        "last_name": last,
        "email": client["email"] or "",
        "password": "",
        "phone": (client["phone_number"] or "").replace("+", "").strip(),
        "country_code": "+971",
        "address": "",
        "city": client["location"] or "Dubai",
        "state": "",
        "zip_code": "",
        "country": "United Arab Emirates",
        "nationality": client["nationality"] or "",
        "linkedin_url": "",
        "github_url": "",
        "portfolio_url": "",
        "resume_path": str(cv_path) if cv_path else "",
        "cover_letter_path": "",
        "education": {
            "school": "",
            "degree": degree,
            "field_of_study": major,
            "graduation_year": "",
            "bachelors_degree": f"{degree} in {major}".strip(" in"),
            "gpa": str(client["gpa"] or ""),
        },
        "current_job_title": major,
        "work_authorization": "Yes",
        "sponsorship_needed": "No",
        "years_of_experience": "2",
        "salary_expectation": "",
        "start_date": "Immediately",
        "gender": client["gender"] or "Prefer not to say",
        "race_ethnicity": "Prefer not to say",
        "veteran_status": "No",
        "disability_status": "Prefer not to say",
        "date_of_birth": "",
        "custom_answers": {
            "hear_about": "Online job board",
            "why_interested": (
                f"I am a {major} professional with skills in {skills_text[:150]}. "
                "I am eager to contribute to your organization."
            ),
            "cover_letter_text": (
                f"I hold a {degree} in {major}. "
                f"My key skills include {skills_text[:250]}. "
                "I am available to start immediately and am excited about this opportunity."
            ),
            "additional_info": (
                f"Skills: {skills_text[:200]}. Available to start immediately."
            ),
        },
        "career_narrative": (
            f"{degree} in {major} with expertise in {keywords_text}. "
            f"Based in {client['location'] or 'UAE'}. Available immediately."
        ),
        "proof_points": [
            {
                "text": f"Skilled in {skills_text[:200]}",
                "keywords": [k.strip() for k in (client["keywords"] or "").split(",")[:5]],
            }
        ],
        "star_stories": [],
    }


# ── CAPTCHA-Aware Runner ─────────────────────────────────────────────────────

async def _run_with_captcha_pause(*, job_url, profile, settings, llm_provider, bundle):
    """Runs the browser-use agent and pauses on CAPTCHA for manual solving."""
    from autoapply.agent.runner import (
        RunResult, _parse_agent_output, _merge_output,
        _save_screenshots, _save_trace, _finalise,
    )
    from autoapply.agent.task_builder import TaskPromptInputs, build_task_prompt
    from datetime import datetime, timezone

    try:
        from browser_use import Agent
        from browser_use.browser.profile import BrowserProfile
    except ImportError as e:
        from autoapply.agent.runner import run_apply
        return await run_apply(
            job_url=job_url, profile=profile,
            settings=settings, llm_provider=llm_provider, bundle=bundle,
        )

    started = datetime.now(timezone.utc)
    result = RunResult(job_url=job_url, job_id=bundle.job_id,
                       outcome="error", started_at=started.isoformat())

    task = build_task_prompt(TaskPromptInputs(
        job_url=job_url, profile=profile,
        submit_threshold=settings.submit_threshold,
    ))
    bundle.save_text("task_prompt.md", task)

    try:
        chat_model = llm_provider.chat_model()
    except Exception as e:
        result.error = f"LLM provider failed: {e}"
        _finalise(result, bundle)
        return result

    # Dedicated, persistent profile (see BOT_PROFILE_DIR): the user logs into Bayt /
    # LinkedIn / Google once via the GUI's "Setup Login", and those sessions are
    # reused here — so applies start already logged in, no in-agent login needed.
    BOT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    browser_profile = BrowserProfile(
        headless=False,
        chromium_sandbox=False,
        executable_path=CHROME_EXE,
        user_data_dir=str(BOT_PROFILE_DIR),
    )
    resume_path = profile.resume_absolute_path()
    available_files = [resume_path] if resume_path else None
    use_vision = llm_provider.name != "deepseek"

    _captcha_paused = {"active": False}

    async def on_step(state, output, step_num):
        if _captcha_paused["active"]:
            return
        try:
            state_str = str(state).lower()
            captcha_keywords = ["verify you are human", "captcha", "i'm not a robot", "cloudflare"]
            if any(kw in state_str for kw in captcha_keywords):
                _captcha_paused["active"] = True
                print("\n" + "="*60)
                print("  *** CAPTCHA DETECTED — Browser is still open ***")
                print("  Please solve the CAPTCHA in the browser window.")
                print("="*60)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input("  Press Enter after solving the CAPTCHA... ")
                )
                print("  Resuming...")
                _captcha_paused["active"] = False
        except Exception:
            pass

    try:
        agent = Agent(
            task=task,
            llm=chat_model,
            browser_profile=browser_profile,
            use_vision=use_vision,
            available_file_paths=available_files,
        )
        # Register step callback if supported
        cb = getattr(agent, "register_new_step_callback", None)
        if callable(cb):
            cb(on_step)
    except Exception as e:
        result.error = f"Agent construction failed: {e}"
        _finalise(result, bundle)
        return result

    try:
        history = await agent.run(max_steps=settings.agent_max_steps)
    except Exception as e:
        result.error = f"Agent.run() raised: {e!r}"
        _finalise(result, bundle)
        return result

    final_text = history.final_result() or ""
    result.raw_agent_output = final_text
    bundle.save_text("agent_final.txt", final_text)

    parsed = _parse_agent_output(final_text)
    if parsed:
        _merge_output(result, parsed)
    else:
        result.outcome = "stopped_for_review"
        result.blockers.append("agent did not return JSON per output contract")

    result.step_count = history.number_of_steps()
    _save_screenshots(bundle, history)
    _save_trace(bundle, history)
    _finalise(result, bundle)
    return result


# ── Main ─────────────────────────────────────────────────────────────────────

async def auto_apply(client_id: int, job_url: str, job_title: str, location: str = "") -> None:
    print("\n" + "="*50)
    print("  Tabashir Auto-Apply")
    print("="*50)
    print(f"Client ID : {client_id}")
    print(f"Job       : {job_title}")
    print(f"URL       : {job_url}")

    # 1. Get candidate
    client = get_client(client_id)
    print(f"Candidate : {client['name']} ({client['email']})")

    # 2. Download CV
    cv_dir = _ROOT / "cvs"
    cv_dir.mkdir(exist_ok=True)
    cv_path = download_cv(client["filename"], cv_dir)
    print(f"CV        : {cv_path or 'NOT FOUND — proceeding without CV'}")

    # 3. Build & save profile
    profile_data = build_profile(client, cv_path)
    profile_path = _ROOT / f"_profile_{client_id}.json"
    profile_path.write_text(json.dumps(profile_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 4. Run auto-apply agent
    settings = get_settings()
    settings.user_profile_path = profile_path
    settings.submit_threshold = 75  # auto-submit when confidence >= 75

    profile_obj = load_profile(profile_path)
    bundle = new_bundle(settings.artifacts_dir, job_url)
    llm_provider = build_provider(settings)

    result = await _run_with_captcha_pause(
        job_url=job_url,
        profile=profile_obj,
        settings=settings,
        llm_provider=llm_provider,
        bundle=bundle,
    )

    # 5. Find last screenshot
    artifact_dir = Path(str(settings.artifacts_dir)) / bundle.job_id
    screenshots = sorted(artifact_dir.glob("*.png")) if artifact_dir.exists() else []
    screenshot = str(screenshots[-1]) if screenshots else ""

    # 6. Map outcome → status
    status_map = {
        "submitted": "Applied",
        "stopped_for_review": "Pending Review",
        "job_expired": "Failed",
        "auth_required": "Failed",
        "captcha": "Failed",
        "error": "Failed",
    }
    status = status_map.get(result.outcome, "Failed")
    failure_reason = "; ".join(result.blockers) if result.blockers else (result.error or "")

    # 7. Record in DB
    record_application(
        client_id=client_id,
        job_title=job_title,
        job_url=job_url,
        location=location,
        status=status,
        failure_reason=failure_reason,
        screenshot_path=screenshot,
    )

    # 8. Decrement quota if submitted
    if result.outcome == "submitted":
        decrement_quota(client_id)
        print("Quota decremented.")

    # 9. Print summary
    print("\n--- Result ---")
    print(f"Outcome   : {result.outcome}")
    print(f"Confidence: {result.confidence}")
    print(f"Fields    : {len(result.fields_filled)} filled")
    print(f"Status    : {status}")
    if failure_reason:
        print(f"Reason    : {failure_reason}")
    print(f"Artifacts : {artifact_dir}")

    # Cleanup temp profile
    profile_path.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    asyncio.run(auto_apply(
        client_id=int(sys.argv[1]),
        job_url=sys.argv[2],
        job_title=sys.argv[3],
        location=sys.argv[4] if len(sys.argv) > 4 else "",
    ))
