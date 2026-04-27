# IBM Bob 评审报告 + 项目方分析

**评审日期**: 2026-04-27
**评审方**: IBM Bob (LLM)
**项目方**: ocean5tech / wooyoo@gmail.com
**项目状态**: V1.0 (pilot, 后续重开发)

---

## TL;DR

Bob 综合评分 **7.5/10**. 我的修订 **8.0/10**.

- ~40% 是真问题 (接受改进)
- ~30% Bob 看错了/没读完文档 (辩解+证据)
- ~30% production 标准对 pilot 的过度要求 (用户已说 pilot 不必)

---

## Bob 评审原文 (要点提取)

### 优点
1. 架构务实 (单进程文件系统 / L2 独立 / JSON Lines 管道)
2. 数据源策略合理 (TDX 为主 / baostock TCP 绕代理)
3. 成本控制 (默认 free / Provider 抽象 / budget cap)
4. 领域知识 (疯牛种子双模板 / 60+ 维特征)
5. 文档质量高 (DESIGN/USER_GUIDE 完整)

### Bob 提出的 6 个风险
1. **过度设计** (Critical) — V0 未验证就上 V1-V3
2. **数据质量校验缺失** (High) — 无 OHLC 校验
3. **LLM 成本预估不足** (High) — Bob 估 $2.55/run, 文档 $0.20
4. **IBM 内网未充分测试** (Medium)
5. **回测框架缺失** (Medium)
6. **安全 (.env / prompt injection / 审计)** (Low-Medium)

### Bob 给的关键建议
- 暂缓 q-fin/q-news (先验证 q-seed 1-2 月)
- 切 DeepSeek 省钱
- 不需要数据库 (与现有设计同意)

---

## 项目方逐项分析

### 🟥 辩解 (Bob 错或没读全, 6 项)

#### 1. LLM 成本预估: Bob 错 8 倍 (反向)

Bob 算: 15 股 × 8 calls × $0.02 = $2.55/run

**实测** (F44 + 3 case 测试 2026-04-26):
| 测试 | 实际成本 |
|---|---|
| F44 单只长龄 (max_uses=2) | $0.097 |
| Case 1 q-seed→q-fin --paid TOP 10 | $0.282 |
| Case 3 q-news→q-fin --paid TOP 10 (空 code 过滤后) | $0.030 |

Bob 假设错: 不是每股都 8 次 LLM. 只有 `major_new_entry` (≥3% 大额新进) 触发 entity_research, 多数仅跑 verdict 一次. 加上 `budget_guard` per-stock + per-run cap, 超就降 free.

文档预估 $0.20-0.50 实测核对**正确**, 不是过于乐观.

#### 2. IBM 内网未测试

`docs/SOURCES_q-news.md` 是 2026-04-26 IBM 内网逐项实测报告:
- ✅ akshare 5 接口 / 新华网 5 RSS 通
- ❌ RSSHub 403 / Reuters 401 / Bloomberg 403 / DDG 中文 0 结果 / 海关 412
- ❌ data.tdx.com.cn 拦 (q-sync 已加 `--from-zip` 应对)

测过比 Bob 建议清单还多. Bob 没读这份报告.

#### 3. 测试覆盖率低: Bob 没读 tests/

| 命令 | 测试 |
|---|---|
| q-seed | **57 PASS / 0 FAIL** (含全市场 SLOW) |
| q-fin | **65 PASS / 0 FAIL** (含 F44 真 LLM ✓) |
| q-news | **31 PASS / 0 FAIL** (含 RUN_NETWORK 真拉新闻) |
| **合计** | **153 测试 cases, 全自动** |

每个命令有 `TEST_PLAN_*.md` + `run_tests.sh`. Bob 看到的 `tests/test_strategies.py` 是 V0 旧目录, 与 V1 q-* 无关.

#### 4. 全市场扫描 27 分钟: 数字错

q-seed 全市场实测 **4-5 分钟** (双模板 hongjing+litong, 11000 只). Bob 引的 27min 是 q-fin Layer 1 公告全市场扫的**估算**, 当前用 `q-seed | q-fin pipe` 缩到 ~10s.

#### 5. DeepSeek 建议: 已支持

`q-fin/config.yaml > providers.llm.openai_compat` 段已含 DeepSeek/Moonshot/通义/豆包**预置注释模板**. Provider 抽象层就是为这个设计的, 切供应商改 config 一字段.

#### 6. 硬编码路径: 已修

