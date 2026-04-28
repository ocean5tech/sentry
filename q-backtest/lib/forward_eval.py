"""走盘前向评估: 给定 (code, as_of_date), 计算 hold_days 后的表现.

入场: T+1 close (用户决定)
退出: T+1+hold_days close
胜利: ret >= win_pct
持仓体验好: win AND min_low_during >= entry_price (max_drawdown >= 0)

一字板检测: T+1 open == high == low == close → 标 skipped_reason="limit_up_no_fill"
"""

import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional


_REC_FMT = "<IIIIIfII"
_REC_SIZE = struct.calcsize(_REC_FMT)


@dataclass
class ForwardResult:
    skipped_reason: Optional[str]   # None / "limit_up_no_fill" / "no_data" / "no_t1" / "no_exit"
    entry_date: Optional[str]
    entry_price: Optional[float]
    exit_date: Optional[str]
    exit_price: Optional[float]
    ret: Optional[float]            # 收益率 (exit - entry) / entry
    min_low_during: Optional[float] # 持仓最低 low
    max_close_during: Optional[float]
    max_drawdown: Optional[float]   # min_low / entry - 1 (持仓最大浮亏)
    win: Optional[bool]             # ret >= win_pct
    good_experience: Optional[bool] # win AND max_drawdown >= 0


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
            "open": o / 100.0,
            "high": h / 100.0,
            "low": l / 100.0,
            "close": c / 100.0,
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def _stock_path(tdx_dir: str, code: str) -> Path:
    """code → .day 文件路径."""
    base = Path(tdx_dir)
    if code.startswith(("60", "68", "11", "5", "9")):     # 沪市 + 科创 + 沪债
        return base / "sh" / "lday" / f"sh{code}.day"
    if code.startswith(("00", "30", "12")):                # 深市 + 创业板
        return base / "sz" / "lday" / f"sz{code}.day"
    if code.startswith(("8", "4", "92")):                  # 北交所
        return base / "bj" / "lday" / f"bj{code}.day"
    return base / "sh" / "lday" / f"sh{code}.day"


def evaluate(code: str, as_of_date: date, hold_days: int, win_pct: float,
             tdx_dir: str) -> ForwardResult:
    """对 (code, as_of_date) 算 hold_days 后表现."""
    path = _stock_path(tdx_dir, code)
    rows = _read_day(path)
    if not rows:
        return ForwardResult(
            skipped_reason="no_data",
            entry_date=None, entry_price=None, exit_date=None, exit_price=None,
            ret=None, min_low_during=None, max_close_during=None, max_drawdown=None,
            win=None, good_experience=None,
        )

    # 找 as_of_date 在 rows 里的索引
    as_of_idx = None
    for i, r in enumerate(rows):
        if r["date"] >= as_of_date:
            as_of_idx = i
            break
    if as_of_idx is None:
        return ForwardResult("no_data", None, None, None, None, None, None, None, None, None, None)

    # T+1 = 下一个交易日
    t1_idx = as_of_idx + 1 if rows[as_of_idx]["date"] == as_of_date else as_of_idx
    if t1_idx >= len(rows):
        return ForwardResult("no_t1", None, None, None, None, None, None, None, None, None, None)

    t1 = rows[t1_idx]
    # 一字板检测: open == high == low == close (一字涨停或一字跌停, 无法成交)
    if t1["open"] == t1["high"] == t1["low"] == t1["close"]:
        return ForwardResult(
            "limit_up_no_fill",
            t1["date"].isoformat(), t1["close"], None, None, None, None, None, None, None, None,
        )

    entry_price = t1["close"]
    entry_date = t1["date"].isoformat()

    # T+1+hold_days = exit
    exit_idx = t1_idx + hold_days
    if exit_idx >= len(rows):
        return ForwardResult(
            "no_exit",
            entry_date, entry_price, None, None, None, None, None, None, None, None,
        )
    exit_row = rows[exit_idx]
    exit_price = exit_row["close"]
    exit_date = exit_row["date"].isoformat()

    # 持仓期最低 low / 最高 close
    holding = rows[t1_idx:exit_idx + 1]
    min_low_during = min(r["low"] for r in holding)
    max_close_during = max(r["close"] for r in holding)

    ret = (exit_price - entry_price) / entry_price
    max_drawdown = (min_low_during - entry_price) / entry_price
    win = ret >= win_pct
    good_experience = win and (min_low_during >= entry_price)   # 持仓中没跌破入场点

    return ForwardResult(
        skipped_reason=None,
        entry_date=entry_date,
        entry_price=round(entry_price, 2),
        exit_date=exit_date,
        exit_price=round(exit_price, 2),
        ret=round(ret, 4),
        min_low_during=round(min_low_during, 2),
        max_close_during=round(max_close_during, 2),
        max_drawdown=round(max_drawdown, 4),
        win=win,
        good_experience=good_experience,
    )
