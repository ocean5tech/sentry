"""Web search provider 抽象."""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    def query(self, q: str, max_results: int = 5) -> list[SearchResult]:
        ...

    def cost_per_call(self) -> float:
        ...
