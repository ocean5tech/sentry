#!/usr/bin/env python3
"""
V0 entry point: scan market, print hits, write logs/v0_YYYYMMDD.csv
Run manually after market close (15:30+).
"""
import sys
import csv
import logging
from datetime import date
from pathlib import Path

# Allow running from project root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.scanner import scan_market

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def main():
    today = date.today().strftime("%Y%m%d")
    log_path = LOG_DIR / f"v0_{today}.csv"

    print("=" * 60)
    print(f"A股量化选股 V0  |  {today}")
    print("=" * 60)
    print("扫描中，请稍候...\n")

    hits = scan_market(use_realtime=True)

    if not hits:
        print("今日无命中股票。")
    else:
        print(f"命中 {len(hits)} 条信号：\n")
        print(f"{'策略':<16} {'代码':<8} {'名称':<12} {'现价':>8}")
        print("-" * 50)
        for strategy_name, symbol, name, price in hits:
            print(f"{strategy_name:<16} {symbol:<8} {name:<12} {price:>8.2f}")

    # Write CSV log for manual tracking
    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["日期", "策略", "代码", "名称", "触发价", "次日涨跌%", "三日涨跌%", "备注"])
        for strategy_name, symbol, name, price in hits:
            writer.writerow([today, strategy_name, symbol, name, f"{price:.2f}", "", "", ""])

    print(f"\n结果已写入: {log_path}")
    print("请在次日、后日收盘后手动填写 '次日涨跌%' 和 '三日涨跌%' 列，跑一周验证胜率。")


if __name__ == "__main__":
    main()
