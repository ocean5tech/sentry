# q-fin 测试计划

**对象**: `q-fin` v1.0.0 (按 `~/sentry/quant/docs/DESIGN_q-fin.md` 实施)
**Phase**: B1 (free mode foundation), B3 后再补 paid 测试
**测试方法**: 黑盒, 命令行调用 + assertion
**通过标准**: 每个 case 所有断言项 PASS

---

## 0. 前置条件

- TDX 数据已 sync 到最新 (data/tdx/)
- baostock + akshare 可访问 (内网 IBM 已测过 baostock 通过 TCP socket)
- 用户首次跑会触发 akshare cache 填充 (~30min cold), 24h 内复用
- q-seed 已跑过, `q-seed/logs/` 至少 1 份 jsonl (q_seed_crosscheck 模块用)

---

## 1. Fixtures

| 文件 | 内容 | 用途 |
|---|---|---|
| `tests/fixtures/input_codes_4.jsonl` | 4 行: 605389/603629/301396/002409 | 验证 `--input` 限范围 |
| `tests/fixtures/input_invalid.jsonl` | 第 2 行少 code 字段 | 验证非法输入 graceful skip |
| `tests/fixtures/input_unknown_code.jsonl` | 1 行 999999 | 验证未知 code skip |
| `tests/fixtures/input_empty.jsonl` | 空 | 验证空输入 |
| `tests/fixtures/bad_config.yaml` | data.tdx_dir 指向 /nonexistent | 验证 fail-fast |
| `tests/fixtures/keywords_minimal.yaml` | 仅 1 条事件类规则 (控制权变更) | 验证自定义 keywords |

---

## 2. Phase B1 测试 Case (free mode)

### 基础

#### F01: --help

- **命令**: `q-fin --help`
- **断言**:
  - exit=0
  - stdout 含 `--paid, --top, --input, --since, --until, --layer1-limit, --modules, --tags, --format, --output, --config, --no-cache, --dry-run`

#### F02: 默认裸跑 (input mode 加速版本)

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 4`
- **断言**:
  - exit=0
  - stdout 4 行 JSON Lines
  - 每行含: `code, name, scan_date, source="q-fin", mode="free", rank, score, layer1_triggers, announcements_90d, shareholders, holder_count_history, concept_status, fundamentals, kline, q_seed_crosscheck, tags, meta`
  - **`verdict` = null** (free 不出 verdict)
  - **`entity_research` = null** (free 不查实体)
  - `meta.mode` = "free"
  - `meta.providers` 不存在 (free 不用 provider)

#### F03: --top N

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 2`
- **断言**: 2 行, rank=1,2

#### F04: --top 大于实际

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 100`
- **断言**: ≤ 4 行, exit=0

---

### Layer 1 关键词 (free)

#### F05: 默认关键词扫到长龄

- **命令**: `q-fin --input <(echo '{"code":"605389"}') --top 1`
- **断言**:
  - 1 行
  - `code = 605389`
  - `layer1_triggers.matched_events` 非空 (长龄 4-22 公告里有"控制权变更"或"要约收购")
  - `layer1_triggers.severity_sum >= 5`

#### F06: 自定义 keywords.yaml

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --keywords tests/fixtures/keywords_minimal.yaml --top 4`
- **断言**: 仅命中 keywords_minimal.yaml 里的"控制权变更"规则, 其他事件类不出现

#### F07: --since/--until 时间窗口

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --since 2026-04-01 --until 2026-04-15 --top 4`
- **断言**: `announcements_90d.window` ∈ [2026-04-01, 2026-04-15]

---

### Layer 2 模块 (free)

#### F08: --modules 过滤 (白名单)

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --modules share,kline --top 4`
- **断言**: 输出 `shareholders` + `kline` 字段非 null, 但 `announcements_90d` / `fundamentals` / `holder_count_history` / `concept_status` 为 null (没跑该模块)

#### F09: shareholders top10 完整保留

- **命令**: `q-fin --input <(echo '{"code":"605389"}') --top 1`
- **断言**: `shareholders.top10_free` = 10 条 (或全部, 若小于 10 条) 数组, 每条含 rank/name/pct/change/is_partnership

#### F10: holder_count_history 8 期

- **命令**: `q-fin --input <(echo '{"code":"605389"}') --top 1`
- **断言**: `holder_count_history` 数组长度 ≥ 4 (理想 8 期, 部分股票期数不足)

#### F10b: tdx_fin 字段精度 (fundamentals 走本地 gpcw)

