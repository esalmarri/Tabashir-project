"""Builds the task prompt for the browser-use agent.

Phase 1 version: minimal prompt. Feeds the agent the job URL, the candidate
profile, the rules, and the output contract. Phase 2 adds JD-derived context
(archetype, proof-point selection) and pre-written drafts.

Design note: we do **not** encode per-ATS heuristics here. The agent is
responsible for navigation. Our job is to give it enough candidate data
(verbatim) that it rarely has to invent anything, plus strict rules so it
knows when to stop rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoapply.domain.profile import Profile


@dataclass
class TaskPromptInputs:
    job_url: str
    profile: Profile
    submit_threshold: int = 75
    # Phase 2 will add: jd_archetype, proof_points_top5, star_stories_top3, drafts


def _candidate_block(p: Profile) -> str:
    """Builds the 'what you know about the applicant' section.

    Everything here is verbatim from the profile — the agent must never paraphrase
    identity fields and should prefer profile data over guesses.
    """
    resume_path = p.resume_absolute_path() or ""
    education = p.education

    lines = [
        "## Candidate profile (authoritative — do not invent or paraphrase identity fields)",
        "",
        "### Identity",
        f"- Full name: {p.full_name}",
        f"- First name: {p.first_name}",
        f"- Last name: {p.last_name}",
        f"- Email: {p.email}",
        f"- Phone: {p.phone} (country code {p.country_code}; full: {p.phone_full})",
        f"- Address: {p.address}, {p.city}, {p.state} {p.zip_code}, {p.country}",
        f"- Nationality: {p.nationality}",
        f"- Date of birth: {p.date_of_birth}",
        "",
        "### Links",
        f"- LinkedIn: {p.linkedin_url}",
        f"- GitHub: {p.github_url}",
        f"- Portfolio: {p.portfolio_url or '(none)'}",
        "",
        "### Resume",
        f"- Resume absolute path: {resume_path or '(missing — do not fabricate)'}",
        "  If a file upload field accepts a resume, upload this file rather than retyping sections.",
        "",
        "### Education",
        f"- School: {education.school}",
        f"- Degree: {education.degree} in {education.field_of_study}",
        f"- Graduation year: {education.graduation_year}",
        f"- Bachelors: {education.bachelors_degree}",
        f"- GPA: {education.gpa}",
        "",
        "### Work context",
        f"- Current title: {p.current_job_title}",
        f"- Years of experience: {p.years_of_experience}",
        f"- Work authorization: {p.work_authorization}",
        f"- Sponsorship needed: {p.sponsorship_needed}",
        f"- Desired start date: {p.start_date}",
        f"- Salary expectation: {p.salary_expectation or '(leave blank unless required)'}",
        "",
        "### Demographics (only if the form explicitly asks)",
        f"- Gender: {p.gender}",
        f"- Race/ethnicity: {p.race_ethnicity}",
        f"- Veteran status: {p.veteran_status}",
        f"- Disability status: {p.disability_status}",
    ]

    ca = p.custom_answers
    lines += [
        "",
        "### Pre-approved free-text answers (use these verbatim or adapt; do not invent new claims)",
        f"- How did you hear about us: {ca.hear_about}",
        f"- Why interested: {ca.why_interested}",
        f"- Cover letter body: {ca.cover_letter_text}",
        f"- Additional info: {ca.additional_info}",
        "",
        "### Career narrative (one-paragraph summary, use for 'tell us about yourself' prompts)",
        p.career_narrative,
    ]

    if p.proof_points:
        lines += ["", "### Proof points (cite verbatim — they contain the real metrics)"]
        for pp in p.proof_points:
            lines.append(f"- {pp.text}")

    if p.star_stories:
        lines += ["", "### STAR stories (use verbatim for behavioral questions)"]
        for s in p.star_stories:
            lines.append(f"- {s.text}")

    return "\n".join(lines)


RULES = """\
## Rules — read carefully before acting

**Navigation**
- Start by visiting the job URL. If a cookie banner blocks interaction, accept or dismiss it so the page is usable. Do not click "Manage preferences" — click accept/dismiss/close.
- Click the application/apply button if the landing page is a job description rather than the form. Do NOT click "I'm interested", "Save", "Watchlist" — only the button that opens the actual application.
- If the site requires sign-in/registration before applying (auth wall), STOP and report `outcome: auth_required` with notes on what the page asked for.
- If the page shows a CAPTCHA, STOP and report `outcome: captcha`.
- If the page shows "Job not found" / "expired" / "closed", STOP and report `outcome: job_expired`.

**Filling**
- Fill every required field using the candidate profile above.
- Upload the resume file (absolute path provided) to any file-upload field that accepts resumes/CVs. Do NOT paste the resume as text unless there is no upload field.
- For free-text questions (cover letter, "why are you interested", etc.), use the pre-approved answers above verbatim, or lightly adapt them to the question. Never invent claims, employers, titles, dates, or metrics not present in the profile.
- For yes/no questions about work authorization, sponsorship, or relocation, use the profile's exact answers.
- If a required field has no answer in the profile and cannot be safely inferred, leave it blank and add it to `skipped_fields` with the reason — do not guess.

**Writing quality**
- ATS-safe ASCII only (no em-dashes, curly quotes, or emojis in form fields).
- Cite real metrics from the proof points; avoid clichés ("passionate", "team player", "go-getter").
- Keep answers concise unless the question requests detail.

**Submit policy**
- After filling, compute a self-assessed confidence score 0-100 using the rubric below.
- If confidence >= {submit_threshold}: click the final Submit button and confirm the confirmation screen/text.
- If confidence < {submit_threshold}: do NOT click Submit. Stop at the review screen and report `outcome: stopped_for_review` with `blockers` explaining what is missing or uncertain.

**Confidence rubric (use this exactly)**
Start at 0. Add/subtract:
  +25 all required fields filled from profile data
  +20 resume uploaded OR no resume field exists
  +15 no visible validation errors
  +15 the visible submit button is labeled "Submit" or "Submit Application"
  +15 no answers required guessing
  +10 a review/summary screen was reached before submit
  -10 for every field you had to guess at
  cap at 40 if you encountered a captcha or auth wall
"""

OUTPUT_CONTRACT = """\
## Output contract

When you finish (submitted, stopped-for-review, or blocked), return a single JSON object:

{
  "outcome": "submitted" | "stopped_for_review" | "auth_required" | "captcha" | "job_expired" | "error",
  "confidence": 0-100 integer,
  "confidence_breakdown": {"<rubric line>": <signed int>, ...},
  "fields_filled": {"<field label>": "<value used>", ...},
  "skipped_fields": [{"label": "...", "reason": "..."}],
  "submit_clicked": true | false,
  "confirmation_text": "<text of confirmation page if submitted, else empty>",
  "final_url": "<current URL when you stopped>",
  "blockers": ["<short description of anything that stopped you>"],
  "notes": "<free-form observations>"
}

Do not include any other text in your final response — just the JSON.
"""


def build_task_prompt(inputs: TaskPromptInputs) -> str:
    """Assembles the full task prompt for `browser_use.Agent(task=...)`."""
    header = (
        f"# Goal\n"
        f"Apply to the job at this URL on behalf of the candidate below.\n"
        f"URL: {inputs.job_url}\n"
    )
    candidate = _candidate_block(inputs.profile)
    rules = RULES.format(submit_threshold=inputs.submit_threshold)
    return "\n\n".join([header, candidate, rules, OUTPUT_CONTRACT])
