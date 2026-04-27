"""Dummy LLM provider. 不联网, 返回固定 mock 响应供算法测试.

两个 profile:
- dummy: pricing=0, 永不触发 budget_guard
- dummy_priced: 给 mock pricing $1/MTok, 让 budget_guard 在离线测试中可触发降级
"""

import json
from .llm_base import LLMResponse


# 长龄液压 605389 entity_research 硬编码响应链 (F44 真 LLM 之外的算法测试用)
_LONGLING_CHAIN = {
    "无锡核芯听涛科技合伙企业(普通合伙)": {
        "identity": "私募合伙企业 (持股载体)",
        "business": "用于产业资本入主长龄液压",
        "key_persons": ["核芯互联科技", "胡康桥"],
        "links_to_dig_deeper": ["核芯互联科技", "胡康桥"],
    },
    "核芯互联科技": {
        "identity": "模拟芯片设计公司",
        "business": "国产替代模拟/混合信号芯片, C 轮估值 30-50 亿",
        "key_persons": ["胡康桥"],
        "links_to_dig_deeper": [],
    },
    "胡康桥": {
        "identity": "自然人, 芯片产业资本家",
        "business": "前 AMD/TI 工程师, 中科院半导体所博士, 创办核芯互联",
        "key_persons": [],
        "links_to_dig_deeper": [],
    },
}


class DummyLLM:
    """固定响应, pricing=0."""
    name = "dummy"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._call_count = 0
        # pricing: 默认 0; dummy_priced 子类覆盖
        self._pricing = cfg.get("pricing", {"default": {"input": 0, "output": 0}})

    def estimate_cost(self, input_tokens: int, output_tokens: int, model_kind: str = "default") -> float:
        p = self._pricing.get("default", {"input": 0, "output": 0})
        return (input_tokens / 1_000_000) * p["input"] + (output_tokens / 1_000_000) * p["output"]

    def chat(self, prompt: str, model_kind: str = "default", max_tokens: int = 1500, tools: list | None = None) -> LLMResponse:
        # dummy 忽略 tools, 仍走 mock 路径
        self._call_count += 1

        # entity_research prompt: 抽 entity 名 → 查硬编码字典
        text = self._mock_response(prompt, model_kind)

        # mock token 数: prompt 长度 / 4 ≈ tokens
        in_tok = max(1, len(prompt) // 4)
        out_tok = max(1, len(text) // 4)
        cost = self.estimate_cost(in_tok, out_tok, model_kind)

        return LLMResponse(
            text=text, input_tokens=in_tok, output_tokens=out_tok,
            model=f"{self.name}-mock", provider=self.name, cost_usd=cost,
            raw={"call_count": self._call_count},
        )

    def _mock_response(self, prompt: str, model_kind: str) -> str:
        # 优先匹配 verdict (更具体的关键词在前, 避免被 entity 分支抢)
        if "题材股分析师" in prompt or ("rating:" in prompt and "stars:" in prompt):
            return json.dumps({
                "rating": 5,
                "stars": "⭐⭐⭐⭐⭐",
                "one_liner": "[dummy mock] 教科书级芯片产业资本借壳",
                "theme_hardness": "硬",
                "entry_suggestion": "[dummy] 等 VWAP20 回踩",
                "key_risks": ["[dummy] mock risk 1"],
                "themes": ["AI", "算力"],
            }, ensure_ascii=False)

        # entity_research prompt
        if "调查以下实体" in prompt:
            for entity, info in _LONGLING_CHAIN.items():
                if entity in prompt:
                    return json.dumps(info, ensure_ascii=False)
            # fallback: 未知实体, 返回空 (含 unknown 标记便于断言)
            return json.dumps({
                "identity": "unknown_entity_dummy", "business": "",
                "key_persons": [], "links_to_dig_deeper": [],
            }, ensure_ascii=False)

        # 兜底
        return "[dummy mock response]"


class DummyPricedLLM(DummyLLM):
    """带 mock pricing 的 dummy, 让 budget_guard 能在离线测试触发降级."""
    name = "dummy_priced"

    def __init__(self, cfg: dict):
        # 默认 $1/MTok in, $5/MTok out — 与 Haiku 同价位
        if "pricing" not in cfg:
            cfg = {**cfg, "pricing": {"default": {"input": 1.0, "output": 5.0}}}
        super().__init__(cfg)
