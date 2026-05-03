#!/usr/bin/env python3
"""
三红吸筹洗盘策略 — 机器学习特征分析

对每笔历史交易提取特征，用随机森林找出盈亏差异的主要来源。

特征列表：
  ma20_slope       : 入场前20日MA20斜率（/价格，正=上升趋势）
  ma20_flatness    : 入场前20日MA20波动率（std/mean，越小越平坦）
  price_vs_ma20    : 入场价/MA20 - 1（在均线上下的位置）
  price_vs_ma60    : 入场价/MA60 - 1
  price_vs_ma120   : 入场价/MA120 - 1
  vol_trend        : 入场前20日成交量趋势（线性回归斜率/均量）
  vol_shrink       : 入场日量/20日均量（越小越缩量）
  c1_to_c3_days    : c1到c3间隔天数（越长越慢牛）
  c3_to_entry_days : c3到入场间隔天数
  c3_drift         : c3基准比c1基准高多少%（慢牛幅度）
  wash_vol_c1      : c1后放量倍数
  wash_vol_c2      : c2后放量倍数
  c3_body_pct      : c3阳线实体大小（%）
  entry_vs_c3mid   : 入场价比c3中值低多少%
  stop_distance    : 止损距离（入场价/止损价 - 1，越大止损空间越大）
"""
import sys, importlib.util, time
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "q-seed" / "lib"))

import core.tdx_loader as tdx
tdx.set_tdx_dir(str(ROOT / "data" / "tdx"))
from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tr_mod = _load("three_red_bars_v4", ROOT / "core/strategies/three_red_bars_v4.py")


