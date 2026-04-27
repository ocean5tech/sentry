# q-news 测试计划

**对象**: `q-news` v1.0.0 (按 `~/sentry/quant/docs/DESIGN_q-news.md` + `SOURCES_q-news.md` 实施)
**Phase**: B4 (free 规则 + paid LLM 兜底)
**测试方法**: 黑盒命令行 + assertion. dummy provider 全自动, 真 LLM 单 case 用户授权后跑.
**通过标准**: 每个 case 所有断言 PASS.

---

## 0. 前置条件

- TDX 日线在 `data/tdx/`
- q-seed 跑过, `~/sentry/quant/q-seed/logs/` 有最新 jsonl (concept_status "已启动" 判定要读)
- q-fin 可选 (有 logs 就一起读)
- akshare 可达 (内网已实测通)
- 新华网 RSS 可达 (内网已实测通)
- `ANTHROPIC_API_KEY` 在 `~/sentry/quant/.env` (仅 N44 真 LLM case 需要)

---

## 1. Fixtures

| 文件 | 内容 | 用途 |
|---|---|---|
| `tests/fixtures/news_sample.jsonl` | 5 条 mock 新闻 (OPEC 减产/AI 订单/FDA 批准/控制权变更/无关新闻) | 离线 rule_engine 测试 |
| `tests/fixtures/input_codes_4.jsonl` | 4 行 (605389/301396/603629/002409) | --input 限定 code 反查 |
| `tests/fixtures/sector_map_minimal.yaml` | 仅 1 个概念 (稀有气体) 含 002409 | 验证概念命中 |
| `tests/fixtures/kb_minimal.yaml` | 仅 1 条规则 (opec_减产) | 验证规则匹配 |
| `tests/fixtures/bad_config.yaml` | data.tdx_dir = /nonexistent | fail-fast 验证 |

---

## 2. Phase B4 测试 Case (free 模式)

### 基础

#### N01: --help

- `q-news --help`
- 断言: exit=0, stdout 含 `--paid, --top, --input, --since, --until, --sources, --tags, --format, --output, --config, --no-cache, --dry-run`

#### N02: 默认裸跑 free 模式 (无 --input)

- 命令: `q-news --top 5`
- 行为: 拉 24h 新闻 (akshare 3 接口 + 新华网 5 RSS), 跑 rule_engine, drop 无规则命中的新闻, 聚合到股票, 输出 TOP 5
- 断言:
  - exit=0
  - stdout ≤5 行 JSON Lines (可能空)
  - 每行含: `code, name, scan_date, source="q-news", mode="free", rank, score, triggered_events[], concept_status, kline, q_seed_crosscheck, tags, meta`
  - `verdict = null` (free 不出 verdict)
  - `meta.events_scanned > 0`, `meta.providers` 不存在 (free 不用 provider)

#### N03: --top N

- `q-news --top 2 --since 2026-04-01`
- 断言: ≤2 行, rank=1,2

#### N04: --top 大于实际命中

- 实际只有 1 条规则命中 → 输出 1 行
- 断言: 行数 ≤ N, 不报错

### 输入

#### N05: --input 反查个股新闻

- `q-news --input tests/fixtures/input_codes_4.jsonl --top 4`
- 行为: 跳过全市场新闻, 对 4 个 code 调 akshare `stock_news_em(symbol)` 反查
- 断言: 输出 code 必须 ∈ {605389, 301396, 603629, 002409}

#### N06: stdin pipe

- `cat input_codes_4.jsonl | q-news --top 4`
- 断言: 同 N05

#### N07: q-seed 管道

- `q-seed --top 5 | q-news --top 3`
- 断言: 输出 code 必须是 q-seed 给的 5 个之一

#### N08: 空输入 (空 input file 或空 q-seed 输出)

- `q-news --input tests/fixtures/input_empty.jsonl`
- 断言: exit=0, stdout 空 (不报错)

### 数据源

#### N09: --sources 限定数据源

- `q-news --sources akshare:cls,rss:新华网-经济 --top 3`
- 断言: meta.sources_used 只含这两个

#### N10: 单个 RSS 拉新闻成功

- `q-news --sources rss:新华网-经济 --top 3 --no-cache`
- 断言: meta.events_scanned > 0 (新华网经济 RSS 通常有 ~20 条新闻)

