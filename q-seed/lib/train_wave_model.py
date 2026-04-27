#!/usr/bin/env python3
"""
Wave+Pierce 信号的 LightGBM 分类器.

目标: 预测 Sig 触发后 T+60 超额 >= +100% (主升浪 / 超级牛股).

特征严格限制在 Sig 触发日(含当日)及之前:
  - 历史 wave w1..w_{N-1} 的 start/peak/trough 及衍生特征 (安全, 都在过去)
  - 触发 wave wN 的 launch 当日 open/high/close (当日可知)
  - 触发日 entry price, 是否 ST, 板别

不使用任何 fut_ / bench_ / 触发波 peak 后续数据.

切分: train 2021-2023, test 2024-2026 (严格 OOS).
"""

import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.stock_names import get_names


LAUNCH_RET = 0.05
LAUNCH_VOL_MUL = 2.0
VOL_WIN = 20
PULLBACK_WIN = 15
LOOKBACK = 60
MIN_WAVE = 2
FUT_WIN = 60   # 只评估 T+60
TARGET_EXCESS = 1.00  # +100%
MAX_WAVES_FEAT = 4   # 特征中保留前 K 次 wave (从 w1 开始, 含触发波)

TRAIN_END = pd.Timestamp("2023-12-31")
TEST_START = pd.Timestamp("2024-01-01")

OUT_CSV = ROOT / "logs" / "wave_train_predictions.csv"
MODEL_PATH = ROOT / "logs" / "wave_lgb.txt"


def board_ok(sym):
    return sym.startswith(("00", "60", "30", "68"))


def is_20cm(sym):
    return sym.startswith(("30", "68"))


