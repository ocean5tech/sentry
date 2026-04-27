import logging
from pathlib import Path

import pandas as pd

from core.data_loader import load_daily, DAILY_DIR
from core.tdx_loader import list_tdx_symbols, TDX_DIR

logger = logging.getLogger(__name__)

LIMIT_UP_FACTOR = 1.097  # 10% with float tolerance; covers main board


def check_first_limit_up(df: pd.DataFrame) -> bool:
    """True if today is a limit-up AND no limit-up in the prior 29 calendar rows."""
    if len(df) < 2:
        return False

    today = df.iloc[-1]
    prev_close = df.iloc[-2]["close"]

    today_limit_up = (
        today["close"] >= prev_close * LIMIT_UP_FACTOR
        and today["close"] == today["high"]
    )
    if not today_limit_up:
        return False

    # Check prior 29 rows (excluding today) for any limit-up
    window = df.iloc[max(0, len(df) - 30) : len(df) - 1]
    for i in range(1, len(window)):
        row = window.iloc[i]
        prev = window.iloc[i - 1]["close"]
        if row["close"] >= prev * LIMIT_UP_FACTOR and row["close"] == row["high"]:
            return False

    return True  # already verified as plain bool via early returns


def check_limit_up_pullback(df: pd.DataFrame, spot_price: float | None = None) -> bool:
    """True if there was a limit-up in past 20 rows AND price is ≥5% below that window's high."""
    if len(df) < 2:
        return False

    window = df.iloc[max(0, len(df) - 20):]
    window_high = window["high"].max()

    has_limit_up = False
    for i in range(1, len(window)):
        row = window.iloc[i]
        prev = window.iloc[i - 1]["close"]
        if row["close"] >= prev * LIMIT_UP_FACTOR and row["close"] == row["high"]:
            has_limit_up = True
            break

    if not has_limit_up:
        return False

    current_price = spot_price if spot_price is not None else df.iloc[-1]["close"]
    return bool(current_price <= window_high * 0.95)


def scan_market(use_realtime: bool = False) -> list[tuple[str, str, str, float]]:
    """
    Scan all parquet files and return hits.
    Returns list of (strategy_name, symbol, name, price).
    V0: use_realtime=False, uses latest daily close as price.
    """

    # 优先用 TDX .day 文件的股票列表，没有则回退 parquet
    symbols = list_tdx_symbols()
    if not symbols:
        symbols = [f.stem for f in sorted(DAILY_DIR.glob("*.parquet"))]
    if not symbols:
        logger.warning("No data found. Run tdx-sync or pull-full first.")
        return []

    hits: list[tuple[str, str, str, float]] = []
    strategies = [
        ("30日首次涨停", check_first_limit_up),
        ("涨停后回调5%+", check_limit_up_pullback),
    ]

    for symbol in symbols:
        try:
            df = load_daily(symbol)
            if df.empty or len(df) < 2:
                continue

            price = float(df.iloc[-1]["close"])

            for strategy_name, strategy_fn in strategies:
                try:
                    hit = strategy_fn(df)
                    if hit:
                        hits.append((strategy_name, symbol, symbol, price))
                except Exception as e:
                    logger.debug(f"[{symbol}] strategy '{strategy_name}' error: {e}")

        except Exception as e:
            logger.debug(f"[{symbol}] load error: {e}")

    return hits