#### N11: akshare cls 单独跑

- `q-news --sources akshare:cls --top 3 --no-cache`
- 断言: meta.events_scanned ≥ 10 (cls 通常 20+ 条)

#### N12: RSS 部分挂自动跳过

- 临时配置一个不存在 RSS URL → q-news 标记 blocked, 不退出
- 命令: `q-news --config tests/fixtures/bad_rss_config.yaml --top 3`
- 断言: stderr 含 "blocked" 或类似 warning, exit=0

### 规则引擎 (free)

#### N13: 单规则单事件命中

- 用 fixtures kb_minimal (只 opec_减产) + sector_map_minimal (只 稀有气体: 002409)
- mock 新闻 "OPEC 决议减产 200 万桶/日"
- 命令: `q-news --config <bypass> --news-file fixtures/news_sample.jsonl --top 5`
  (注: `--news-file` 是测试模式 flag, 跳过 fetcher 直接读静态)
- 断言: 输出 1 行, code=002409, triggered_events 含 reasoning_chain 5 层

#### N14: 多规则同时命中同一新闻

- 例: 一条新闻同时含 "OPEC" "减产" "中东" → opec_减产 + 油价_地缘冲突 都激活
- 断言: triggered_events 数组长度 ≥2, 各自 reasoning_chain 不同

#### N15: triggers 不足 2 个不激活

- 新闻仅含 "OPEC" 一个 trigger (没 "减产")
- 断言: 该规则不激活, 这条新闻被 drop

#### N16: 未命中规则 free 模式 drop

- mock 新闻 "某城市天气预报" (无规则命中)
- 断言: 该新闻不出现在 stdout, stderr 或 logs/unmatched_events.jsonl 记录

### concept_status (核心 alpha)

#### N17: 概念未启动 (利好但 q-seed 没命中)

- 假设 002409 不在 q-seed top 30
- mock OPEC 减产 → 命中稀有气体 → 002409
- 断言:
  - `concept_status.已启动 = false`
  - `concept_status.active_in_qseed = 0`
  - `concept_status.active_pct < 0.3`
  - `concept_status.verdict` 含 "🟢" 或 "未启动"

#### N18: 概念已启动 (q-seed 大量命中)

- 假设稀有气体板块 5 只股票, 4 只在 q-seed top 30 (active_pct=0.8)
- 断言:
  - `concept_status.已启动 = true`
  - `concept_status.verdict` 含 "🔴" 或 "已启动"

#### N19: q-seed logs 不存在

- 临时把 q_seed_logs_dir 指到不存在路径
- 断言: `concept_status.已启动 = null`, `verdict` 含 "无 q-seed 数据" 或类似, 不退出

### 输出格式

#### N20: --format jsonl (默认)

- `q-news --top 3 --format jsonl`
- 断言: 每行有效 JSON, 字段完整

#### N21: --format md

- `q-news --top 3 --format md`
- 断言: stdout 含 `| code |` 表头, 不含 `{`

#### N22: --format both --output result

- 断言: 生成 `result.jsonl` + `result.md`

#### N23: 自动 logs 备份

- 跑后 `logs/q-news_top<N>_YYYYMMDD_HHMM.jsonl` 自动写
- 断言: 文件存在且行数与 stdout 一致

### 失败模式

#### N24: 非法 --input 行 (缺 code)

- input_invalid.jsonl 第 2 行无 code
- 断言: exit=0, stderr warn 跳过, 其他行正常

#### N25: 非法 --paid 值

- `q-news --paid foobar`
- 断言: exit=2, stderr 含错误说明

#### N26: bad config 路径

- `q-news --config /nonexistent/config.yaml`
- 断言: exit≠0, stderr 含路径

#### N27: BrokenPipe 优雅退出

- `q-news --top 100 | head -1`
- 断言: exit=0, 不抛 BrokenPipeError 异常

#### N28: Ctrl-C 中断

- (手动) 跑全市场 fetch 中按 Ctrl-C
- 断言: 已 fetch 部分写到 logs/ 部分文件, exit=130

### Cache

#### N29: 24h cache 加速

