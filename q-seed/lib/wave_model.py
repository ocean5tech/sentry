"""
Wave + Pierce 形态识别 + 特征构造.
端口自 scripts/train_wave_model.py + scripts/find_similar_to_template.py 的 build_features_strict.
所有阈值/窗口参数通过 WaveParams dataclass 注入, 不硬编码.
"""

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd


@dataclass
class WaveParams:
    """形态/特征参数, 从 q-seed/config.yaml 的 wave_params 段加载."""
    launch_ret: float = 0.05
    launch_vol_mul: float = 2.0
    vol_win: int = 20
    pullback_win: int = 15
    lookback: int = 60
    min_wave: int = 2
    max_waves_feat: int = 4
    spike_win: int = 5
    spike_skip: int = 5
    min_triangle_len: int = 9
    price_center: float = 80.0

    @classmethod
    def from_dict(cls, d: dict) -> "WaveParams":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: d[k] for k in keys if k in d})


@dataclass
class FilterParams:
    skip_st: bool = True
    min_listing_days: int = 130
    exclude_suspended: bool = True
    board_whitelist: tuple = ("00", "60", "30", "68")

    @classmethod
    def from_dict(cls, d: dict) -> "FilterParams":
        return cls(
            skip_st=d.get("skip_st", True),
            min_listing_days=d.get("min_listing_days", 130),
            exclude_suspended=d.get("exclude_suspended", True),
            board_whitelist=tuple(d.get("board_whitelist", ["00", "60", "30", "68"])),
        )


def board_ok(sym: str, fp: FilterParams) -> bool:
    return sym.startswith(tuple(fp.board_whitelist))


def is_20cm(sym: str) -> bool:
    return sym.startswith(("30", "68"))


def check_wave(launches: list, min_count: int, mode: str = "strict") -> bool:
    """检查 Wave + Pierce 条件.
    mode='strict': HH 严格递增, 每次历史 pierce 必须 (宏景型)
    mode='loose':  HH 允许 1 次例外, Pierce 允许 1 次例外 (利通型 W 型)
    HL 始终允许 1 次例外 (两种模式相同)
    """
    if len(launches) < min_count:
        return False
    historical = launches[:-1]
    if len(historical) < min_count - 1:
        return False
    if any(l.get("trough_price") is None for l in historical):
        return False

    peaks = [l["peak_price"] for l in launches]
    hh_viol = sum(1 for i in range(len(peaks) - 1) if peaks[i+1] <= peaks[i])
    hh_max = 1 if mode == "loose" else 0
    if hh_viol > hh_max:
        return False

    troughs = [l["trough_price"] for l in historical]
    if len(troughs) >= 2:
        hl_viol = sum(1 for i in range(len(troughs) - 1) if troughs[i+1] <= troughs[i])
        if hl_viol > 1:
            return False

    pierce_viol = sum(1 for l in historical if not (l["trough_price"] < l["launch_open"]))
    pierce_max = 1 if mode == "loose" else 0
    if pierce_viol > pierce_max:
        return False
    return True


def find_launches(O, H, L, C, V, dates, wp: WaveParams) -> list:
    n = len(O)
    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    v_ma = pd.Series(V).shift(1).rolling(wp.vol_win, min_periods=wp.vol_win).mean().values
    is_launch_arr = (ret >= wp.launch_ret) & (V > v_ma * wp.launch_vol_mul) & (v_ma > 0)

    launches = []
    for t in range(n):
        if is_launch_arr[t]:
            launches.append({"launch_i": t})

    for k, la in enumerate(launches):
        t = la["launch_i"]
        if k + 1 < len(launches):
            next_t = launches[k+1]["launch_i"]
        else:
            next_t = min(t + wp.pullback_win + 1, n)

        la["launch_open"] = float(O[t])
        la["launch_high_day"] = float(H[t])
        la["launch_close"] = float(C[t])
        la["launch_date"] = dates[t]

        if next_t > t:
            pi = t + int(np.argmax(H[t:next_t]))
            la["peak_price"] = float(H[pi])
            la["peak_date"] = dates[pi]
        else:
            la["peak_price"] = float(H[t])
            la["peak_date"] = dates[t]

        if t + 1 < next_t:
            ti = t + 1 + int(np.argmin(L[t+1:next_t]))
            la["trough_price"] = float(L[ti])
            la["trough_date"] = dates[ti]
        else:
            la["trough_price"] = None
            la["trough_date"] = None

    return launches


