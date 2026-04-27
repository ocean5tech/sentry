# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Behavioral Guidelines

### 1. Think Before Coding

Before implementing, state assumptions explicitly. If multiple interpretations exist, present them — don't pick silently. If something is unclear, stop and ask. Push back when a simpler approach exists.

### 2. Simplicity First

Minimum code that solves the problem. No features beyond what was asked, no abstractions for single-use code, no speculative flexibility. If it could be 50 lines, don't write 200.

### 3. Surgical Changes

Touch only what the request requires. Don't improve adjacent code, refactor things that aren't broken, or match a different style. Remove only imports/variables made unused by *your* changes — leave pre-existing dead code alone unless asked.

### 4. Goal-Driven Execution

For multi-step tasks, state a brief plan with verifiable checkpoints before starting. Strong success criteria ("write a test that reproduces it, then make it pass") beat weak ones ("make it work").

---

## Project

A-share quantitative stock screening system on WSL2. Scans the full A-share market (~11,000 symbols) against user-defined strategies and outputs CSV/JSON hit lists. Currently **V0** — manual run only, no push notifications yet.

## Shell Commands

All commands are in `~/sentry/quant/scripts/` and on `$PATH`:

```bash
q-sync               # Download latest TDX daily zip and extract to data/tdx/
q-sync --status      # Check download progress
q-pull               # Full baostock pull (3-year history, ~5300 stocks, slow)
q-pull-update        # Incremental baostock update (only stocks not updated today)
q-scan --strategy fvg_pullback --min-score 11111
q-scan --formula "CLOSE>=REF(CLOSE,1)*1.097 AND CLOSE==HIGH"
q-scan --input hits.csv --filter "CLOSE<20"
q-seed-hongjing       # 疯牛种子-宏景型 (短期波浪+严格 HH/Pierce)
q-seed-litong         # 疯牛种子-利通型 (W 型双底+loose HH/Pierce)
q-seed-hongjing 50    # TOP 50
q-seed-litong --skip-scan  # 跳过扫描, 直接拉基本面
```

## 疯牛种子 (Mad Bull Seed) — 双 seed 并列

用户私人命名的选股方法, 现有两个并列子模式. 当用户说:
- "疯牛种子" / "种子选股" → 跑哪个**问用户**(不要默选)
- "宏景种子" / "seed-hongjing" → `q-seed-hongjing`
- "利通种子" / "seed-litong" → `q-seed-litong`
- "两个种子都跑" / "all seed" → 顺序跑 `q-seed-hongjing` 然后 `q-seed-litong`

为何两个 seed 而不合并: 两种庄家行为模式差异大 (宏景=短期波浪强势, 利通=长期 W 型反转). 平均向量会失真, 既不像宏景也不像利通的"中庸形态"反而排前. 双 seed 保留模式特异性, "双命中" = 强信号.

### 内核

找形态最像**两个标杆股**主升浪前 K 线的活跃候选股, 然后用基本面+人工方向过滤.

| Seed | 模板 | 模式 | 适用形态 |
|---|---|---|---|
| `q-seed-hongjing` | 宏景科技 (301396), 3 个 Sig 日期 | strict | 短期 (3月) 波浪, HH 严格递增, 每次 Pierce |
| `q-seed-litong` | 利通电子 (603629), 4 个 Sig 日期 | loose | 长期 (9月) W 型双底, HH 允许 1 次例外, Pierce 允许 1 次例外 |

### 完整流程 (单个 q-seed 一键执行)

**Step 1: 全市场扫描 Wave+Pierce 信号** — `scripts/find_similar_to_template.py --template <hongjing|litong>` (复用 `train_wave_model.py` 的特征定义)

  扫 11k 只股票, 识别每个 Sig 触发点:
  - **Launch 启动日**: ret ≥ +5% AND V > 前 20 日均量 × 2
  - **Wave 波浪 (strict)**: 60 日内 ≥ 2 次 launch, higher highs **严格递增** + higher lows (允许 1 次例外) + 每次 historical pullback 都击穿 launch open (Pierce)
  - **Wave 波浪 (loose)**: 同上但 HH 允许 1 次例外 + Pierce 允许 1 次例外 (能识别 W 型双底洗盘, 如利通电子)
  - **触发**: 今天又是 launch 且过去满足上述

**Step 2: 相似度 KNN** — 同脚本

  特征 (60+ 维, 严格不含 sig_date 之后数据):
  - 历史 wave 形态: amp / drawdown / pierce / 时长 (前 4 个 wave)
  - 波浪统计量: 振幅均值/极值/加速度, pierce 均值/最大, 价格累计涨幅
  - **Spike 尖头度**: 每个历史 wave launch 后 5 日内有 close < launch close (避免上方延展)
  - **Triangle 反喇叭口**: 最近一次 historical launch + 5 日 到 trigger 间分 3 段, 振幅是否严格递减 + 收缩率
  - **入场价位 60-100**: 60 ≤ entry ≤ 100 二值 + log 距 80 元中点的距离
  - 触发日 OHC + 是否 ST/退市 + 板别 (10/20CM)

  z-score 标准化所有特征, 取**对应模板**的所有 Sig 日期的特征均值作为 query 向量, 算欧氏距离, 输出最近 60 天内触发的 Sig 中距离最小的 → `logs/similar_to_<template>.csv` (≈300-600 行, loose 模式更多).

