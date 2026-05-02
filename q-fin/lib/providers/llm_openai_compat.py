"""OpenAI 兼容 provider. 覆盖 OpenAI / DeepSeek / Moonshot / 通义千问 / 豆包.
区分靠 base_url + api_key_env + default_model. 用 openai SDK.

DeepSeek 推理模型 (deepseek-reasoner / deepseek-v4-*) 使用 reasoning_content 字段,
需要更大的 max_tokens (≥3000) 并从 reasoning_content 回退.
"""

import os

from .llm_base import LLMResponse

# 推理模型标识 —— 这些模型有 reasoning_content 字段，需要大 max_tokens
_REASONING_MODEL_PREFIXES = ("deepseek-reasoner", "deepseek-v4", "o1", "o3")


def _is_reasoning_model(model_name: str) -> bool:
    return any(model_name.startswith(p) for p in _REASONING_MODEL_PREFIXES)


class OpenAICompatLLM:
    name = "openai_compat"

    def __init__(self, cfg: dict, provider_name: str = "openai_compat"):
        self.name = provider_name
        self.cfg = cfg
        api_key = os.environ.get(cfg.get("api_key_env", "OPENAI_API_KEY"))
        if not api_key:
            raise RuntimeError(f"env {cfg.get('api_key_env')} missing")
        import openai
        self._client = openai.OpenAI(api_key=api_key, base_url=cfg.get("base_url"))
        self._default_model = cfg["default_model"]
        self._verdict_model = cfg.get("verdict_model", self._default_model)
        self._pricing = cfg.get("pricing", {})

    def _model_for(self, kind: str) -> str:
        return self._verdict_model if kind == "verdict" else self._default_model

    def estimate_cost(self, input_tokens: int, output_tokens: int, model_kind: str = "default") -> float:
        pkey = "verdict" if model_kind == "verdict" else "default"
        p = self._pricing.get(pkey) or {"input": 0.5, "output": 2.0}
        return (input_tokens / 1_000_000) * p["input"] + (output_tokens / 1_000_000) * p["output"]

    def chat(self, prompt: str, model_kind: str = "default", max_tokens: int = 1500,
             tools: list | None = None) -> LLMResponse:
        model = self._model_for(model_kind)

        # 推理模型需要更大的 max_tokens（reasoning chain + final output）
        if _is_reasoning_model(model):
            max_tokens = max(max_tokens, 4000)

        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )

        msg = resp.choices[0].message
        # 推理模型: content 可能为空，回退到 reasoning_content 的最后部分
        text = msg.content or ""
        if not text and hasattr(msg, "reasoning_content") and msg.reasoning_content:
            # reasoning_content 是思维链，取最后 800 字作为"结论"注入
            rc = msg.reasoning_content
            text = rc[-800:] if len(rc) > 800 else rc

        in_tok  = resp.usage.prompt_tokens
        out_tok = resp.usage.completion_tokens
        cost = self.estimate_cost(in_tok, out_tok, model_kind)
        return LLMResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            model=model, provider=self.name, cost_usd=cost,
            raw={"id": resp.id, "finish_reason": resp.choices[0].finish_reason},
        )
