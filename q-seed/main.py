#!/usr/bin/env python3
"""
q-seed: 形态相似度选股 (疯牛种子 hongjing + litong)
默认行为: 跑两个模板 → 合并去重 → TOP 30 → JSON Lines stdout + logs/ 备份
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

VERSION = "q-seed v1.0.0"


def parse_args():
    ap = argparse.ArgumentParser(
        prog="q-seed",
        description="形态相似度选股 (疯牛种子 hongjing + litong). 永远 free, 不调 LLM."
    )
    ap.add_argument("--top", type=int, default=None,
                    help="输出 TOP N (默认读 config.output.default_top=30)")
    ap.add_argument("--template", choices=["hongjing", "litong", "both"], default="both",
                    help="只跑某模板 (默认 both, 合并去重后按最小距离排序)")
    ap.add_argument("--input", dest="input_file", default=None,
                    help="输入 JSON Lines 文件 (或 -) 限定扫描范围")
    ap.add_argument("--since", default=None, help="sig_date >= 此日期 (YYYY-MM-DD)")
    ap.add_argument("--until", default=None, help="sig_date <= 此日期 (YYYY-MM-DD)")
    ap.add_argument("--format", dest="fmt", choices=["jsonl", "md", "both"], default="jsonl",
                    help="输出格式 (默认 jsonl)")
    ap.add_argument("--output", default=None,
                    help="输出文件 (默认 stdout, 同时自动写 logs/ 备份)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"),
                    help=f"指定 config.yaml (默认 {ROOT / 'config.yaml'})")
    ap.add_argument("--no-fundamentals", action="store_true",
                    help="关闭基本面追加字段 (默认关闭, q-fin 负责这个) — placeholder, 本版无效")
    return ap.parse_args()


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def warn(msg: str):
    print(f"WARN: {msg}", file=sys.stderr)


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        die(f"config not found: {path}", code=1)
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_input_codes(input_file: str | None) -> list[str] | None:
    """读 --input 或 stdin 的 JSON Lines, 返回 code 列表.
    返回值:
      - None: 走默认全市场 (无 --input 且 stdin 是 tty 或为空)
      - []:   显式 --input 但读到空 (用户意图明确, 后续 exit 0)
      - [...]: 有效 code 列表
    """
    src = None
    explicit = False
    if input_file:
        explicit = True
        if input_file == "-":
            src = sys.stdin
        else:
            p = Path(input_file)
            if not p.exists():
                die(f"input file not found: {input_file}", code=2)
            src = open(p, encoding="utf-8")
    elif not sys.stdin.isatty():
        src = sys.stdin

    if src is None:
        return None

    codes = []
    for lineno, raw in enumerate(src, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            warn(f"input line {lineno}: invalid JSON ({e}), skip")
            continue
        if "code" not in obj:
            warn(f"input line {lineno}: missing 'code' field, skip")
            continue
        codes.append(str(obj["code"]).strip())

    if not codes:
        if explicit:
            warn("no input codes (explicit --input)")
            return []
        # stdin 自动侦测但空 → fallback 全市场
        return None
    return codes


def setup_data_paths(cfg: dict):
    """注入 lib/core 模块的全局 path (替代硬编码相对路径)."""
    data = cfg.get("data", {})
    tdx_dir = data.get("tdx_dir")
    names_csv = data.get("stock_names_csv")

    if not tdx_dir or not Path(tdx_dir).exists():
        die(f"data.tdx_dir not found: {tdx_dir}", code=1)

    from core import tdx_loader, stock_names
    tdx_loader.set_tdx_dir(tdx_dir)
    if names_csv:
        stock_names.set_cache_path(names_csv)


def load_templates(cfg: dict, which: str) -> dict:
    tpls = cfg.get("templates", {})
    if which == "both":
        return tpls
    if which not in tpls:
        die(f"template '{which}' not in config.templates")
    return {which: tpls[which]}


def scan_for_template(syms: list[str], names: dict, tpl_cfg: dict,
                       wp, fp, load_daily_fn) -> list[dict]:
    """对 syms 跑一个模板的 KNN. 返回所有 Sig 行 (含特征)."""
    from wave_model import scan_one_features
    rows = []
    for s in syms:
        rows.extend(
            scan_one_features(s, names.get(s, ""), tpl_cfg["mode"], wp, fp, load_daily_fn)
        )
    return rows


def emit_jsonl(record: dict, fp_out, also_stdout: bool):
    line = json.dumps(record, ensure_ascii=False, default=str)
    if fp_out:
        fp_out.write(line + "\n")
    if also_stdout:
        print(line, flush=True)


def render_md_table(records: list[dict], scan_date: str, top_n: int) -> str:
    lines = [f"# q-seed TOP {len(records)} · {scan_date}", ""]
    lines.append("| # | code | name | 模板 | dist | sig_date | entry | safety | ret60 | 备注 |")
    lines.append("|---|------|------|------|------|----------|-------|--------|-------|------|")
    for r in records:
        rank = r["rank"]
        code = r["code"]
        name = r["name"]
        tpl = r["best_template"]
        dist = f"{r['best_dist']:.3f}"
        det = r["details"].get(tpl, {}) or {}
        sig = det.get("sig_date", "")
        entry = f"{det.get('entry', 0):.2f}" if det.get("entry") else ""
        safety = r.get("kline", {}).get("kline_safety", "")
        ret60 = r.get("kline", {}).get("ret60", 0)
        ret60_s = f"{ret60*100:+.1f}%" if isinstance(ret60, (int, float)) else ""
        n_waves = det.get("n_waves", "")
        note = f"n_waves={n_waves}, 20cm={det.get('is_20cm','')}"
        lines.append(f"| {rank} | {code} | {name} | {tpl} | {dist} | {sig} | {entry} | {safety} | {ret60_s} | {note} |")
    return "\n".join(lines) + "\n"


def main():
    args = parse_args()
    t_start = time.time()

    cfg = load_config(args.config)
    setup_data_paths(cfg)

    # 此时 lib/core 已配置好, 可以正常 import
    from core.data_loader import load_daily
    from core.tdx_loader import list_tdx_symbols
    from core.stock_names import get_names
    from wave_model import WaveParams, FilterParams
    from similar_knn import compute_distances, filter_recent, topn_by_distance
    from kline_snapshot import SafetyThresholds, make_snapshot

    wp = WaveParams.from_dict(cfg.get("wave_params", {}))
    fp = FilterParams.from_dict(cfg.get("filter", {}))
    th = SafetyThresholds.from_dict(cfg.get("kline_safety", {}))

    top_n = args.top if args.top is not None else cfg.get("output", {}).get("default_top", 30)
    recent_days = wp.lookback if not args.since else 0
    recent_days = cfg.get("wave_params", {}).get("recent_days", 60)

    # 输入决定扫描范围
    input_codes = read_input_codes(args.input_file)
    names = get_names()
    # 选定模板
    templates = load_templates(cfg, args.template)
    template_codes = set(tpl["code"] for tpl in templates.values())

    # user_scoped: 用户显式给了 input list (含 --input 文件 / stdin 实际有数据)
    # 注意: stdin 自动侦测但读到 0 行的情况已在 read_input_codes 里 fallback 成 None
    user_scoped = (input_codes is not None and len(input_codes) > 0)

    if input_codes is not None and len(input_codes) == 0:
        # 显式 --input 但空, exit 0
        print("[q-seed] no input codes from --input, exit", file=sys.stderr)
        sys.exit(0)

    if not user_scoped:
        syms = list_tdx_symbols()
        if not syms:
            die("no symbols in tdx_dir (data corrupt or path wrong?)", code=1)
        print(f"[q-seed] 全市场扫描 {len(syms)} 只", file=sys.stderr)
    else:
        from core.tdx_loader import day_path
        syms = []
        for c in input_codes:
            if day_path(c).exists():
                syms.append(c)
            else:
                warn(f"code {c} not found in TDX, skip")
        # 自动把模板 code 加入扫描 (KNN 需要模板自身的特征做基准)
        # 但模板 code 仅参与 KNN 计算, 不出现在最终输出 (除非用户也输入了它)
        for tc in template_codes:
            if tc not in syms and day_path(tc).exists():
                syms.append(tc)
        if not syms:
            print(f"[q-seed] no scannable codes after TDX filter", file=sys.stderr)
            sys.exit(0)
        print(f"[q-seed] limited scan to {len(syms)} codes (含模板 {sorted(template_codes)})", file=sys.stderr)

    user_input_set = set(input_codes) if user_scoped else None

    print(f"[q-seed] templates: {list(templates.keys())}", file=sys.stderr)

    per_template_results = {}
    for tname, tpl in templates.items():
        print(f"[q-seed] scanning template={tname} mode={tpl['mode']}", file=sys.stderr)
        rows = scan_for_template(syms, names, tpl, wp, fp, load_daily)
        if not rows:
            warn(f"no Sig found for template={tname}")
            continue
        import pandas as pd
        df = pd.DataFrame(rows)

        # KNN 距离
        df_with_dist = compute_distances(df, tpl["code"], tpl["sig_dates"])
        if df_with_dist.empty:
            if cfg.get("fallback", {}).get("on_template_corrupt", "error") == "error":
                die(f"template {tname} ({tpl['code']}) sig_dates not found in scan results", code=2)
            warn(f"template {tname} not found in scan results, skip")
            continue

        # 时间过滤
        # --input 模式下不按 recent_days 过滤 (用户已经显式给了 code list)
        # 但显式 --since/--until 永远生效
        if user_scoped and not args.since and not args.until:
            df_filtered = df_with_dist
        else:
            df_filtered = filter_recent(df_with_dist, recent_days, args.since, args.until)
        if df_filtered.empty:
            warn(f"template={tname}: no candidate in time window")
            continue
        per_template_results[tname] = df_filtered

    if not per_template_results:
        warn(f"no candidates in recent_days={recent_days} for any template")
        sys.exit(0)

    # 先把每模板自己排好序的候选准备好
    per_template_top: dict[str, dict[str, dict]] = {}   # tname → {code → row dict}
    per_template_sorted: dict[str, list[dict]] = {}     # tname → [row, ...] 按 dist 升序
    for tname, df in per_template_results.items():
        df_top = topn_by_distance(df, max(top_n * 3, 100))
        per_template_top[tname] = {}
        sorted_rows = []
        for _, r in df_top.iterrows():
            row = r.to_dict()
            per_template_top[tname][row["code"]] = row
            sorted_rows.append(row)
        per_template_sorted[tname] = sorted_rows

    # 若用户用 --input 限定了范围, 过滤掉非用户输入的 code
    # (自动加进来的模板 code 仅用于 KNN 基准, 不算用户想看的结果)
    if user_input_set is not None:
        for tname in per_template_sorted:
            per_template_sorted[tname] = [
                r for r in per_template_sorted[tname] if r["code"] in user_input_set
            ]
            per_template_top[tname] = {
                k: v for k, v in per_template_top[tname].items() if k in user_input_set
            }

    # 合并策略
    out_cfg = cfg.get("output", {})
    balance = out_cfg.get("balance_per_template", True)
    min_per_template = int(out_cfg.get("min_per_template", 10))
    template_names = list(per_template_results.keys())
    is_both = (args.template == "both" and len(template_names) > 1)

    merged: dict[str, dict] = {}

    def _add_or_update(row: dict, tname: str):
        code = row["code"]
        cur = merged.get(code)
        if cur is None:
            merged[code] = {
                "code": code,
                "name": row["name"],
                "best_template": tname,
                "best_dist": float(row["dist"]),
                "templates_matched": [tname],
            }
        else:
            if tname not in cur["templates_matched"]:
                cur["templates_matched"].append(tname)
            if float(row["dist"]) < cur["best_dist"]:
                cur["best_template"] = tname
                cur["best_dist"] = float(row["dist"])

    if not is_both or not balance:
        # 单模板, 或关了均衡: 全局 dist 升序合并
        for tname, rows in per_template_sorted.items():
            for r in rows:
                _add_or_update(r, tname)
    else:
        # both + balance: round-robin 取每模板 TOP, 保证均衡
        # 1. 各取 ceil(top/2) 进 quota
        quota = {tname: max(top_n // len(template_names), min_per_template)
                 for tname in template_names}
        # 余数给排在前面的模板
        remainder = top_n - sum(quota.values())
        if remainder > 0:
            # 按字母序均匀分余数 (deterministic)
            for i, tname in enumerate(sorted(template_names)):
                if i < remainder:
                    quota[tname] += 1
        # 2. 各模板按 dist 取 quota[tname] 个 (双命中合并)
        idx_per = {tname: 0 for tname in template_names}
        added_per = {tname: 0 for tname in template_names}

        # 先按 quota 取, 不够时把空缺让给其他模板
        # round-robin: 一轮一只, 各模板每轮取 1 只 (按 dist 升序)
        max_rounds = max(quota.values()) + 5  # 留余量
        for _ in range(max_rounds):
            progressed = False
            for tname in template_names:
                if added_per[tname] >= quota[tname]:
                    continue
                rows = per_template_sorted[tname]
                while idx_per[tname] < len(rows):
                    r = rows[idx_per[tname]]
                    idx_per[tname] += 1
                    code = r["code"]
                    if code in merged:
                        # 双命中, 算给当前模板的 templates_matched, 不占 quota
                        _add_or_update(r, tname)
                    else:
                        _add_or_update(r, tname)
                        added_per[tname] += 1
                        progressed = True
                        break
            if not progressed:
                break
            if len(merged) >= top_n:
                break

        # 如果某模板 quota 没用满 (候选不够), 把剩余名额让给其他模板
        deficit = top_n - len(merged)
        if deficit > 0:
            # 按全局 dist 顺序, 把还未加入的候选按顺序加进来
            all_remaining = []
            for tname, rows in per_template_sorted.items():
                for r in rows[idx_per[tname]:]:
                    if r["code"] not in merged:
                        all_remaining.append((float(r["dist"]), tname, r))
            all_remaining.sort(key=lambda x: x[0])
            for _, tname, r in all_remaining[:deficit]:
                _add_or_update(r, tname)

    # 排序并截取 TOP N (按 best_dist)
    ranked = sorted(merged.values(), key=lambda x: x["best_dist"])[:top_n]

    # 准备输出
    scan_date = datetime.now().strftime("%Y-%m-%d")
    config_hash = ""  # placeholder
    duration_ms = int((time.time() - t_start) * 1000)

    records = []
    for rank, item in enumerate(ranked, 1):
        code = item["code"]
        details = {}
        for tname in templates:
            if code in per_template_top.get(tname, {}):
                r = per_template_top[tname][code]
                details[tname] = {
                    "rank": int(per_template_top[tname][code].get("dist") and 0) or rank,
                    "dist": round(float(r["dist"]), 4),
                    "sig_date": r["sig_date"],
                    "entry": round(float(r["entry"]), 2),
                    "n_waves": int(r.get("n_waves", 0)),
                    "spike_ratio": round(float(r.get("spike_ratio", 0)), 4),
                    "triangle_strict": int(r.get("triangle_strict", 0)),
                    "amp_shrinkage": round(float(r.get("amp_shrinkage", 0)), 4),
                    "pierce_mean": round(float(r.get("pierce_mean", 0)), 4),
                    "pierce_max": round(float(r.get("pierce_max", 0)), 4),
                    "amp_mean": round(float(r.get("amp_mean", 0)), 4),
                    "is_20cm": int(r.get("is_20cm", 0)),
                    "is_st": int(r.get("is_st", 0)),
                }
            else:
                details[tname] = None

        # K 线快照
        try:
            df = load_daily(code)
            kline = make_snapshot(df, th)
        except Exception as e:
            warn(f"kline snapshot failed for {code}: {e}")
            kline = {}

        rec = {
            "code": code,
            "name": item["name"],
            "scan_date": scan_date,
            "source": "q-seed",
            "rank": rank,
            "score": round(item["best_dist"], 4),
            "score_type": "knn_distance_asc",
            "templates_matched": item["templates_matched"],
            "best_template": item["best_template"],
            "best_dist": round(item["best_dist"], 4),
            "details": details,
            "kline": kline,
            "meta": {
                "scanner_version": VERSION,
                "config_hash": config_hash,
                "scan_duration_ms": duration_ms,
                "templates_used": list(templates.keys()),
            },
        }
        records.append(rec)

    # 输出
    out_dir = ROOT / cfg.get("output", {}).get("jsonl_dir", "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    auto_jsonl = out_dir / f"q-seed_top{top_n}_{ts}.jsonl"
    auto_md = out_dir / f"q-seed_top{top_n}_{ts}.md"

    # 决定 stdout 输出格式
    fp_jsonl = None
    fp_md = None
    if args.output:
        op = Path(args.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        if args.fmt in ("jsonl", "both"):
            fp_jsonl = open(f"{op}.jsonl" if args.fmt == "both" else str(op), "w", encoding="utf-8")
        if args.fmt in ("md", "both"):
            fp_md = open(f"{op}.md" if args.fmt == "both" else str(op), "w", encoding="utf-8")
    # 自动备份永远写 jsonl
    fp_auto_jsonl = open(auto_jsonl, "w", encoding="utf-8")
    fp_auto_md = open(auto_md, "w", encoding="utf-8") if cfg.get("output", {}).get("md_companion", True) else None

    # 写 stdout 决策
    use_stdout_jsonl = (args.output is None) and (args.fmt in ("jsonl",))
    use_stdout_md = (args.output is None) and (args.fmt in ("md",))
    use_stdout_both = (args.output is None) and (args.fmt == "both")

    for rec in records:
        line = json.dumps(rec, ensure_ascii=False, default=str)
        fp_auto_jsonl.write(line + "\n")
        if fp_jsonl:
            fp_jsonl.write(line + "\n")
        if use_stdout_jsonl or use_stdout_both:
            print(line, flush=True)

    fp_auto_jsonl.close()

    md_text = render_md_table(records, scan_date, top_n)
    if fp_auto_md:
        fp_auto_md.write(md_text)
        fp_auto_md.close()
    if fp_md:
        fp_md.write(md_text)
        fp_md.close()
    if use_stdout_md or use_stdout_both:
        print(md_text)

    if fp_jsonl:
        fp_jsonl.close()

    print(f"[q-seed] done. {len(records)} records. logs: {auto_jsonl.name}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-seed] interrupted", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        # 下游 pipe 关闭 (例如 | head, | jq exit), 不算错
        try:
            sys.stderr.close()
        except Exception:
            pass
        sys.exit(0)