- 第 1 次 cold 跑 → 第 2 次 warm 跑 ≤ cold × 30%
- 仅 fetcher 层 cache, rule_engine 总是重跑 (规则可改不用刷 cache)

---

## 3. Phase B4 paid 测试 Case (LLM 兜底)

### N30: --paid + dummy provider 基础调用

- `q-news --top 3 --paid --llm dummy --search dummy`
- 断言:
  - mode=standard
  - meta.providers={llm:dummy, search:dummy}
  - 未命中规则的新闻 → LLM 兜底产出 reasoning_chain (来自 dummy mock)
  - meta.total_cost_usd = 0

### N31: --paid + 缺 API key 自动降 free

- 临时 unset ANTHROPIC_API_KEY
- 命令: `q-news --paid --llm anthropic --top 3`
- 断言: stderr 含 fallback, mode=free, total_cost_usd 不存在

### N32: --paid=deep 启用 web_search

- `q-news --paid=deep --llm dummy --search dummy --top 3`
- 断言: mode=deep, dummy search 被调

### N33: budget 超限触发 fallback (dummy_priced)

- `q-news --paid --llm dummy_priced --budget 0.0001 --top 5`
- 断言: 至少 1 个 record verdict._skip_reason="run_cap_exceeded" 或 mode=partial_standard

### N44: 真 LLM 端到端 ⚠️ 仅用户授权后跑

- 命令: `q-news --paid --llm anthropic --top 3 --budget 0.10 --since 2026-04-25`
- 行为: 真拉新闻 + Haiku LLM 兜底未命中规则的事件
- 断言:
  - exit=0
  - meta.total_cost_usd ≤ 0.10
  - 至少 1 条事件经 LLM 推理 (reasoning_method="llm")
  - 这些事件的 reasoning_chain 至少 3 层 (impact + analysis + concept)
- **预算预估**: $0.05-0.10 (含可选 web_search; 单事件 LLM tokens 与 q-fin entity_research 相近)

---

## 4. 已确认决策

| # | 决策 |
|---|---|
| NT-1 | 默认 free 永远不烧 token; --paid / --paid=deep 显式启用 |
| NT-2 | 输出 stock-based, 与 q-seed/q-fin schema 对齐 (含 q_seed_crosscheck + q_fin_crosscheck) |
| NT-3 | 同一事件多股触发不去重, 用 score 排序 + balance_per_concept (类似 q-seed balance_per_template) |
| NT-4 | 未命中规则 free 模式 drop, 写 logs/unmatched_events.jsonl 供后续补规则 |
| NT-5 | 个股新闻 (`--input`) 走 akshare stock_news_em 反查 |
| NT-6 | RSS 部分挂自动 skip 标 blocked, 不退出 |
| NT-7 | concept_status."已启动" = 概念内 q-seed 命中股 / 概念股总数 ≥0.5 |

---

## 5. 不覆盖项 (V1+)

| 项 | 原因 |
|---|---|
| 海关一手数据 | customs.gov.cn 412 反爬, 用 akshare macro 替代, 单测不做 |
| RSSHub 第三方 | 内网拦, 单测不做 |
| 历史回溯 | 用户已明确 V0 不做 |
| 国际 RSS (Reuters/Bloomberg) | 拦, V0 跳过 |
| 全市场反查个股新闻 (5300 只) | 太慢, 留给 q-seed/q-fin 缩范围后再做 |

---

## 6. 等用户拍板 (B4 实施前)

| # | 议题 | 我建议 |
|---|---|---|
| NT-A | N13 测试要不要加 `--news-file` 测试模式 flag (跳过 fetcher 直接读 fixture)? | 加, 否则无法离线测 rule_engine |
| NT-B | concept_status 判 "已启动" 阈值 active_pct ≥0.5? 还是 0.3? | 0.5 (一半概念股已动 → 算启动). 写到 config 用户可调 |
| NT-C | sector_map.stocks 为空时 (你还在填) 怎么办? | rule 命中但 sector_map 没 stocks → 输出概念事件但 code=null (不进股票排序). stderr 提示 "请填 sector_map" |
| NT-D | N44 真 LLM 测试默认跳过吗? | 跳过, 用户用 `RUN_REAL_LLM=1 bash tests/run_tests.sh` 显式启用 |

回 OK / 调整, 我开干 B4.4 骨架.
