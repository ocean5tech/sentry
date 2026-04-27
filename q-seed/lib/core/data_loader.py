import time
import random
import logging
import atexit
from pathlib import Path

import pandas as pd

# baostock + pyarrow 改成 lazy import — q-seed 只读 TDX, 不需要它们
# 如果调 update_daily / parquet fallback 才会按需 import

DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)

# baostock 正确 schema 的列名集合，用于检测坏文件
_EXPECTED_COLS = {"date", "open", "high", "low", "close", "volume"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

_bs_logged_in = False

def _ensure_login():
    global _bs_logged_in
    import baostock as bs
    if not _bs_logged_in:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        _bs_logged_in = True
        atexit.register(bs.logout)

def _to_bs_code(symbol: str) -> str:
    """000001 → sz.000001 / 600001 → sh.600001 / 8xxxxx → bj.8xxxxx"""
    if symbol.startswith("6"):
        return f"sh.{symbol}"
    elif symbol.startswith("8") or symbol.startswith("4"):
        return f"bj.{symbol}"
    else:
        return f"sz.{symbol}"

def _parquet_path(symbol: str) -> Path:
    return DAILY_DIR / f"{symbol}.parquet"

def _is_valid_and_fresh(symbol: str) -> bool:
    """
    Returns True if the parquet exists, has correct baostock schema,
    and was already updated today. Any file last updated before today
    will be re-fetched for incremental update.
    """
    path = _parquet_path(symbol)
    if not path.exists():
        return False
    try:
        tbl = pq.read_table(path)
        if not _EXPECTED_COLS.issubset(set(tbl.column_names)):
            return False  # old akshare schema
        dates = tbl.column("date").to_pandas()
        last = pd.to_datetime(dates).max().normalize()
        today = pd.Timestamp.today().normalize()
        return last >= today
    except Exception:
        return False


def update_daily(symbol: str, full_refresh: bool = False) -> bool:
    """Incrementally update one stock's daily OHLCV parquet. Returns True on success."""
    _ensure_login()
    path = _parquet_path(symbol)

    start_date = "2020-01-01"
    if not full_refresh and path.exists():
        try:
            existing = pq.read_table(path, columns=["date"]).to_pandas()
            # 如果是旧 akshare schema（没有正确列），强制全量重下
            tbl_cols = pq.read_table(path).column_names
            if not _EXPECTED_COLS.issubset(set(tbl_cols)):
                logger.warning(f"[{symbol}] old schema detected, re-downloading")
                path.unlink()
                start_date = "2020-01-01"
            elif not existing.empty:
                last = pd.to_datetime(existing["date"].max())
                start_date = (last - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
        except Exception:
            start_date = "2020-01-01"

    bs_code = _to_bs_code(symbol)
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount,turn,pctChg",
            start_date=start_date,
            end_date="2099-12-31",
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            logger.error(f"[{symbol}] baostock error: {rs.error_msg}")
            return False

        rows = []
        while True:
            row = rs.get_row_data()
            if not row:
                break
            rows.append(row)

    except Exception as e:
        logger.error(f"[{symbol}] fetch failed: {e}")
        return False

    if not rows:
        return True  # suspended / delisted

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close",
                                      "volume", "amount", "turnover", "pct_chg"])
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)

    if not full_refresh and path.exists():
        old = pq.read_table(path).to_pandas()
        old["date"] = pd.to_datetime(old["date"])
        cutoff = df["date"].min()
        old = old[old["date"] < cutoff]
        df = pd.concat([old, df], ignore_index=True)

    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)
    return True


def update_all_daily():
    """
    Update all A-share stocks serially.
    Skips stocks that already have fresh valid data (within 7 days).
    On first run ~60-90 min; subsequent runs ~5-10 min.
    """
    _ensure_login()

    try:
        rs = bs.query_stock_basic()
        rows = []
        while True:
            row = rs.get_row_data()
            if not row:
                break
            rows.append(row)
        stock_df = pd.DataFrame(rows, columns=rs.fields)
    except Exception as e:
        logger.error(f"Failed to fetch stock list: {e}")
        return

    stock_df = stock_df[(stock_df["type"] == "1") & (stock_df["status"] == "1")]
    symbols = stock_df["code"].str.split(".").str[1].dropna().tolist()
    total = len(symbols)

    # 断点续传：跳过已有最新数据的股票
    pending = [s for s in symbols if not _is_valid_and_fresh(s)]
    skipped = total - len(pending)
    logger.info(f"Total {total} stocks — {skipped} already up-to-date, updating {len(pending)}")

    if not pending:
        logger.info("All stocks are up-to-date. Nothing to do.")
        return

    ok = failed = 0
    for i, symbol in enumerate(pending, 1):
        success = update_daily(symbol)
        if success:
            ok += 1
        else:
            failed += 1
        if i % 50 == 0 or i == len(pending):
            logger.info(f"Progress: {i}/{len(pending)} — ok={ok} failed={failed}")
        time.sleep(0.05 + random.uniform(0, 0.05))

    logger.info(f"Done. ok={ok} failed={failed} skipped={skipped}")


def get_spot() -> pd.DataFrame:
    """Returns latest close price for all downloaded stocks (V0 uses daily close)."""
    parquet_files = sorted(DAILY_DIR.glob("*.parquet"))
    if not parquet_files:
        return pd.DataFrame()
    rows = []
    for path in parquet_files:
        df = load_daily(path.stem)
        if df.empty:
            continue
        rows.append({"code": path.stem, "price": float(df.iloc[-1]["close"])})
    return pd.DataFrame(rows)


def load_daily(symbol: str) -> pd.DataFrame:
    """
    Load a stock's daily history. Prefers TDX .day file if available,
    falls back to parquet (baostock data).
    """
    from core.tdx_loader import read_day
    df = read_day(symbol)
    if not df.empty:
        return df

    # fallback: parquet (按需 import pyarrow)
    path = _parquet_path(symbol)
    if not path.exists():
        return pd.DataFrame()
    import pyarrow.parquet as pq
    df = pq.read_table(path).to_pandas()
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)
