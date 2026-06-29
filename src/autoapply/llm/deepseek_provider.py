"""DeepSeek provider for browser-use.

DeepSeek's API is OpenAI-compatible, so we reuse browser-use's own ChatOpenAI
with a custom base_url. This gives the object the `.provider` and `.model`
attributes browser-use's agent service requires.

Recommended models:
  deepseek-chat      (V3 — fast, very cheap)
  deepseek-reasoner  (R1 — slower, stronger reasoning)

DeepSeek does not support vision/image input, so the agent uses text-only
DOM extraction (use_vision=False in runner.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


@dataclass
class DeepSeekProvider:
    api_key: str
    model: str = "deepseek-chat"
    use_vision: bool = False  # DeepSeek does not accept image input

    @property
    def name(self) -> str:
        return "deepseek"

    def chat_model(self) -> Any:
        from browser_use.llm.models import ChatOpenAI  # type: ignore

        return ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=DEEPSEEK_BASE_URL,
        )
