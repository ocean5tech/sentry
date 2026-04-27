"""Dummy search. 不联网, 返回固定结果, 算法测试用."""

from .search_base import SearchResult


class DummySearch:
    name = "dummy"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.call_count = 0

    def cost_per_call(self) -> float:
        return 0.0

    def query(self, q: str, max_results: int = 5) -> list[SearchResult]:
        self.call_count += 1
        return [
            SearchResult(
                title=f"[dummy] result for {q[:30]}",
                url="https://dummy.example.com/1",
                snippet=f"[dummy mock snippet for query: {q[:50]}]",
            )
        ][:max_results]
