import struct
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

TDX_DIR = Path(__file__).parent.parent / "data" / "tdx"
TDX_URL = "https://data.tdx.com.cn/vipdoc/hsjday.zip"

# .day 每条记录 32 字节：date open high low close amount(f32) volume reserved
_RECORD_SIZE = 32
_FMT = "<IIIIIfII"


def _symbol_to_market(symbol: str) -> str:
    if symbol.startswith("6"):
        return "sh"
    elif symbol.startswith("8") or symbol.startswith("4"):
        return "bj"
    else:
        return "sz"


def day_path(symbol: str) -> Path:
    market = _symbol_to_market(symbol)
    return TDX_DIR / market / "lday" / f"{market}{symbol}.day"


def read_day(symbol: str) -> pd.DataFrame:
    """Read a TDX .day binary file directly into a DataFrame."""
    path = day_path(symbol)
    if not path.exists():
        return pd.DataFrame()

    with open(path, "rb") as f:
        data = f.read()

    n = len(data) // _RECORD_SIZE
    if n == 0:
        return pd.DataFrame()

    rows = []
    for i in range(n):
        chunk = data[i * _RECORD_SIZE: (i + 1) * _RECORD_SIZE]
        d, o, h, l, c, amt, vol, _ = struct.unpack(_FMT, chunk)
        if c == 0:
            continue  # 停牌占位行
        rows.append({
            "date":     pd.Timestamp(str(d)),
            "open":     round(o / 100, 2),
            "high":     round(h / 100, 2),
            "low":      round(l / 100, 2),
            "close":    round(c / 100, 2),
            "volume":   vol,
            "amount":   round(amt, 2),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df.sort_values("date").reset_index(drop=True)


def list_tdx_symbols() -> list[str]:
    """Return all stock symbols available in data/tdx/."""
    if not TDX_DIR.exists():
        return []
    symbols = []
    for market in ["sh", "sz", "bj"]:
        lday = TDX_DIR / market / "lday"
        if not lday.exists():
            continue
        for f in lday.glob("*.day"):
            symbol = f.stem[2:]  # sh600000 → 600000
            # 排除指数（000xxx 开头的 sh 文件）
            if market == "sh" and symbol.startswith("000"):
                continue
            symbols.append(symbol)
    return sorted(symbols)
