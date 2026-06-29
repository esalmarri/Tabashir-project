"""Abstract LLM provider.

browser-use expects a LangChain-compatible chat model object. To keep the
rest of the app provider-agnostic we wrap construction behind this factory:
callers get a chat model ready to hand to `browser_use.Agent(llm=...)`.

Supported providers:
  anthropic  — Claude (default, vision-capable)
  openai     — GPT-4o / GPT-4o-mini (vision-capable)
  deepseek   — DeepSeek-V3 / DeepSeek-R1 (text-only; vision disabled)
"""

from __future__ import annotations

from typing import Any, Protocol

from autoapply.config import Settings


class LLMProvider(Protocol):
    """Provider returns a LangChain-compatible chat model for browser-use."""

    def chat_model(self) -> Any:
        """Returns the object to pass to `browser_use.Agent(llm=...)`."""
        ...

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...


def build_provider(settings: Settings) -> LLMProvider:
    """Factory: picks a concrete provider from settings.

    Reads LLM_PROVIDER from the environment (or .env). Raises clearly if the
    required API key is missing so the user sees a helpful message immediately
    rather than a cryptic auth error 30 seconds into a run.
    """
    if settings.llm_provider == "anthropic":
        from autoapply.llm.anthropic_provider import AnthropicProvider

        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set.\n"
                "Add it to .env:  ANTHROPIC_API_KEY=sk-ant-..."
            )
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
        )

    if settings.llm_provider == "openai":
        from autoapply.llm.openai_provider import OpenAIProvider

        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set.\n"
                "Add it to .env:  OPENAI_API_KEY=sk-..."
            )
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
        )

    if settings.llm_provider == "deepseek":
        from autoapply.llm.deepseek_provider import DeepSeekProvider

        if not settings.deepseek_api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set.\n"
                "Add it to .env:  DEEPSEEK_API_KEY=sk-..."
            )
        return DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: '{settings.llm_provider}'. "
        "Choose: anthropic, openai, deepseek"
    )
