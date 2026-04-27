"""DuckDuckGo search. 免费, 默认. 限流时 retry+sleep."""

import time
from typing import Any

from .search_base import SearchResult


class DuckDuckGoSearch:
    name = "duckduckgo"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.retries = cfg.get("retries", 2)
        self.retry_sleep = cfg.get("retry_sleep_seconds", 3)

    def cost_per_call(self) -> float:
        return 0.0

    def query(self, q: str, max_results: int = 5) -> list[SearchResult]:
        from duckduckgo_search import DDGS
        attempt = 0
        last_err: Exception | None = None
        while attempt <= self.retries:
            try:
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
            except Exception as e:
                last_err = e
                attempt += 1
                if attempt > self.retries:
                    raise
                time.sleep(self.retry_sleep)
        return []  # unreachable
