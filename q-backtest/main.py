#!/usr/bin/env python3
"""
q-backtest: walk-forward 回测 q-seed 候选的后续表现.

流程:
  1. 给定 [start, end] 日期范围
  2. 对每个交易日 as_of_date:
     a. 调 q-seed --as-of-date <date> --top N (限制 K 线 < as_of_date)
     b. 对每个候选, T+1 close 入场, T+1+hold_days close 退出
     c. 算 ret / max_drawdown / win / good_experience
  3. 按 template 拆分统计输出
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from lib.trade_calendar import get_trading_days, trading_days_between
from lib.forward_eval import evaluate, ForwardResult


def parse_args():
    ap = argparse.ArgumentParser(prog="q-backtest", description="walk-forward 回测 q-seed")
    ap.add_argument("--start", default=None, help="回测起始日期 YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="回测结束日期 YYYY-MM-DD")
    ap.add_argument("--hold-days", type=int, default=None,
                    help="T+1 持仓天数 (默认 config.defaults.hold_days=20)")
    ap.add_argument("--win-pct", type=float, default=None,
                    help="胜利阈值 (默认 0.05 即 +5%)")
    ap.add_argument("--top", type=int, default=None,
                    help="每个 as_of_date 每模板取 top N 候选 (默认 5)")
    ap.add_argument("--templates", default=None,
                    help="逗号分隔模板列表 (默认全部 6 个)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--step", type=int, default=5,
                    help="跳过 N 个交易日做一次 backtest (默认 5, 每周一次)")
    ap.add_argument("--output", default=None, help="输出 jsonl (默认 logs/q-backtest_<ts>.jsonl)")
    ap.add_argument("--quiet", action="store_true", help="少打印")
    return ap.parse_args()


def log(msg, quiet=False):
    if not quiet:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True, file=sys.stderr)


def run_qseed_all_templates(q_seed_cmd: str, as_of_date: date, top_total: int,
                              timeout: int = 1800) -> list[dict]:
    """调 q-seed 一次, --template both 跑所有 6 模板, 返回全部候选."""
    cmd = [q_seed_cmd,
           "--as-of-date", as_of_date.isoformat(),
           "--template", "both",
           "--top", str(top_total)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def group_by_template(candidates: list[dict], templates: list[str], top_per_template: int) -> dict[str, list[dict]]:
    """按 details.<template>.dist 对每模板独立排序取 top N."""
    out: dict[str, list[dict]] = {t: [] for t in templates}
    for cand in candidates:
        details = cand.get("details", {}) or {}
        for tname in templates:
            t_det = details.get(tname)
            if not t_det:
                continue
            d = t_det.get("dist")
            if d is None:
                continue
            row = dict(cand)
            row["_template_dist"] = float(d)
            row["_template"] = tname
            out[tname].append(row)
    # 按模板内 dist 升序, 取 top N
    for tname in templates:
        out[tname].sort(key=lambda r: r["_template_dist"])
        out[tname] = out[tname][:top_per_template]
    return out


def main():
    args = parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    defaults = cfg.get("defaults", {})
    hold_days = args.hold_days or defaults.get("hold_days", 20)
    win_pct = args.win_pct if args.win_pct is not None else defaults.get("win_pct", 0.05)
    top = args.top or defaults.get("top_per_template", 5)
    templates = (args.templates.split(",") if args.templates
                 else defaults.get("templates", ["hongjing", "litong"]))
    start_str = args.start or defaults.get("start_date", "2025-08-01")
    end_str = args.end or defaults.get("end_date", "2026-04-22")

    start_d = date.fromisoformat(start_str)
    end_d = date.fromisoformat(end_str)

    log(f"=== q-backtest ===")
    log(f"窗口: {start_d} → {end_d}")
    log(f"持仓: {hold_days} 交易日 / 胜利: ret >= {win_pct*100:.1f}%")
    log(f"模板: {templates}")
    log(f"step: {args.step} (每 {args.step} 日 backtest 一次)")

    # 加载交易日历
    sse_path = cfg["data"]["tdx_dir"] + "/sh/lday/sh000001.day"
    all_days = get_trading_days(sse_path)
    log(f"交易日历加载完成: {all_days[0]} ~ {all_days[-1]} ({len(all_days)} 天)")

    bt_days = trading_days_between(all_days, start_d, end_d)
    bt_days = bt_days[::args.step]    # 每 step 日抽 1 天
    log(f"将跑 {len(bt_days)} 个 as_of_date (按 step={args.step})")

    # 输出文件
    out_dir = ROOT / cfg.get("output", {}).get("jsonl_dir", "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(args.output) if args.output else (out_dir / f"q-backtest_{ts}.jsonl")

    tdx_dir = cfg["data"]["tdx_dir"]
    q_seed_cmd = cfg["q_seed_command"]

    all_results = []
    template_stats: dict[str, list] = {t: [] for t in templates}

    # 计算 q-seed --top 总量: 每模板 N 个, 6 模板 → top 6N + buffer
    qseed_total_top = max(top * len(templates) * 2, 30)

    for i, as_of_date in enumerate(bt_days, 1):
        log(f"({i}/{len(bt_days)}) {as_of_date}: q-seed --top {qseed_total_top} --template both")
        all_candidates = run_qseed_all_templates(q_seed_cmd, as_of_date, qseed_total_top)
        log(f"   q-seed 输出 {len(all_candidates)} 候选, 按模板分组取 top {top}")
        per_tpl = group_by_template(all_candidates, templates, top)

        for tname in templates:
            for cand in per_tpl[tname]:
                code = cand.get("code")
                if not code:
                    continue
                fr = evaluate(code, as_of_date, hold_days, win_pct, tdx_dir)
                rec = {
                    "as_of_date": as_of_date.isoformat(),
                    "template": tname,
                    "code": code,
                    "name": cand.get("name", ""),
                    "template_dist": round(cand.get("_template_dist", 0), 3),
                    "score": cand.get("score"),
                    "qseed_rank_global": cand.get("rank"),
                    # forward eval
                    "skipped_reason": fr.skipped_reason,
                    "entry_date": fr.entry_date,
                    "entry_price": fr.entry_price,
                    "exit_date": fr.exit_date,
                    "exit_price": fr.exit_price,
                    "ret": fr.ret,
                    "max_drawdown": fr.max_drawdown,
                    "min_low_during": fr.min_low_during,
                    "max_close_during": fr.max_close_during,
                    "win": fr.win,
                    "good_experience": fr.good_experience,
                }
                all_results.append(rec)
                if fr.skipped_reason is None:
                    template_stats[tname].append(rec)

    # 写 jsonl
    with open(out_path, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    log(f"jsonl 已写: {out_path} ({len(all_results)} 条)")

    # 统计报告
    print()
    print("=" * 80)
    print(f"q-backtest 报告 ({start_d} ~ {end_d})")
    print(f"持仓 {hold_days}d, 胜利 +{win_pct*100:.0f}%, 模板 {','.join(templates)}")
    print("=" * 80)

    grand_total = 0
    grand_win = 0
    grand_good = 0

    for tname in templates:
        recs = template_stats[tname]
        if not recs:
            print(f"\n{tname}: 0 个有效信号")
            continue
        total = len(recs)
        wins = sum(1 for r in recs if r["win"])
        goods = sum(1 for r in recs if r["good_experience"])
        avg_ret = sum(r["ret"] for r in recs) / total * 100
        avg_dd = sum(r["max_drawdown"] for r in recs) / total * 100
        skipped_one_word = sum(1 for r in all_results if r["template"] == tname and r["skipped_reason"] == "limit_up_no_fill")

        print(f"\n{tname}:")
        print(f"  样本: {total} (一字板 skip {skipped_one_word})")
        print(f"  胜率 (ret >= {win_pct*100:.0f}%):                    {wins}/{total} = {wins/total*100:.1f}%")
        print(f"  持仓体验好 (胜 + 持仓最低 >= 入场):    {goods}/{total} = {goods/total*100:.1f}%  ⭐")
        print(f"  平均收益: {avg_ret:+.2f}%")
        print(f"  平均最大回撤: {avg_dd:+.2f}%")

        # top 3 winners
        recs_by_ret = sorted(recs, key=lambda r: r["ret"], reverse=True)[:3]
        print(f"  Top 3:", end="")
        for r in recs_by_ret:
            print(f" {r['code']}({r['ret']*100:+.1f}%)", end="")
        print()

        grand_total += total
        grand_win += wins
        grand_good += goods

    print(f"\n{'=' * 80}")
    if grand_total > 0:
        print(f"总: 样本 {grand_total} / 胜 {grand_win} ({grand_win/grand_total*100:.1f}%) / 持仓体验好 {grand_good} ({grand_good/grand_total*100:.1f}%)")
    print(f"详细 jsonl: {out_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-backtest] interrupted", file=sys.stderr); sys.exit(130)
