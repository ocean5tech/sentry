"""
Stock name cache. Fetches from baostock and saves to data/stock_names.csv.
Reload with refresh=True or by deleting the cache file.
"""

import csv
import logging
from pathlib import Path

# baostock 改成 lazy import (q-seed 只用本地缓存 csv, 不联网刷新)

logger = logging.getLogger(__name__)

_CACHE_PATH = Path(__file__).parent.parent / "data" / "stock_names.csv"
_names: dict[str, str] | None = None


def set_cache_path(path: str | Path):
    """Override cache path at startup (for q-seed L2 独立环境)."""
    global _CACHE_PATH, _names
    _CACHE_PATH = Path(path)
    _names = None  # 重置缓存


def _bs_code_to_symbol(code: str) -> str:
    """'sh.600519' → '600519'"""
    return code.split(".", 1)[-1]


def _fetch_from_baostock() -> dict[str, str]:
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"baostock login failed: {lg.error_msg}")
    try:
        rs = bs.query_stock_basic()
        result = {}
        while rs.next():
            row = rs.get_row_data()
            code, name = row[0], row[1]
            result[_bs_code_to_symbol(code)] = name
        return result
    finally:
        bs.logout()


def _load_cache() -> dict[str, str]:
    result = {}
    with open(_CACHE_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            result[row["code"]] = row["name"]
    return result


def _save_cache(names: dict[str, str]):
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["code", "name"])
        for code, name in sorted(names.items()):
            w.writerow([code, name])


def get_names(refresh: bool = False) -> dict[str, str]:
    global _names
    if _names is not None and not refresh:
        return _names
    if _CACHE_PATH.exists() and not refresh:
        _names = _load_cache()
        return _names
    logger.info("Fetching stock names from baostock...")
    _names = _fetch_from_baostock()
    _save_cache(_names)
    return _names


def get_name(symbol: str, fallback: str = "") -> str:
    return get_names().get(symbol, fallback)
