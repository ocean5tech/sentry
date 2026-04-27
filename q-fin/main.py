#!/usr/bin/env python3
"""
q-fin: 事件驱动题材股检索 (借壳/控制权变更/主业转型)
默认 free, --paid / --paid=deep 启用 LLM 实体调查 (B3 才实施)
"""

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

warnings.filterwarnings("ignore")

VERSION = "q-fin v1.0.0"


def parse_args():
    ap = argparse.ArgumentParser(
        prog="q-fin",
        description="事件驱动题材股检索器 (借壳/控制权变更/主业转型). 默认 free."
    )
    ap.add_argument("--paid", nargs="?", const="standard", default=None,
                    help="启用付费档. --paid=standard (默认) / --paid=deep. B3 才实施")
    ap.add_argument("--top", type=int, default=15, help="输出 TOP N (默认 15)")
    ap.add_argument("--input", dest="input_file", default=None,
                    help="JSON Lines 输入文件 / -. 主流用法: q-seed | q-fin")
    ap.add_argument("--since", default=None, help="公告窗口起 (默认 90 天前)")
    ap.add_argument("--until", default=None, help="公告窗口止 (默认今天)")
    ap.add_argument("--keywords", default=None, help="自定义关键词 yaml")
    ap.add_argument("--include-st", action="store_true", help="不剔除 ST")
    ap.add_argument("--modules", default=None,
                    help="只跑这些模块 (ann/share/holder/concept/fund/kline/qseed) 逗号分隔")
    ap.add_argument("--tags", default=None, help="只输出命中这些 tag 的 (逗号)")
    ap.add_argument("--format", dest="fmt", choices=["jsonl", "md", "both"], default="jsonl")
    ap.add_argument("--output", default=None)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅打印 Layer 1 候选 + 估算成本, 不实际跑 Layer 2")
    ap.add_argument("--llm", default=None, help="临时切 LLM provider (anthropic/openai_compat/dummy/dummy_priced)")
    ap.add_argument("--search", default=None, help="临时切 search provider (duckduckgo/dummy/anthropic_tool)")
    ap.add_argument("--budget", type=float, default=None,
                    help="覆盖 daily_usd_cap (例 --budget 0.10 限本次跑总开销)")
    ap.add_argument("--max-depth", type=int, default=None,
                    help="覆盖 entity_research max_depth (standard 默认 2, deep 默认 3)")
    ap.add_argument("--entity-budget-per-stock", type=float, default=None,
                    help="覆盖 per-stock 实体调查预算 (默认 0.10)")
    return ap.parse_args()


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr); sys.exit(code)


def warn(msg):
    print(f"WARN: {msg}", file=sys.stderr)


def info(msg):
    print(f"[q-fin] {msg}", file=sys.stderr)


