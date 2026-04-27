"""Anthropic Claude provider. 用官方 anthropic SDK."""

import os
from typing import Any

from .llm_base import LLMResponse


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        api_key = os.environ.get(cfg.get("api_key_env", "ANTHROPIC_API_KEY"))
        if not api_key:
            raise RuntimeError(f"env {cfg.get('api_key_env')} missing")
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._default_model = cfg["default_model"]
        self._verdict_model = cfg.get("verdict_model", self._default_model)
        # pricing: {haiku: {input, output}, sonnet: {input, output}} per 1M tokens
        self._pricing = cfg.get("pricing", {})

    def _model_for(self, kind: str) -> str:
        return self._verdict_model if kind == "verdict" else self._default_model

    def _pricing_key(self, model: str) -> str:
        if "sonnet" in model.lower():
            return "sonnet"
        if "haiku" in model.lower():
            return "haiku"
        if "opus" in model.lower():
            return "opus"
        return "haiku"  # 兜底

    def estimate_cost(self, input_tokens: int, output_tokens: int, model_kind: str = "default") -> float:
        model = self._model_for(model_kind)
        pkey = self._pricing_key(model)
        p = self._pricing.get(pkey) or self._pricing.get("haiku") or {"input": 1.0, "output": 5.0}
        return (input_tokens / 1_000_000) * p["input"] + (output_tokens / 1_000_000) * p["output"]

    def chat(
        self,
        prompt: str,
        model_kind: str = "default",
        max_tokens: int = 1500,
        tools: list | None = None,
    ) -> LLMResponse:
        model = self._model_for(model_kind)
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if tools:
            kwargs["tools"] = tools

        # web_search_20250305 是 server-side tool, 自动 multi-turn 直到 Claude 给最终答案
        resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if hasattr(b, "text"))
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens

        # 算 web_search 工具消耗 (单独计费): web_search_requests * $0.01
        search_calls = 0
        srv_usage = getattr(resp.usage, "server_tool_use", None)
        if srv_usage and getattr(srv_usage, "web_search_requests", None):
            search_calls = srv_usage.web_search_requests

        cost = self.estimate_cost(in_tok, out_tok, model_kind) + search_calls * 0.01

        return LLMResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            model=model, provider=self.name, cost_usd=cost,
            raw={"id": resp.id, "stop_reason": resp.stop_reason, "search_calls": search_calls},
        )