**Step 3: 基本面调查** — `scripts/fetch_candidate_fundamentals.py --from-csv --top N`

  对 TOP N 候选用 baostock 拉:
  - 证监会行业
  - 最近季度: 净利率 / 毛利率 / ROE / 净利润绝对值 / EPS_TTM
  - 同比: 净利润 YoY / 扣非净利润 YoY / 净资产 YoY

  → `logs/fundamentals_<template>.csv` (按 from-csv 输入文件名自动推导)

**Step 4 (人工)**: 按用户感兴趣的 8 个方向筛选 + 标注:
  AI / 算力 / 太空 / 矿产 / 稀土 / 贵金属 / 创新药 / 小行业冠军

  并人工补:
  - 概念升级故事 (是否像宏景 "传统 IT → AI 算力" 的概念切换)
  - 行业冠军 / 定价权 (baostock 不提供, 凭研报常识)

### 关键参数 (硬编码)

```
LAUNCH_RET = 0.05        # 启动涨幅阈值
LAUNCH_VOL_MUL = 2.0     # 放量倍数
VOL_WIN = 20             # 量均期
PULLBACK_WIN = 15        # 回踩窗口
LOOKBACK = 60            # 波浪回看
MIN_WAVE = 2             # 最少 launch 次数
SPIKE_WIN = 5            # 尖头判定窗口
SPIKE_SKIP = 5           # 三角窗口起点偏移
PRICE_CENTER = 80        # 入场价中点
RECENT_DAYS = 60         # 候选最近 N 日

TEMPLATES = {
  hongjing: code=301396, dates=[2024-09-30, 2025-12-09, 2026-01-12], mode=strict
  litong:   code=603629, dates=[2025-12-10, 2026-01-14, 2026-01-27, 2026-02-02], mode=loose
}
```

### 重要警告 (汇报候选时必须复述)

这是**相似度搜索**, 不是预测. 宏景是 1.4% base rate 的**幸存者**, TOP 候选大部分仍会失败. 仅作研究池, 不是买入信号. 必须叠加题材 + 基本面 + 人工判断.

## Architecture

Single-process, file-based, no database, no scheduler, no web UI.

- **`core/tdx_loader.py`** — reads TDX `.day` binary files directly from `data/tdx/{sh,sz,bj}/lday/`
- **`core/data_loader.py`** — `load_daily(symbol)` tries TDX first, falls back to parquet in `data/daily/`; `update_all_daily()` uses baostock
- **`core/formula_engine.py`** — translates TDX formula language to Python and executes against a DataFrame; supports Chinese variable names, `/* */` comments, `H/L/C/V` aliases, `XG:` labels, `REF(array, array)` vectorized lookback
- **`core/mytt.py`** — MyTT indicator library (MA, EMA, MACD, CROSS, BARSLAST, COUNT, etc.)
- **`core/strategies/fvg_pullback.py`** — Python strategy returning a 0–11111 score; template for new strategies
- **`core/stock_names.py`** — fetches stock names from baostock, caches to `data/stock_names.csv`
- **`scripts/run_scan.py`** — CLI backend for q-scan (formula mode, filter mode, strategy mode)

## Data Sources

- **Primary**: TDX `.day` binary files via `q-sync` (fast, full market, daily zip from `data.tdx.com.cn`)
- **Fallback**: baostock TCP socket via `q-pull` — bypasses IBM corporate proxy (`9.36.235.13:8080`) which blocks HTTP-based sources

## Key Constraints

- No database — parquet + numpy only
- No Docker
- No LLM calls (future V4)
- No itchat/wxpy — 企业微信 app API only (future V1)
- Serial scan loop — no threading
- Stale stock filter: skip symbols whose last data row is >30 days old (catches delisted stocks)

## Adding a New Strategy

Create `core/strategies/<name>.py` with:
```python
NAME = "策略显示名"
MIN_BARS = 65

def scan(df) -> dict | None:
    # return None if no signal
    # return {"score": int, "flag1": bool, ...} on hit
```

Run with `q-scan --strategy <name>`.

## Known Pitfalls

- `bool & float` fails in numpy — cast boolean arrays to `int8` after assignments; `_ref_smart` in `formula_engine.py` handles this for REF
- `REF(bool_array, N)` — use `_ref_smart` (not mytt's REF) which handles array-valued N for element-wise lookback
- TDX `.day` format: 32-byte records — date uint32, open/high/low/close uint32÷100, amount float32, volume uint32, reserved
- Limit-up threshold: `close >= prev_close * 1.097`, not `pct_chg >= 9.9` (breaks on ex-rights days)
- Suspended days create row gaps — `REF(CLOSE,1)` is the previous *row*, not previous calendar day
