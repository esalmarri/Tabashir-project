"""OpenAI provider for browser-use.

Uses browser-use's own ChatOpenAI adapter which has the `.provider` and
`.model` attributes the agent service requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class OpenAIProvider:
    api_key: str
    model: str = "gpt-4o"

    @property
    def name(self) -> str:
        return "openai"

    def chat_model(self) -> Any:
        from browser_use.llm.models import ChatOpenAI  # type: ignore

        return ChatOpenAI(model=self.model, api_key=self.api_key)
