# q-seed 测试计划

**对象**: `q-seed` v1.0.0 (按 `~/sentry/quant/docs/DESIGN_q-seed.md` 实施的版本)
**测试目标**: 验证 q-seed 在所有支持的参数组合 + 失败模式下行为符合 DESIGN 文档承诺
**测试方法**: 黑盒, 通过命令行调用 + 对比 stdout / stderr / 产出文件 / exit code
**通过标准**: 每个 case 的所有 "断言" 项都满足

---

## 0. 前置条件 (所有 case 共享)

- `~/sentry/quant/data/tdx/{sh,sz}/lday/` 已存在, 含 2026-04-22 (或更新) 的日线数据
- `~/sentry/quant/data/stock_names.csv` 已 fetch (含 ≥ 5000 个股票名)
- `q-seed/.venv/` 已建好, 装齐 `requirements.txt` (pandas, numpy, pyyaml)
- `q-seed/q-seed` (bash 入口) 在 `$PATH` 或可用绝对路径调用
- `q-seed/config.yaml` 是 DESIGN §3 的版本, 模板硬编码 hongjing(301396) + litong(603629)
- 模板的 `sig_dates` 在 TDX 数据里都能找到对应交易日

---

## 1. 数据 fixtures

| 文件 | 内容 | 用途 |
|---|---|---|
| `tests/fixtures/input_codes_4.jsonl` | 4 行 JSON, code = `301396` (宏景), `603629` (利通), `605389` (长龄液压), `002409` (华特气体) | 验证 `--input` 限定范围 |
| `tests/fixtures/input_invalid.jsonl` | 第 2 行少 `code` 字段 | 验证非法输入 stderr |
| `tests/fixtures/input_unknown_code.jsonl` | 1 行 `{"code":"999999"}` | 验证未知 code 的 graceful skip |
| `tests/fixtures/input_empty.jsonl` | 空文件 | 验证空输入 |

---

## 2. 测试 Case 列表

每个 case 5 段: **目的 / 命令 / 预期 / 验证方法 / 通过断言**.

---

### C01: 默认裸跑 (smoke test)

- **目的**: 验证 `q-seed` 不带任何参数能跑完, 输出符合 schema
- **命令**: `q-seed`
- **预期**:
  - exit code = 0
  - stdout: 30 行 JSON Lines (默认 `--top 30`)
  - 每行含 §6 schema 的所有顶层字段: `code, name, scan_date, source="q-seed", rank, score, score_type, templates_matched, best_template, best_dist, details, kline, meta`
  - `templates_matched` 至少含一个 ∈ {"hongjing","litong"}
  - `details.{best_template}` 不为 null, 含 `n_waves >= 2, sig_date <= today`
  - `kline.kline_safety` ∈ {"🔴","🟡","🟢","⚪"}
  - 文件 `q-seed/logs/q-seed_top30_<YYYYMMDD_HHMM>.jsonl` 自动产生
  - 同时产生 `.md` 伴生文件
- **验证方法**:
  ```bash
  q-seed > /tmp/c01.jsonl 2>/tmp/c01.err
  echo "exit=$?"
  wc -l /tmp/c01.jsonl                                    # 应为 30
  jq -e '.code, .source, .templates_matched, .details, .kline' /tmp/c01.jsonl | head -20
  jq -r '.kline.kline_safety' /tmp/c01.jsonl | sort -u   # 应在 4 档内
  ls -la ~/sentry/quant/q-seed/logs/ | tail -5
  ```
- **通过断言**:
  - [ ] exit=0
  - [ ] stdout 行数 = 30
  - [ ] 每行 `jq` 解析成功
  - [ ] `kline_safety` 全部 ∈ {🔴,🟡,🟢,⚪}
  - [ ] logs/ 下有今天时间戳的 .jsonl 和 .md
  - [ ] stderr 无 ERROR / Traceback

---

### C02: `--top N` 参数 (小)