def build_features(historical, trigger, entry_close, sig_date, name, code, wp: WaveParams) -> dict:
    """只用 sig_date (含) 之前的信息构造特征."""
    feats: dict[str, Any] = {}
    N = len(historical) + 1
    feats["n_waves"] = N

    for i, w in enumerate(historical[:wp.max_waves_feat]):
        p = f"w{i+1}"
        feats[f"{p}_amp"] = w["peak_price"] / w["launch_open"] - 1
        feats[f"{p}_dd"] = w["trough_price"] / w["peak_price"] - 1 if w["peak_price"] > 0 else 0
        feats[f"{p}_pierce"] = (w["launch_open"] - w["trough_price"]) / w["launch_open"]
        feats[f"{p}_to_peak_days"] = (w["peak_date"] - w["launch_date"]).days
        feats[f"{p}_to_trough_days"] = (w["trough_date"] - w["launch_date"]).days
        feats[f"{p}_body"] = w["launch_close"] / w["launch_open"] - 1
    for i in range(len(historical), wp.max_waves_feat):
        p = f"w{i+1}"
        for suf in ("amp", "dd", "pierce", "to_peak_days", "to_trough_days", "body"):
            feats[f"{p}_{suf}"] = 0.0

    amps = [w["peak_price"] / w["launch_open"] - 1 for w in historical]
    pierces = [(w["launch_open"] - w["trough_price"]) / w["launch_open"] for w in historical]
    dds = [w["trough_price"] / w["peak_price"] - 1 for w in historical]
    feats["amp_mean"] = float(np.mean(amps)) if amps else 0.0
    feats["amp_max"] = float(np.max(amps)) if amps else 0.0
    feats["amp_min"] = float(np.min(amps)) if amps else 0.0
    feats["amp_accel"] = amps[-1] - amps[0] if len(amps) >= 2 else 0.0
    feats["pierce_mean"] = float(np.mean(pierces)) if pierces else 0.0
    feats["pierce_max"] = float(np.max(pierces)) if pierces else 0.0
    feats["dd_mean"] = float(np.mean(dds)) if dds else 0.0
    feats["dd_worst"] = float(np.min(dds)) if dds else 0.0

    peaks = [w["peak_price"] for w in historical]
    feats["peak_to_peak_gain"] = peaks[-1] / peaks[0] - 1 if len(peaks) >= 2 else 0.0
    troughs = [w["trough_price"] for w in historical]
    feats["trough_to_trough_gain"] = troughs[-1] / troughs[0] - 1 if len(troughs) >= 2 else 0.0

    feats["total_span_days"] = (trigger["launch_date"] - historical[0]["launch_date"]).days if historical else 0
    feats["last_launch_gap"] = (trigger["launch_date"] - historical[-1]["launch_date"]).days if historical else 0

    feats["total_gain_from_w1"] = entry_close / historical[0]["launch_open"] - 1 if historical else 0.0
    feats["gain_over_last_peak"] = entry_close / peaks[-1] - 1 if peaks else 0.0
    feats["gain_over_last_trough"] = entry_close / troughs[-1] - 1 if troughs else 0.0

    feats["trigger_open"] = trigger["launch_open"]
    feats["trigger_close"] = entry_close
    feats["trigger_body"] = entry_close / trigger["launch_open"] - 1
    feats["trigger_day_high"] = trigger["launch_high_day"]
    feats["trigger_close_at_high"] = (trigger["launch_high_day"] - entry_close) / trigger["launch_high_day"] if trigger["launch_high_day"] > 0 else 0.0

    feats["entry_price"] = entry_close
    feats["log_entry"] = float(np.log(entry_close)) if entry_close > 0 else 0.0

    feats["is_20cm"] = int(is_20cm(code))
    nm = name or ""
    feats["is_st"] = int("ST" in nm.upper())
    feats["is_delist"] = int("退" in nm or "*ST" in nm)

    return feats


