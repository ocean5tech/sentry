"""OpenAI 兼容 provider. 一个类覆盖 OpenAI / DeepSeek / Moonshot / 通义千问 / 豆包.
区分靠 base_url + api_key_env + default_model. 用 openai SDK.
"""

import os

from .llm_base import LLMResponse


class OpenAICompatLLM:
    name = "openai_compat"

    def __init__(self, cfg: dict):
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

    def chat(self, prompt: str, model_kind: str = "default", max_tokens: int = 1500, tools: list | None = None) -> LLMResponse:
        # tools 参数当前未在 openai_compat 支持 (各 provider tool 协议不一)
        # 调用方传 tools 时静默忽略, 让逻辑降级为纯文本 chat
        model = self._model_for(model_kind)
        resp = self._client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        in_tok = resp.usage.prompt_tokens
        out_tok = resp.usage.completion_tokens
        cost = self.estimate_cost(in_tok, out_tok, model_kind)
        return LLMResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            model=model, provider=self.name, cost_usd=cost,
            raw={"id": resp.id, "finish_reason": resp.choices[0].finish_reason},
        )
