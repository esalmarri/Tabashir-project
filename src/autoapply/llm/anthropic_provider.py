"""Anthropic (Claude) provider for browser-use.

browser-use's agent service directly accesses `llm.provider` and `llm.model`.
langchain_anthropic.ChatAnthropic already has `.model` but lacks `.provider`,
so we add it after construction. The object also has `.ainvoke()` which is
what the agent actually calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AnthropicProvider:
    api_key: str
    model: str = "claude-sonnet-4-5"

    @property
    def name(self) -> str:
        return "anthropic"

    def chat_model(self) -> Any:
        from langchain_anthropic import ChatAnthropic  # type: ignore

        chat = ChatAnthropic(
            model=self.model,
            api_key=self.api_key,
            temperature=0.0,
            max_tokens=4096,
        )
        # browser-use's agent service reads llm.provider at startup and in logs.
        # langchain_anthropic has .model but not .provider — add it.
        object.__setattr__(chat, "provider", "anthropic")
        return chat
