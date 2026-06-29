"""Central configuration loaded from environment (.env aware)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Environment-driven settings.

    Precedence: process env > .env file > defaults.
    Anything not set here belongs in a subsystem's own config.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM ---
    llm_provider: Literal["anthropic", "openai", "deepseek"] = "anthropic"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"

    # --- Paths ---
    user_profile_path: Path = Field(default=PROJECT_ROOT / "user_profile.json")
    artifacts_dir: Path = Field(default=PROJECT_ROOT / "artifacts")

    def model_post_init(self, __context: object) -> None:
        """Resolve relative paths against PROJECT_ROOT so they work regardless
        of the current working directory when the script is invoked."""
        if not self.user_profile_path.is_absolute():
            object.__setattr__(
                self, "user_profile_path", PROJECT_ROOT / self.user_profile_path
            )
        if not self.artifacts_dir.is_absolute():
            object.__setattr__(
                self, "artifacts_dir", PROJECT_ROOT / self.artifacts_dir
            )

    # --- Agent ---
    agent_max_steps: int = 60
    headless: bool = False

    # --- Submit policy ---
    submit_threshold: int = 75
    trust_build_threshold: int = 90
    trust_build_count: int = 20


def get_settings() -> Settings:
    """Returns a fresh Settings read. Kept as a function (not module-level
    singleton) so tests can override env and re-import without surprises."""
    return Settings()
