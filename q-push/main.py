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
    """渲染三角旗收敛信息块."""
    p = r.get("pennant") or {}
    if not p.get("detected"):
        return []
    lines = ["【三角旗收敛】"]
    start, end = p.get("pennant_start", ""), p.get("pennant_end", "")
    if start and end:
        lines[0] += f" {start} ～ {end}"
    days = p.get("days_to_apex")
    compression = p.get("compression")
    if days is not None:
        if days > 0:
            lines.append(f"- 距收敛尖端: 约 {days} 个交易日")
        elif days == 0:
            lines.append("- 今日达到收敛尖端 ⚡")
        else:
            lines.append(f"- 已穿过尖端 {abs(days)} 个交易日（随时起爆）")
    if compression is not None:
        lines.append(f"- 压缩比: {compression:.0%}（越小越收敛）")
    if p.get("near_breakout"):
        lines.append("- ⚡ 即将突破，建议建底仓观察")
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

    for key, label in [("c1_date","第1根"), ("c2_date","第2根"), ("c3_date","第3根"), ("fourth_date","第4根")]:
        if r.get(key):
            lines.append(f"- {label}: {r[key]}")

    if c3_mid:
        lines.append(f"- 第3根中值(买入参考): {c3_mid:.2f}")
    if stop_px:
        diff = round((stop_px - c3_mid) / c3_mid * 100, 1) if c3_mid else None
        lines.append(f"- 止损: {stop_px:.2f}" + (f"（{diff:+.1f}%）" if diff else ""))
    if fourth_h:
        lines.append(f"- 前高(第4根): {fourth_h:.2f}")
    if vol_ratio is not None:
        lines.append(f"- 今日量比: {vol_ratio:.2f}x")
    if cur_vs_mid is not None:
        lines.append(f"- 当前偏离中值: {cur_vs_mid:+.1f}%")
    if launch_ret:
        ld = r.get("launch_date", "")
        lines.append(f"- 起爆日: {ld} 涨幅 {launch_ret}%")

    if sig == "三红买入":
        lines.append("- ✅ 今日已回调至中值区+缩量，可建仓（止损第3根LOW）")
        lines.append("- 等待起爆日(≥8%大阳)出现后追加仓位，移止损至起爆中值")
    elif sig == "三红起爆":
        lines.append("- ⚡ 起爆日已出现！跟进加仓，止损移至起爆日中值，持3天卖出")
    else:
        lines.append("- 等待价格回调至中值区且缩量时买入")

    return lines


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

        score_str = f"score={score}" if score is not None else ""

        # 主行
        lines.append(f"**{emoji} {name} ({code})**{rating_str}")
        # 副行
        sub = []
        if score_str: sub.append(score_str)
        if concept_str: sub.append(concept_str.strip(" ·"))
        if one_liner: sub.append(one_liner)
        if sub:
            lines.append("> " + " · ".join(sub))

        # boll_support 专用详情
        if r.get("signal_label") in ("下沿开仓", "中线缩量", "上沿突破加仓", "回踩上沿"):
            for dl in _fmt_boll_detail(r):
                lines.append(f"> {dl}")

        # 三角旗收敛 (q-seed)
        for dl in _fmt_pennant(r):
            lines.append(f"> {dl}")
        # 三角旗缩量挖坑买入点
        p = r.get("pennant") or {}
        if p.get("detected") and p.get("dip_today"):
            lines.append("> 【🔔 三角旗挖坑买入点】今日缩量下跌 = 候选入场时机")
            lines.append(">   等待后续≥10%放量大涨确认起爆，出现前轻仓观察")

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
