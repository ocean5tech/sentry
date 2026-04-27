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
