"""LLM provider 抽象基类.

所有 provider 实现必须满足:
- chat(prompt, model_kind="default") -> LLMResponse
- model_kind ∈ {"default", "verdict"}, factory 决定具体模型名
- 价格/上限不在 provider 里管 (budget_guard.py 单独管)
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str             # 实际调的模型 (例: claude-haiku-4-5-20251001)
    provider: str          # provider 名 (anthropic / openai_compat / dummy / dummy_priced)
    cost_usd: float        # 由 provider 内部按 pricing 算
    raw: dict = field(default_factory=dict)  # 原始 SDK response (debug)


class LLMProvider(Protocol):
    name: str

    def chat(
        self,
        prompt: str,
        model_kind: str = "default",
        max_tokens: int = 1500,
        tools: list | None = None,
    ) -> LLMResponse:
        ...

    def estimate_cost(self, input_tokens: int, output_tokens: int, model_kind: str = "default") -> float:
        """超调用前估算成本 (budget_guard 用)."""
        ...
