"""Layer 2 q-seed 交叉验证. 读 q-seed/logs/ 最新 jsonl, 看候选是否在 q-seed top 列表."""

import json
from pathlib import Path
from typing import Optional


def load_latest_qseed_codes(q_seed_logs_dir: str | Path) -> Optional[dict[str, dict]]:
    """读 q-seed logs 最新且**最丰富** jsonl, 返回 {code: row}.

    策略: glob 全部 q-seed_*.jsonl, 按 (mtime desc, line_count desc) 选; 即同时段优先选行数多的.
    目的: 避免选到 top1/top5 这种小文件而错过更全的扫描结果.
    """
    p = Path(q_seed_logs_dir)
    if not p.exists():
        return None
    files = list(p.glob("q-seed_top*_*.jsonl"))
    if not files:
        return None

    # 计算 (mtime, line_count) 用于排序
    def _info(f):
        try:
            n = sum(1 for _ in f.open(encoding="utf-8") if _.strip())
        except Exception:
            n = 0
        return (f.stat().st_mtime, n)

    scored = [(f, _info(f)) for f in files]
    # 优先选行数多 (覆盖广) 的, 同行数再选 mtime 最新的
    # 这样避免选到 top5/top1 这种调试小文件
    scored.sort(key=lambda x: (x[1][1], x[1][0]), reverse=True)
    latest = scored[0][0]

    out = {}
    try:
        for line in latest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            out[r["code"]] = r
    except Exception:
        return None
    return out


def crosscheck(code: str, qseed_idx: Optional[dict]) -> dict:
    """对单 code 返回 q_seed_crosscheck 字段."""
    if qseed_idx is None:
        return {"is_in_q_seed": None, "matched_templates": [], "best_dist": None, "best_rank": None, "_err": "qseed_logs_not_found"}
    if code not in qseed_idx:
        return {"is_in_q_seed": False, "matched_templates": [], "best_dist": None, "best_rank": None}
    r = qseed_idx[code]
    return {
        "is_in_q_seed": True,
        "matched_templates": r.get("templates_matched", []),
        "best_template": r.get("best_template"),
        "best_dist": r.get("best_dist"),
        "best_rank": r.get("rank"),
        "crosscheck_bonus": 0.5,
    }