def build_features_strict(historical, trigger, C, H, L, n, entry_close,
                          sig_date, name, code, wp: WaveParams) -> dict:
    """在 build_features 基础上加 spike / triangle / price_zone 特征 (KNN 用)."""
    feats = build_features(historical, trigger, entry_close, sig_date, name, code, wp)

    # 1. 尖头度: 每个 historical wave launch 后 spike_win 日内有一天 close < launch close
    spikes: list[int] = []
    for w in historical:
        t = w["launch_i"]
        if t + wp.spike_win < n:
            after_min = float(np.min(C[t+1:t+1+wp.spike_win]))
            spike = (after_min < C[t])
        else:
            spike = False
        spikes.append(int(spike))
    trig_t = trigger["launch_i"]
    trig_close_at_high = (entry_close >= trigger["launch_high_day"] * 0.97)
    feats["spike_ratio"] = float(sum(spikes) / max(len(spikes), 1)) if spikes else 0.0
    feats["all_spike"] = int(all(spikes)) if spikes else 0
    feats["spike_count"] = int(sum(spikes))
    feats["trig_close_at_high"] = int(trig_close_at_high)

    # 2. 反喇叭口
    if historical:
        win_start = historical[-1]["launch_i"] + wp.spike_skip
    else:
        win_start = trig_t - 20
    win_end = trig_t
    win_len = win_end - win_start
    if win_len >= wp.min_triangle_len:
        seg_size = win_len // 3
        amps = []
        for k in range(3):
            s = win_start + k * seg_size
            e = win_start + (k + 1) * seg_size if k < 2 else win_end
            if e > s:
                sh = float(np.max(H[s:e]))
                sl = float(np.min(L[s:e]))
                sm = (sh + sl) / 2
                amps.append((sh - sl) / sm if sm > 0 else 0.0)
        if len(amps) == 3:
            feats["triangle_strict"] = int(amps[0] > amps[1] > amps[2])
            feats["amp_shrinkage"] = (amps[0] - amps[2]) / amps[0] if amps[0] > 0 else 0.0
            feats["amp_seg1"] = amps[0]
            feats["amp_seg2"] = amps[1]
            feats["amp_seg3"] = amps[2]
        else:
            for k_ in ("triangle_strict", "amp_shrinkage", "amp_seg1", "amp_seg2", "amp_seg3"):
                feats[k_] = 0.0
    else:
        for k_ in ("triangle_strict", "amp_shrinkage", "amp_seg1", "amp_seg2", "amp_seg3"):
            feats[k_] = 0.0

    # 3. 入场价位
    feats["in_60_100"] = int(60 <= entry_close <= 100)
    feats["log_entry_dist_80"] = float(np.log(entry_close / wp.price_center)) if entry_close > 0 else 0.0

    return feats


def scan_one_features(sym: str, name: str, mode: str,
                      wp: WaveParams, fp: FilterParams,
                      load_daily_fn) -> list[dict]:
    """扫一只股票, 返回所有 Sig 触发点的特征.
    load_daily_fn: 函数 (symbol) → DataFrame, 由调用方注入 (依赖反转, L2 友好).
    """
    if not board_ok(sym, fp):
        return []
    if fp.skip_st and "ST" in (name or "").upper():
        return []
    try:
        df = load_daily_fn(sym)
    except Exception:
        return []
    if df is None or df.empty:
        return []
    n = len(df)
    if n < wp.vol_win + wp.lookback + 5:
        return []
    if n < fp.min_listing_days:
        return []

    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    C = df["close"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].dt.date.values

    if fp.exclude_suspended and V[-1] == 0:
        return []

    launches = find_launches(O, H, L, C, V, dates, wp)
    if len(launches) < wp.min_wave:
        return []

    rows = []
    for k, la in enumerate(launches):
        t = la["launch_i"]
        window_start = t - wp.lookback
        prev = [l for l in launches[:k] if l["launch_i"] >= window_start]
        all_l = prev + [la]
        if not check_wave(all_l, wp.min_wave, mode=mode):
            continue
        feats = build_features_strict(prev, la, C, H, L, n,
                                       float(C[t]), la["launch_date"], name, sym, wp)
        row = {
            "code": sym,
            "name": name,
            "sig_date": la["launch_date"].isoformat(),
            "entry": float(C[t]),
            "is_20cm": int(is_20cm(sym)),
            "_launch": la,  # 给 kline_snapshot 用
            **feats,
        }
        rows.append(row)
    return rows