def read_index(path):
    raw = open(path, "rb").read()
    rows = []
    for i in range(len(raw) // 32):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", raw[i*32:(i+1)*32])
        if c:
            rows.append({"date": pd.Timestamp(str(d)), "close": c / 100})
    return pd.DataFrame(rows)


def build_sh300():
    sh = read_index(ROOT / "data" / "tdx" / "sh" / "lday" / "sh000300.day")
    sh = sh.sort_values("date").reset_index(drop=True)
    closes = sh["close"].values.astype(float)
    dates_arr = sh["date"].dt.date.values
    return closes, {d: i for i, d in enumerate(dates_arr)}


def check_wave(launches, min_count, mode="strict"):
    """检查 Wave+Pierce 条件.
    mode='strict': HH 严格递增, 每次历史 pierce 必须 (宏景型)
    mode='loose':  HH 允许 1 次例外, Pierce 允许 1 次例外 (利通型 W 形)
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


def find_launches(O, H, L, C, V, dates):
    n = len(O)
    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    v_ma = pd.Series(V).shift(1).rolling(VOL_WIN, min_periods=VOL_WIN).mean().values
    is_launch_arr = (ret >= LAUNCH_RET) & (V > v_ma * LAUNCH_VOL_MUL) & (v_ma > 0)

    launches = []
    for t in range(n):
        if is_launch_arr[t]:
            launches.append({"launch_i": t})

    # 对每个 launch, 填充 peak/trough 基于 [launch_i, next_launch_i) 区间
    # 注意: 对最后一个 launch, next_t = launch_i + PULLBACK_WIN, 使用了未来数据
    # 这些值只用于历史 wave (w1..w_{N-1}); 触发波 peak/trough 永远不能进特征
    for k, la in enumerate(launches):
        t = la["launch_i"]
        if k + 1 < len(launches):
            next_t = launches[k+1]["launch_i"]
        else:
            next_t = min(t + PULLBACK_WIN + 1, n)

        la["launch_open"] = float(O[t])
        la["launch_high_day"] = float(H[t])   # 当日 high (安全)
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


def build_features(historical, trigger, entry_close, sig_date, name, code):
    """只用 sig_date (含) 之前的信息构造特征."""
    feats = {}
    N = len(historical) + 1
    feats["n_waves"] = N

    # 历史 wave 特征 (用 peak/trough, 这些都是"下一 launch 之前"的数据, 安全)
    for i, w in enumerate(historical[:MAX_WAVES_FEAT]):
        p = f"w{i+1}"
        feats[f"{p}_amp"] = w["peak_price"] / w["launch_open"] - 1
        feats[f"{p}_dd"] = w["trough_price"] / w["peak_price"] - 1 if w["peak_price"] > 0 else 0
        feats[f"{p}_pierce"] = (w["launch_open"] - w["trough_price"]) / w["launch_open"]
        feats[f"{p}_to_peak_days"] = (w["peak_date"] - w["launch_date"]).days
        feats[f"{p}_to_trough_days"] = (w["trough_date"] - w["launch_date"]).days
        feats[f"{p}_body"] = w["launch_close"] / w["launch_open"] - 1
    # 如果历史 wave 不够 MAX_WAVES_FEAT, 用 0 填
    for i in range(len(historical), MAX_WAVES_FEAT):
        p = f"w{i+1}"
        for suf in ("amp", "dd", "pierce", "to_peak_days", "to_trough_days", "body"):
            feats[f"{p}_{suf}"] = 0.0

    # 历史波浪统计量
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

    # peaks 序列升幅
    peaks = [w["peak_price"] for w in historical]
    if len(peaks) >= 2:
        feats["peak_to_peak_gain"] = peaks[-1] / peaks[0] - 1
    else:
        feats["peak_to_peak_gain"] = 0.0

    # troughs 序列
    troughs = [w["trough_price"] for w in historical]
    if len(troughs) >= 2:
        feats["trough_to_trough_gain"] = troughs[-1] / troughs[0] - 1
    else:
        feats["trough_to_trough_gain"] = 0.0

    # 时间跨度
    feats["total_span_days"] = (trigger["launch_date"] - historical[0]["launch_date"]).days
    feats["last_launch_gap"] = (trigger["launch_date"] - historical[-1]["launch_date"]).days

    # 入场价相对历史
    feats["total_gain_from_w1"] = entry_close / historical[0]["launch_open"] - 1
    feats["gain_over_last_peak"] = entry_close / peaks[-1] - 1 if peaks else 0.0
    feats["gain_over_last_trough"] = entry_close / troughs[-1] - 1 if troughs else 0.0

    # 触发日当日 (安全)
    feats["trigger_open"] = trigger["launch_open"]
    feats["trigger_close"] = entry_close
    feats["trigger_body"] = entry_close / trigger["launch_open"] - 1
    feats["trigger_day_high"] = trigger["launch_high_day"]
    feats["trigger_close_at_high"] = (trigger["launch_high_day"] - entry_close) / trigger["launch_high_day"]

    # 入场价位水平
    feats["entry_price"] = entry_close
    feats["log_entry"] = float(np.log(entry_close)) if entry_close > 0 else 0.0

    # 板别 / ST
    feats["is_20cm"] = int(is_20cm(code))
    nm_u = (name or "").upper()
    feats["is_st"] = int("ST" in nm_u)
    feats["is_delist"] = int("退" in (name or "") or "*ST" in (name or ""))

    return feats


def scan_one(sym, name, sh_closes, sh_idx_map):
    if not board_ok(sym):
        return []
    try:
        df = load_daily(sym)
    except Exception:
        return []
    n = len(df)
    if n < VOL_WIN + LOOKBACK + FUT_WIN + 5:
        return []

    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    C = df["close"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].dt.date.values

    launches = find_launches(O, H, L, C, V, dates)
    if len(launches) < MIN_WAVE:
        return []

    fut = np.full(n, np.nan)
    if n > FUT_WIN:
        fut[:-FUT_WIN] = C[FUT_WIN:] / C[:-FUT_WIN] - 1

    bench = np.full(n, np.nan)
    for j, d in enumerate(dates):
        i = sh_idx_map.get(d, -1)
        if i < 0: continue
        if 0 <= i < len(sh_closes) - FUT_WIN:
            bench[j] = sh_closes[i + FUT_WIN] / sh_closes[i] - 1

    results = []
    for k, la in enumerate(launches):
        t = la["launch_i"]
        window_start = t - LOOKBACK
        prev = [l for l in launches[:k] if l["launch_i"] >= window_start]
        all_l = prev + [la]
        if not check_wave(all_l, MIN_WAVE):
            continue

        # 构造特征 (严格不含未来)
        feats = build_features(prev, la, float(C[t]), la["launch_date"], name, sym)
        row = {
            "code": sym,
            "name": name,
            "sig_date": la["launch_date"].isoformat(),
            "entry": float(C[t]),
            "fut_60": float(fut[t]) if not np.isnan(fut[t]) else None,
            "bench_60": float(bench[t]) if not np.isnan(bench[t]) else None,
        }
        row.update(feats)
        results.append(row)
    return results


def main():
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score, average_precision_score

    sh_closes, sh_idx_map = build_sh300()
    names = get_names()
    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只")

    all_rows = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 {len(all_rows):,}")
        rs = scan_one(s, names.get(s, ""), sh_closes, sh_idx_map)
        if rs:
            all_rows.extend(rs)

    df = pd.DataFrame(all_rows)
    df["sig_date_dt"] = pd.to_datetime(df["sig_date"])
    # 只保留 fut_60 可得的样本
    df = df[df["fut_60"].notna() & df["bench_60"].notna()].copy()
    df["excess_60"] = df["fut_60"] - df["bench_60"]
    df["label"] = (df["excess_60"] >= TARGET_EXCESS).astype(int)
    print(f"\n全样本 {len(df):,}  正样本 (excess>={TARGET_EXCESS*100:.0f}%) {int(df['label'].sum()):,} ({df['label'].mean()*100:.2f}%)")

    # 时间切分
    train = df[df["sig_date_dt"] <= TRAIN_END].copy()
    test = df[df["sig_date_dt"] >= TEST_START].copy()
    print(f"Train {len(train):,} (正 {int(train['label'].sum()):,})  Test {len(test):,} (正 {int(test['label'].sum()):,})")

    feat_cols = [c for c in df.columns if c not in
                 ("code", "name", "sig_date", "sig_date_dt", "entry", "fut_60", "bench_60", "excess_60", "label")]
    print(f"特征维度: {len(feat_cols)}")

    X_tr = train[feat_cols].values.astype(np.float32)
    y_tr = train["label"].values
    X_te = test[feat_cols].values.astype(np.float32)
    y_te = test["label"].values

    pos_ratio = y_tr.mean()
    spw = (1 - pos_ratio) / max(pos_ratio, 1e-6)
    print(f"scale_pos_weight={spw:.1f}")

    lgb_train = lgb.Dataset(X_tr, y_tr, feature_name=feat_cols)
    params = {
        "objective": "binary",
        "metric": ["auc", "binary_logloss"],
        "learning_rate": 0.05,
        "num_leaves": 31,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "min_data_in_leaf": 50,
        "scale_pos_weight": spw,
        "verbose": -1,
    }
    model = lgb.train(params, lgb_train, num_boost_round=300,
                       valid_sets=[lgb_train], callbacks=[lgb.log_evaluation(0)])

    # 预测
    p_tr = model.predict(X_tr)
    p_te = model.predict(X_te)

    def report_split(y, p, name):
        if y.sum() == 0:
            print(f"{name}: 无正样本")
            return
        auc = roc_auc_score(y, p)
        ap = average_precision_score(y, p)
        print(f"\n=== {name} ===")
        print(f"AUC = {auc:.4f}  Average Precision = {ap:.4f}  正样本占比 {y.mean()*100:.2f}%")
        order = np.argsort(-p)
        for K in (50, 100, 200, 500):
            if K > len(y): continue
            top = order[:K]
            prec = y[top].mean() * 100
            lift = prec / (y.mean() * 100) if y.mean() > 0 else 0
            print(f"  Top {K:4}: 正样本命中 {int(y[top].sum())}/{K} = {prec:.1f}%  Lift {lift:.2f}x")

    report_split(y_tr, p_tr, "Train (2021-2023)")
    report_split(y_te, p_te, "Test  (2024-2026) [OOS]")

    # 特征重要性
    imp = pd.DataFrame({
        "feature": feat_cols,
        "gain": model.feature_importance(importance_type="gain"),
        "split": model.feature_importance(importance_type="split"),
    }).sort_values("gain", ascending=False)
    print("\n=== 特征重要性 Top 20 (by gain) ===")
    print(imp.head(20).to_string(index=False))

    # 正样本特征分布 (用户要的"向量范围")
    pos = df[df["label"] == 1]
    neg = df[df["label"] == 0]
    print(f"\n=== 正样本 (n={len(pos)}) vs 负样本 (n={len(neg)}) 特征分布 (前 15 重要特征) ===")
    top_feats = imp["feature"].head(15).tolist()
    for f in top_feats:
        p25, p50, p75 = pos[f].quantile([0.25, 0.5, 0.75])
        n25, n50, n75 = neg[f].quantile([0.25, 0.5, 0.75])
        print(f"  {f:22}  正: p25={p25:>7.3f} p50={p50:>7.3f} p75={p75:>7.3f}  负: p25={n25:>7.3f} p50={n50:>7.3f} p75={n75:>7.3f}")

    # 输出测试集 Top 预测 (OOS)
    test_out = test.copy()
    test_out["pred"] = p_te
    test_out = test_out.sort_values("pred", ascending=False)
    print("\n=== Test OOS Top 30 预测 (按 pred 降序) ===")
    disp = test_out.head(30)[["code", "name", "sig_date", "entry", "fut_60", "bench_60", "excess_60", "label", "pred"]].copy()
    disp["fut_60%"] = disp["fut_60"] * 100
    disp["excess_60%"] = disp["excess_60"] * 100
    print(disp[["code", "name", "sig_date", "entry", "fut_60%", "excess_60%", "label", "pred"]].to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    # 保存
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    all_out = df.copy()
    all_out["pred"] = np.concatenate([p_tr, p_te]) if False else np.nan  # 先全算
    # 重新按原顺序算预测
    X_all = df[feat_cols].values.astype(np.float32)
    all_out["pred"] = model.predict(X_all)
    all_out[["code", "name", "sig_date", "entry", "fut_60", "bench_60", "excess_60", "label", "pred"]].to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n全样本预测已写入 {OUT_CSV}")
    model.save_model(str(MODEL_PATH))
    print(f"模型已保存 {MODEL_PATH}")


if __name__ == "__main__":
    main()
