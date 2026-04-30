#!/usr/bin/env python3
"""
q-news: 事件驱动新闻 → A股推理
默认 free 规则引擎; --paid 启用 LLM 兜底未命中规则的事件.
"""

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

warnings.filterwarnings("ignore")

VERSION = "q-news v1.0.0"


def parse_args():
    ap = argparse.ArgumentParser(
        prog="q-news",
        description="事件驱动新闻 → A股推理. 默认 free 规则引擎."
    )
    ap.add_argument("--paid", nargs="?", const="standard", default=None,
                    help="启用付费档. --paid=standard / --paid=deep")
    ap.add_argument("--top", type=int, default=30, help="输出 TOP N (默认 30)")
    ap.add_argument("--input", dest="input_file", default=None,
                    help="JSON Lines 输入 / -. 反向查个股新闻")
    ap.add_argument("--news-file", default=None,
                    help="测试模式: 跳过 fetcher 直接读静态 jsonl")
    ap.add_argument("--since", default=None, help="新闻起始 ISO 时间")
    ap.add_argument("--until", default=None, help="新闻结束 ISO 时间")
    ap.add_argument("--window-hours", type=int, default=None, help="时间窗口 (默认 config.mode.default_window_hours)")
    ap.add_argument("--sources", default=None,
                    help="限定数据源 (逗号: akshare:cls,rss:新华网-经济)")
    ap.add_argument("--tags", default=None, help="只输出命中这些 tag 的")
    ap.add_argument("--format", dest="fmt", choices=["jsonl", "md", "both"], default="jsonl")
    ap.add_argument("--output", default=None)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="拉新闻 + 跑规则 + 估成本, 不调 LLM")
    ap.add_argument("--llm", default=None, help="临时切 LLM provider")
    ap.add_argument("--search", default=None, help="临时切 search provider")
    ap.add_argument("--budget", type=float, default=None, help="覆盖 daily_usd_cap")
    return ap.parse_args()


def die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr); sys.exit(code)


def warn(msg):
    print(f"WARN: {msg}", file=sys.stderr)


def info(msg):
    print(f"[q-news] {msg}", file=sys.stderr)