- **目的**: 验证 `--top 5` 输出 5 行, 排序 by best_dist asc
- **命令**: `q-seed --top 5`
- **预期**:
  - stdout 5 行
  - `rank` 字段连续 1..5
  - `best_dist` 严格非递减 (允许并列)
- **通过断言**:
  - [ ] 行数 = 5
  - [ ] `jq -r '.rank'` 输出 `1\n2\n3\n4\n5`
  - [ ] `jq -r '.best_dist'` 序列 sorted asc

---

### C03: `--top N` 参数 (大, 超候选数)

- **目的**: 当请求 N 大于实际候选数时, 输出实际数量, 不报错
- **命令**: `q-seed --top 5000`
- **预期**:
  - exit=0
  - stdout 行数 ≤ 5000 (实际 ~300-600)
  - stderr 无 error (允许 warning "requested 5000 > actual N, returning N")
- **通过断言**:
  - [ ] exit=0
  - [ ] 行数 < 5000 但 > 0

---

### C04: `--template hongjing`

- **目的**: 验证只跑宏景模板
- **命令**: `q-seed --template hongjing --top 10`
- **预期**:
  - 每行 `templates_matched` = `["hongjing"]`
  - `details.hongjing` 不为 null, `details.litong` = null
  - `best_template` = "hongjing"
- **通过断言**:
  - [ ] `jq -e '.templates_matched == ["hongjing"]' --top 10 行` 全 true
  - [ ] `jq -e '.details.litong == null'` 全 true

---

### C05: `--template litong`

- **目的**: 同 C04, 验证利通
- **命令**: `q-seed --template litong --top 10`
- **预期**: 镜像 C04 (litong 不为 null, hongjing = null)

---

### C06: `--template both` (显式, 默认值)

- **目的**: 验证显式 `--template both` 与默认行为一致
- **命令**: `q-seed --template both --top 30`
- **预期**: 输出与 C01 相同 (因为 C01 也是 both)
- **通过断言**: 与 C01 输出 diff (允许 scan 时间差异, 主体一致)

---

### C07: `--input` 文件输入

- **目的**: 验证 `--input` 限定扫描范围到 4 个 code
- **命令**: `q-seed --input tests/fixtures/input_codes_4.jsonl --top 5`
- **预期**:
  - stdout ≤ 4 行 (不是所有 4 个都能命中, 因为模板自身可能因 sig_date 在窗口外被过滤)
  - 输出的 code 必须 ∈ 输入的 4 个
- **通过断言**:
  - [ ] `jq -r '.code'` 输出全部 ∈ {301396, 603629, 605389, 002409}

---

### C08: stdin 管道输入

- **目的**: 验证 stdin 可作输入源
- **命令**: `cat tests/fixtures/input_codes_4.jsonl | q-seed --top 5`
- **预期**: 同 C07
- **通过断言**: 行为与 C07 一致

---

### C09: stdin + 单行输入

- **目的**: 验证最小输入 (1 个 code)
- **命令**: `echo '{"code":"301396"}' | q-seed --top 1`
- **预期**:
  - stdout = 1 行
  - `code` = "301396"
  - `templates_matched` 含 "hongjing" (它就是模板自己)
  - `best_dist` ≈ 0 (与自己最像)
- **通过断言**:
  - [ ] 行数 = 1
  - [ ] code = 301396
  - [ ] best_dist < 0.5

---

### C10: `--since` 时间过滤

- **目的**: 验证 sig_date >= since 才输出
- **命令**: `q-seed --since 2026-04-15 --top 30`
- **预期**: 所有输出的 `details.<template>.sig_date` >= 2026-04-15
- **通过断言**:
  - [ ] `jq -r '.details[].sig_date' | sort -u` 最早日期 >= 2026-04-15

---

### C11: `--since` + `--until` 区间

- **目的**: 验证窗口
- **命令**: `q-seed --since 2026-04-01 --until 2026-04-15 --top 30`
- **预期**: sig_date ∈ [2026-04-01, 2026-04-15]
- **通过断言**: `jq` 验证日期区间

