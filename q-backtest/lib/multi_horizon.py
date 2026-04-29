"""多 hold_days horizon 评估.
对单个 (code, as_of_date) 一次读 K 线, 同时算 5/10/20/40/80 天后的表现.
"""

import struct
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


_REC_FMT = "<IIIIIfII"
_REC_SIZE = struct.calcsize(_REC_FMT)


def _read_day(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    data = path.read_bytes()
    for i in range(0, len(data), _REC_SIZE):
        if i + _REC_SIZE > len(data):
            break
        dt, o, h, l, c, _amt, _v, _r = struct.unpack(_REC_FMT, data[i:i+_REC_SIZE])
        y, md = divmod(dt, 10000)
        m, d = divmod(md, 100)
        rows.append({
            "date": date(y, m, d),
            "open": o / 100.0, "high": h / 100.0,
            "low": l / 100.0, "close": c / 100.0,
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def _stock_path(tdx_dir: str, code: str) -> Path:
    base = Path(tdx_dir)
    if code.startswith(("60", "68", "11", "5", "9")):
        return base / "sh" / "lday" / f"sh{code}.day"
    if code.startswith(("00", "30", "12")):
        return base / "sz" / "lday" / f"sz{code}.day"
    if code.startswith(("8", "4", "92")):
        return base / "bj" / "lday" / f"bj{code}.day"
    return base / "sh" / "lday" / f"sh{code}.day"


# Module cache: code → rows (避免重复 disk read)
_KLINE_CACHE: dict[str, list[dict]] = {}


def get_kline(code: str, tdx_dir: str) -> list[dict]:
    if code not in _KLINE_CACHE:
        _KLINE_CACHE[code] = _read_day(_stock_path(tdx_dir, code))
    return _KLINE_CACHE[code]


def evaluate_multi(code: str, as_of_date: date, hold_days_list: list[int],
                   win_pct: float, tdx_dir: str,
                   max_drawdown_allowed: float = 0.07) -> dict:
    """对一个 (code, as_of_date) 一次性算多个 hold_days 的结果.

    返回 {
      'skipped_reason': str | None,
      'entry_date': str | None,
      'entry_price': float | None,
      'horizons': {hd: {exit_date, exit_price, ret, max_drawdown, min_low_during,
                        max_close_during, win, good_experience}, ...}
    }
    """
    rows = get_kline(code, tdx_dir)
    base_result = {
        "skipped_reason": None,
        "entry_date": None,
        "entry_price": None,
        "horizons": {},
    }

    if not rows:
        base_result["skipped_reason"] = "no_data"
        return base_result

    # 找 as_of_date 索引
    as_of_idx = None
    for i, r in enumerate(rows):
        if r["date"] >= as_of_date:
            as_of_idx = i
            break
    if as_of_idx is None:
        base_result["skipped_reason"] = "no_data"
        return base_result

    t1_idx = as_of_idx + 1 if rows[as_of_idx]["date"] == as_of_date else as_of_idx
    if t1_idx >= len(rows):
        base_result["skipped_reason"] = "no_t1"
        return base_result

    t1 = rows[t1_idx]
    if t1["open"] == t1["high"] == t1["low"] == t1["close"]:
        base_result["skipped_reason"] = "limit_up_no_fill"
        base_result["entry_date"] = t1["date"].isoformat()
        base_result["entry_price"] = round(t1["close"], 2)
        return base_result

    entry_price = t1["close"]
    base_result["entry_date"] = t1["date"].isoformat()
    base_result["entry_price"] = round(entry_price, 2)

    # 对每个 hold_days 独立计算
    for hd in hold_days_list:
        exit_idx = t1_idx + hd
        if exit_idx >= len(rows):
            base_result["horizons"][hd] = {"reason": "no_exit"}
            continue
        exit_row = rows[exit_idx]
        exit_price = exit_row["close"]
        holding = rows[t1_idx:exit_idx + 1]
        min_low = min(r["low"] for r in holding)
        max_close = max(r["close"] for r in holding)
        ret = (exit_price - entry_price) / entry_price
        max_dd = (min_low - entry_price) / entry_price
        win = ret >= win_pct
        # 持仓体验好: win AND 持仓中 low 不低于 entry × (1 - max_drawdown_allowed)
        # max_drawdown_allowed=0.07 → 允许 7% 浮亏 (用户决定)
        good_exp = win and (min_low >= entry_price * (1 - max_drawdown_allowed))

        base_result["horizons"][hd] = {
            "exit_date": exit_row["date"].isoformat(),
            "exit_price": round(exit_price, 2),
            "ret": round(ret, 4),
            "min_low_during": round(min_low, 2),
            "max_close_during": round(max_close, 2),
            "max_drawdown": round(max_dd, 4),
            "win": win,
            "good_experience": good_exp,
        }

    return base_result