def read_input_codes(input_file: str | None) -> list[str] | None:
    """读 --input 或 stdin. 区分显式 vs 自动侦测 (q-seed 教训 #3).
    返回:
      None: 走默认 (不限定 code, 全市场新闻拉)
      []: 显式 --input 但读到空 (后续 exit 0)
      [...]: 有效 code list
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
                die(f"--input not found: {input_file}", code=2)
            src = open(p, "r", encoding="utf-8")
    elif not sys.stdin.isatty():
        src = sys.stdin

    if src is None:
        return None

    codes = []
    for ln in src:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as e:
            warn(f"input line malformed: {e}; line={ln[:60]}"); continue
        if "code" not in obj:
            warn(f"input line missing 'code', skip: {ln[:60]}"); continue
        codes.append(str(obj["code"]).strip())

    if not codes:
        if explicit:
            return []
        return None
    return codes


def load_config(p: str) -> dict:
    pp = Path(p)
    if not pp.exists():
        die(f"config not found: {pp}", code=1)
    cfg = yaml.safe_load(pp.read_text(encoding="utf-8")) or {}
    return cfg


def main():
    t0 = time.time()
    args = parse_args()
    cfg = load_config(args.config)

    # 模式
    paid_level = args.paid
    if paid_level is not None and paid_level not in ("standard", "deep"):
        die(f"--paid must be standard or deep, got {paid_level!r}", code=2)
    mode = "free" if paid_level is None else paid_level

    # 加载 .env (paid only)
    if mode != "free":
        try:
            from dotenv import load_dotenv
            env_path = ROOT.parent / ".env"
            if env_path.exists():
                load_dotenv(env_path)
        except ImportError:
            pass

    # ===== 加载 lib =====
    import akshare as ak
    from cache import JsonCache
    import news_fetcher as fetcher
    import rule_engine as re_mod
    import concept_status as cs_mod
    import q_fin_crosscheck as qfc_mod
    import scoring as score_mod

    cache = JsonCache(
        cache_dir=ROOT / cfg.get("data", {}).get("cache_dir", "data_cache"),
        ttl_hours=cfg.get("data", {}).get("cache_ttl_hours", 1),
        disabled=args.no_cache,
    )

    # 知识库
    kb = re_mod.load_kb(ROOT / cfg.get("knowledge_base_file", "./config/knowledge_base.yaml"))
    sm = re_mod.load_sector_map(ROOT / cfg.get("sector_map_file", "./config/sector_map.yaml"))
    info(f"loaded {len(kb.get('rules', {}))} rules, {len(sm)} concepts")

    # 输入: 限定 code → 反向查个股新闻 vs 默认 → 全市场
    input_codes = read_input_codes(args.input_file)
    if input_codes is not None and len(input_codes) == 0:
        info("explicit --input empty, exit")
        sys.exit(0)

    # ===== 拉新闻 =====
    if args.news_file:
        # 测试模式: 直接读静态 jsonl
        info(f"news-file mode: reading {args.news_file}")
        events = []
        for ln in Path(args.news_file).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln: continue
            events.append(json.loads(ln))
    elif input_codes:
        # 反向查个股新闻
        info(f"individual news for {len(input_codes)} codes")
        events = []
        ind_func = cfg.get("sources", {}).get("akshare", {}).get("individual_news_func", "stock_news_em")
        for code in input_codes:
            events.extend(fetcher.fetch_individual(code, ak, cache, ind_func))
    else:
        # 全市场拉
        sources_filter = args.sources.split(",") if args.sources else None
        info(f"fetching all news, sources_filter={sources_filter}")
        events = fetcher.fetch_all(cfg, cache, ak, sources_filter)

    # 时间窗口过滤 (input 模式跳过, 个股新闻数量本身可控)
    if not input_codes and not args.news_file:
        window_hours = args.window_hours or cfg.get("mode", {}).get("default_window_hours", 24)
        events = fetcher.filter_by_window(events, hours=window_hours,
                                          since_iso=args.since, until_iso=args.until)
    info(f"events_after_filter: {len(events)}")

    # ===== 规则引擎匹配 =====
    min_match = cfg.get("mode", {}).get("triggers_min_match", 2)
    rule_hits = []           # list of (news, [matched_rules])
    unmatched = []
    for e in events:
        rules = re_mod.match_news(e, kb, min_match=min_match)
        if rules:
            rule_hits.append((e, rules))
        else:
            unmatched.append(e)

    info(f"rule hits: {len(rule_hits)}, unmatched: {len(unmatched)}")

    # ===== unmatched 写日志 =====
    if unmatched:
        unmatched_log = ROOT / cfg.get("output", {}).get("unmatched_log", "./logs/unmatched_events.jsonl")
        unmatched_log.parent.mkdir(parents=True, exist_ok=True)
        with open(unmatched_log, "a", encoding="utf-8") as f:
            for e in unmatched[:200]:  # 避免无限增长
                f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

    # ===== 展开到 stock-level =====
    expanded = []  # list of {code, name, concept, news, rule}
    for news, matched_rules in rule_hits:
        expanded.extend(re_mod.expand_to_stocks(matched_rules, sm, news))
    info(f"expanded to stock-level: {len(expanded)}")

    # ===== 聚合: 按 code 分组 (None code 单独保留) =====
    by_code: dict = {}
    for x in expanded:
        key = x["code"] or f"_event:{x['concept']}"
        if key not in by_code:
            by_code[key] = {
                "code": x["code"],
                "name": x["name"],
                "concepts": set(),
                "events": [],   # list of (news, rule, role)
            }
        by_code[key]["concepts"].add(x["concept"])
        by_code[key]["events"].append((x["news"], x["rule"], x.get("role"), x["concept"]))

    # ===== 准备 concept_status (只算用到的概念, 节省 IO) =====
    qseed_topn = cfg.get("concept_status", {}).get("qseed_topn_threshold", 30)
    qseed_active = cs_mod.load_qseed_active_codes(
        cfg.get("data", {}).get("q_seed_logs_dir", ""), top_n=qseed_topn
    )
    cs_threshold = cfg.get("concept_status", {}).get("active_pct_threshold", 0.5)
    concept_cache: dict = {}

    def _cs(concept: str) -> dict:
        if concept not in concept_cache:
            concept_cache[concept] = cs_mod.evaluate(concept, sm, qseed_active, cs_threshold)
        return concept_cache[concept]

    # qfin index
    qfin_idx = qfc_mod.load_latest_qfin_codes(cfg.get("data", {}).get("q_fin_logs_dir", ""))

    # ===== 构建 records =====
    records = []
    for key, agg in by_code.items():
        code = agg["code"]
        # triggered_events: 每个新闻 + 命中的规则
        triggered = []
        for news, rule, role, concept in agg["events"]:
            triggered.append({
                "event_id": news.get("event_id"),
                "title": news.get("title"),
                "source": news.get("source"),
                "publish_time": news.get("publish_time"),
                "url": news.get("url"),
                "concept": concept,
                "role": role,
                "reasoning_chain": rule.get("reasoning_chain"),
                "reasoning_method": "rule_engine",
                "rule": {
                    "id": rule.get("rule_id"),
                    "confidence": rule.get("confidence"),
                    "direction": rule.get("direction"),
                    "priority": rule.get("priority"),
                },
            })

        # concept_status: 取最相关的概念的 status (取 priority 最高的)
        concepts_list = list(agg["concepts"])
        concepts_list.sort(key=lambda c: (sm.get(c, {}).get("priority", 9), c))
        primary_concept = concepts_list[0] if concepts_list else None
        cs_payload = _cs(primary_concept) if primary_concept else None

        # q_seed_crosscheck
        if code and qseed_active is not None:
            qsc = {
                "is_in_q_seed": code in qseed_active,
            }
        else:
            qsc = {"is_in_q_seed": None, "_reason": "no_qseed_logs_or_event_only"}

        # q_fin_crosscheck
        qfc = qfc_mod.crosscheck(code, qfin_idx) if code else {"has_q_fin_record": None, "_reason": "event_only"}

        # tags
        all_tags = set()
        for news, _, _, _ in agg["events"]:
            for t in news.get("tags", []):
                all_tags.add(t)
        for c in concepts_list:
            all_tags.add(c)

        rec = {
            "code": code,
            "name": agg["name"],
            "scan_date": datetime.now().date().isoformat(),
            "source": "q-news",
            "mode": mode,
            "triggered_events": triggered,
            "concept_status": cs_payload,
            "q_seed_crosscheck": qsc,
            "q_fin_crosscheck": qfc,
            "tags": sorted(all_tags),
        }
        rec["score"] = score_mod.score_record(rec, cfg.get("scoring", {}).get("weights", {}))
        records.append(rec)

    # ===== 排序 + 截取 TOP N =====
    records.sort(key=lambda r: r["score"], reverse=True)

    # tag 过滤
    if args.tags:
        want = set(args.tags.split(","))
        records = [r for r in records if want & set(r.get("tags", []))]

    # balance_per_concept: 防单一概念占满 TOP N
    out_cfg = cfg.get("output", {})
    if out_cfg.get("balance_per_concept", True) and len(records) > args.top:
        max_per_concept = max(1, args.top // 3)   # 每概念最多 ⌈top/3⌉ 个 (e.g. top10→3)
        seen_per: dict[str, int] = {}
        balanced = []
        leftover = []
        for r in records:
            cs = r.get("concept_status") or {}
            c = cs.get("concept") or "(none)"
            if seen_per.get(c, 0) < max_per_concept:
                balanced.append(r)
                seen_per[c] = seen_per.get(c, 0) + 1
            else:
                leftover.append(r)
            if len(balanced) >= args.top:
                break
        # 不足 top N 时用 leftover 补齐
        if len(balanced) < args.top:
            need = args.top - len(balanced)
            balanced.extend(leftover[:need])
        records = balanced[:args.top]
    else:
        records = records[:args.top]

    for i, r in enumerate(records, 1):
        r["rank"] = i

    # ===== paid: cninfo 重大公告扫描 + LLM 利多/利空推理 =====
    cninfo_results: list[dict] = []   # free 模式保持空, 输出部分安全引用
    if mode != "free":
        from providers.llm_factory import get_llm
        from providers.search_factory import get_search
        from budget_guard import BudgetGuard
        import cninfo_scanner
        import event_analyst as ea

        llm    = get_llm(cfg["providers"]["llm"])
        search = get_search(cfg["providers"]["search"])
        b_cfg = cfg["budget"]
        budget = BudgetGuard(
            run_cap_usd=b_cfg["daily_usd_cap"],
            per_stock_cap_usd=b_cfg.get("per_stock_cap_usd", 0.05),
            cost_log_path=ROOT / b_cfg.get("cost_log", "data_cache/cost_log.jsonl"),
            on_exceeded=b_cfg.get("on_exceeded", "fallback_free"),
        )
        info(f"paid mode={mode} llm={llm.name} search={search.name}")

        # ── Layer B: cninfo 全市场重大公告 (最近7天) ──────────────────
        info("扫描 cninfo 最近7天重大公告...")
        announcements = cninfo_scanner.scan_recent(n_days=7)
        info(f"过滤后高影响公告: {len(announcements)} 条")

        # 读最近 q-seed/q-backtest 命中记录 (用于交叉验证)
        qseed_recent_codes: set[str] = set()
        qbt_logs = sorted((ROOT.parent / "q-backtest" / "logs").glob("q-backtest-fast_*.jsonl"),
                          reverse=True)
        if qbt_logs:
            import json as _json
            try:
                for line in qbt_logs[0].read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        r = _json.loads(line)
                        if r.get("template_dist", 99) < 8:
                            qseed_recent_codes.add(r.get("code", ""))
            except Exception:
                pass
        info(f"q-seed 近期命中库: {len(qseed_recent_codes)} 只")

        for ann in announcements:
            if not budget.can_spend(0.001, code=ann["code"])[0]:
                break
            analysis = ea.analyze_stock_announcement(
                llm=llm, budget=budget,
                code=ann["code"], name=ann["name"],
                title=ann["title"], ann_type=ann["type"], date=ann["date"],
            )
            if analysis is None:
                continue

            in_qseed = ann["code"] in qseed_recent_codes
            # 推送决策
            if analysis.impact == "高":
                push_decision = "直接推送"
            elif in_qseed:
                push_decision = "直接推送"   # 事件+形态双命中
            elif analysis.impact == "中":
                push_decision = "交叉验证"
            else:
                push_decision = "观察池"

            cninfo_results.append({
                "code":          ann["code"],
                "name":          ann["name"],
                "ann_title":     ann["title"],
                "ann_type":      ann["type"],
                "ann_date":      ann["date"],
                "ann_url":       ann["url"],
                "direction":     analysis.direction,
                "impact":        analysis.impact,
                "logic":         analysis.logic,
                "push_decision": push_decision,
                "in_qseed":      in_qseed,
                "model":         analysis.model,
                "cost_usd":      analysis.cost_usd,
            })

        # ── Layer A: 大盘/板块新闻 LLM 推理 (增强现有 events) ─────────
        top_news = events[:10]   # 取规则引擎已匹配的前10条新闻做 LLM 增强
        for r in records[:5]:    # 只给 top5 record 加 verdict
            ev = r.get("_matched_event") or {}
            title   = ev.get("title", r.get("headline", ""))
            content = ev.get("content", "") or ev.get("summary", "")
            source  = ev.get("source", "news")
            if not title:
                continue
            analysis = ea.analyze_market_news(
                llm=llm, budget=budget,
                source=source, title=title, content=content,
            )
            if analysis:
                r["verdict"] = {
                    "direction":        analysis.direction,
                    "impact":           analysis.impact,
                    "logic":            analysis.logic,
                    "affected_sectors": analysis.affected_sectors,
                    "affected_stocks":  analysis.affected_stocks,
                    "model":            analysis.model,
                }
            else:
                r["verdict"] = None
            r["entity_research"] = None

        info(f"cninfo 扫描完成: {len(cninfo_results)} 条有效分析, "
             f"直接推送 {sum(1 for x in cninfo_results if x['push_decision']=='直接推送')} 条")

    # meta
    duration_ms = int((time.time() - t0) * 1000)
    sources_used = sorted({e["source"] for e, _ in rule_hits})
    final_mode = mode
    for r in records:
        meta = {
            "scanner_version": VERSION,
            "mode": final_mode,
            "events_scanned": len(events),
            "events_matched": len(rule_hits),
            "events_unmatched": len(unmatched),
            "sources_used": sources_used,
            "scan_duration_ms": duration_ms,
        }
        r["meta"] = meta

    # ===== 输出 =====
    out_dir = ROOT / cfg.get("output", {}).get("jsonl_dir", "logs")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    auto_jsonl = out_dir / f"q-news_top{len(records)}_{ts}.jsonl"
    auto_md = out_dir / f"q-news_top{len(records)}_{ts}.md"

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

    # cninfo 结果写到单独文件
    if cninfo_results:
        cninfo_path = out_dir / f"q-news_cninfo_{ts}.jsonl"
        with open(cninfo_path, "w", encoding="utf-8") as cf:
            for r in cninfo_results:
                cf.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        info(f"cninfo jsonl: {cninfo_path} ({len(cninfo_results)} 条)")
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
    md_lines = [f"# q-news TOP {len(records)} · {datetime.now().date().isoformat()}", ""]
    md_lines.append("| # | code | name | score | concept | 已启动 | events |")
    md_lines.append("|---|------|------|-------|---------|--------|--------|")
    for r in records:
        cs = r.get("concept_status") or {}
        verdict = (cs.get("verdict") or "")[:20]
        ev_titles = "; ".join(e["title"][:25] for e in r.get("triggered_events", [])[:2])
        md_lines.append(
            f"| {r['rank']} | {r.get('code') or '-'} | {(r.get('name') or '')[:15]} | {r['score']} "
            f"| {(cs.get('concept') or '-')[:15]} | {verdict} | {ev_titles[:60]} |"
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
        print("\n[q-news] interrupted", file=sys.stderr); sys.exit(130)
    except BrokenPipeError:
        try: sys.stderr.close()
        except: pass
        sys.exit(0)