# ── 特征提取版回测 ──────────────────────────────────────
def backtest_with_features(df) -> list[dict]:
    n = len(df)
    if n < 120 + 90:
        return []

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    big = [i for i in range(1, n)
           if C[i-1] > 0
           and (C[i] - C[i-1]) / C[i-1] >= tr_mod.MIN_CANDLE_PCT
           and C[i] > O[i]]
    if len(big) < 3:
        return []

    seen_c3 = set()
    records = []

    for k in range(2, len(big)):
        c3_i = big[k]
        if c3_i > n - 90:
            break
        if c3_i in seen_c3:
            continue

        # 找 c2
        c2_i = None
        for j in range(k - 1, max(k - 15, -1), -1):
            sp = c3_i - big[j]
            if sp > tr_mod.MAX_SPACING: break
            if sp >= tr_mod.MIN_SPACING: c2_i = big[j]; break
        if c2_i is None: continue

        # 找 c1
        c1_i = None
        idx2 = big.index(c2_i)
        for j2 in range(idx2 - 1, max(idx2 - 15, -1), -1):
            sp = c2_i - big[j2]
            if sp > tr_mod.MAX_SPACING: break
            if sp >= tr_mod.MIN_SPACING: c1_i = big[j2]; break
        if c1_i is None: continue

        sp1, sp2 = c2_i - c1_i, c3_i - c2_i
        if sp1 > 0 and sp2 > 0 and max(sp1, sp2) / min(sp1, sp2) > tr_mod.MAX_SPACING_RATIO:
            continue

        # 缓慢上升约束
        bp = [C[i-1] for i in (c1_i, c2_i, c3_i) if i > 0 and C[i-1] > 0]
        if len(bp) < 2 or bp[0] <= 0: continue
        drift = (bp[-1] - bp[0]) / bp[0]
        if drift < 0 or drift > tr_mod.BASE_DRIFT_MAX: continue

        # 洗盘放量
        vol_ma_c1 = float(np.mean(V[max(0, c1_i-19): c1_i+1]))
        vol_ma_c2 = float(np.mean(V[max(0, c2_i-19): c2_i+1]))
        wash_r1 = tr_mod._wash_vol_ratio(V, c1_i, vol_ma_c1)
        wash_r2 = tr_mod._wash_vol_ratio(V, c2_i, vol_ma_c2)
        if wash_r1 < tr_mod.WASH_VOL_MULT or wash_r2 < tr_mod.WASH_VOL_MULT:
            continue

        seen_c3.add(c3_i)

        # 找第4根
        fourth_i = None
        c3_set = {c1_i, c2_i, c3_i}
        for fi in range(c3_i + 1, min(c3_i + tr_mod.FOURTH_WINDOW + 1, n)):
            if fi in c3_set or C[fi-1] <= 0: continue
            if (C[fi]-C[fi-1])/C[fi-1] >= tr_mod.MIN_CANDLE_PCT and C[fi] > O[fi]:
                fourth_i = fi; break
        if fourth_i is None: continue

        c3_mid   = (O[c3_i] + C[c3_i]) / 2
        stop_lvl = min(L[c1_i], L[c2_i], L[c3_i])
        vol_base = float(np.mean(V[max(0, c3_i-19): c3_i+1]))
        if vol_base <= 0: continue

        # 找入场点
        entry_i = None
        for ri in range(fourth_i + 1, min(fourth_i + tr_mod.RETRACE_WINDOW + 1, n - 1)):
            vol_ma = float(np.mean(V[max(0, ri-19): ri+1]))
            if vol_ma <= 0: continue
            if C[ri] < c3_mid and V[ri] / vol_ma <= 0.50:
                entry_i = ri; break
        if entry_i is None or entry_i + 1 >= n - 90: continue

        entry_price = float(O[entry_i + 1])
        if entry_price <= 0 or stop_lvl <= 0: continue

        # 出场
        launch_i = None
        for li in range(entry_i + 1, min(entry_i + 61, n - 1)):
            if C[li-1] <= 0: continue
            if (C[li]-C[li-1])/C[li-1] >= tr_mod.LAUNCH_PCT and C[li] > O[li]:
                launch_i = li; break

        exit_i    = min(launch_i + 3, n-1) if launch_i else min(entry_i + 60, n-1)
        exit_type = "launch+3" if launch_i else "timeout"
        for si in range(entry_i + 1, exit_i + 1):
            if C[si] < stop_lvl:
                exit_i = si; exit_type = "stop_loss"; break

        ret = (C[exit_i] / entry_price - 1) * 100
        if not (-60 <= ret <= 200): continue

        # ── 特征提取 ──────────────────────────────────────
        # MA20 在入场日前后的斜率和平坦度
        ma20_arr = np.array([np.mean(C[max(0,i-19):i+1]) for i in range(max(0,entry_i-19), entry_i+1)])
        if len(ma20_arr) >= 5 and ma20_arr.mean() > 0:
            x = np.arange(len(ma20_arr))
            ma20_slope = float(np.polyfit(x, ma20_arr, 1)[0]) / float(ma20_arr.mean())
            ma20_flatness = float(ma20_arr.std()) / float(ma20_arr.mean())
        else:
            ma20_slope = ma20_flatness = 0.0

        # 价格位置
        ma20_val  = float(np.mean(C[max(0, entry_i-19): entry_i+1]))
        ma60_val  = float(np.mean(C[max(0, entry_i-59): entry_i+1]))
        ma120_val = float(np.mean(C[max(0, entry_i-119): entry_i+1]))
        p_vs_ma20  = (entry_price / ma20_val  - 1) if ma20_val  > 0 else 0
        p_vs_ma60  = (entry_price / ma60_val  - 1) if ma60_val  > 0 else 0
        p_vs_ma120 = (entry_price / ma120_val - 1) if ma120_val > 0 else 0

        # 成交量趋势
        vol20 = V[max(0, entry_i-19): entry_i+1]
        if len(vol20) >= 5 and vol20.mean() > 0:
            x = np.arange(len(vol20))
            vol_trend = float(np.polyfit(x, vol20, 1)[0]) / float(vol20.mean())
        else:
            vol_trend = 0.0

        vol_ma_entry = float(np.mean(V[max(0, entry_i-19): entry_i+1]))
        vol_shrink   = float(V[entry_i]) / vol_ma_entry if vol_ma_entry > 0 else 1.0

        # ── 各阶段时间间距 ──────────────────────────────
        c1_to_c2_days = c2_i - c1_i
        c2_to_c3_days = c3_i - c2_i
        c3_to_entry_days = entry_i - c3_i

        # ── c1/c2 洗盘回调质量 ──────────────────────────
        # 洗盘是否回调到开盘价附近（该阳线的开盘价 = 庄家成本区）
        PULLBACK_WIN = 10  # 大阳后10个交易日内的最低价

        def pullback_vs_open(candle_idx, open_price):
            """大阳后PULLBACK_WIN天内最低价 vs 开盘价，负数=回调超过开盘，正数=没到开盘"""
            if open_price <= 0:
                return 0.0
            end = min(candle_idx + PULLBACK_WIN + 1, n)
            lows = L[candle_idx + 1: end]
            if len(lows) == 0:
                return 0.0
            return float((min(lows) - open_price) / open_price * 100)

        def days_to_pullback(candle_idx, open_price):
            """大阳后几天收盘价首次跌破开盘价，没到则返回PULLBACK_WIN"""
            for d in range(1, PULLBACK_WIN + 1):
                idx = candle_idx + d
                if idx >= n:
                    break
                if C[idx] <= open_price:
                    return d
            return PULLBACK_WIN

        c1_pullback_pct  = pullback_vs_open(c1_i, O[c1_i])
        c2_pullback_pct  = pullback_vs_open(c2_i, O[c2_i])
        c1_pullback_days = days_to_pullback(c1_i, O[c1_i])
        c2_pullback_days = days_to_pullback(c2_i, O[c2_i])

        # 两次洗盘是否都回到开盘价（干净洗盘标志）
        clean_wash = int(c1_pullback_pct <= 0 and c2_pullback_pct <= 0)

        records.append({
            # 标签
            "ret_pct":    round(ret, 2),
            "win":        int(ret > 0),
            "exit_type":  exit_type,
            # 特征：趋势
            "ma20_slope":       round(ma20_slope, 5),
            "ma20_flatness":    round(ma20_flatness, 5),
            "price_vs_ma20":    round(p_vs_ma20, 4),
            "price_vs_ma60":    round(p_vs_ma60, 4),
            "price_vs_ma120":   round(p_vs_ma120, 4),
            "vol_trend":        round(vol_trend, 5),
            "vol_shrink":       round(vol_shrink, 3),
            # 特征：形态结构
            "c1_to_c2_days":    c1_to_c2_days,
            "c2_to_c3_days":    c2_to_c3_days,
            "c3_to_entry_days": c3_to_entry_days,
            "c3_drift":         round(drift, 4),
            "wash_vol_c1":      round(wash_r1, 2),
            "wash_vol_c2":      round(wash_r2, 2),
            # 特征：洗盘质量（核心新增）
            "c1_pullback_pct":  round(c1_pullback_pct, 2),   # 负=回调超开盘(好)
            "c2_pullback_pct":  round(c2_pullback_pct, 2),
            "c1_pullback_days": c1_pullback_days,             # 越快越干净
            "c2_pullback_days": c2_pullback_days,
            "clean_wash":       clean_wash,                   # 两次都回到开盘=1
            # 特征：入场质量
            "c3_body_pct":      round((C[c3_i]-O[c3_i])/O[c3_i]*100, 2) if O[c3_i]>0 else 0,
            "entry_vs_c3mid":   round((entry_price - c3_mid)/c3_mid*100, 2) if c3_mid>0 else 0,
            "stop_distance":    round((entry_price/stop_lvl - 1)*100, 2) if stop_lvl>0 else 0,
        })

    return records


