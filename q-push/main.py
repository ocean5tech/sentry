#!/usr/bin/env python3
"""
q-push: 读 stdin JSON Lines, 抽取 simple info, POST 企业微信群机器人 webhook.

输入: q-seed / q-fin / q-news 的 JSON Lines 输出
推送: markdown 格式, 含股票代码/名称/score/⭐/一句话/dashboard 链接
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml
import urllib.request
import urllib.error

ROOT = Path(__file__).parent


def parse_args():
    ap = argparse.ArgumentParser(prog="q-push", description="推送 JSON Lines 到企业微信")
    ap.add_argument("--tag", default="信号", help="标题标签 (默认 '信号')")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="不真推, 打印 markdown")
    ap.add_argument("--no-link", action="store_true", help="不带 dashboard 链接")
    return ap.parse_args()


def load_env():
    """加载 ~/sentry/quant/.env"""
    env_path = ROOT.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def get_field(rec: dict, path: str):
    """嵌套字段读取, 例 'verdict.one_liner'"""
    cur = rec
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    return cur


def _next_trading_day(base: str) -> str:
    """返回 base (YYYY-MM-DD) 后第一个非周末交易日（简单版，不含节假日数据库）."""
    import datetime
    d = datetime.date.fromisoformat(base) + datetime.timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += datetime.timedelta(days=1)
    return d.isoformat()


def _fmt_boll_detail(r: dict) -> list[str]:
    """为 boll_support 信号生成详细交易参数块."""
    signal_label = r.get("signal_label", "")
    lower  = r.get("lower")
    upper  = r.get("upper")
    ma20   = r.get("ma20")
    cur    = r.get("close") or r.get("last_price") or r.get("current_price")
    vol_ratio = r.get("vol_ratio")
    boll_pos  = r.get("boll_pos")
    resilience = r.get("resilience")
    exchange   = r.get("exchange", "")
    scan_date  = r.get("scan_date", r.get("date", time.strftime("%Y-%m-%d")))

    if lower is None or upper is None:
        return []

    lines = [f"【{signal_label}信号】({exchange})"]

    boll_width = round((upper - lower) / ma20 * 100, 1) if ma20 else None
    lines.append(f"- 布林区间: {lower:.2f} ～ {upper:.2f}"
                 + (f"（宽度 {boll_width}%）" if boll_width else ""))
    if boll_pos is not None:
        lines.append(f"- 当前位置: {boll_pos:.1f}%（0=下轨, 100=上轨）")
    if vol_ratio is not None:
        vol_desc = "缩量" if vol_ratio < 0.8 else ("放量" if vol_ratio > 1.5 else "平量")
        lines.append(f"- 量比: {vol_ratio:.2f}x（{vol_desc}）")
    if resilience is not None:
        lines.append(f"- 抗跌超额: +{resilience:.2f}%（大盘跌时跑赢指数）")

    # 入场/止损/目标
    lines.append("【操作建议】")
    t1 = _next_trading_day(scan_date)
    if signal_label in ("下沿开仓", "中线缩量"):
        entry = round(lower * 1.01, 2) if lower else cur
        stop  = round(lower * 0.97, 2) if lower else None
        tgt   = round(upper, 2) if upper else None
        stop_pct = round((stop - entry) / entry * 100, 1) if stop and entry else None
        tgt_pct  = round((tgt  - entry) / entry * 100, 1) if tgt  and entry else None
        lines.append(f"- T+1 入场参考: {entry:.2f} 元（{t1}）")
        if stop:  lines.append(f"- 止损: {stop:.2f}（{stop_pct:+.1f}%，破下轨离场）")
        if tgt:   lines.append(f"- 目标: {tgt:.2f}（{tgt_pct:+.1f}%，均值回归至上轨）")
    elif signal_label == "回踩上沿":
        prior_high = r.get("prior_high")
        if prior_high:
            lines.append(f"- 近期高点: {prior_high:.2f}（突破后回踩确认）")
        entry = round(upper * 1.01, 2) if upper else cur  # 上轨上方1%确认入场
        stop  = round(upper * 0.97, 2) if upper else None  # 跌破上轨止损
        tgt   = round(prior_high * 1.10, 2) if prior_high else round(cur * 1.15, 2)
        stop_pct = round((stop - entry) / entry * 100, 1) if stop and entry else None
        tgt_pct  = round((tgt  - entry) / entry * 100, 1) if tgt  and entry else None
        lines.append(f"- T+1 入场参考: {entry:.2f} 元（{t1}）")
        if stop:  lines.append(f"- 止损: {stop:.2f}（{stop_pct:+.1f}%，破上轨支撑离场）")
        if tgt:   lines.append(f"- 目标: {tgt:.2f}（{tgt_pct:+.1f}%，超越前高）")
    else:  # 突破加仓
        entry = cur
        stop  = round(upper * 0.97, 2) if upper else None
        tgt   = round(cur * 1.15, 2) if cur else None
        stop_pct = round((stop - entry) / entry * 100, 1) if stop and entry else None
        tgt_pct  = round((tgt  - entry) / entry * 100, 1) if tgt  and entry else None
        lines.append(f"- T+1 入场参考: {entry:.2f} 元（{t1}，追涨需谨慎）")
        if stop:  lines.append(f"- 止损: {stop:.2f}（{stop_pct:+.1f}%，跌回上轨下方离场）")
        if tgt:   lines.append(f"- 目标: {tgt:.2f}（{tgt_pct:+.1f}%）")

    lines.append("⚠️ 仅供研究参考，不构成投资建议")
    return lines


def _fmt_pennant(r: dict) -> list[str]:
    """渲染三角旗收敛信息块（含高低点日期和价格）."""
    p = r.get("pennant") or {}
    if not p.get("detected"):
        return []
    start, end = p.get("pennant_start", ""), p.get("pennant_end", "")
    header = "【三角旗收敛】"
    if start and end:
        header += f" {start} ～ {end}"
    lines = [header]

    # 摆动高点（应逐步下降）
    sh = p.get("swing_highs", [])
    if sh:
        pts = "  →  ".join(f"{x['date']} {x['price']}" for x in sh)
        lines.append(f"- 高点序列（递降）: {pts}")

    # 摆动低点（应逐步上升）
    sl = p.get("swing_lows", [])
    if sl:
        pts = "  →  ".join(f"{x['date']} {x['price']}" for x in sl)
        lines.append(f"- 低点序列（递升）: {pts}")

    days = p.get("days_to_apex")
    compression = p.get("compression")
    if compression is not None:
        lines.append(f"- 收敛进度: {compression:.0%}（越小越紧）")
    if days is not None:
        if days > 0:
            lines.append(f"- 距尖端: 约 {days} 个交易日")
        elif days == 0:
            lines.append("- 今日达到收敛尖端 ⚡")
        else:
            lines.append(f"- 已过尖端 {abs(days)} 个交易日（随时起爆）")
    if p.get("near_breakout"):
        lines.append("- ⚡ 已临近突破窗口，轻仓候补")
    return lines


def _fmt_hongjing_detail(r: dict) -> list[str]:
    """宏景型信号推理链：形态相似度 + 三角旗 + 入场建议."""
    detail = (r.get("details") or {}).get("hongjing") or {}
    kline  = r.get("kline") or {}
    p      = r.get("pennant") or {}
    cur    = kline.get("current_price") or r.get("close")
    scan_date = r.get("scan_date", time.strftime("%Y-%m-%d"))

    lines = ["【宏景型信号】"]

    # 相似度
    dist = detail.get("dist")
    sig_date = detail.get("sig_date", "")
    if dist is not None:
        quality = "极高" if dist < 4.5 else ("高" if dist < 6.0 else "中")
        lines.append(f"- 形态相似度: {quality}（KNN距离 {dist:.2f}，越小越像宏景科技起爆前）")
    if sig_date:
        lines.append(f"- 信号触发日: {sig_date}")

    # MA20 / 近期走势
    ret5  = kline.get("ret5")
    ret20 = kline.get("ret20")
    vr    = kline.get("volume_ratio_5d_20d")
    if ret5 is not None:
        lines.append(f"- 近5日涨跌: {ret5:+.1%}，近20日: {ret20:+.1%}")
    if vr is not None:
        vol_desc = "放量" if vr > 1.3 else ("缩量" if vr < 0.8 else "平量")
        lines.append(f"- 量比(5d/20d): {vr:.2f}x（{vol_desc}）")

    # 三角旗（如检测到则展开；未检测到简单说明）
    if p.get("detected"):
        for l in _fmt_pennant(r):
            lines.append(l)
        if p.get("dip_today"):
            lines.append("- 🔔 今日缩量下跌 = 挖坑买入候选，等≥10%大阳确认起爆")
    else:
        lines.append("- 三角旗: 尚未满足对称收敛条件，持续跟踪")

    # LLM 评判（paid 模式下才有）
    v = r.get("verdict") or {}
    if v.get("one_liner") or v.get("key_risks"):
        lines.append("【LLM评判】")
        if v.get("one_liner"):
            lines.append(f"- {v['one_liner']}")
        risks = v.get("key_risks") or []
        if risks:
            lines.append(f"- 风险: {' / '.join(risks[:3])}")
        entry_sug = v.get("entry_suggestion")
        if entry_sug:
            lines.append(f"- 建议: {entry_sug}")

    # 股东人数变化（在 shareholders.holder_count 里）
    hc = (r.get("shareholders") or {}).get("holder_count") or {}
    chg = hc.get("holder_count_chg_pct")
    if chg is not None:
        cur_cnt = hc.get("holder_count_current", "?")
        hc_date = hc.get("holder_count_date", "?")
        if chg < -10:
            trend = f"↓{abs(chg):.1f}%（筹码集中，利多）"
        elif chg > 10:
            trend = f"↑{chg:.1f}%（散户涌入，需谨慎）"
        else:
            trend = f"{chg:+.1f}%（基本稳定）"
        lines.append(f"- 股东人数({hc_date}): {cur_cnt:,} 户，环比 {trend}")

    # 入场建议
    if cur:
        t1 = _next_trading_day(scan_date)
        entry = round(cur * 1.01, 2)
        stop  = round(cur * 0.92, 2)
        tgt   = round(cur * 1.40, 2)
        stop_pct = round((stop - entry) / entry * 100, 1)
        tgt_pct  = round((tgt  - entry) / entry * 100, 1)
        lines.append("【入场建议】")
        lines.append(f"- T+1 参考: {entry:.2f} 元（{t1}，待三角旗/起爆日确认）")
        lines.append(f"- 止损: {stop:.2f}（{stop_pct:+.1f}%，跌破则形态失效）")
        lines.append(f"- 目标: {tgt:.2f}（{tgt_pct:+.1f}%，宏景型历史均收益 +32~61%）")

    lines.append("⚠️ 形态相似度选股，仅供研究参考，非投资建议")
    return lines


def _fmt_three_red_bars(r: dict) -> list[str]:
    """渲染三根红棍信号块（旧版兼容）."""
    sig = r.get("signal_label", "")
    if sig not in ("三根红棍", "第四根大阳"):
        return []
    candles = r.get("big_candles") or []
    n = r.get("n_candles", len(candles))
    avg_sp = r.get("avg_spacing")
    lines = [f"【{sig}信号】({n}根大阳)"]
    for k, c in enumerate(candles, 1):
        d = c.get("date", f"{c.get('bars_ago','?')}日前")
        lines.append(f"- 第{k}根: {d} 涨幅 {c.get('ret_pct', '?')}%")
    if avg_sp:
        lines.append(f"- 平均间距: {avg_sp:.0f}个交易日")
    if sig == "第四根大阳":
        lines.append("- ⭐ 第四根大阳出现，强势买入信号")
    else:
        lines.append("- 观察中，等待第四根大阳确认")
    return lines


def _fmt_three_red_v4(r: dict) -> list[str]:
    """渲染三红买入 V4 信号块."""
    sig = r.get("signal_label", "")
    if sig not in ("三红观察", "三红买入", "三红起爆"):
        return []

    c3_mid    = r.get("c3_mid")
    stop_px   = r.get("stop_price")
    fourth_h  = r.get("fourth_high")
    vol_ratio = r.get("vol_ratio")
    cur_vs_mid = r.get("cur_vs_mid")
    launch_ret = r.get("launch_ret")

    emoji = {"三红观察": "👁", "三红买入": "🔔", "三红起爆": "⚡"}[sig]
    lines = [f"【{emoji} {sig}】"]

    # 三根阳线 + 第4根日期
    for key, label in [("c1_date","第1阳"), ("c2_date","第2阳"), ("c3_date","第3阳"), ("fourth_date","第4阳(确认)")]:
        if r.get(key):
            lines.append(f"- {label}: {r[key]}")

    if c3_mid:
        lines.append(f"- 第3阳中值(入场参考): {c3_mid:.2f}")
    if stop_px:
        diff = round((stop_px - c3_mid) / c3_mid * 100, 1) if c3_mid else None
        lines.append(f"- 初始止损(第3阳LOW): {stop_px:.2f}" + (f"（{diff:+.1f}%）" if diff else ""))
    if fourth_h:
        lines.append(f"- 第4阳高点(短期阻力): {fourth_h:.2f}")
    if vol_ratio is not None:
        lines.append(f"- 今日量比: {vol_ratio:.2f}x")
    if cur_vs_mid is not None:
        lines.append(f"- 当前偏离中值: {cur_vs_mid:+.1f}%")

    # 起爆日信息（含距今天数）
    if launch_ret:
        ld = r.get("launch_date", "")
        days_ago = r.get("launch_days_ago")
        ago_str = f"今日" if days_ago == 0 else (f"昨日" if days_ago == 1 else f"{days_ago}日前")
        lines.append(f"- ⚡ 起爆日: {ld}（{ago_str}）涨幅 +{launch_ret}%，止损移至起爆日中值")

    if sig == "三红买入":
        lines.append("- ✅ 今日在回调买入点：价格≤第3阳中值×103% + 缩量")
        lines.append("- 操作：轻仓建底，等起爆日(≥8%大阳)再加仓，止损第3阳LOW")
    elif sig == "三红起爆":
        lines.append("- ⚡ 起爆日刚出现！可跟进加仓，止损移至起爆日中值，持3天卖出")
    else:
        lines.append("- 等待价格回调至中值区且缩量时买入")

    return lines


def format_three_red_batch(hits: list[dict], scan_date: str = "", max_bytes: int = 4000) -> str:
    """将三红策略所有命中整理成一条汇总消息，控制在 max_bytes 字节内."""
    if not scan_date:
        scan_date = time.strftime("%Y-%m-%d")
    score5 = [h for h in hits if h.get("score") == 5]
    score4 = [h for h in hits if h.get("score") == 4]

    lines = [f"## ⚡ 三红买入策略 · {scan_date}", ""]

    if score5:
        lines.append(f"**🚀 三红起爆（今日/昨日起爆）** {len(score5)} 只")
        for h in score5:
            ld = h.get("launch_date", "?")
            days_ago = h.get("launch_days_ago", "?")
            ago = "今日" if days_ago == 0 else "昨日"
            lret = h.get("launch_ret", "?")
            c3 = h.get("c3_date", "?")
            stop = h.get("stop_price", "?")
            lines.append(f"- **{h.get('name','')}({h.get('code','')})** "
                         f"第3阳:{c3} 起爆:{ld}({ago})+{lret}% 止损:{stop}")
        lines.append("")

    if score4:
        lines.append(f"**🔔 三红买入点（今日回调区+缩量）** {len(score4)} 只")
        footer = ["", "操作：买入点轻仓建底止损第3阳LOW，起爆后加仓移止损至起爆中值持3天卖",
                  "⚠️ 仅供研究参考"]
        footer_bytes = len("\n".join(footer).encode("utf-8"))
        used = len("\n".join(lines).encode("utf-8"))
        budget = max_bytes - footer_bytes - used - 50

        for h in score4:
            c3 = h.get("c3_date", "?")
            fd = h.get("fourth_date", "?")
            c3m = h.get("c3_mid")
            stop = h.get("stop_price", "?")
            vs = h.get("cur_vs_mid")
            vr = h.get("vol_ratio")
            mid_str = f"中:{c3m:.2f} " if c3m else ""
            vs_str = f"{vs:+.0f}% " if vs is not None else ""
            vr_str = f"量:{vr:.1f}x" if vr is not None else ""
            row = (f"- **{h.get('name','')}({h.get('code','')})** "
                   f"第3阳:{c3} 第4:{fd} {mid_str}{vs_str}{vr_str} 止:{stop}")
            row_bytes = len(row.encode("utf-8"))
            if budget - row_bytes < 0:
                lines.append(f"  ...（仅列部分，共{len(score4)}只）")
                break
            lines.append(row)
            budget -= row_bytes + 1  # +1 for newline
        lines.append("")

    lines.append("操作：买入点轻仓建底止损第3阳LOW，起爆后加仓移止损至起爆中值持3天卖")
    lines.append("⚠️ 仅供研究参考")
    return "\n".join(lines)


def format_markdown(records: list[dict], tag: str, cfg: dict, include_link: bool) -> str:
    """records → 企业微信 markdown 文本."""
    out_cfg = cfg.get("output", {})
    max_n = out_cfg.get("max_records_per_msg", 5)
    emojis = out_cfg.get("emoji_rank", ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"])
    truncate = out_cfg.get("truncate_one_liner", 30)

    records = records[:max_n]
    today = records[0].get("scan_date", time.strftime("%Y-%m-%d"))
    source = records[0].get("source", "q-?")

    lines = [f"## 🌟 {source} · {tag} ({today})", ""]

    for i, r in enumerate(records):
        rank = r.get("rank", i + 1)
        emoji = emojis[i] if i < len(emojis) else f"{rank}."
        code = r.get("code") or "-"
        name = r.get("name") or ""
        score = r.get("score")

        # ⭐ 评级
        stars = get_field(r, "verdict.stars") or ""
        rating = get_field(r, "verdict.rating")
        rating_str = f" {stars}" if stars else (f" ⭐ x{rating}" if rating else "")

        # 一句话
        one_liner = (get_field(r, "verdict.one_liner") or "")[:truncate]

        # 概念 (q-news)
        concept = get_field(r, "concept_status.concept")
        started = get_field(r, "concept_status.已启动")
        concept_str = ""
        if concept:
            mark = "🔴" if started else ("🟢" if started is False else "⚪")
            concept_str = f" · {concept} {mark}"

        # 主行
        lines.append(f"**{emoji} {name} ({code})**{rating_str}")
        # 副行 (不显示原始 score 数字，投资者看不懂)
        sub = []
        if concept_str: sub.append(concept_str.strip(" ·"))
        if one_liner: sub.append(one_liner)
        if sub:
            lines.append("> " + " · ".join(sub))

        # boll_support 专用详情
        if r.get("signal_label") in ("下沿开仓", "中线缩量", "上沿突破加仓", "回踩上沿"):
            for dl in _fmt_boll_detail(r):
                lines.append(f"> {dl}")

        # 宏景型选股详情（q-seed 来源）
        elif r.get("source") == "q-seed" and r.get("details", {}).get("hongjing") is not None:
            for dl in _fmt_hongjing_detail(r):
                lines.append(f"> {dl}")

        # 三根红棍信号 (旧版)
        for dl in _fmt_three_red_bars(r):
            lines.append(f"> {dl}")

        # 三红买入 V4
        for dl in _fmt_three_red_v4(r):
            lines.append(f"> {dl}")

        # 重组/注资分析（有则展示）
        v   = r.get("verdict") or {}
        ma  = r.get("ma_research") or {}
        acq_name = v.get("acquirer_name") or ma.get("acquirer")
        acq_bg   = v.get("acquirer_background") or ma.get("acquirer_background")
        restr    = v.get("restructure_direction") or ma.get("deal_direction")
        tgt_co   = ma.get("target_company")
        tgt_biz  = ma.get("target_business")
        deal_str = ma.get("deal_structure")
        scenario = ma.get("scenario")
        conf     = ma.get("confidence")

        if acq_name or tgt_co or restr:
            lines.append("> 【重组/注资调查】")
            if scenario and scenario != "不明":
                lines.append(f"> - 模式: {scenario}{'（'+conf+'置信度）' if conf else ''}")
            if tgt_co:   lines.append(f"> - 收购标的: {tgt_co}")
            if tgt_biz:  lines.append(f"> - 标的业务: {tgt_biz}")
            if deal_str: lines.append(f"> - 交易方式: {deal_str}")
            if acq_name: lines.append(f"> - 入主方: {acq_name}")
            if acq_bg:   lines.append(f"> - 入主方背景: {acq_bg}")
            if restr:    lines.append(f"> - 重组方向: {restr}")

        lines.append("")

    # dashboard 链接
    if include_link:
        url = cfg.get("dashboard", {}).get("base_url", "http://localhost:8501")
        q = cfg.get("dashboard", {}).get("date_query", "?date={scan_date}").format(scan_date=today)
        lines.append(f"[查看 dashboard]({url}{q})")

    return "\n".join(lines)


def post_webhook(webhook_url: str, msg: str, msg_type: str = "markdown",
                 retry_times: int = 2, timeout: int = 10) -> dict:
    """POST 企业微信群机器人. 返回 {ok, errcode, errmsg}."""
    if msg_type == "markdown":
        body = {"msgtype": "markdown", "markdown": {"content": msg}}
    else:
        body = {"msgtype": "text", "text": {"content": msg}}

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    last_err = None
    for attempt in range(retry_times + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_body = json.loads(resp.read().decode("utf-8"))
                return {"ok": resp_body.get("errcode") == 0, **resp_body}
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            if attempt < retry_times:
                time.sleep(1)
                continue
    return {"ok": False, "errcode": -1, "errmsg": str(last_err)}


def main():
    args = parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    # 读 stdin
    if sys.stdin.isatty():
        print("ERROR: 需要 stdin (e.g. q-fin --top 5 | q-push)", file=sys.stderr)
        sys.exit(2)

    records = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"WARN: invalid JSON: {e}", file=sys.stderr)

    if not records:
        print("WARN: stdin 无有效 records, 不推送", file=sys.stderr)
        sys.exit(0)

    include_link = cfg.get("output", {}).get("include_link", True) and not args.no_link
    msg = format_markdown(records, args.tag, cfg, include_link)

    if args.dry_run:
        print("=== q-push DRY-RUN ===")
        print(msg)
        print(f"\n(共 {len(records)} 条, 推送前 {min(len(records), cfg.get('output', {}).get('max_records_per_msg', 5))} 条)")
        return

    # 加载 env, 取 webhook
    load_env()
    env_var = cfg.get("wechat", {}).get("webhook_url_env", "WECHAT_WEBHOOK_URL")
    webhook = os.environ.get(env_var)
    if not webhook:
        print(f"ERROR: env {env_var} 未设, 请检查 ~/sentry/quant/.env", file=sys.stderr)
        sys.exit(1)

    msg_type = cfg.get("wechat", {}).get("msg_type", "markdown")
    retry = cfg.get("wechat", {}).get("retry_times", 2)
    timeout = cfg.get("wechat", {}).get("timeout_seconds", 10)

    print(f"[q-push] 推送 {len(records)} 条 (前 {min(len(records), 5)} 入消息) → 企业微信...", file=sys.stderr)
    result = post_webhook(webhook, msg, msg_type, retry, timeout)
    if result.get("ok"):
        print(f"[q-push] ✅ 推送成功", file=sys.stderr)
    else:
        print(f"[q-push] ❌ 推送失败: {result.get('errmsg')} (errcode={result.get('errcode')})", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-push] interrupted", file=sys.stderr); sys.exit(130)