---

### C12: `--format md`

- **目的**: 验证 markdown 表格输出
- **命令**: `q-seed --top 5 --format md`
- **预期**:
  - stdout 是 markdown 表格 (开头 `# q-seed TOP 5 · 2026-04-25`)
  - 包含 `| # | code | name | 模板 | dist | sig_date | entry | safety | ret60 | 备注 |` 表头
  - 5 行数据行
- **通过断言**:
  - [ ] stdout 含 `| # |` 表头
  - [ ] 数据行数 = 5

---

### C13: `--format both`

- **目的**: 验证 jsonl + md 同时输出
- **命令**: `q-seed --top 3 --format both --output /tmp/c13`
- **预期**: 产生 `/tmp/c13.jsonl` 和 `/tmp/c13.md`
- **通过断言**:
  - [ ] 两个文件都存在
  - [ ] jsonl 3 行, md 含表格

---

### C14: 管道下游 (q-seed 输出 → q-seed)

- **目的**: 验证 q-seed 自回路 (输出可作为输入)
- **命令**: `q-seed --top 30 | q-seed --top 10`
- **预期**:
  - 第二个 q-seed 输出 ≤ 10 行
  - 输出 code 全部 ∈ 第一次 30 行的 code
- **通过断言**:
  - [ ] 行数 ≤ 10
  - [ ] code 都是上游产出过的

---

### C15: 非法 stdin (缺 code 字段)

- **目的**: 验证非法 JSON 行被 graceful skip + stderr warning
- **命令**: `q-seed --input tests/fixtures/input_invalid.jsonl --top 5`
- **预期**:
  - exit=0 (其他行能处理)
  - stderr 含 "missing 'code' field" 或类似 warning
  - stdout 行数 < 5 (非法行被跳过)
- **通过断言**:
  - [ ] exit=0
  - [ ] stderr 含 warning 字样
  - [ ] stdout 不空

---

### C16: 未知 code

- **目的**: 验证 TDX 找不到的 code 被 graceful skip
- **命令**: `q-seed --input tests/fixtures/input_unknown_code.jsonl`
- **预期**:
  - exit=0
  - stdout 0 行
  - stderr 含 "code 999999 not found in TDX"
- **通过断言**:
  - [ ] exit=0
  - [ ] stdout 空

---

### C17: 空输入

- **目的**: 验证空输入不崩
- **命令**: `q-seed --input tests/fixtures/input_empty.jsonl`
- **预期**:
  - exit=0
  - stdout 0 行
  - stderr 含 "no input codes" warning

---

### C18: 错误 config 路径

- **目的**: 验证 config 不存在时 fail fast
- **命令**: `q-seed --config /nonexistent/config.yaml`
- **预期**:
  - exit=1
  - stderr 含 "config not found: /nonexistent/config.yaml"
- **通过断言**:
  - [ ] exit ≠ 0
  - [ ] stderr 含具体路径

---

### C19: TDX 目录配置错 (用临时配置测)

- **目的**: 验证数据缺失时 fail fast (不动真数据)
- **命令**: `q-seed --config tests/fixtures/bad_config.yaml --top 5`
  - bad_config.yaml 内容: 复制主 config, 把 `data.tdx_dir` 改成 `/nonexistent/tdx`
- **预期**:
  - exit=1
  - stderr 含 "tdx_dir not found" 或类似
- **通过断言**:
  - [ ] exit ≠ 0
  - [ ] stderr 含 `/nonexistent/tdx`

---

### C20: `--help`

- **目的**: 帮助文档完整
- **命令**: `q-seed --help`
- **预期**:
  - exit=0
  - stdout 含所有参数说明: `--top, --template, --input, --since, --until, --format, --output, --config, --no-fundamentals`
- **通过断言**: 每个参数名能 grep 到

---

## 3. 性能基准 (非严格, 仅参考)

