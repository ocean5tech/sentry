# CLAUDE.md

## Rules

1. **Think before coding** — state assumptions; ask if ambiguous; push back on over-engineering.
2. **Simplicity first** — minimum code; no speculative features; if 50 lines work, don't write 200.
3. **Surgical changes** — touch only what's asked; don't refactor adjacent code.
4. **Plan before multi-step tasks** — state checkpoints; strong success criteria.

## Project

A-share quant screening system on WSL2. Full market (~11k stocks), file-based, no DB, no scheduler.

## Shell Commands

```bash
q-sync               # Download TDX daily zip → data/tdx/
q-pull               # Full baostock pull (slow, 3yr history)
q-pull-update        # Incremental baostock update
q-scan --strategy fvg_pullback --min-score 11111
q-scan --formula "CLOSE>=REF(CLOSE,1)*1.097 AND CLOSE==HIGH"
q-seed-hongjing [N]  # 疯牛种子-宏景型 (strict wave, template 301396)
q-seed-litong [N]    # 疯牛种子-利通型 (loose W-bottom, template 603629)
q-backtest-fast      # Walk-forward 回测 multi-horizon (5/10/20/40/80d)
q-fin                # LLM event research (~$0.10/stock, ask before running)
q-push               # Push candidates to WeChat webhook
q-dashboard          # Streamlit dashboard on :8501
```

## 疯牛种子 路由

- "疯牛种子" / "种子选股" → **问用户**跑哪个 (不要默选)
- "宏景" → `q-seed-hongjing`; "利通" → `q-seed-litong`; "都跑" → 顺序跑两个
- 6 模板: hongjing/litong/xiangnong(300475)/fujing(002222)/yunnange(002428)/lanqi(688008)
- SEED_CODES (自身排除): {301396, 603629, 300475, 002222, 002428, 688008}

**警告**: 相似度搜索, 不是预测. 宏景是 1.4% base-rate 幸存者. 仅研究池, 必须叠加题材 + 基本面 + 人工判断.

## Architecture

- `core/tdx_loader.py` — TDX `.day` binary reader (`data/tdx/{sh,sz,bj}/lday/`)
- `core/data_loader.py` — `load_daily()`: TDX first, falls back to parquet
- `core/formula_engine.py` — TDX formula → Python; REF(array,array) vectorized
- `core/mytt.py` — MA/EMA/MACD/CROSS/BARSLAST/COUNT etc.
- `core/strategies/fvg_pullback.py` — strategy template (score 0–11111)
- `scripts/run_scan.py` — q-scan CLI backend

## Key Constraints

- No DB (parquet + numpy only), no Docker, no threading
- baostock TCP bypasses IBM proxy (`9.36.235.13:8080`)
- Stale filter: skip symbols with last row >30 days old
- 企业微信: push 1股1条, 财经分析师语气; q-fin 仅"非常非常好"的股才用

## Adding a Strategy

Create `core/strategies/<name>.py`:
```python
NAME = "显示名"
MIN_BARS = 65
def scan(df) -> dict | None: ...  # None=no signal; dict must have "score" key
```

## Known Pitfalls

- `bool & float` in numpy → cast bool arrays to `int8`; `_ref_smart` in formula_engine handles REF
- TDX `.day`: 32-byte records, prices = uint32 ÷ 100
- Limit-up: `close >= prev_close * 1.097` (not pct_chg — breaks on ex-rights days)
- Suspended days create row gaps — `REF(CLOSE,1)` = previous *row*, not calendar day