- **命令**: 直接调 `lib/fundamentals.py` 的 `get_latest("605389")` (q-fin 内部跑测试模块)
- **断言**:
  - `data_source = "tdx_gpcw"`
  - `period = "2025-12-31"` (最新可用)
  - `eps`, `revenue`, `net_profit`, `total_assets` 4 个字段都非 None
  - `net_margin = net_profit / revenue` 在 [0, 1] 区间 (合理净利率)

#### F10c: tdx_fin 跨期 YoY 计算

- **命令**: `get_latest("605389")` 检查 yoy_net_profit / yoy_revenue
- **断言**: 两个 yoy 字段非 None, 数值在 -100% ~ +1000% 范围内

#### F10d: tdx_fin 跨期回退 (新股票次新可能近期没数据)

- **命令**: `get_latest("301396")` (宏景, 2025-12-31 没数据应回退到 2025-09-30)
- **断言**:
  - `period = "2025-09-30"` (回退一期)
  - `src_file = "gpcw20250930.dat"`
  - 字段非 None

#### F11: kline_safety 4 档

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 4`
- **断言**: 每行 `kline.kline_safety` ∈ {🔴, 🟡, 🟢, ⚪}

#### F12: q_seed_crosscheck (q-seed logs 已存在时)

- **前置**: 先跑 `q-seed --top 30` 产生 logs
- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 4`
- **断言**: 至少 1 行 `q_seed_crosscheck.is_in_q_seed = true` (605389 长龄通常在 hongjing TOP 30)

#### F13: q_seed logs 不存在时 (优雅降级)

- **前置**: 临时清空 q-seed/logs/ (备份回去)
- **命令**: 同 F12
- **断言**:
  - exit=0
  - 每行 `q_seed_crosscheck.is_in_q_seed = null`
  - stderr 含 "q-seed logs not found, crosscheck skipped"

---

### 输入输出 (free)

#### F14: --input 文件

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 5`
- **断言**: ≤ 4 行, code ⊂ {605389, 603629, 301396, 002409}

#### F15: stdin 管道

- **命令**: `cat tests/fixtures/input_codes_4.jsonl | q-fin --top 5`
- **断言**: 与 F14 同

#### F16: q-seed 上游管道

- **命令**: `q-seed --template hongjing --top 10 | q-fin --top 5`
- **断言**:
  - exit=0
  - ≤ 5 行
  - 输出 code 全部 ∈ q-seed 输出的 code

#### F17: --tags 过滤

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --tags AI,算力 --top 4`
- **断言**: 输出每行 `tags` 至少含 "AI" 或 "算力"

#### F18: --format md

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 3 --format md`
- **断言**: stdout 是 markdown 表格, 含表头, 3 行数据

#### F19: --format both --output

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 3 --format both --output /tmp/f19`
- **断言**: `/tmp/f19.jsonl` + `/tmp/f19.md` 都生成

#### F20: 自动 logs 备份

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --top 4`
- **断言**: `q-fin/logs/q-fin_top4_YYYYMMDD_HHMM.jsonl` 自动产生

---

### 失败模式

#### F21: 非法 stdin (缺 code 字段)

- **命令**: `q-fin --input tests/fixtures/input_invalid.jsonl --top 5`
- **断言**: exit=0, stderr 含 "missing 'code'", 输出 < 行数

#### F22: 未知 code

- **命令**: `q-fin --input tests/fixtures/input_unknown_code.jsonl`
- **断言**: exit=0, stdout 空, stderr 含 "999999"

#### F23: 空输入

- **命令**: `q-fin --input tests/fixtures/input_empty.jsonl`
- **断言**: exit=0, stdout 空

#### F24: 错误 config

- **命令**: `q-fin --config /nonexistent/config.yaml`
- **断言**: exit=1, stderr 含 path

#### F25: bad tdx_dir

- **命令**: `q-fin --config tests/fixtures/bad_config.yaml --top 5`
- **断言**: exit=1, stderr 含 "/nonexistent/tdx"

#### F26: akshare 接口挂 (mock)

- **命令**: 通过临时 config 把 `sources.akshare.enabled: false` → 跑 q-fin
- **断言**: exit=0, 子模块字段 null, stderr 含 "akshare disabled or unavailable"

---

### 缓存

#### F27: cache 二次运行加速

- **前置**: 删 data_cache/ 重新冷跑一次
- **命令**: 同一命令跑 2 次, 测耗时
- **断言**: 第二次耗时 ≤ 第一次的 30% (cache 命中)

#### F28: --no-cache 强制重拉

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --no-cache --top 4`
- **断言**: 即使有 cache, 耗时与冷跑接近 (cache 不命中)

---

### 全市场 (SLOW, RUN_SLOW=1 才跑)

#### F29: 默认全市场扫 (cold)

- **命令**: `q-fin --top 15`
- **断言**: 
  - exit=0
  - 15 行
  - 耗时 ≤ 60 min (冷跑可能慢, 暖跑 < 5min)
  - logs 有备份

