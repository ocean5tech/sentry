"""交易日历: 从 TDX 上证指数 .day 文件派生 (即所有有数据的日期).
不依赖 akshare (内网).
"""

import struct
from datetime import date
from pathlib import Path

_REC_FMT = "<IIIIIfII"
_REC_SIZE = struct.calcsize(_REC_FMT)


def get_trading_days(sse_index_path: str) -> list[date]:
    """读上证指数 .day 文件, 返回所有交易日 (升序)."""
    p = Path(sse_index_path)
    if not p.exists():
        raise FileNotFoundError(f"上证指数文件不存在: {p}")
    days = []
    data = p.read_bytes()
    for i in range(0, len(data), _REC_SIZE):
        if i + _REC_SIZE > len(data):
            break
        date_int = struct.unpack(_REC_FMT, data[i:i+_REC_SIZE])[0]
        y, md = divmod(date_int, 10000)
        m, d = divmod(md, 100)
        days.append(date(y, m, d))
    days.sort()
    return days


def trading_days_between(all_days: list[date], start: date, end: date) -> list[date]:
    """[start, end] 之间的交易日列表."""
    return [d for d in all_days if start <= d <= end]


def add_trading_days(all_days: list[date], from_day: date, n: int) -> date | None:
    """from_day + n 个交易日. n 可为负."""
    try:
        idx = all_days.index(from_day)
    except ValueError:
        # from_day 非交易日, 取最近的下一个交易日
        for i, d in enumerate(all_days):
            if d > from_day:
                idx = i
                break
        else:
            return None
    target_idx = idx + n
    if 0 <= target_idx < len(all_days):
        return all_days[target_idx]
    return None
