"""Profile domain model.

Loads the existing `user_profile.json` into a typed structure and exposes
a couple of convenience accessors the agent task prompt needs.

We intentionally keep the JSON schema stable (see plan: Phase 3 moves this
into a Postgres jsonb column; the *shape* must not change).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Education(BaseModel):
    model_config = ConfigDict(extra="allow")
    school: str = ""
    degree: str = ""
    field_of_study: str = ""
    graduation_year: str = ""
    bachelors_degree: str = ""
    gpa: str = ""


class CustomAnswers(BaseModel):
    model_config = ConfigDict(extra="allow")
    hear_about: str = ""
    why_interested: str = ""
    cover_letter_text: str = ""
    additional_info: str = ""


class ProofPoint(BaseModel):
    text: str
    keywords: list[str] = Field(default_factory=list)


class StarStory(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    """Typed view over user_profile.json.

    `extra="allow"` so unknown fields survive round-tripping.
    """

    model_config = ConfigDict(extra="allow")

    # Identity
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    country_code: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = ""
    nationality: str = ""

    # Links
    linkedin_url: str = ""
    github_url: str = ""
    portfolio_url: str = ""
    resume_path: str = ""
    cover_letter_path: str = ""

    # Background
    education: Education = Field(default_factory=Education)
    current_job_title: str = ""
    work_authorization: str = ""
    sponsorship_needed: str = ""
    years_of_experience: str = ""
    salary_expectation: str = ""
    start_date: str = ""
    gender: str = ""
    race_ethnicity: str = ""
    veteran_status: str = ""
    disability_status: str = ""
    date_of_birth: str = ""

    # Writing
    custom_answers: CustomAnswers = Field(default_factory=CustomAnswers)
    career_narrative: str = ""
    proof_points: list[ProofPoint] = Field(default_factory=list)
    star_stories: list[StarStory] = Field(default_factory=list)

    # --- Derived accessors ---

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def phone_full(self) -> str:
        if self.country_code and self.phone:
            return f"{self.country_code} {self.phone}"
        return self.phone

    def resume_absolute_path(self) -> str | None:
        """Absolute path to the resume file, or None if missing/unreadable.

        The browser-use agent needs a real filesystem path to pass to
        `<input type=file>` fields.
        """
        if not self.resume_path:
            return None
        p = Path(self.resume_path).expanduser()
        if not p.is_absolute():
            # Resolve relative to project root (two levels up from this file's package root)
            p = (Path(__file__).resolve().parents[3] / self.resume_path).resolve()
        return str(p) if p.exists() else None


def load_profile(path: str | Path) -> Profile:
    """Loads user_profile.json from disk into a validated `Profile`."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Profile file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    return Profile.model_validate(raw)


# Keep a simple dataclass alias around for callers who want lighter-weight access.
@dataclass
class ProfileSnapshot:
    """Read-only projection of a Profile for agent prompt building."""

    profile: Profile
    source_path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "ProfileSnapshot":
        p = Path(path).expanduser().resolve()
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls(profile=Profile.model_validate(raw), source_path=p, raw=raw)