| Case | 耗时上限 | 验证 |
|---|---|---|
| C01 全市场 (~11k 只 × 2 模板) | ≤ 20 min | `time q-seed` |
| C07 4 个 code | ≤ 30 秒 | `time q-seed --input ...` |
| C09 1 个 code | ≤ 10 秒 | - |

---

## 4. 跑测试

`tests/run_tests.sh` 串起所有 case, 输出汇总报告:

```
Test C01 DEFAULT-TOP30 ........................ PASS (8m 32s)
Test C02 TOP-5 ................................ PASS (8m 31s)
Test C03 TOP-5000-OVERFLOW .................... PASS (8m 30s)
Test C04 TEMPLATE-HONGJING-ONLY ............... PASS (4m 12s)
Test C05 TEMPLATE-LITONG-ONLY ................. PASS (4m 11s)
Test C06 TEMPLATE-BOTH-EXPLICIT ............... PASS (8m 30s)
Test C07 INPUT-5-CODES ........................ PASS (12s)
Test C08 STDIN-PIPE ........................... PASS (12s)
Test C09 STDIN-1-CODE ......................... PASS (3s)
Test C10 SINCE-FILTER ......................... PASS (8m 28s)
Test C11 SINCE-UNTIL-WINDOW ................... PASS (8m 30s)
Test C12 FORMAT-MD ............................ PASS (8m 31s)
Test C13 FORMAT-BOTH .......................... PASS (3s)
Test C14 SELF-PIPE ............................ PASS (16m)
Test C15 INVALID-STDIN ........................ PASS (12s)
Test C16 UNKNOWN-CODE ......................... PASS (3s)
Test C17 EMPTY-INPUT .......................... PASS (1s)
Test C18 BAD-CONFIG-PATH ...................... PASS (1s)
Test C19 MISSING-TDX-DIR ...................... PASS (2s)
Test C20 HELP ................................. PASS (0.1s)

Total: 20 PASS / 0 FAIL
Wall time: ~1h 40min (主要被全市场 case 拖)
```

**注**: 全市场 case 耗时长, 实战会用 `--cache` (后续加) 或并行跑. 这一版串行就好.

---

## 5. 跑测试的判定规则

每个 case 的"通过断言"项 **全部** 通过才算 PASS, 否则 FAIL.

FAIL 时记录:
- 实际 exit code
- stderr 全文
- stdout 前 5 行
- 哪条断言不满足

---

## 6. 测试不覆盖的 (后续版本再加)

| 项 | 原因 |
|---|---|
| 并发竞争 | 单进程脚本无并发 |
| 内存压力 | 全市场 < 1GB, 默认机器扛得住 |
| TDX 数据破损/截断 | 边角 case, 出现时再补 |
| 多 config 切换 | V0 单 config |
| `--no-fundamentals` 实际效果 | DESIGN 里说 q-fin 负责基本面, q-seed 这版本根本不出基本面字段, flag 留 placeholder. 不测 |

---

## 7. 已确认决策 (2026-04-25)

| # | 决策 |
|---|---|
| T1 | ✅ 4 个 fixture code = 301396 / 603629 / 605389 / 002409 |
| T2 | ✅ 性能基准为参考; 仅 C09 单 code 必须 < 10s 为硬要求 |
| T3 | ✅ 全市场 case 真跑一次, V0 不加 `--limit-codes` 加速选项 |
| T4 | ✅ C19 改用临时错配置 `tests/fixtures/bad_config.yaml` (指向不存在的 tdx_dir), 不动真数据 |
| T5 | ✅ 全自动: jq + bash assertion. 异常时人工 review 具体行 |

---

回 "OK 开干" 我就:
1. 按 T1-T5 的默认/你的修改写测试 fixture + run_tests.sh
2. 实装 q-seed 代码 (按 DESIGN_q-seed.md, 复用 0424 已建的 lib/core)
3. 跑 20 个 case, 修 bug 直到全 PASS
4. 把通过结果给你 review
