"""Anthropic 内置 web_search tool. 仅当 llm.active=anthropic 时可用.
V0 实现简化: 只暴露 query() 接口, 实际触发由 LLM 自主决定 (通过 tools=[{type: web_search}]).
当前 entity_research 流程不直接调用此 provider.query() — 仅在 main.py 启动期校验配置一致性.
"""

from .search_base import SearchResult


class AnthropicToolSearch:
    name = "anthropic_tool"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def cost_per_call(self) -> float:
        return float(self.cfg.get("cost_per_call_usd", 0.01))

    def query(self, q: str, max_results: int = 5) -> list[SearchResult]:
        # V0: 不实现独立调用. 让 entity_research 改路由到 duckduckgo.
        # 真正使用要把 tool 装到 anthropic.messages.create(tools=[...]) 调用上, 当前架构 LLM 调用是通用的不带 tools.
        raise NotImplementedError(
            "anthropic_tool standalone query 不实现; 在 V1 把 web_search tool 集成到 LLMProvider.chat 接口里"
        )
