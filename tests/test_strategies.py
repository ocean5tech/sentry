"""
Backtest / unit tests for scanner strategies using synthetic DataFrames.
Run with: python -m pytest tests/
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.scanner import check_first_limit_up, check_limit_up_pullback, LIMIT_UP_FACTOR


def make_df(closes: list[float], highs: list[float] | None = None) -> pd.DataFrame:
    if highs is None:
        highs = closes[:]
    dates = pd.date_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "close": closes,
        "high": highs,
        "low": closes,
        "volume": [1_000_000] * len(closes),
    })


class TestFirstLimitUp:
    def test_hit_no_prior_limit_up(self):
        # 30 flat days, then one limit-up today
        closes = [10.0] * 30 + [10.0 * LIMIT_UP_FACTOR]
        highs = closes[:]
        df = make_df(closes, highs)
        assert check_first_limit_up(df) is True

    def test_miss_prior_limit_up_within_29_days(self):
        closes = [10.0] * 5 + [10.0 * LIMIT_UP_FACTOR] + [11.0] * 24 + [11.0 * LIMIT_UP_FACTOR]
        highs = closes[:]
        df = make_df(closes, highs)
        assert check_first_limit_up(df) is False

    def test_miss_not_limit_up_today(self):
        closes = [10.0] * 30 + [10.5]
        df = make_df(closes)
        assert check_first_limit_up(df) is False

    def test_miss_today_limit_up_but_not_equal_high(self):
        # close == limit-up level but high > close (intraday spike then pullback)
        closes = [10.0] * 30 + [10.0 * LIMIT_UP_FACTOR]
        highs = closes[:-1] + [closes[-1] * 1.02]  # high > close
        df = make_df(closes, highs)
        assert check_first_limit_up(df) is False

    def test_too_short(self):
        df = make_df([10.0])
        assert check_first_limit_up(df) is False


class TestLimitUpPullback:
    def test_hit(self):
        # limit-up on day 10, then price drops 6% from high
        closes = [10.0] * 9 + [10.0 * LIMIT_UP_FACTOR] + [9.0] * 10
        highs = closes[:]
        df = make_df(closes, highs)
        # spot price is 6% below the window high
        window_high = max(highs[-20:])
        spot = window_high * 0.93
        assert check_limit_up_pullback(df, spot) is True

    def test_miss_no_limit_up_in_window(self):
        closes = [10.0] * 30
        df = make_df(closes)
        assert check_limit_up_pullback(df) is False

    def test_miss_pullback_insufficient(self):
        closes = [10.0] * 9 + [10.0 * LIMIT_UP_FACTOR] + [10.5] * 10
        highs = closes[:]
        df = make_df(closes, highs)
        assert check_limit_up_pullback(df) is False

    def test_too_short(self):
        df = make_df([10.0])
        assert check_limit_up_pullback(df) is False
