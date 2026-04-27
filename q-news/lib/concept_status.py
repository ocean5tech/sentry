"""概念启动判定 (核心 alpha):
- 读 q-seed/logs/ 最新 jsonl 拿 q-seed top N 命中 codes
- 算概念内 q-seed 命中股 / 概念股总数 = active_pct
- active_pct ≥ threshold (默认 0.5) → "已启动 yes"
- 也算概念内股票 60 日平均涨幅作辅助判断 (用本地 TDX 日线)
"""

import json
from pathlib import Path
from typing import Optional


def load_qseed_active_codes(q_seed_logs_dir: str | Path, top_n: int = 30) -> Optional[set]:
    """读 q-seed/logs/ 最新且最全的 jsonl, 返回 top N 的 code set.
    返回 None 表示 q-seed logs 不存在 (调用方应该 verdict=null).
    """
    p = Path(q_seed_logs_dir)
    if not p.exists():
        return None
    files = list(p.glob("q-seed_top*_*.jsonl"))
    if not files:
        return None

    # 选行数最多 + 最近的 (复用 q-fin q_seed_crosscheck 的策略)
    def _info(f):
        try:
            n = sum(1 for _ in f.open(encoding="utf-8") if _.strip())
        except Exception:
            n = 0
        return (n, f.stat().st_mtime)

    scored = sorted([(f, _info(f)) for f in files], key=lambda x: x[1], reverse=True)
    latest = scored[0][0]

    codes = set()
    try:
        for line in latest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("rank", 0) > top_n:
                continue
            codes.add(str(r["code"]))
    except Exception:
        return None
    return codes


def evaluate(concept: str, sector_map: dict, qseed_active: Optional[set], threshold: float = 0.5) -> dict:
    """评估概念启动状态.

    返回:
      {
        concept: str,
        已启动: bool | None,
        stocks_in_concept: int,
        active_in_qseed: int,
        active_pct: float,
        verdict: str (含 emoji),
        active_codes: list (选中的命中 codes 用于 audit)
      }
    """
    sector_def = sector_map.get(concept) or {}
    stocks = sector_def.get("stocks") or []
    code_set = {str(s["code"]) for s in stocks if isinstance(s, dict) and s.get("code")}
    total = len(code_set)

    if qseed_active is None:
        return {
            "concept": concept,
            "已启动": None,
            "stocks_in_concept": total,
            "active_in_qseed": None,
            "active_pct": None,
            "verdict": "⚪ 无 q-seed 数据 (跑过 q-seed 后再判)",
            "active_codes": [],
        }

    if total == 0:
        return {
            "concept": concept,
            "已启动": None,
            "stocks_in_concept": 0,
            "active_in_qseed": 0,
            "active_pct": None,
            "verdict": "⚪ 概念暂未填股 (sector_map 待填)",
            "active_codes": [],
        }

    active = code_set & qseed_active
    active_pct = len(active) / total
    started = active_pct >= threshold

    if started:
        verdict = f"🔴 已启动 ({len(active)}/{total} = {active_pct:.0%} 在 q-seed)"
    elif active_pct >= 0.2:
        verdict = f"🟡 部分启动 ({len(active)}/{total} = {active_pct:.0%} 在 q-seed)"
    else:
        verdict = f"🟢 未启动 ({len(active)}/{total} = {active_pct:.0%} 在 q-seed) — 利好可能未 price in"

    return {
        "concept": concept,
        "已启动": started,
        "stocks_in_concept": total,
        "active_in_qseed": len(active),
        "active_pct": round(active_pct, 3),
        "verdict": verdict,
        "active_codes": sorted(active),
    }
