#!/usr/bin/env python3
"""
q-scan entry point.

Mode 1 — formula scan:
    q-scan --formula "CLOSE>=REF(CLOSE,1)*1.097 AND CLOSE==HIGH"
    q-scan --formula-file strategies/first_limit_up.tdx --output hits.csv

Mode 2 — filter existing list:
    q-scan --input hits.csv --filter "CLOSE<20 AND VOL>500000"

Mode 3 — Python strategy scan:
    q-scan --strategy fvg_pullback
    q-scan --strategy fvg_pullback --min-score 11000 --output results/hits.csv
"""

import argparse
import csv
import importlib.util
import json
import sys
import logging
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.tdx_loader import list_tdx_symbols
from core.data_loader import load_daily, DAILY_DIR
from core.formula_engine import run_formula, check_future_functions, translate_formula
from core.stock_names import get_name

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

STRATEGIES_DIR = Path(__file__).parent.parent / "core" / "strategies"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def get_all_symbols() -> list[str]:
    syms = list_tdx_symbols()
    if not syms:
        syms = [f.stem for f in sorted(DAILY_DIR.glob("*.parquet"))]
    return syms


_STALE_CUTOFF = date.today() - timedelta(days=30)


def _is_fresh(df) -> bool:
    last_date = df.iloc[-1]["date"]
    if hasattr(last_date, "date"):
        last_date = last_date.date()
    return last_date >= _STALE_CUTOFF


def run_scan(formula: str, symbols: list[str], quiet: bool = False) -> list[dict]:
    hits = []
    total = len(symbols)
    for i, symbol in enumerate(symbols, 1):
        if not quiet and total > 100 and i % 200 == 0:
            print(f"\r  {i}/{total} ({i*100//total}%)", end="", flush=True)
        try:
            df = load_daily(symbol)
            if df.empty or len(df) < 10 or not _is_fresh(df):
                continue
            if run_formula(formula, df):
                last = df.iloc[-1]
                hits.append({
                    "code":   symbol,
                    "name":   get_name(symbol),
                    "date":   str(last["date"].date()),
                    "close":  round(float(last["close"]), 2),
                    "open":   round(float(last["open"]),  2),
                    "high":   round(float(last["high"]),  2),
                    "low":    round(float(last["low"]),   2),
                    "volume": int(last["volume"]),
                })
        except Exception:
            pass
    if not quiet and total > 100:
        print()
    return hits


def run_strategy_scan(
    strategy_mod,
    symbols: list[str],
    min_score: int = 10000,
    quiet: bool = False,
) -> list[dict]:
    hits = []
    total = len(symbols)
    for i, symbol in enumerate(symbols, 1):
        if not quiet and total > 100 and i % 200 == 0:
            print(f"\r  {i}/{total} ({i*100//total}%)", end="", flush=True)
        try:
            df = load_daily(symbol)
            if df.empty or not _is_fresh(df):
                continue
            # 若策略 scan() 接受 symbol 参数则传入（向后兼容）
            import inspect as _inspect
            _params = _inspect.signature(strategy_mod.scan).parameters
            result = strategy_mod.scan(df, symbol=symbol) if "symbol" in _params else strategy_mod.scan(df)
            if result is None or result["score"] < min_score:
                continue
            last = df.iloc[-1]
            row = {
                "code":   symbol,
                "name":   get_name(symbol),
                "date":   str(last["date"].date()),
                "score":  result["score"],
                "close":  round(float(last["close"]), 2),
                "high":   round(float(last["high"]),  2),
                "low":    round(float(last["low"]),   2),
                "volume": int(last["volume"]),
            }
            row.update({k: ("✓" if v else "✗") if isinstance(v, bool) else v
                        for k, v in result.items() if k not in ("score",)})
            hits.append(row)
        except Exception:
            pass
    if not quiet and total > 100:
        print()
    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits


def load_strategy(name: str):
    path = STRATEGIES_DIR / f"{name}.py"
    if not path.exists():
        available = [p.stem for p in STRATEGIES_DIR.glob("*.py") if p.stem != "__init__"]
        print(f"错误：策略 '{name}' 不存在。可用策略: {', '.join(available) or '(无)'}", file=sys.stderr)
        sys.exit(1)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_input_file(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_output(hits: list[dict], path: Path, fmt: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(hits, f, ensure_ascii=False, indent=2)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=hits[0].keys())
            writer.writeheader()
            writer.writerows(hits)


def print_hits(hits: list[dict], limit: int = 50):
    if not hits:
        return
    has_score = "score" in hits[0]
    if has_score:
        flag_keys = [k for k in hits[0] if k not in ("code", "name", "date", "score", "close", "high", "low", "volume")]
        header = f"{'代码':<8} {'名称':<10} {'日期':<12} {'评分':>6} {'最新价':>8} " + " ".join(f"{k:>5}" for k in flag_keys)
        print(f"\n{header}")
        print("-" * (50 + len(flag_keys) * 6))
        for h in hits[:limit]:
            flags = " ".join(f"{h.get(k,''):>5}" for k in flag_keys)
            name = h.get("name", "")[:8]
            print(f"{h['code']:<8} {name:<10} {h['date']:<12} {h['score']:>6} {h['close']:>8.2f} {flags}")
    else:
        print(f"\n{'代码':<8} {'名称':<10} {'日期':<12} {'最新价':>8} {'最高':>8} {'最低':>8} {'成交量':>12}")
        print("-" * 72)
        for h in hits[:limit]:
            name = h.get("name", "")[:8]
            print(f"{h['code']:<8} {name:<10} {h['date']:<12} {h['close']:>8.2f} "
                  f"{h['high']:>8.2f} {h['low']:>8.2f} {h['volume']:>12,}")
    if len(hits) > limit:
        print(f"  ... 共 {len(hits)} 条，完整结果见输出文件")


def resolve_formula(args_formula, args_formula_file) -> str | None:
    if args_formula_file:
        p = Path(args_formula_file)
        if not p.exists():
            print(f"错误：公式文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        return p.read_text(encoding="utf-8").strip()
    return args_formula


def guard_future_functions(formula: str):
    bad = check_future_functions(formula)
    if bad:
        print(f"错误：公式含未来函数，拒绝执行: {', '.join(bad)}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="q-scan",
        description="A股选股扫描工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 模式1：通达信公式扫全市场
  q-scan --formula "CLOSE>=REF(CLOSE,1)*1.097 AND CLOSE==HIGH"
  q-scan --formula-file strategies/my.tdx --output results/hits.csv

  # 模式2：从已有列表进一步筛选
  q-scan --input hits.csv --filter "CLOSE<20"

  # 模式3：Python策略扫全市场
  q-scan --strategy fvg_pullback
  q-scan --strategy fvg_pullback --min-score 11000 --output results/hits.csv
        """
    )

    g1 = parser.add_argument_group("模式1：通达信公式扫全市场")
    g1.add_argument("--formula",      "-f",  help="通达信公式字符串")
    g1.add_argument("--formula-file",        help="通达信公式文件路径（.tdx）")

    g2 = parser.add_argument_group("模式2：筛选已有列表")
    g2.add_argument("--input",        "-i",  help="输入股票列表（CSV 或 JSON）")
    g2.add_argument("--filter",              help="筛选条件（通达信公式字符串）")
    g2.add_argument("--filter-file",         help="筛选条件文件（.tdx）")

    g3 = parser.add_argument_group("模式3：Python策略")
    g3.add_argument("--strategy",     "-s",  help="策略名称（core/strategies/ 下的文件名）")
    g3.add_argument("--min-score",    type=int, default=10000,
                                             help="最低评分阈值（默认10000，即最近有突破）")

    g4 = parser.add_argument_group("输出选项")
    g4.add_argument("--output", "-o",        help="输出文件路径")
    g4.add_argument("--format",              choices=["csv", "json"], default="csv")
    g4.add_argument("--no-save",             action="store_true", help="只打印，不写文件")
    g4.add_argument("--quiet",       "-q",   action="store_true", help="不显示进度条")

    args = parser.parse_args()
    today_str = date.today().strftime("%Y%m%d")
    hits: list[dict] = []

    # ── 模式3：Python策略 ─────────────────────────────────────
    if args.strategy:
        mod = load_strategy(args.strategy)
        symbols = get_all_symbols()
        if not symbols:
            print("错误：没有找到数据，请先运行 q-sync 或 q-pull", file=sys.stderr)
            sys.exit(1)
        name = getattr(mod, "NAME", args.strategy)
        print(f"策略：{name}  最低评分：{args.min_score}")
        print(f"扫描 {len(symbols)} 只股票...")
        hits = run_strategy_scan(mod, symbols, min_score=args.min_score, quiet=args.quiet)

    # ── 模式1：公式扫全市场 ───────────────────────────────────
    elif args.formula or args.formula_file:
        formula = resolve_formula(args.formula, args.formula_file)
        guard_future_functions(formula)
        try:
            translate_formula(formula)
        except Exception as e:
            print(f"错误：公式解析失败: {e}", file=sys.stderr)
            sys.exit(1)
        symbols = get_all_symbols()
        if not symbols:
            print("错误：没有找到数据，请先运行 q-sync 或 q-pull", file=sys.stderr)
            sys.exit(1)
        print(f"公式：{formula[:100]}{'...' if len(formula) > 100 else ''}")
        print(f"扫描 {len(symbols)} 只股票...")
        hits = run_scan(formula, symbols, quiet=args.quiet)

    # ── 模式2：筛选已有列表 ───────────────────────────────────
    elif args.input:
        filter_formula = resolve_formula(args.filter, args.filter_file)
        if not filter_formula:
            print("错误：--input 需要配合 --filter 或 --filter-file 使用", file=sys.stderr)
            sys.exit(1)
        guard_future_functions(filter_formula)
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"错误：文件不存在: {args.input}", file=sys.stderr)
            sys.exit(1)
        input_hits = load_input_file(input_path)
        symbols = [row["code"] for row in input_hits if "code" in row]
        if not symbols:
            print("错误：输入文件没有 'code' 列", file=sys.stderr)
            sys.exit(1)
        print(f"筛选条件：{filter_formula[:100]}")
        print(f"从 {len(symbols)} 只股票中进一步筛选...")
        hits = run_scan(filter_formula, symbols, quiet=args.quiet)

    else:
        parser.print_help()
        sys.exit(0)

    # ── 输出结果 ──────────────────────────────────────────────
    print(f"\n命中 {len(hits)} 只股票")
    print_hits(hits)

    if not args.no_save and hits:
        out_path = Path(args.output) if args.output else LOG_DIR / f"scan_{today_str}.{args.format}"
        save_output(hits, out_path, args.format)
        print(f"\n结果已写入: {out_path}")


if __name__ == "__main__":
    main()
