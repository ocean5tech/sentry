"""预算保护 + cost_log.

三层硬上限:
  - per-stock: entity_research.budget_per_stock_usd
  - per-run:   budget.daily_usd_cap (覆盖整个 run, 不真按"日"算; 用户可自加 daily 维度)
  - 单次调用: 调前 estimate_cost <= 剩余预算才放行

超限 (run 级别) 按 budget.on_exceeded:
  - fallback_free: 剩余 stocks 自动降 free 模式 (不调 LLM)
  - error: 直接 exit 1
  - warn_continue: stderr warn 继续 (不推荐, 留给用户自负)

cost_log.jsonl 每行: {ts, code, provider, model, input_tok, output_tok, cost_usd, kind}
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional


class BudgetExceeded(Exception):
    pass


class BudgetGuard:
    def __init__(
        self,
        run_cap_usd: float,
        per_stock_cap_usd: float,
        cost_log_path: Path,
        on_exceeded: str = "fallback_free",
    ):
        self.run_cap = float(run_cap_usd)
        self.per_stock_cap = float(per_stock_cap_usd)
        self.cost_log_path = Path(cost_log_path)
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.on_exceeded = on_exceeded

        self._run_total = 0.0
        self._per_stock_total: dict[str, float] = {}
        self._fallback_triggered = False

    @property
    def run_total(self) -> float:
        return self._run_total

    @property
    def remaining(self) -> float:
        return max(0.0, self.run_cap - self._run_total)

    @property
    def fallback_triggered(self) -> bool:
        return self._fallback_triggered

    def per_stock_used(self, code: str) -> float:
        return self._per_stock_total.get(code, 0.0)

    def per_stock_remaining(self, code: str) -> float:
        return max(0.0, self.per_stock_cap - self.per_stock_used(code))

    def can_spend(self, est_cost_usd: float, code: Optional[str] = None) -> tuple[bool, str]:
        """检查能否花 est_cost_usd. 返回 (allowed, reason)."""
        if self._fallback_triggered:
            return False, "fallback_already_triggered"
        if self._run_total + est_cost_usd > self.run_cap:
            return False, f"run_cap_exceeded ({self._run_total:.4f}+{est_cost_usd:.4f}>{self.run_cap})"
        if code is not None:
            used = self._per_stock_total.get(code, 0.0)
            if used + est_cost_usd > self.per_stock_cap:
                return False, f"per_stock_cap_exceeded ({used:.4f}+{est_cost_usd:.4f}>{self.per_stock_cap})"
        return True, "ok"

    def record(
        self,
        cost_usd: float,
        provider: str,
        model: str,
        input_tok: int,
        output_tok: int,
        code: Optional[str] = None,
        kind: str = "entity_research",
    ):
        """实际产生消费后记账."""
        self._run_total += cost_usd
        if code is not None:
            self._per_stock_total[code] = self._per_stock_total.get(code, 0.0) + cost_usd

        # cost_log
        with open(self.cost_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "code": code,
                "provider": provider,
                "model": model,
                "input_tok": input_tok,
                "output_tok": output_tok,
                "cost_usd": cost_usd,
                "kind": kind,
            }) + "\n")

        # 超限触发降级 (record 之后, run_total 已含本次消费)
        if self._run_total >= self.run_cap and not self._fallback_triggered:
            self._handle_exceeded()

    def _handle_exceeded(self):
        if self.on_exceeded == "fallback_free":
            self._fallback_triggered = True
            print(
                f"[budget_guard] run_cap ${self.run_cap:.4f} reached "
                f"(spent ${self._run_total:.4f}), fallback_free for remaining stocks",
                file=sys.stderr,
            )
        elif self.on_exceeded == "error":
            raise BudgetExceeded(f"run_cap ${self.run_cap} exceeded (spent ${self._run_total})")
        elif self.on_exceeded == "warn_continue":
            print(f"[budget_guard] WARN: run_cap exceeded but continuing (spent ${self._run_total:.4f})",
                  file=sys.stderr)
            # 不设 fallback_triggered, 继续花钱
        else:
            raise ValueError(f"unknown on_exceeded: {self.on_exceeded}")

    def summary(self) -> dict:
        return {
            "run_total_usd": round(self._run_total, 6),
            "run_cap_usd": self.run_cap,
            "remaining_usd": round(self.remaining, 6),
            "fallback_triggered": self._fallback_triggered,
            "per_stock_totals": {k: round(v, 6) for k, v in self._per_stock_total.items()},
        }