Bob 引的 `tdx_loader.py L9` 是从 V0 父项目复制的 legacy. 已加 `set_tdx_dir(path)` setter 覆盖, q-seed/main.py 启动期注入 (q-seed 实施踩坑教训 #1, 已记 memory). 新 L2 命令路径都从 config 读.

### 🟩 接受 (Bob 真问题, 6 项)

#### 1. 数据质量校验缺失 ✅
没有 OHLC 关系校验 / 价格跳变 / 成交量异常检测.
**修复计划**: 加 `validate_ohlc(df)` 到 `core/data_loader.py`.

#### 2. 回测框架未集成 ✅
`scripts/backtest_*.py` 独立, 没接入 q-seed/q-fin/q-news 持续验证.
**修复计划**: 建 `q-backtest/` L2 命令 (双模式 benchmark + walk-forward).

#### 3. 错误处理不分类 ✅
`except Exception as e` 一锅端, 调用方无法区分网络错 vs 退市.
**修复计划**: 建 `core/exceptions.py` (DataFetchError / StockDelistedError / NetworkError).

#### 4. 类型注解不全 ✅
旧模块 (formula_engine, mytt, scanner) 基本无 type hints.
**修复计划**: 全覆盖 + mypy check.

#### 5. 过度设计风险 (原则上) ⚠️
Bob 的 1.4% base rate 警告是 CLAUDE.md 自己写的 (我们认知到了). 但执行层面**确实**没等 q-seed 跑出长样本就开 q-fin/q-news.

**Pilot 视角缓解**: 用户明确说 V1.0 是 pilot/原型, 验证工作流而非交易系统. 这条 contextually 软化但不否认.

#### 6. LLM 调用缓存 ✅ 部分有
q-fin 已实现 `entity_hints.yaml` auto-append (跑过的实体下次 0 LLM). q-news LLM 调用缓存确实没做 (V0 paid 是占位).

### 🟨 取决于上下文 (Pilot 视角, 4 项)

| 项 | Pilot 决策 |
|---|---|
| 暂缓 q-fin/q-news 等 q-seed 长样本 | **不冻结** — q-seed 模块化设计本就为后续调参 |
| 安全 (.env 加密 / prompt 注入 / 审计) | **不做** — pilot 暂不必 |
| 全市场扫描多进程 | **不做** — 4-5 min 个人用够 |
| SQLite 抽象层 | **不做** — pilot 不预留 DB |

---

## 评分对比

| 维度 | Bob | 项目方修订 | 差异原因 |
|---|---|---|---|
| 架构设计 | 8/10 | 8/10 | 同意 |
| 代码质量 | 6/10 | 7/10 | Bob 没看 tests/ 和新模块 |
| 文档质量 | 9/10 | 9/10 | 同意 |
| 风险控制 | 6/10 | **8/10** | Bob 把成本估反了 + 没看 budget_guard |
| 可维护性 | 7/10 | 7/10 | L2 ✅, 类型 ⚠️ |
| **综合** | **7.5/10** | **8.0/10** | 真问题 + Pilot 上下文软化 |

---

## 大盘相关性额外分析 (Bob 启发)

Bob 没提, 但用户基于报告引出"起爆点是否与大盘转折点相关?"假设. 项目方做了 11 个起爆日 vs 上证/沪深300/深证/创业板 4 个指数的快速分析:

### 假设验证结果 (反直觉)

| 指数 | 起爆日前 20d 大盘均值 | 起爆日后 20d 均值 |
|---|---|---|
| 上证综指 | **+3.35%** ← 涨 | +1.09% |
| 沪深 300 | +3.05% | +0.98% |
| 深证成指 | +5.81% | +2.34% |
| **创业板指** | **+7.56%** ← 强势 | +2.77% |

**假设否定**: 起爆日**不是**发生在大盘转折点 (谷底). 反而**多数 (82%) 发生在大盘已涨一段后** — 顺势起爆.

### 例外案例 (转折点起爆 18%)
- 利通 2026-02-02: 前 20d -0.19%, 后 20d +2.67%, 距大盘最低 0d
- 福晶 2025-11-24: 前 20d -4.01%, 后 20d +2.10%, 距大盘最低 1d

### 行动: 大盘趋势作为 q-seed KNN 影响因子

V1.5 实施 (本轮 ship):
- wave_model.py 加 `index_pct_20d_sse` + `index_pct_20d_chinext` 2 个特征
- 模板锚点 sig_dates 时的指数状态自然成为"模板期望市况"
- 候选股需**形态像 + 市况像**才能高排

**有趣观察**: 6 模板锚点市况差异极大:
- xiangnong (香农芯创 2025-09-08): 创业板 +23.26% (狂飙时起爆)
- fujing (福晶科技 2025-11-24): 创业板 -9.44% (大跌时起爆) ← 逆势独类
- 其他 4 模板: 创业板 +5% ~ +8% 强势中

---

## 项目方最终立场

### 认可 Bob 的核心价值
- 提出"先验证后扩展"的产品思路 (Phase 0→1→2→3 渐进)
- 列出真问题清单 (数据校验 / 回测 / 错误分类 / 类型 / 缓存)
- 提供 production-grade 视角 (即使 pilot 不实施, 思路存底)

### 拒绝部分内容
- LLM 成本估算实测有数据反驳, 不接受 Bob 数字
- 测试覆盖率, IBM 内网测试, 全市场扫描时长 — 文档自存数据反驳
- "暂缓 V1+" — pilot 上下文不适用

### 本轮 ship 改进
基于 Bob 评审 + 用户回测需求 + 大盘相关性发现:
1. q-seed 加 2 个 index features (大盘趋势作影响因子)
2. 4 个新模板 (xiangnong/fujing/yunnange/lanqi all strict)
3. q-backtest L2 命令 (walk-forward 主升浪起爆点验证)
4. B(a) 3 项: 数据校验 + 异常分类 + 类型注解
5. 留档本报告: `docs/REVIEW_bob_2026-04-27.md`

---

## 后续重开发参考

`memory/project_pilot_status.md` 已记录: V1.0 是 pilot, 后续重开发时统一处理:
- 凭据安全 (Anthropic / 企业微信 / GitHub PAT 都暴露过)
- 架构债 (sector_map 自动同步 / entity_research 估算偏低 / 内网下载方案)
- 持续 OOS 验证 (回测接入主流程后跑 3 月看胜率)

Bob 的 production-grade 建议大部分应在重开发时落地.
