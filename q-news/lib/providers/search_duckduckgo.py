"""DuckDuckGo search, 失败自动降级 Tavily.

优先级: DDG (免费无限) → Tavily (免费 1000次/月)
DDG 被代理拦截或限流时自动切 Tavily, 无需手动干预.
"""

import os
import time

from .search_base import SearchResult


class DuckDuckGoSearch:
    name = "duckduckgo"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.retries = cfg.get("retries", 2)
        self.retry_sleep = cfg.get("retry_sleep_seconds", 3)
        # Tavily fallback: cfg 里指定 env var 名, 或直接写 key, 或默认 TAVILY_API_KEY
        key_env = cfg.get("tavily_fallback_key_env", "TAVILY_API_KEY")
        self._tavily_key = (
            cfg.get("tavily_fallback_key")
            or os.environ.get(key_env, "")
        )

    def cost_per_call(self) -> float:
        return 0.0

    def _query_ddg(self, q: str, max_results: int) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            rows = list(ddgs.text(q, max_results=max_results))
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", "") or r.get("url", ""),
                snippet=r.get("body", "") or r.get("snippet", ""),
            )
            for r in rows
        ]

    def _query_tavily(self, q: str, max_results: int) -> list[SearchResult]:
        from tavily import TavilyClient
        client = TavilyClient(api_key=self._tavily_key)
        resp = client.search(q, max_results=max_results)
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", "") or r.get("snippet", ""),
            )
            for r in resp.get("results", [])
        ]

    def query(self, q: str, max_results: int = 5) -> list[SearchResult]:
        # 1. 先试 DDG
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                results = self._query_ddg(q, max_results)
                if results:
                    return results
            except Exception as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(self.retry_sleep)

        # 2. DDG 失败 → 降级 Tavily
        if self._tavily_key:
            try:
                return self._query_tavily(q, max_results)
            except Exception:
                pass

        # 3. 两者都失败 → 抛最后一个 DDG 异常
        if last_err:
            raise last_err
        return []
