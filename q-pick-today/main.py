#!/usr/bin/env python3
"""
q-pick-today: 一键当日选股 + q-fin 深度推理 + 企业微信推送

流程:
  1. (可选) rsync Windows 端今日数据
  2. q-backtest-fast 单日跑 → 得 5 个模板 top 候选 (litong 已停用)
  3. 过滤 sig_date == today (真当日起爆)
  4. 评级 ⭐, 选 ⭐⭐⭐⭐+ 跑 q-fin --paid --max-depth 3 (递归调研入主方)
  5. 每股一条消息推企业微信 (含推理链 + 风险提示)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).parent


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True, file=sys.stderr)


def parse_args():
    ap = argparse.ArgumentParser(prog="q-pick-today",
                                 description="一键当日选股 + q-fin 推理 + 企业微信推送")
    ap.add_argument("--date", default=None, help="目标交易日 YYYY-MM-DD (默认今天)")
    ap.add_argument("--skip-sync", action="store_true", help="跳过 rsync (假设 data/tdx 已最新)")
    ap.add_argument("--skip-fin", action="store_true", help="跳过 q-fin 深度调研 (省 token)")
    ap.add_argument("--skip-push", action="store_true", help="跳过推送, 仅打印")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--budget", type=float, default=None, help="覆盖每日总预算 USD")
    return ap.parse_args()


def rate_stars(dist: float, win80: float, thresholds: list) -> int:
    """根据 dist + win80 决定 ⭐ 评级."""
    for t in thresholds:
        if dist <= t["max_dist"] and win80 >= t["min_win80"]:
            return t["min_stars"]
    return 1


def run_qbacktest(cmd: str, target_date: str, templates: list) -> list[dict]:
    """跑 q-backtest-fast 单日, 返回 jsonl records (含全模板 top 5)."""
    tpl_str = ",".join(templates)
    log(f"q-backtest-fast --start {target_date} --end {target_date} --templates {tpl_str}")
    proc = subprocess.run(
        [cmd, "--start", target_date, "--end", target_date, "--step", "1",
         "--top", "5", "--templates", tpl_str, "--hold-days", "5"],
        capture_output=True, text=True, timeout=1200,
    )
    if proc.returncode != 0:
        log(f"q-backtest-fast failed: {proc.stderr[-500:]}")
        return []

    # 找最新生成的 jsonl
    logs_dir = Path("/home/wyatt/sentry/quant/q-backtest/logs")
    files = sorted(logs_dir.glob("q-backtest-fast_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        log("no q-backtest jsonl output")
        return []
    return [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines() if l.strip()]


def run_qfin_research(qfin_cmd: str, code: str, max_depth: int, budget: float) -> dict | None:
    """调 q-fin --paid --max-depth N 对单股做深度调研, 返回输出 dict."""
    log(f"  q-fin --paid --max-depth {max_depth} for {code} (budget ${budget:.2f})")
    proc = subprocess.run(
        [qfin_cmd, "--paid", f"--max-depth", str(max_depth),
         "--top", "1", "--budget", str(budget)],
        input=json.dumps({"code": code}) + "\n",
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        log(f"    q-fin failed: {proc.stderr[-300:]}")
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line: continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def format_chain(chain: dict, prefix: str = "  ") -> list[str]:
    """递归格式化 entity_research chain 为 ASCII 树状文本."""
    lines = []
    if not chain: return lines
    name = chain.get("entity", "?")
    identity = (chain.get("identity") or "")[:35]
    business = (chain.get("business") or "")[:55]
    src = chain.get("_source", "")
    src_mark = "" if src == "llm" else " (hints)" if src == "hints" else f" ({src})"
    lines.append(f"{prefix}└─ **{name}** {src_mark}")
    if identity:
        lines.append(f"{prefix}   {identity}")
    if business:
        lines.append(f"{prefix}   _{business}_")
    sub = chain.get("sub") or []
    for i, s in enumerate(sub):
        new_prefix = prefix + ("   " if i == len(sub) - 1 else "   ")
        lines.extend(format_chain(s, new_prefix))
    return lines


TPL_NAMES = {
    "hongjing":  "宏景科技型",
    "litong":    "利通电子型",
    "xiangnong": "香农芯创型",
    "fujing":    "福晶科技型",
    "yunnange":  "云南锗业型",
    "lanqi":     "澜起科技型",
}


def build_msg(rec: dict, tpl_info: dict, fin_data: dict | None,
              entry_price: float, disclaimer: str) -> str:
    """每股一条 markdown 消息: q-seed 基础 + q-fin 推理链(如有)."""
    code      = rec["code"]
    name      = rec["name"]
    sig_date  = rec["sig_date"]
    dist      = rec["template_dist"]
    tpl       = rec["template"]
    etype     = rec.get("explosion_type", "首次")
    win80     = tpl_info["win80"]
    avg_ret   = tpl_info["avg_ret80"]
    match_pct = max(0, min(100, round(100 - dist * 4)))
    target    = entry_price * (1 + avg_ret)
    stop      = entry_price * 0.93
    hold      = "80天" if win80 >= 0.45 else "20天"
    tpl_name  = TPL_NAMES.get(tpl, tpl)
    etype_mark = "★★ 再次起爆" if etype == "再次" else "首次起爆"

    has_fin = bool(fin_data)
    attention = "高关注" if (has_fin and (fin_data.get("verdict") or {}).get("rating", 0) >= 3) \
                else "中等关注" if has_fin else "形态信号"
    scan_date = (fin_data or {}).get("scan_date") or rec.get("as_of_date", "")

    m = []
    m.append(f"## 📊 截至 {scan_date} 收盘 · 选股信号")
    m.append("")
    m.append(f"**{name} ({code})** — {attention}")
    m.append("")

    # ── q-seed 部分 ──────────────────────────────
    m.append("【形态匹配 (q-seed)】")
    m.append(f"- 模板: **{tpl_name}** 匹配度 **{match_pct}%** (dist={dist:.2f})")
    m.append(f"- 起爆日: {sig_date}  [{etype_mark}]")
    m.append(f"- 历史胜率: 80天 **{win80*100:.0f}%**, 均收益 +{avg_ret*100:.0f}%")
    m.append(f"- T+1 入场参考: **{entry_price:.2f}** 元")
    m.append(f"- 止损: {stop:.2f} (-7%)  目标: {target:.2f} (+{avg_ret*100:.0f}%)  持仓: {hold}")

    # ── q-fin 部分 (如有) ──────────────────────────
    if fin_data:
        v    = fin_data.get("verdict") or {}
        er   = fin_data.get("entity_research") or {}
        meta = fin_data.get("meta") or {}
        l1   = fin_data.get("layer1_triggers") or {}

        llm_prov  = v.get("verdict_provider") or meta.get("providers", {}).get("llm", "?")
        llm_model = v.get("verdict_model") or "?"
        srch_prov = meta.get("providers", {}).get("search", "?")

        m.append("")
        m.append("【深度调研 (q-fin)】")
        m.append(f"> LLM: {llm_prov}/{llm_model} | 搜索: {srch_prov}")

        if v.get("rating"):
            rating_stars = "⭐" * v["rating"]
            m.append(f"评级: {rating_stars} ({v['rating']}/5)  **{v.get('one_liner','')}**")

        # Layer 1 事件
        events = l1.get("matched_events") or []
        if events:
            m.append("")
            m.append("**触发事件:**")
            for e in events[:3]:
                m.append(f"- {e.get('keyword','')} ({e.get('ann_date','')})")

        # 推理链
        steps = er.get("reasoning_steps") or []
        if steps:
            m.append("")
            m.append("**调研推理链:**")
            for s in steps:
                depth = s.get("depth", 0)
                prefix = "  " * depth + ("- " if depth == 0 else "  - ")
                src    = "(搜索)" if s.get("search_used") else "(缓存)"
                entity = s["entity"]
                conclusion = (s.get("conclusion") or "").replace("[缓存] ", "").replace("[缓存]","")
                m.append(f"{prefix}**{entity}** {src}")
                for sn in (s.get("search_snippets") or [])[:2]:
                    m.append(f"{'  '*(depth+1)}  · {sn}")
                if conclusion:
                    m.append(f"{'  '*(depth+1)}  结论: {conclusion[:70]}")
                if s.get("business_summary"):
                    m.append(f"{'  '*(depth+1)}  _{s['business_summary'][:80]}_")

        # 实体树 (仅 depth>0 时才有额外价值，否则和推理链重复)
        if er.get("chain") and not steps:
            m.append("")
            m.append(f"**实体树** (深度{er.get('max_depth_used',1)}层 ${er.get('budget_used_usd',0):.3f}):")
            for cl in format_chain(er["chain"])[:15]:
                m.append(cl)

        # 风险
        risks = v.get("key_risks") or []
        if risks:
            m.append("")
            m.append("**风险:**")
            for rk in risks[:3]:
                m.append(f"- {rk[:85]}")
    else:
        m.append("")
        m.append("_（本股未运行 q-fin 深度调研）_")

    m.append("")
    m.append("---")
    m.append(disclaimer.strip())
    return "\n".join(m)


def push_message(qpush_cmd: str, code: str, name: str, msg_md: str, tag: str = "今日选股"):
    """单股一条 markdown 消息推送 (绕 q-push 默认逻辑, 直接构造)."""
    # q-push 期望 stdin JSONL → markdown. 我们已经构造好 markdown, 用 webhook 直推.
    import urllib.request
    webhook = os.environ.get("WECHAT_WEBHOOK_URL")
    if not webhook:
        # 加载 .env
        env_path = Path("/home/wyatt/sentry/quant/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("WECHAT_WEBHOOK_URL="):
                    webhook = line.split("=", 1)[1].strip()
                    break
    if not webhook:
        log("WECHAT_WEBHOOK_URL 未设, 仅打印")
        print(msg_md)
        return False

    body = {"msgtype": "markdown", "markdown": {"content": msg_md[:4000]}}    # 企业微信 4096 字符上限
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            r = json.loads(resp.read().decode("utf-8"))
            if r.get("errcode") == 0:
                log(f"  ✅ 推送 {name} ({code}) 成功")
                return True
            else:
                log(f"  ❌ 推送 {name} 失败: {r.get('errmsg')}")
                return False
    except Exception as e:
        log(f"  ❌ webhook 失败: {e}")
        return False


def get_latest_close(code: str, tdx_dir: str) -> float:
    """直接读 TDX .day 取最新 close."""
    import struct
    base = Path(tdx_dir)
    if code.startswith(("60", "68", "11", "5", "9")):
        path = base / "sh" / "lday" / f"sh{code}.day"
    elif code.startswith(("00", "30", "12")):
        path = base / "sz" / "lday" / f"sz{code}.day"
    else:
        path = base / "sh" / "lday" / f"sh{code}.day"
    if not path.exists():
        return 0.0
    fmt = "<IIIIIfII"
    sz = struct.calcsize(fmt)
    data = path.read_bytes()
    if len(data) < sz:
        return 0.0
    last = data[-sz:]
    _dt, _o, _h, _l, c, *_ = struct.unpack(fmt, last)
    return c / 100.0


# ===================================================================
def main():
    args = parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    target_date = args.date or date.today().isoformat()
    log(f"=== q-pick-today {target_date} ===")
    log(f"模板: {cfg['templates']} (litong 已停用)")

    # ─── Step 1: rsync (可选) ───
    if not args.skip_sync:
        target_d = date.fromisoformat(target_date)
        mmdd = target_d.strftime("%m%d")
        win_dir = cfg["data"]["windows_dir_pattern"].format(mmdd=mmdd)
        win_path = Path(win_dir)
        if win_path.exists():
            log(f"rsync from {win_dir} ...")
            backup = Path(cfg["data"]["backup_tdx"])
            if backup.exists():
                subprocess.run(["rm", "-rf", str(backup)], check=False)
            local = Path(cfg["data"]["local_tdx"])
            if local.exists():
                subprocess.run(["mv", str(local), str(backup)], check=True)
            local.mkdir(parents=True, exist_ok=True)
            subprocess.run(["rsync", "-a", f"{win_dir}/", str(local) + "/"], check=True)
            log(f"  ✅ rsync 完成")
        else:
            log(f"  ⚠️ Windows 数据目录不存在: {win_dir}, 用现有 data/tdx")

    # ─── Step 2: q-backtest-fast ───
    records = run_qbacktest(cfg["commands"]["q_backtest_fast"], target_date, cfg["templates"])
    if not records:
        log("❌ q-backtest 无输出, 退出")
        sys.exit(1)
    log(f"q-backtest 输出 {len(records)} 候选")

    # ─── Step 3: 过滤 sig_date == target_date ───
    same_day = [r for r in records if r.get("sig_date") == target_date]
    log(f"sig_date == {target_date} 的候选: {len(same_day)}")
    if not same_day:
        log(f"⚠️ {target_date} 没新起爆点, 不推送")
        # 但还是发一条空消息让用户知道
        msg = f"# 📭 {target_date} 无新起爆\n\n所有 5 模板今日均无 sig_date == {target_date} 的新触发标的.\n\n"
        msg += cfg["push"]["disclaimer"].strip()
        if not args.skip_push:
            push_message(cfg["commands"]["q_push"], "EMPTY", "EMPTY", msg, "今日空")
        return

    # ─── Step 4: 评级 ⭐ ───
    # 回测数据: 2025-08-01~2026-04-01, step=1, dist<5严格匹配
    TPL_STATS = {
        "yunnange":  {"win80": 0.775, "avg_ret80": 0.317},
        "lanqi":     {"win80": 0.548, "avg_ret80": 0.166},
        "hongjing":  {"win80": 0.558, "avg_ret80": 0.186},
        "fujing":    {"win80": 0.491, "avg_ret80": 0.314},
        "litong":    {"win80": 0.406, "avg_ret80": 0.098},
        "xiangnong": {"win80": 0.895, "avg_ret80": 0.606},
    }
    for r in same_day:
        tpl = r["template"]
        win80 = TPL_STATS.get(tpl, {}).get("win80", 0.4)
        r["_stars"] = rate_stars(r["template_dist"], win80, cfg["star_thresholds"])

    # 按 ⭐ + dist 排序
    same_day.sort(key=lambda r: (-r["_stars"], r["template_dist"]))
    log(f"⭐ 分布: {[(r['code'], r['_stars']) for r in same_day]}")

    # ─── Step 5: 选 ⭐⭐⭐⭐+ 跑 q-fin ───
    qfin_cfg = cfg["q_fin"]
    threshold = qfin_cfg.get("threshold_stars", 4)
    daily_budget = args.budget or qfin_cfg.get("daily_total_budget", 1.00)
    spent_total = 0.0
    fin_results: dict[str, dict] = {}

    if not args.skip_fin and qfin_cfg.get("enabled"):
        for r in same_day:
            if r["_stars"] < threshold: continue
            if r["code"] in fin_results: continue   # dedupe (双模板命中同股)
            if spent_total >= daily_budget:
                log(f"  ⚠️ 日总预算 ${daily_budget:.2f} 用完, 跳过剩余")
                break
            per_stock_budget = min(qfin_cfg.get("budget_per_stock", 0.15),
                                    daily_budget - spent_total)
            data = run_qfin_research(
                cfg["commands"]["q_fin"], r["code"],
                qfin_cfg.get("max_depth", 3), per_stock_budget,
            )
            if data:
                fin_results[r["code"]] = data
                cost = (data.get("meta") or {}).get("total_cost_usd", 0) or 0
                spent_total += cost
                log(f"    花费 ${cost:.4f}, 累计 ${spent_total:.4f}")
    log(f"q-fin 总花费: ${spent_total:.4f}")

    # ─── Step 6: 每股一条推送 ───
    log(f"开始推送 {len(same_day)} 条...")
    pushed = set()
    for r in same_day:
        code = r["code"]
        if code in pushed: continue   # dedupe (双模板命中只推一次, 选最高 ⭐)
        # 选该 code 最高 ⭐ 的 record (双命中时)
        candidates_for_code = [x for x in same_day if x["code"] == code]
        candidates_for_code.sort(key=lambda x: (-x["_stars"], x["template_dist"]))
        best = candidates_for_code[0]
        tpl_info = TPL_STATS[best["template"]]
        cur = get_latest_close(code, cfg["data"]["local_tdx"])
        msg = build_msg(best, tpl_info, fin_results.get(code), cur, cfg["push"]["disclaimer"])

        if args.skip_push:
            print(msg); print("---")
        else:
            push_message(cfg["commands"]["q_push"], code, best["name"], msg, "今日选股")
            time.sleep(2)   # 防 webhook 限流
        pushed.add(code)

    log(f"✅ q-pick-today 完成: {len(pushed)} 股, 花费 ${spent_total:.4f}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