# ── ML 分析 ──────────────────────────────────────────
FEATURES = [
    "ma20_slope", "ma20_flatness",
    "price_vs_ma20", "price_vs_ma60", "price_vs_ma120",
    "vol_trend", "vol_shrink",
    "c1_to_c2_days", "c2_to_c3_days", "c3_to_entry_days", "c3_drift",
    "c1_pullback_pct", "c2_pullback_pct",
    "c1_pullback_days", "c2_pullback_days", "clean_wash",
    "wash_vol_c1", "wash_vol_c2",
    "c3_body_pct", "entry_vs_c3mid", "stop_distance",
]

def run_ml(records: list[dict]):
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.model_selection import cross_val_score
    from sklearn.preprocessing import StandardScaler
    import numpy as np

    X = np.array([[r[f] for f in FEATURES] for r in records])
    y = np.array([r["win"] for r in records])

    print(f"\n样本: {len(y)}  盈利={y.sum()}({y.mean():.1%})  亏损={len(y)-y.sum()}")

    # ── 随机森林：特征重要性 ──────────────────────────
    rf = RandomForestClassifier(n_estimators=300, max_depth=6,
                                 min_samples_leaf=20, random_state=42, n_jobs=-1)
    cv_scores = cross_val_score(rf, X, y, cv=5, scoring="roc_auc")
    print(f"\n随机森林 5折 AUC: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")
    print("(AUC=0.5 随机猜, AUC>0.6 有区分能力, AUC>0.7 有实用价值)")

    rf.fit(X, y)
    importances = rf.feature_importances_
    ranked = sorted(zip(FEATURES, importances), key=lambda x: -x[1])

    print("\n特征重要性排名（随机森林）:")
    for i, (feat, imp) in enumerate(ranked):
        bar = "█" * int(imp * 200)
        win_vals  = [r[feat] for r in records if r["win"] == 1]
        loss_vals = [r[feat] for r in records if r["win"] == 0]
        w_med = np.median(win_vals)
        l_med = np.median(loss_vals)
        print(f"  {i+1:2d}. {feat:<22} {imp:.3f}  {bar}")
        print(f"       盈利中位={w_med:+.4f}  亏损中位={l_med:+.4f}  差值={w_med-l_med:+.4f}")

    # ── 决策树：可读规则 ──────────────────────────────
    top5 = [f for f, _ in ranked[:5]]
    X_top = np.array([[r[f] for f in top5] for r in records])
    dt = DecisionTreeClassifier(max_depth=4, min_samples_leaf=50, random_state=42)
    dt.fit(X_top, y)
    dt_cv = cross_val_score(dt, X_top, y, cv=5, scoring="roc_auc")
    print(f"\n决策树（Top5特征, depth=4）5折 AUC: {dt_cv.mean():.3f}")
    print("\n决策树规则（盈利概率最高的路径）:")
    print(export_text(dt, feature_names=top5, max_depth=4))


# ── 主流程 ──────────────────────────────────────────
def main():
    symbols = list_tdx_symbols()
    print(f"扫描 {len(symbols)} 只，提取特征...", flush=True)

    all_records = []
    t0 = time.time()
    for i, sym in enumerate(symbols):
        if i % 2000 == 0 and i > 0:
            print(f"  {i}/{len(symbols)}  records={len(all_records)}  {time.time()-t0:.0f}s", flush=True)
        try:
            df = load_daily(sym)
            if df is not None:
                all_records.extend(backtest_with_features(df))
        except Exception:
            pass

    print(f"完成: {time.time()-t0:.0f}s  总样本={len(all_records)}", flush=True)
    run_ml(all_records)


if __name__ == "__main__":
    main()
