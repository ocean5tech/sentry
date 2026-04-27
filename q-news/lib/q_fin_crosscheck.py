"""q-fin 反查: 读 q-fin/logs/ 最新 jsonl, 看 code 是否在 q-fin TOP 输出过.
也直接读 q-fin lib (复用 fundamentals tdx_fin) 拿 yoy_ni 等基本面快照.

V0 简化: 仅 logs lookup, fundamentals 反查留给 V1.
"""

import json
from pathlib import Path
from typing import Optional


def load_latest_qfin_codes(q_fin_logs_dir: str | Path) -> Optional[dict[str, dict]]:
    """读 q-fin logs 最新 jsonl, 返回 {code: row}. 找不到返回 None."""
    p = Path(q_fin_logs_dir)
    if not p.exists():
        return None
    files = list(p.glob("q-fin_top*_*.jsonl"))
    if not files:
        return None

    def _info(f):
        try:
            n = sum(1 for _ in f.open(encoding="utf-8") if _.strip())
        except Exception:
            n = 0
        return (n, f.stat().st_mtime)

    scored = sorted([(f, _info(f)) for f in files], key=lambda x: x[1], reverse=True)
    latest = scored[0][0]

    out = {}
    try:
        for line in latest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            out[str(r["code"])] = r
    except Exception:
        return None
    return out


def crosscheck(code: str, qfin_idx: Optional[dict]) -> dict:
    """单 code 反查 q-fin 输出."""
    if qfin_idx is None:
        return {"has_q_fin_record": None, "_reason": "no_qfin_logs"}
    row = qfin_idx.get(str(code))
    if row is None:
        return {"has_q_fin_record": False}
    fund = row.get("fundamentals") or {}
    return {
        "has_q_fin_record": True,
        "qfin_rank": row.get("rank"),
        "qfin_score": row.get("score"),
        "fundamentals_period": fund.get("period"),
        "fundamentals_yoy_ni": fund.get("yoy_net_profit"),
        "fundamentals_yoy_rev": fund.get("yoy_revenue"),
    }