#### F30: dry-run

- **命令**: `q-fin --dry-run`
- **断言**:
  - exit=0
  - stderr 含 "Layer 1 candidates: N" + "Estimated --paid cost: $X.XX"
  - 不实际跑 Layer 2

---

## 3. Phase B2/B3 测试 Case (paid mode)

**指导原则**: 全部 case 默认走 `--llm dummy` (0 成本, 算法验证), 仅 F44 走真实 anthropic 验证长龄能追到胡康桥. 真 LLM 单 case 预算上限 `$0.10`.

### B2 Provider 抽象 (dummy 路径)

#### F31: dummy LLM provider 基础调用

- **目的**: 验证 provider factory + dummy 实现可正确返回 mock 响应
- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --paid --top 4 --llm dummy --search dummy`
- **断言**:
  - exit=0
  - 4 行 JSON, 每行 `meta.providers = {"llm": "dummy", "search": "dummy"}`
  - 每行 `meta.total_cost_usd == 0.0`
  - `verdict` 非 null, 含 `rating, one_liner, key_risks`
  - `entity_research` 非 null, 含 `chain` 字段

#### F32: dummy 模式 cost_log 不增长

- **命令**: 跑两次 F31, 比对 `data_cache/cost_log.jsonl` 行数
- **断言**: 两次行数相同 (dummy 应该不写 cost_log) 或行数差等于实际 LLM 调用次数 × 0 美元

#### F33: budget_guard 超限降级

- **命令**: `q-fin --input tests/fixtures/input_codes_4.jsonl --paid --llm dummy --budget 0.0001 --top 4`
- **断言**:
  - exit=0
  - **dummy 是 0 成本所以不会真触发**, 改用 mock_pricing dummy: `--llm dummy_priced` (config 里给 dummy 配 pricing>0 的备用 profile, 见 FT-A)
  - 或简单: 跳过此 case 改在 B3 用 anthropic 跑 F33b
- **替代 F33b** (B3 用): `q-fin ... --paid --budget 0.001` → stderr 含 "budget exceeded, fallback to free", `meta.mode = "partial_standard"`, 部分 record verdict=null

#### F34: provider 配置冲突 (anthropic_tool 要求 llm=anthropic)

- **命令**: `q-fin --paid --llm openai_compat --search anthropic_tool ...`
- **断言**: exit=2, stderr 含 "anthropic_tool requires llm=anthropic"

#### F35: 缺 API key 自动降 free

- **命令**: 临时 `unset ANTHROPIC_API_KEY; q-fin --paid --llm anthropic --top 1 --input ...`
- **断言**: exit=0, stderr 含 "ANTHROPIC_API_KEY missing, fallback to free", `meta.mode = "free"`, verdict=null

### B3 实体调查 + verdict (dummy 路径)

#### F36: entity_hints.yaml 命中跳过 LLM

- **前置**: `config/entity_hints.yaml` 加一条 `partnerships."无锡核芯听涛科技合伙企业(普通合伙)"` 含 gp/controller
- **命令**: `q-fin --input <(echo '{"code":"605389"}') --paid --llm dummy --top 1`
- **断言**:
  - `entity_research.chain.entity` = "无锡核芯听涛科技合伙企业(普通合伙)"
  - `entity_research.chain.source = "hints"` (不是 LLM 调用)
  - `entity_research.budget_used_usd = 0.0`

#### F37: max_depth 配置生效

- **命令**: `q-fin ... --paid --llm dummy --max-depth 1` 跑长龄
- **断言**: `entity_research.max_depth_used = 1`, chain.sub 长度 0 (不递归到 1 层以下)

#### F38: --paid=deep 启用 web search 扩展

- **命令**: `q-fin --input ... --paid=deep --llm dummy --search dummy --top 1`
- **断言**:
  - `mode = "deep"`, `meta.providers.search = "dummy"`
  - `entity_research.max_depth_used = 3`
  - `verdict` 含 `theme_hardness` 字段
  - dummy search 应被调用至少 1 次 (mock 返回 1 条结果, dummy 实现里加计数器, 输出含 search_calls > 0)

#### F39: verdict TOP N 升级模型

- **命令**: `q-fin --input fixtures/input_codes_4.jsonl --paid --llm dummy --top 4`
- **断言**: 前 5 名 (此处 4 个) 的 `verdict.verdict_model` 应 = config `verdict_model`; 若超过 5 名后续用 `default_model` (此 case 无)

#### F40: per-stock budget 上限 enforcement

- **命令**: `q-fin ... --paid --entity-budget-per-stock 0.01 --llm dummy_priced` (备用 profile)
- **断言**: `entity_research.budget_used_usd <= 0.01`, 若超限则 chain.sub 截断, 含 `_truncated_reason: "per_stock_budget"`

### B3 真 LLM 验证 (单 case, ~$0.05)

#### F44: 长龄液压 entity_chain 追到胡康桥 ⭐ 关键验证

- **前置**: `ANTHROPIC_API_KEY` 有效, `entity_hints.yaml` 清空 (确保走真 LLM)
- **命令**: `BUDGET=0.10 q-fin --input <(echo '{"code":"605389"}') --paid --llm anthropic --search duckduckgo --top 1 --budget 0.10`
- **断言**:
  - exit=0
  - `entity_research.chain.entity` 含 "核芯听涛"
  - `entity_research.chain.sub[*].entity` 包含 "核芯互联" (子节点之一)
  - 进一步 sub 中含 "胡康桥" 或描述提到 (允许 embedded 在 background 字段)
  - `meta.total_cost_usd <= 0.10`
  - 跑完后 `entity_hints.yaml` 自动追加该 3 个实体 (auto-cache)

### 其他

#### F42: --paid + --modules 子集 (不跑 entity)

- **命令**: `q-fin --paid --modules ann,share,fund --top 1 --input ...`
- **断言**: `verdict = null`, `entity_research = null` (没 entity 模块)

#### F43: --dry-run 估成本不真跑

- **命令**: `q-fin --paid --dry-run --input fixtures/input_codes_4.jsonl`
- **断言**: stdout 含 "Estimated --paid cost: $X.XX", 不调 LLM (cost_log 不增长), exit=0

---

## 4. 已确认决策 (用户已拍板)

| # | 决策 |
|---|---|
| F-T1 | 默认 free, 永远不烧钱; `--paid` / `--paid=deep` 显式启用 |
| F-T2 | provider 抽象, config 切换; B1 阶段不做 provider, B2 单独做 |
| F-T3 | Layer 1 全市场 27min 接受, 24h cache 必做 |
| F-T4 | balance_per_template 不适用 (q-fin 不分模板) |
| F-T5 | 全自动断言, jq 替代用 .venv/bin/python -c |

---

## 5. 跑测试

`tests/run_tests.sh`:
```
RUN_SLOW=1 → 跑 F29/F30 全市场
默认 → 跳过 SLOW
```

汇总报告类似 q-seed.

---

## 6. 不覆盖项 (后续版本)

| 项 | 原因 |
|---|---|
| 龙虎榜 (dragon module) | DESIGN F2 已说 V0 不做 |
| 历史回溯/复现 | 用户已明确不做 (Q12) |
| 多 config 切换 | V0 单 config |
| 内网 akshare 拦截测试 | 用户运行环境再实测 |
| 并发抓取 | V0 单线程, 留给后续优化 |

---

## 7. 等你审核 (B1 已完成, 此处仅 B2/B3 新增问题)

| # | 问题 | 我建议 |
|---|---|---|
| FT-A | dummy LLM 是不是该有"带价格但不联网"的 profile, 让 budget_guard 能在离线测试里被触发? | 加. 命名 `dummy_priced`, mock pricing $1/MTok in/out, 真实调用返回固定 mock 文本但 budget_guard 按 mock token 数算成本 |
| FT-B | F44 真 LLM 测试单次预算 $0.10 够吗? 想再保守? | 够 (Haiku 全套含 web 搜约 $0.04). 给硬上限 $0.10 防失控 |
| FT-C | F44 跑完写 entity_hints.yaml 应该用 .bak 备份再覆盖? 还是直接追加? | 直接追加 (yaml 末尾 append) + git diff 可见. 不备份 |
| FT-D | dummy LLM 的 mock 响应应该是固定 (deterministic) 还是带随机? | 固定, 让断言可写死. 长龄案例返回硬编码 "核芯听涛 → 核芯互联 → 胡康桥" 链 |
| FT-E | duckduckgo 限流后 retry 几次? 用户网络环境可能不稳 | 2 次 retry + 3s sleep, 失败 fallback skip_entity (不退出) |
| FT-F | F38 deep 模式 max_depth=3 真 LLM 跑成本飙到 $0.20+, 是否 dummy 跑足够? | dummy 跑足够; deep 真 LLM 不在 V0 测试 |

回 "B2/B3 开干" 我就:
1. 实施 lib/providers/ (5+4 个 provider 文件 + factory)
2. 实施 budget_guard / entity_research / llm_verdict
3. main.py 接入 --paid 路径
4. 跑 dummy case (F31-F43) → 修 bug → 全 PASS
5. 跑 F44 真 LLM 验证长龄 (限 $0.10)
6. 给报告
