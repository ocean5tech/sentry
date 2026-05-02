"""单股深度分析报告生成（技术信号 + q-fin基本面 + DeepSeek分析师）"""
import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _llm(system: str, user: str, max_tokens: int = 800) -> str:
    import urllib.request
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return "(LLM不可用：未配置DEEPSEEK_API_KEY)"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.1,   # 低温度，减少幻觉
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def _run_qfin(code: str, name: str, close: float) -> dict:
    rec = {"code": code, "name": name, "date": str(date.today()),
           "score": 0, "close": round(close, 2)}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp_in = f.name
    tmp_out = tmp_in.replace(".jsonl", "_out.jsonl")
    try:
        subprocess.run(
            [str(ROOT / "q-fin/.venv/bin/python"), str(ROOT / "q-fin/main.py"),
             "--paid", "--input", tmp_in, "--format", "jsonl", "--output", tmp_out],
            timeout=300, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if os.path.exists(tmp_out):
            for line in open(tmp_out):
                r = json.loads(line)
                if r.get("code") == code:
                    return r
    except Exception:
        pass
    finally:
        for p in (tmp_in, tmp_out):
            try: os.unlink(p)
            except: pass
    return {}


def _scan_signals(df, code: str) -> list[str]:
    """扫描所有策略信号，返回描述列表."""
    _init_tdx()
    signals = []
    try:
        # 三红买入策略（在主 core/strategies 里）
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location(
            "three_red_bars_v4",
            str(ROOT / "core" / "strategies" / "three_red_bars_v4.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.scan(df, symbol=code)
        if r and r.get("score", 0) >= 4:
            c3   = r.get("c3_date", "")
            ld   = r.get("launch_date", "")
            lret = r.get("launch_ret", "")
            stop = r.get("stop_price", "")
            c3mid = r.get("c3_mid", "")
            days4 = r.get("days_since_4th", "")
            if r.get("score") == 5:
                signals.append(
                    f"⚡【三红起爆】第3阳={c3}，起爆日={ld}(+{lret}%)，"
                    f"止损={stop}，起爆后持3天卖出"
                )
            else:
                signals.append(
                    f"🔔【三红买入点】第3阳={c3}，第4阳后{days4}天，"
                    f"今日回调至中值{c3mid}附近，止损={stop}"
                )
    except Exception:
        pass

    try:
        # 三角旗收敛（宏景型）
        from pennant import detect_pennant, is_dip_day
        p = detect_pennant(df)
        if p.get("detected"):
            start = p.get("pennant_start", "")
            end   = p.get("pennant_end", "")
            comp  = p.get("compression", 0)
            days  = p.get("days_to_apex", "?")
            dip   = is_dip_day(df)
            sh    = " → ".join(f"{x['date']}@{x['price']}" for x in p.get("swing_highs", [])[:3])
            sl    = " → ".join(f"{x['date']}@{x['price']}" for x in p.get("swing_lows", [])[:3])
            dip_str = "，今日缩量下跌=挖坑买入候选" if dip else ""
            signals.append(
                f"📐【三角旗收敛】{start}~{end}，压缩{comp:.0%}，距尖端{days}天{dip_str}\n"
                f"  高点↓: {sh}\n  低点↑: {sl}"
            )
    except Exception:
        pass

    try:
        # 科创/创业板平台突破
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "kechuang_breakout",
            str(ROOT / "core" / "strategies" / "kechuang_breakout.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.scan(df, symbol=code)
        if r:
            signals.append(
                f"🚀【科创平台突破】评分{r.get('score','')}，"
                f"振幅{r.get('platform_amp','')}%，量比{r.get('vol_ratio','')}x"
            )
    except Exception:
        pass

    try:
        # 布林带信号
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "boll_support",
            str(ROOT / "core" / "strategies" / "boll_support.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        r = mod.scan(df, symbol=code)
        if r and r.get("score", 0) >= 3:
            signals.append(
                f"📊【布林带{r.get('signal_label','')}】"
                f"下轨{r.get('lower','')}，上轨{r.get('upper','')}，"
                f"当前位置{r.get('boll_pos','')}%"
            )
    except Exception:
        pass

    return signals


def _init_tdx():
    """初始化 TDX 数据路径."""
    import sys, yaml
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "q-seed" / "lib"))
    import core.tdx_loader as tdx_loader
    cfg_path = ROOT / "q-seed" / "config.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text())
        tdx_dir = (cfg.get("data") or {}).get("tdx_dir", str(ROOT / "data" / "tdx"))
    else:
        tdx_dir = str(ROOT / "data" / "tdx")
    tdx_loader.set_tdx_dir(tdx_dir)


def generate(code: str) -> str:
    """主入口：返回分析师报告字符串"""
    import sys
    _init_tdx()
    from core.data_loader import load_daily
    from core.stock_names import get_name

    df = load_daily(code)
    if df is None or df.empty:
        return f"找不到 {code} 的行情数据，请确认代码正确。"

    name = get_name(code) or code
    close_arr = df["close"].values.astype(float)
    high_arr  = df["high"].values.astype(float)
    low_arr   = df["low"].values.astype(float)
    vol_arr   = df["volume"].values.astype(float)
    price = float(close_arr[-1])

    def ret(n):
        return (close_arr[-1] / close_arr[-n - 1] - 1) * 100 if len(close_arr) > n else 0

    # MA20
    import numpy as np
    ma20 = float(np.mean(close_arr[-20:])) if len(close_arr) >= 20 else price
    ma20_dev = (price / ma20 - 1) * 100
    # 近20日量比
    vol_ratio_5_20 = float(np.mean(vol_arr[-5:]) / np.mean(vol_arr[-20:])) if len(vol_arr) >= 20 else 1.0
    # 近60日最高最低
    high60 = float(np.max(high_arr[-60:])) if len(high_arr) >= 60 else float(np.max(high_arr))
    low60  = float(np.min(low_arr[-60:]))  if len(low_arr)  >= 60 else float(np.min(low_arr))

    price_ctx = (
        f"股票：{name}（{code}）\n"
        f"现价：{price:.2f} 元\n"
        f"涨幅：5日{ret(5):+.1f}% / 20日{ret(20):+.1f}% / 60日{ret(60):+.1f}%\n"
        f"MA20：{ma20:.2f}（偏离{ma20_dev:+.1f}%）\n"
        f"近5/20日量比：{vol_ratio_5_20:.2f}x\n"
        f"60日区间：{low60:.2f} ~ {high60:.2f}"
    )

    # 技术信号扫描（最重要的上下文）
    signals = _scan_signals(df, code)

    # q-fin 基本面（异步运行，3分钟超时）
    qf = _run_qfin(code, name, price)
    verdict = qf.get("verdict") or {}
    fund    = qf.get("fundamentals") or {}
    anns    = qf.get("announcements_90d") or {}
    holders = qf.get("shareholders") or {}
    hc      = holders.get("holder_count") or {}

    # 基本面摘要
    fin_lines = []
    if fund.get("industry"):
        fin_lines.append(f"行业：{fund['industry']}")
    if fund.get("yoy_net_profit") is not None:
        fin_lines.append(f"净利润同比：{fund['yoy_net_profit']:+.1f}%  营收同比：{fund.get('yoy_revenue',0):+.1f}%")
    if fund.get("eps") is not None:
        fin_lines.append(f"EPS：{fund['eps']:.3f}  市值：{fund.get('market_cap_yi',0):.1f}亿")
    if hc.get("holder_count_chg_pct") is not None:
        chg = hc["holder_count_chg_pct"]
        trend = "↓筹码集中" if chg < -10 else ("↑散户涌入" if chg > 10 else "稳定")
        fin_lines.append(f"股东人数({hc.get('holder_count_date','')})：{hc.get('holder_count_current',0):,}户，"
                         f"环比{chg:+.1f}%({trend})")

    # 公告
    ann_titles = [t["title"][:30] for t in anns.get("key_titles", [])[:3]]
    risk_flags = anns.get("risk_flags", [])
    if ann_titles:
        fin_lines.append(f"近期公告：{'；'.join(ann_titles)}")
    if risk_flags:
        fin_lines.append(f"风险提示：{'、'.join(risk_flags)}")

    # 主要股东
    top_holders = (holders.get("top10_free") or [])[:3]
    if top_holders:
        holder_str = "；".join(f"{h['name'][:8]}{h.get('pct',0):.1f}%" for h in top_holders if h.get("name"))
        fin_lines.append(f"前三股东：{holder_str}")

    # q-fin DeepSeek 初判（仅作参考，不作为最终结论）
    if verdict.get("one_liner"):
        fin_lines.append(f"q-fin初判（{verdict.get('stars','')}）：{verdict['one_liner']}")

    # 组合完整上下文
    context_parts = [price_ctx]
    if signals:
        context_parts.append("\n【量化系统触发信号】（用户询问该股的原因）")
        context_parts.extend(signals)
    else:
        context_parts.append("\n【量化系统】本股未触发特定买入信号")
    if fin_lines:
        context_parts.append("\n【基本面数据】")
        context_parts.extend(fin_lines)

    context = "\n".join(context_parts)

    system = """你是一位专注A股的量化+技术面分析师，同时参考基本面。

分析原则：
1. 用户询问此股，通常是因为量化系统检测到技术信号（三红买入/三角旗/平台突破等），优先解读该信号的意义
2. 基本面数据（营收、净利润等）是辅助参考，短线技术策略不强求基本面完美
3. kline已涨幅大不自动等于"卖出"——需结合技术形态判断是突破还是已见顶
4. 给出明确可操作的建议，不要模棱两可

输出格式（严格遵守，每项独立一行）：

【结论】买入 / 持有 / 观望 / 卖出（四选一）
【信号解读】（解释量化信号的含义和可信度，1-2句）
【核心逻辑】（2-3句，结合技术+基本面说最关键的理由）
【最大风险】（1-2条，具体说明）
【操作建议】（入场价位 / 止损位 / 目标位 或 "暂不介入"）
【综合评分】★★★☆☆（1-5星）

总字数不超过320字，直接输出，不要前言。"""

    report = _llm(system, f"请分析以下股票：\n\n{context}")
    disclaimer = "\n⚠️ 仅供参考，不构成投资建议。请独立判断，严格止损。"
    return f"=== {name}（{code}）分析报告 ===\n\n{report}{disclaimer}"