def load_config(path):
    p = Path(path)
    if not p.exists():
        die(f"config not found: {path}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_keywords(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_paths(cfg):
    data = cfg.get("data", {})
    if not data.get("tdx_dir") or not Path(data["tdx_dir"]).exists():
        die(f"data.tdx_dir not found: {data.get('tdx_dir')}")
    from core import tdx_loader, stock_names
    tdx_loader.set_tdx_dir(data["tdx_dir"])
    if data.get("stock_names_csv"):
        stock_names.set_cache_path(data["stock_names_csv"])


def read_input_codes(input_file):
    explicit = bool(input_file)
    src = None
    if input_file:
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
    for ln, raw in enumerate(src, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            warn(f"input line {ln}: invalid JSON ({e}), skip")
            continue
        if "code" not in obj:
            warn(f"input line {ln}: missing 'code' field, skip")
            continue
        c = obj["code"]
        # 拒绝 null/None/empty (e.g. q-news 输出 sector_map 待填的 event-only 记录 code=null)
        if c is None or str(c).strip().lower() in ("", "null", "none"):
            warn(f"input line {ln}: code is null/empty, skip")
            continue
        codes.append(str(c).strip())

    if not codes:
        if explicit:
            warn("no input codes (explicit --input)")
            return []
        return None
    return codes


def main():
    args = parse_args()
    t0 = time.time()

    cfg = load_config(args.config)
    setup_paths(cfg)

    # 加载 keywords
    kw_path = args.keywords or cfg.get("keywords_file") or "./config/keywords.yaml"
    if not Path(kw_path).is_absolute():
        kw_path = ROOT / kw_path
    kw_cfg = load_keywords(kw_path)

    # 模式
    paid_level = args.paid  # None / "standard" / "deep"
    if paid_level is not None and paid_level not in ("standard", "deep"):
        die(f"--paid 必须是 standard 或 deep, 不能是 {paid_level!r}", code=2)
    mode = "free" if paid_level is None else paid_level

    # 加载 .env (共用 ~/sentry/quant/.env)
    if mode != "free":
        try:
            from dotenv import load_dotenv
            env_path = ROOT.parent / ".env"
            if env_path.exists():
                load_dotenv(env_path)
        except ImportError:
            pass

    # 读输入
    input_codes = read_input_codes(args.input_file)

    if input_codes is not None and len(input_codes) == 0:
        info("explicit --input but empty, exit")
        sys.exit(0)

    if input_codes is None:
        # 全市场 — V0 还没实现 Layer 1 全市场扫
        die("V0 暂不支持全市场扫, 请用 --input 或 q-seed | q-fin 管道", code=2)

    info(f"limited scan to {len(input_codes)} codes")

    # 加载所有 lib 模块
    import akshare as ak
    from core.data_loader import load_daily
    from core.stock_names import get_names
    from cache import JsonCache
    from fundamentals import FundamentalsService, FundamentalsConfig
    import announcements as ann_mod
    import shareholders as sh_mod
    import holder_count as hc_mod
    import concept_change as cc_mod
    import kline_safety as ks_mod
    import q_seed_crosscheck as qsc_mod
    import layer1_triggers as l1_mod
    import scoring as score_mod
    import tagger as tag_mod

    # 模块开关 (paid 时默认还跑 entity + verdict, 由 mode 决定)
    if args.modules:
        enabled = set(args.modules.split(","))
    else:
        enabled = {"ann", "share", "holder", "concept", "fund", "kline", "qseed"}
        if mode != "free":
            enabled |= {"entity", "verdict"}

    # Providers + budget (仅 paid)
    llm = None
    search = None
    budget = None
    providers_used = None
    if mode != "free":
        from providers.llm_factory import get_llm
        from providers.search_factory import get_search
        from budget_guard import BudgetGuard

        providers_cfg = cfg.get("providers", {})
        llm_active = args.llm or providers_cfg.get("llm", {}).get("active", "anthropic")
        search_active = args.search or providers_cfg.get("search", {}).get("active", "duckduckgo")
        try:
            llm = get_llm(providers_cfg.get("llm", {}), override=args.llm)
        except Exception as e:
            warn(f"LLM provider init failed ({e}), fallback to free mode")
            mode = "free"
            enabled.discard("entity")
            enabled.discard("verdict")
        if mode != "free":
            try:
                search = get_search(
                    providers_cfg.get("search", {}),
                    override=args.search,
                    llm_active=llm_active,
                )
            except Exception as e:
                die(f"search provider init failed: {e}", code=2)

            run_cap = args.budget if args.budget is not None else cfg.get("budget", {}).get("daily_usd_cap", 0.50)
            per_stock_cap = args.entity_budget_per_stock if args.entity_budget_per_stock is not None \
                else cfg.get("entity_research", {}).get("budget_per_stock_usd", 0.10)
            budget = BudgetGuard(
                run_cap_usd=run_cap,
                per_stock_cap_usd=per_stock_cap,
                cost_log_path=ROOT / cfg.get("budget", {}).get("cost_log", "./data_cache/cost_log.jsonl"),
                on_exceeded=cfg.get("budget", {}).get("on_exceeded", "fallback_free"),
            )
            providers_used = {"llm": llm_active, "search": search_active}
            info(f"mode={mode} llm={llm_active} search={search_active} run_cap=${run_cap:.4f} per_stock=${per_stock_cap:.4f}")

    # 服务初始化
    cache = JsonCache(
        cache_dir=ROOT / cfg.get("data", {}).get("cache_dir", "data_cache"),
        ttl_hours=cfg.get("data", {}).get("cache_ttl_hours", 24),
        disabled=args.no_cache,
    )
    fund_cfg = FundamentalsConfig(
        fin_dir=Path(cfg["data"]["tdx_fin_dir"]),
        field_map_path=ROOT / cfg.get("field_map_file", "./config/gpcw_field_map.yaml"),
        load_recent_periods=cfg.get("sources", {}).get("tdx_fin", {}).get("load_recent_periods", 8),
    )
    fund_svc = FundamentalsService(fund_cfg) if "fund" in enabled else None

    qseed_idx = qsc_mod.load_latest_qseed_codes(
        cfg.get("data", {}).get("q_seed_logs_dir", "")
    ) if "qseed" in enabled else None
    if "qseed" in enabled and qseed_idx is None:
        warn("q-seed logs not found, crosscheck will be null")

    safety_th = ks_mod.SafetyThresholds(
        red=1.30, yellow=1.10, green=0.90,
        vwap_window=cfg.get("kline_safety", {}).get("vwap_window", 60)
    )

    # 时间窗口
    today = date.today()
    until = args.until or today.isoformat()
    since = args.since or (today - timedelta(days=90)).isoformat()

    # st 过滤
    skip_st = not args.include_st

    names = get_names()

    # 主循环: 每 code 跑 layer 2
    records = []
    for code in input_codes:
        nm = names.get(code, "")
        if skip_st and "ST" in nm.upper():
            warn(f"{code} {nm} is ST, skip (用 --include-st 打开)")
            continue

        rec: dict = {
            "code": code,
            "name": nm,
            "scan_date": today.isoformat(),
            "source": "q-fin",
            "mode": mode,
            "rank": 0,
            "score": 0.0,
            "verdict": None,
            "entity_research": None,
        }

        # 模块 1: 公告
        if "ann" in enabled:
            rec["announcements_90d"] = ann_mod.analyze(code, since, until, kw_cfg, ak, cache)
        else:
            rec["announcements_90d"] = None

        # 模块 2: 股东
        if "share" in enabled:
            rec["shareholders"] = sh_mod.analyze(code, kw_cfg, ak, cache)
        else:
            rec["shareholders"] = None

        # 模块 3: 户数
        if "holder" in enabled:
            rec["holder_count_history"] = hc_mod.analyze(code, ak, cache)
        else:
            rec["holder_count_history"] = None

        # 模块 4: 概念
        if "concept" in enabled and rec.get("announcements_90d"):
            rec["concept_status"] = cc_mod.derive(rec["announcements_90d"])
        else:
            rec["concept_status"] = None

        # 模块 5: 基本面
        if "fund" in enabled and fund_svc:
            f = fund_svc.get_latest(code)
            rec["fundamentals"] = f
        else:
            rec["fundamentals"] = None

        # 模块 6: K 线
        if "kline" in enabled:
            try:
                df = load_daily(code)
                rec["kline"] = ks_mod.make_snapshot(df, safety_th)
            except Exception as e:
                warn(f"{code} kline failed: {e}")
                rec["kline"] = {}
        else:
            rec["kline"] = None

        # 模块 7: q-seed 交叉
        if "qseed" in enabled:
            rec["q_seed_crosscheck"] = qsc_mod.crosscheck(code, qseed_idx)
        else:
            rec["q_seed_crosscheck"] = None

        # Layer 1 触发派生 (从已拉数据)
        if rec.get("announcements_90d") and rec.get("shareholders"):
            rec["layer1_triggers"] = l1_mod.derive(
                rec["announcements_90d"], rec["shareholders"], kw_cfg
            )
        else:
            rec["layer1_triggers"] = {}

        # Tagger
        industry = (rec.get("fundamentals") or {}).get("industry", "")
        rec["tags"] = tag_mod.derive_tags(rec.get("announcements_90d") or {}, kw_cfg, industry)

        # Score
        rec["score"] = score_mod.score_candidate(rec, kw_cfg)

        # tag 过滤
        if args.tags:
            want = set(args.tags.split(","))
            if not (want & set(rec["tags"])):
                continue

        records.append(rec)

    # 排序按 score 降序
    records.sort(key=lambda x: x["score"], reverse=True)
    records = records[:args.top]
    for i, r in enumerate(records, 1):
        r["rank"] = i

    # paid: entity_research + verdict (仅 mode != free)
    if mode != "free" and llm is not None and budget is not None:
        # max_depth 解析
        er_cfg = dict(cfg.get("entity_research", {}))
        if mode == "deep":
            er_cfg["max_depth"] = args.max_depth or er_cfg.get("max_depth_deep", 3)
        else:
            er_cfg["max_depth"] = args.max_depth or er_cfg.get("max_depth_standard", 2)

        verdict_top_n = cfg.get("providers", {}).get("llm", {}).get(
            providers_used["llm"], {}
        ).get("verdict_model_top_n", 5)

        hints_path = ROOT / cfg.get("entity_hints_file", "./config/entity_hints.yaml")

        if args.dry_run:
            info(f"[dry-run] would invoke entity_research + verdict for {len(records)} stocks. estimated cost ~${0.02 * len(records):.2f}")
        else:
            from entity_research import research as er_research
            from llm_verdict import make_verdict

            for r in records:
                # entity_research: 仅当有 major_new_entry 才查
                if "entity" in enabled:
                    major = (r.get("shareholders") or {}).get("major_new_entry") or {}
                    root_entity = major.get("name") if major else None
                    min_pct = er_cfg.get("min_shareholder_pct_for_research", 0.03) * 100
                    if root_entity and major.get("pct", 0) >= min_pct:
                        r["entity_research"] = er_research(
                            root_entity=root_entity,
                            code=r["code"],
                            cfg=er_cfg,
                            llm=llm,
                            search=search,
                            budget=budget,
                            hints_path=hints_path,
                        )
                        # 字段补 mode + provider 标识
                        r["entity_research"]["mode"] = mode
                        r["entity_research"]["llm_provider"] = llm.name
                        r["entity_research"]["search_provider"] = search.name if search else None

                # verdict (无 major_new_entry 也跑, 综合所有数据出 ⭐)
                if "verdict" in enabled:
                    r["verdict"] = make_verdict(
                        rec=r,
                        rank=r["rank"],
                        llm=llm,
                        budget=budget,
                        verdict_top_n=verdict_top_n,
                    )

                # 中途降级触发 → 后续 record verdict/entity 直接 None
                if budget.fallback_triggered:
                    info(f"budget exceeded after rank {r['rank']}, remaining stocks fallback to free")
                    break

    # meta
    duration_ms = int((time.time() - t0) * 1000)
    final_mode = mode
    if budget is not None and budget.fallback_triggered:
        final_mode = f"partial_{mode}"

    for r in records:
        meta = {
            "scanner_version": VERSION,
            "mode": final_mode,
            "scan_duration_ms": duration_ms,
        }
        if providers_used:
            meta["providers"] = providers_used
        if budget is not None:
            meta["total_cost_usd"] = round(budget.run_total, 6)
            meta["budget_remaining_usd"] = round(budget.remaining, 6)
        r["meta"] = meta

    # 输出
    out_dir = ROOT / cfg.get("output", {}).get("jsonl_dir", "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    auto_jsonl = out_dir / f"q-fin_top{len(records)}_{ts}.jsonl"
    auto_md = out_dir / f"q-fin_top{len(records)}_{ts}.md"

    fp_jsonl = None
    fp_md = None
    if args.output:
        op = Path(args.output)
        op.parent.mkdir(parents=True, exist_ok=True)
        if args.fmt in ("jsonl", "both"):
            fp_jsonl = open(f"{op}.jsonl" if args.fmt == "both" else str(op), "w", encoding="utf-8")
        if args.fmt in ("md", "both"):
            fp_md = open(f"{op}.md" if args.fmt == "both" else str(op), "w", encoding="utf-8")

    fp_auto = open(auto_jsonl, "w", encoding="utf-8")
    fp_auto_md = open(auto_md, "w", encoding="utf-8") if cfg.get("output", {}).get("md_companion", True) else None

    for r in records:
        line = json.dumps(r, ensure_ascii=False, default=str)
        fp_auto.write(line + "\n")
        if fp_jsonl:
            fp_jsonl.write(line + "\n")
        if not args.output and args.fmt in ("jsonl",):
            print(line, flush=True)
    fp_auto.close()

    # MD
    md_lines = [f"# q-fin TOP {len(records)} · {today.isoformat()}", ""]
    md_lines.append("| # | code | name | score | tags | major_new_entry | YoY_NI | safety | qseed |")
    md_lines.append("|---|------|------|-------|------|------------------|--------|--------|-------|")
    for r in records:
        major = (r.get("shareholders") or {}).get("major_new_entry") or {}
        major_str = f"{major.get('name','')[:15]} {major.get('pct',0):.1f}%" if major else "—"
        yoy = ((r.get("fundamentals") or {}).get("yoy_net_profit"))
        yoy_str = f"{yoy:+.1f}%" if yoy is not None else "—"
        safety = (r.get("kline") or {}).get("kline_safety", "")
        qsc = r.get("q_seed_crosscheck") or {}
        qsc_str = "✓" + (qsc.get("best_template", "") or "") if qsc.get("is_in_q_seed") else "—"
        md_lines.append(
            f"| {r['rank']} | {r['code']} | {r['name']} | {r['score']} "
            f"| {','.join(r.get('tags',[]))[:30]} | {major_str} | {yoy_str} | {safety} | {qsc_str} |"
        )
    md_text = "\n".join(md_lines) + "\n"
    if fp_auto_md:
        fp_auto_md.write(md_text); fp_auto_md.close()
    if fp_md:
        fp_md.write(md_text); fp_md.close()
    if not args.output and args.fmt in ("md", "both"):
        print(md_text)

    if fp_jsonl:
        fp_jsonl.close()

    info(f"done. {len(records)} records. logs: {auto_jsonl.name} ({duration_ms/1000:.1f}s)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-fin] interrupted", file=sys.stderr); sys.exit(130)
    except BrokenPipeError:
        try: sys.stderr.close()
        except: pass
        sys.exit(0)
