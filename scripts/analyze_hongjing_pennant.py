#!/usr/bin/env python3
"""
对宏景 q-seed TOP 候选股逐一做三角旗深度验证.
输出: 起止日期、高低点序列、MA20走势、时长、是否符合宏景风格.
"""
import sys, json
import numpy as np
ROOT = __file__
import os; ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.data_loader import load_daily

SWING_WIN = 2
WINDOW_SIZES = [22, 28, 35, 45, 60]  # trading days


def local_highs(H, w=SWING_WIN):
    return [i for i in range(w, len(H) - w) if H[i] >= max(H[i - w: i + w + 1])]


def local_lows(L, w=SWING_WIN):
    return [i for i in range(w, len(L) - w) if L[i] <= min(L[i - w: i + w + 1])]


def ma20_trend(C_arr):
    n = len(C_arr)
    ma = np.array([np.mean(C_arr[max(0, i - 19): i + 1]) for i in range(n)])
    slope = float(np.polyfit(np.arange(n, dtype=float), ma, 1)[0]) / (np.mean(ma) + 1e-9) * 100
    rising = sum(1 for i in range(1, n) if ma[i] >= ma[i - 1])
    return round(slope, 4), round(rising / max(n - 1, 1), 2), ma


def lows_monotone(sl_prices):
    if len(sl_prices) < 2:
        return "N/A", 0.0
    rises = sum(1 for i in range(1, len(sl_prices)) if sl_prices[i] > sl_prices[i - 1])
    ratio = rises / (len(sl_prices) - 1)
    if ratio >= 1.0:
        label = "单调上升✅"
    elif ratio >= 0.67:
        label = "基本上升⚠️"
    else:
        label = "不规则❌"
    return label, ratio


def analyze(code, name, rank):
    df = load_daily(code)
    if df is None or len(df) < 50:
        return f"#{rank:2d} {code} {name}: 数据不足\n"

    result_lines = []
    found = None

    for win in WINDOW_SIZES:
        if len(df) < win + 5:
            continue
        sub = df.tail(win).reset_index(drop=True)
        H = sub["high"].values.astype(float)
        L = sub["low"].values.astype(float)
        C = sub["close"].values.astype(float)
        dates_str = sub["date"].dt.strftime("%Y-%m-%d").values
        n = len(sub)

        sh = local_highs(H)
        sl = local_lows(L)
        if len(sh) < 2 or len(sl) < 2:
            continue

        coef_h = np.polyfit(sh, H[sh], 1)
        coef_l = np.polyfit(sl, L[sl], 1)
        s_h, s_l = float(coef_h[0]), float(coef_l[0])
        if s_h >= 0 or s_l <= 0:
            continue

        denom = s_h - s_l
        if abs(denom) < 1e-10:
            continue
        apex_x = (coef_l[1] - coef_h[1]) / denom
        days_to_apex = int(round(apex_x - (n - 1)))
        if days_to_apex < -10 or days_to_apex > 25:
            continue

        mid = float(np.mean(C[-5:]))
        w0 = (float(np.polyval(coef_h, 0)) - float(np.polyval(coef_l, 0))) / mid
        wn = (float(np.polyval(coef_h, n - 1)) - float(np.polyval(coef_l, n - 1))) / mid
        if w0 <= 0 or wn <= 0 or wn >= w0:
            continue

        # 通道内: 当前价不超出上沿8%，不低于下沿8%
        cur = float(C[-1])
        upper_now = float(np.polyval(coef_h, n - 1))
        lower_now = float(np.polyval(coef_l, n - 1))
        if cur > upper_now * 1.08 or cur < lower_now * 0.92:
            continue

        found = dict(
            win=win, sh=sh, sl=sl, H=H, L=L, C=C,
            dates=dates_str, s_h=s_h, s_l=s_l,
            days_to_apex=days_to_apex, compression=wn / w0,
        )
        break

    if not found:
        return f"#{rank:2d} {code} {name}: ❌ 无三角旗（或已起爆/已跌破）\n"

    dates = found["dates"]
    H, L, C = found["H"], found["L"], found["C"]
    sh, sl = found["sh"], found["sl"]
    win = found["win"]
    apex = found["days_to_apex"]
    comp = found["compression"]
    s_h = found["s_h"]

    start_dt, end_dt = dates[0], dates[-1]
    dur_ok = 20 <= win <= 65

    # MA20
    ma_slope, ma_mono, _ = ma20_trend(C)
    ma_ok = ma_slope > 0 and ma_mono >= 0.6

    # 高低点序列
    sh_pts = [(dates[i], round(H[i], 2)) for i in sh[:5]]
    sl_pts = [(dates[i], round(L[i], 2)) for i in sl[:5]]
    low_label, low_ratio = lows_monotone([p for _, p in sl_pts])
    low_ok = low_ratio >= 0.67

    is_hongjing = dur_ok and ma_ok and low_ok
    overall = "✅ 符合宏景风格" if is_hongjing else "⚠️ 不完全符合宏景风格"

    result_lines.append(f"#{rank:2d} {code} {name}")
    result_lines.append(
        f"    三角旗期间: {start_dt} → {end_dt}  ({win}个交易日)  "
        f"{'✅时长合理' if dur_ok else '❌时长不合(需20-65天)'}"
    )
    result_lines.append(f"    起点收盘: {C[0]:.2f}   末点收盘: {C[-1]:.2f}")

    # 高点序列
    sh_str = "  →  ".join(f"{d[5:]} {p}" for d, p in sh_pts)
    result_lines.append(f"    高点序列↓: {sh_str}")

    # 低点序列
    sl_str = "  →  ".join(f"{d[5:]} {p}" for d, p in sl_pts)
    result_lines.append(f"    低点序列:  {sl_str}  [{low_label}]")

    result_lines.append(
        f"    MA20走势:  slope={ma_slope:+.4f}%/bar  单调比={ma_mono:.0%}  "
        f"{'✅稳健上升' if ma_ok else '❌杂乱/下降'}"
    )
    result_lines.append(
        f"    收敛进度:  apex={apex:+d}天  压缩={comp:.0%}  "
        f"{'⚡近期起爆' if abs(apex) <= 15 else '观察中'}"
    )
    result_lines.append(f"    综合评估: {overall}")
    result_lines.append("")
    return "\n".join(result_lines)


def main():
    log_path = os.path.join(ROOT, "q-seed", "logs", "q-seed_top20_20260502_0915.jsonl")
    if not os.path.exists(log_path):
        print(f"ERROR: {log_path} not found"); return

    with open(log_path) as f:
        recs = [json.loads(l) for l in f if l.strip()]

    print("宏景候选股 三角旗深度验证（含MA20+低点单调+时长约束）")
    print("=" * 70)
    print()
    for r in recs:
        print(analyze(r["code"], r["name"], r["rank"]))


if __name__ == "__main__":
    main()
