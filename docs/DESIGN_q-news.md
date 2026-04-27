# q-news 设计文档

**命令**: `q-news` — 事件驱动推理 (新闻 → 产业链 → A 股)
**版本**: v2 草案 / 待用户确认
**遵守协议**: L2 独立性 · JSON Lines 输出 · 结果尽量保全 · 默认 free (`--paid` 启用 LLM) · LLM/Search provider 抽象

---

## 1. 定位与职责

每日/按需 拉最近 24~72h 的财经/地缘/行业新闻, 做**多层推理**:
- 事件 → 受影响产业链 → A 股受益股

输出"今日值得关注的事件驱动选股", 每只票带完整推理路径 (可审查).

**例子** (来自 0424 会话):
- 输入新闻: "伊朗扣押油轮 霍尔木兹海峡紧张"
- 推理链:
  1. 霍尔木兹封锁 → 中东原油运输受阻 (rule)
  2. 原油运输受阻 → 炼化副产品减少 (rule)
  3. 炼化副产品减少 → 氩气供应紧张 (knowledge_base)
  4. 氩气供应紧张 → 氩气供应商受益 (rule)
  5. 氩气供应商 → 002409 华特气体 (sector_map)
- 输出: 002409 华特气体, confidence=0.85, 验证: WTI +4.2%, 工业气体指数 +1.8%

**单一职责**: 不做形态, 不做基本面, 只做"事件 → 股票"的因果链.

---

## 2. 三档模式 (`--paid` flag)

| 档 | 启用方式 | 含义 | 成本 | 典型用途 |
|---|---|---|---|---|
| free (默认) | 不加任何 flag | 纯规则引擎 (knowledge_base.yaml + sector_map.yaml) | **$0** | 日常扫描, 规则覆盖的事件类型可处理 |
| standard | `--paid` | free + 规则未命中或低置信度时, 用 default_model (例: Haiku) 兜底推理 | ~$0.20/run | 日常推荐 (规则 + LLM 互补) |
| deep | `--paid=deep` | standard + 关键事件升 verdict_model (例: Sonnet) + web_search 验证关键论点 | ~$0.45/run | 重大事件日 / 周末深扫 |

**关键约定**:
- **默认 free**: 裸跑 `q-news` 永远不花钱, 全靠规则引擎. 规则未命中的新闻直接 drop.
- **`--paid` 用哪个 LLM** 由 config `providers.llm.active` 决定 (anthropic/openai_compat/dummy)
- **三命令统一 `--paid` 语义** (`q-seed` 无此参数)

---

## 3. L2 独立目录结构

```
~/sentry/quant/q-news/
├── q-news                   # bash 入口
├── main.py
├── config.yaml              # 数据源 / providers / budget
├── requirements.txt         # pandas, akshare, feedparser, anthropic, openai, duckduckgo-search, pyyaml, httpx
├── .venv/
├── lib/
│   ├── __init__.py
│   ├── news_fetcher.py      # akshare 新闻接口 + RSS 抓取
│   ├── news_normalizer.py   # 统一新闻对象 (title/source/time/url/body)
│   ├── rule_engine.py       # 规则知识库 YAML 加载 + 规则匹配
│   ├── knowledge_base.py    # 产业链知识图谱 (YAML 维护)
│   ├── sector_map.py        # 概念/行业 → A 股列表 (akshare + 本地缓存)
│   ├── llm_reasoner.py      # 走 providers.llm 抽象 (默认 Haiku, deep 升 Sonnet)
│   ├── budget_guard.py      # 每日 token 预算上限 + 超限降级 (读 provider pricing)
│   ├── market_check.py      # 关联期货/指数验证 (WTI/沪深 300/行业指数)
│   ├── scoring.py           # confidence × market_check 综合分
│   ├── cache.py             # 新闻+推理结果缓存 (24h)
│   └── providers/           # LLM + Search provider 抽象 (与 q-fin 同结构, 各自独立持有)
│       ├── __init__.py
│       ├── llm_base.py
│       ├── llm_anthropic.py
│       ├── llm_openai_compat.py    # OpenAI / DeepSeek / Moonshot / 通义千问
│       ├── llm_dummy.py
│       ├── llm_factory.py
│       ├── search_base.py
│       ├── search_duckduckgo.py    # 默认 free
│       ├── search_anthropic_tool.py
│       └── search_factory.py
├── config/
│   ├── knowledge_base.yaml  # 产业链规则 (事件 → 产业链 → 概念)
│   ├── sector_map.yaml      # 概念 → 股票代码 (手维 + akshare 补)
│   └── rss_feeds.yaml       # RSS 源列表
├── data_cache/              # 已拉新闻 + 已推理结果 + cost_log.jsonl
├── logs/
└── README.md
```

**L2 要点**:
- API key env 共用 `~/sentry/quant/.env` (3 命令共享), 但 `lib/providers/` 各命令各自一份 (L2 独立)
- knowledge_base.yaml 是**可维护的产业链知识图谱**, 人工 + (deep 模式) LLM 协作增补
- `providers/` 与 q-fin **完全同结构** (代码可参考但不 import), 切 provider 时改 q-news 自己的 config

---

## 4. 配置文件

### 4.1 `config.yaml`

```yaml
# q-news/config.yaml

data:
  cache_dir: "./data_cache"
  cache_ttl_hours: 24
  stock_names_csv: "/home/wyatt/sentry/quant/data/stock_names.csv"

# 数据源
sources:
  akshare:
    enabled: true
    interfaces:
      - "stock_news_em"              # 东财个股新闻
      - "news_cctv"                  # 央视新闻联播摘要 (宏观)
      - "stock_zh_a_disclosure_report_cninfo"  # 交易所公告
    since_hours: 24
  rss:
    enabled: true
    feeds_file: "./config/rss_feeds.yaml"
    timeout_seconds: 10
    parallel: 4
  tdx_fin:
    enabled: false                   # 本版不用 (财报在 q-fin 里)

# 知识库
knowledge:
  kb_file: "./config/knowledge_base.yaml"
  sector_map_file: "./config/sector_map.yaml"

# 模式 (--paid 触发, 默认 free)
mode:
  default: "free"                                # free / standard / deep
  max_reasoning_depth: 5                         # 链路最多 5 层
  min_confidence_rule_for_skip_llm: 0.6          # 规则置信度 >= 此 不升 LLM (即使 --paid)
  min_confidence_haiku_for_skip_sonnet: 0.6      # Haiku 置信度 >= 此 不升 Sonnet (--paid=deep)

# Provider 抽象层 (LLM + Search), 切换供应商只改这里
providers:
  llm:
    active: "anthropic"                          # anthropic / openai_compat / dummy
    anthropic:
      api_key_env: "ANTHROPIC_API_KEY"
      default_model: "claude-haiku-4-5-20251001" # --paid 用
      verdict_model: "claude-sonnet-4-6"         # --paid=deep 关键事件升级用
      max_tokens_per_call: 1500
      temperature: 0.3
      prompt_cache: true
      pricing:
        haiku:  {input: 1.0, output: 5.0}
        sonnet: {input: 3.0, output: 15.0}
      web_search_bundled: true
    openai_compat:                               # 一类覆盖 OpenAI/DeepSeek/Moonshot/通义千问/豆包
      api_key_env: "OPENAI_API_KEY"
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4o-mini"
      verdict_model: "gpt-4o"
      max_tokens_per_call: 1500
      pricing:
        default: {input: 0.15, output: 0.60}
        verdict: {input: 2.50, output: 10.00}
      # DeepSeek/Moonshot/通义 切换示例: 见 q-fin DESIGN
    dummy:
      pricing: {default: {input: 0, output: 0}}

  search:
    active: "duckduckgo"                         # duckduckgo (默认 free) / anthropic_tool / serpapi
    duckduckgo: {}
    anthropic_tool:
      requires_llm: "anthropic"
      cost_per_call_usd: 0.01
    # serpapi: 用户自加, 见 q-fin DESIGN

# 预算保护
budget:
  daily_usd_cap: 0.50                            # --paid/--paid=deep 的日上限
  on_exceeded: "fallback_free"                   # fallback_free / error / warn_continue
  cost_log: "./data_cache/cost_log.jsonl"

# 市场验证
market_check:
  enabled: true
  futures_list: ["WTI", "沪金", "沪铜", "焦煤", "天然气"]
  index_list: ["sh000300", "sh000905"]
  chg_threshold: 0.005

# 输出
output:
  default_top: 30
  jsonl_dir: "logs"
  md_companion: true
  retention_days: 30
  min_confidence: 0.3                            # 置信度低于此不输出

# 降级
fallback:
  on_akshare_fail: "continue"
  on_rss_fail: "continue_other_feeds"
  on_llm_fail: "fallback_free"
  on_search_fail: "skip_validation"
```

### 3.2 `config/knowledge_base.yaml` (核心, 示例片段)

```yaml
# 产业链知识图谱
# 每条规则: trigger (关键词/pattern) → 推理路径 → target_sector
rules:
  - id: "geopolitics.hormuz"
    name: "霍尔木兹海峡封锁"
    triggers:
      keywords: ["霍尔木兹", "Hormuz", "伊朗扣押油轮"]
      source_priority: ["reuters", "al_jazeera", "jin10"]
    reasoning_chain:
      - {from: "霍尔木兹封锁", to: "中东原油运输受阻", weight: 0.9}
      - {from: "中东原油运输受阻", to: "全球原油价格上涨", weight: 0.85}
      - {from: "全球原油价格上涨", to: "油气开采受益", weight: 0.8}
      - {from: "原油运输受阻", to: "炼化副产品(氩气等)减少", weight: 0.5}
      - {from: "炼化副产品减少", to: "氩气/氪气/氙气供应紧张", weight: 0.6}
    target_sectors:
      - {sector: "石油开采", confidence_mul: 0.9}
      - {sector: "工业气体", confidence_mul: 0.7}
      - {sector: "航运", confidence_mul: 0.6}
    market_check_hint:
      - {symbol: "WTI", direction: "up"}

  - id: "policy.rare_earth_export_control"
    name: "稀土出口管制"
    triggers:
      keywords: ["稀土出口管制", "稀土禁止"]
    reasoning_chain:
      - {from: "中国稀土出口限制", to: "海外稀土供应紧张", weight: 0.95}
      - {from: "海外稀土供应紧张", to: "稀土价格上涨", weight: 0.9}
      - {from: "稀土价格上涨", to: "国内稀土开采受益", weight: 0.85}
    target_sectors:
      - {sector: "稀土", confidence_mul: 0.95}
      - {sector: "磁材", confidence_mul: 0.7}
```

### 3.3 `config/sector_map.yaml` (示例)

```yaml
# 概念 / 行业 → A 股列表
# 维护方式: akshare stock_board_concept_cons_em 自动拉 + 手工补
sectors:
  工业气体:
    codes: ["002409", "688268", "688267"]
    names: ["华特气体", "华特达因", "华懋科技"]
    source: "akshare+manual"
    updated: "2026-04-24"
  石油开采:
    codes: ["601857", "600028", "601088"]
    names: ["中国石油", "中国石化", "中国神华"]
  稀土:
    codes: ["600111", "000831", "600259"]
    names: ["北方稀土", "中国稀土", "广晟有色"]
```

### 3.4 `config/rss_feeds.yaml`

```yaml
feeds:
  - {name: "财联社", url: "https://www.cls.cn/rss-feeds", tag: "a_share"}
  - {name: "金十数据", url: "https://www.jin10.com/rss/", tag: "commodity"}
  - {name: "路透中文", url: "https://cn.reuters.com/rss", tag: "geopolitics"}
  - {name: "BBC 中文", url: "https://www.bbc.com/zhongwen/simp/index.xml", tag: "geopolitics"}
  - {name: "Al Jazeera", url: "https://www.aljazeera.com/xml/rss/all.xml", tag: "middle_east"}
  - {name: "新华经济", url: "http://www.xinhuanet.com/rss/fortunerss.xml", tag: "macro"}
  - {name: "国资委", url: "http://www.sasac.gov.cn/rss.xml", tag: "soe_reform"}
```

---

## 5. CLI 参数

```
q-news [OPTIONS]

# 模式 (默认 free 不花钱)
  --paid                       启用 standard (规则未命中升 default_model 兜底)
  --paid=deep                  启用 deep (+verdict_model 升级关键事件 + web_search 验证)
  # 无 --paid → 永远 free (纯规则引擎)

# 时间窗口
  --since YYYY-MM-DD[THH:MM]   新闻起始时间 (默认 24h 前)
  --until YYYY-MM-DD[THH:MM]   新闻结束 (默认现在)

# 数据源
  --sources a,b,c              仅用这些源 (akshare / rss:财联社 / rss:reuters)
  --tags AI,算力                仅输出命中 tag 的

# 输出范围
  --top N                      输出 TOP N (默认 30)

# 预算 (仅 --paid 生效)
  --budget USD                 临时覆盖 daily_usd_cap

# Provider 临时切换 (config 已有, 命令行可覆盖)
  --llm <name>                 临时换 LLM provider (anthropic / openai_compat / dummy)
  --search <name>              临时换 search provider (duckduckgo / anthropic_tool)
  --model <name>               临时强制某模型 (覆盖 default_model 或 verdict_model)

# 输入
  --input <file|->             限定 code 集合 (JSON Lines / stdin)
                                 → 反向查个股新闻
# 输出
  --format {jsonl|md|both}
  --output <file>
  --config <path>
  --dry-run                    不调 LLM, 不写文件, 只打印规则覆盖率 + 估算成本
  -h, --help
```

**默认行为** (裸跑 `q-news`, free 模式):
1. 读 config + knowledge_base + sector_map
2. 拉最近 24h 所有源新闻 (akshare + RSS)
3. 对每条新闻跑规则引擎, 命中 rule 的直接产出候选
4. 规则未命中 / 低置信度 → **直接 drop** (free 模式无 LLM 兜底)
5. 聚合 → 股票维度 → 按 confidence 排序 → TOP 30
6. JSON Lines stdout + 落盘

**`--paid` 行为**: 在第 4 步, 低置信度新闻升 default_model (例 Haiku) 推理.
**`--paid=deep` 行为**: 升级链 default_model → verdict_model (例 Sonnet), 加 web_search 验证关键论点.

**典型用法**:
```bash
q-news                                # 日常 free, 纯规则
q-news --sources rss:reuters --tags 地缘   # 只看国际地缘
q-news --input <(q-seed --top 30)     # q-seed 候选里看最近是否有事件驱动
q-news --paid                         # standard, ~$0.20/run
q-news --paid=deep                    # deep, ~$0.45/run
q-news --paid --llm openai_compat     # 临时用 DeepSeek 省钱
q-news --dry-run --paid               # 估成本不真跑
```

---

## 6. 输入规约

三种模式:
1. `--input file.jsonl` → 读 code, 反向搜个股新闻 (akshare stock_news_em per code)
2. stdin → 同上
3. 默认 → 拉全局新闻, 推理到股票

**注**: `--input` 模式和默认模式**路径不同**:
- 默认 = 新闻驱动 → 股票 (事件发现)
- `--input` = 股票驱动 → 相关新闻 (候选核实)

---

## 7. 输出 JSON Lines Schema

每行一个股票 (可含多个触发事件):

```json
{
  "code": "002409",
  "name": "华特气体",
  "scan_date": "2026-04-24",
  "source": "q-news",
  "rank": 1,
  "score": 0.72,
  "score_type": "confidence_x_market_check",
  "tags": ["地缘事件", "工业气体", "原油链"],
  "sector_inferred": "工业气体",

  "events": [
    {
      "event_id": "20260424-iran-hormuz-tanker",
      "title": "伊朗扣押油轮 霍尔木兹海峡紧张",
      "source": "reuters",
      "source_type": "rss",
      "publish_time": "2026-04-24T09:30:00+08:00",
      "url": "https://www.reuters.com/...",
      "summary": "伊朗革命卫队扣押一艘悬挂利比里亚国旗的油轮, 霍尔木兹海峡紧张局势升级...",
      "full_text_hash": "sha256:…",

      "matched_rule_id": "geopolitics.hormuz",
      "reasoning_chain": [
        {"layer":1,"from":"霍尔木兹封锁","to":"中东原油运输受阻","by":"rule","weight":0.9},
        {"layer":2,"from":"原油运输受阻","to":"炼化副产品减少","by":"rule","weight":0.5},
        {"layer":3,"from":"炼化副产品减少","to":"氩气供应紧张","by":"knowledge_base","weight":0.6},
        {"layer":4,"from":"氩气供应紧张","to":"氩气供应商受益","by":"rule","weight":0.85},
        {"layer":5,"from":"氩气供应商","to":"002409 华特气体","by":"sector_map","weight":0.9}
      ],
      "reasoning_mode": "free",
      "reasoning_method": "rule_engine",
      "reasoning_confidence": 0.85,

      "llm_audit": null,
      "tokens_used": 0,
      "cost_usd": 0.0
    },
    {
      "event_id": "20260424-opec-cut",
      "title": "OPEC+ 意外减产 50 万桶",
      "source": "jin10",
      "publish_time": "2026-04-24T14:00:00+08:00",
      "matched_rule_id": null,
      "reasoning_mode": "standard",
      "reasoning_method": "llm_default_model",
      "reasoning_confidence": 0.68,
      "llm_audit": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "prompt_hash": "sha256:…",
        "raw_output_excerpt": "OPEC 减产 → 油价上升 → 炼化副产品减少..."
      },
      "tokens_used": 1430,
      "cost_usd": 0.0023,
      "reasoning_chain": [...]
    }
  ],

  "market_check": {
    "checked_at": "2026-04-24T15:05:00+08:00",
    "futures": [
      {"symbol": "WTI", "chg_pct": 4.2, "expected": "up", "match": true}
    ],
    "index": [
      {"symbol": "sh000300", "chg_pct": -0.3, "note": "中性"}
    ],
    "sector_index": [
      {"sector": "工业气体", "chg_pct": 1.8}
    ],
    "stock_today_chg_pct": 3.5,
    "validation_score": 0.85
  },

  "context": {
    "industry": "基础化工",
    "company_main_business": "氩气、氪气、氙气等特种气体",
    "market_share_note": "国内氩气前 3 (人工标注)",
    "market_cap_yi": 89.5
  },

  "evidence": [
    {"event_id": "20260424-iran-hormuz-tanker", "weight": 0.85, "detail": "规则引擎命中产业链 5 层"},
    {"event_id": "20260424-opec-cut", "weight": 0.50, "detail": "Haiku 推理补充佐证"}
  ],

  "meta": {
    "scanner_version": "q-news v2.0.0",
    "mode": "standard",
    "providers": {"llm": "anthropic", "search": "duckduckgo"},
    "total_news_scanned": 1243,
    "rule_hits": 89,
    "llm_escalations": 23,
    "total_cost_usd": 0.087,
    "budget_remaining_usd": 0.41
  }
}
```

**字段保全原则**:
- `events[]` 可多条, 不去重 (多事件叠加驱动 = 更强信号).
- `reasoning_chain` 每层保留 `from/to/by/weight`, 可审查每一步逻辑.
- `llm_audit` 记录模型/prompt hash/节选, 可复现或追溯错误.
- `market_check` 独立模块, 即使推理正确但市场没反应, `validation_score` 会低, 方便下游过滤假阳性.
- `meta` 记录成本/覆盖率, 用户可监控预算.

---

## 8. 推理引擎

三档逐渐升级, 每档都包括前一档.

### 8.1 free 模式 — 规则引擎 (默认, 无 `--paid`)

流程:
1. 新闻 title + body 做关键词匹配, 命中 `knowledge_base.rules[*].triggers`
2. 按 `reasoning_chain` 逐层推演
3. 在 `target_sectors` 里查 sector → `sector_map.yaml` 查 stocks
4. confidence = Π(layer_weight) × target_sector.confidence_mul
5. **未命中规则的新闻直接 drop** (不升 LLM, 因为是 free 模式)

**特点**: 零成本, 可解释, 但只能推已知规则. 新事件类型无能为力.

### 8.2 standard 模式 — `--paid`

free 之上, 规则未命中 / 低置信度新闻**升 LLM** (走 `providers.llm` 抽象, 用 `default_model`).

Prompt 模板:

```
你是一个 A 股产业链分析师. 给定下列新闻, 推理它如何影响中国 A 股某些行业/个股.

新闻: {title}
正文: {body}

要求:
1. 给出 <= 5 层推理链, 每层格式: {from, to, reason_type}
2. 最终指向一个或多个 A 股概念/行业
3. 给出置信度 0-1
4. 只用下列已知概念列表: [... 从 sector_map 读 ...]

输出 JSON:
{
  "reasoning_chain": [...],
  "target_sectors": [...],
  "confidence": 0.XX
}
```

启用 prompt caching (system prompt + 概念列表缓存, 提高命中率, 降低成本).

### 8.3 deep 模式 — `--paid=deep`

standard 之上, 进一步:
- LLM 结果置信度 < 0.6 时升 `verdict_model` (例 Sonnet)
- 关键论点用 `providers.search` 联网验证 (例: 推理路径里说"WTI 上涨", 实际查 WTI 当日行情确认)
- 推理深度上限放宽到 `mode.max_reasoning_depth` (默认 5)

### 8.4 推理升级链 (伪代码)

```python
mode = parse_paid_flag(args)        # "free" / "standard" / "deep"

for news in news_pool:
    result = rule_engine(news)
    if result.confidence >= mode.min_confidence_rule_for_skip_llm:
        emit(result)
        continue

    if mode == "free":
        drop(news)                  # 不升 LLM
        continue

    # standard / deep
    if not budget_guard.allow():
        fallback_to_rule_or_drop(news)
        continue

    llm = providers.llm.default_model       # 例: Haiku / DeepSeek-chat / GPT-4o-mini
    r1 = llm_reasoner(news, model=llm)
    if r1.confidence >= mode.min_confidence_haiku_for_skip_sonnet:
        emit(r1); continue

    if mode == "deep" and budget_guard.allow_expensive():
        verdict = providers.llm.verdict_model    # 例: Sonnet / GPT-4o / DeepSeek-reasoner
        r2 = llm_reasoner(news, model=verdict)
        if mode == "deep":
            r2 = augment_with_web_search(r2, providers.search)   # 关键论点联网验证
        emit(r2); continue

    emit(r1)                        # best effort
```

---

## 9. 预算保护

`budget_guard.py` 逻辑 (与 q-fin 同设计):
- 读 `data_cache/cost_log.jsonl` 累计今日消费
- 预 check: 估算当前 call 的 input/output tokens × **provider pricing** → 是否超 daily_usd_cap
- 超限时按 `config.budget.on_exceeded`:
  - `fallback_free`: 跳过 LLM, 降级 free 模式 (规则引擎)
  - `error`: 直接退出
  - `warn_continue`: stderr warn 但继续 (生产不推荐)
- 每次 call 完成后追加 `cost_log.jsonl`: `{ts, provider, model, input_tok, output_tok, cost_usd, news_id}`

**模型价格不再硬编码**, 从 `config.providers.llm.<active>.pricing` 动态读. 这样换 provider (例 DeepSeek) 时, 预算保护自动按新价格算, 业务代码无需改.

---

## 10. 失败模式

| 场景 | 行为 |
|---|---|
| akshare / RSS 全挂 | stderr error, exit 1 (没数据源) |
| 部分 RSS 挂 | 按 `fallback.on_rss_fail` 处理, 默认继续跑能通的 |
| LLM provider 的 API key 缺 | `--paid` / `--paid=deep` 自动降 free, stderr 提示 |
| LLM provider API 调用失败 | 按 `fallback.on_llm_fail`, 默认 fallback_free |
| 预算超限 | 按 `config.budget.on_exceeded`, 默认 fallback_free |
| knowledge_base 格式错 | exit 2, 指具体行 |
| LLM 返回非法 JSON | 重试 1 次, 再失败 → 降 free 处理该新闻 |
| market_check / search 接口挂 | 按 `fallback.on_search_fail`, 默认 skip_validation; market_check 字段置 null |
| `search.active=anthropic_tool` 但 `llm.active != anthropic` | 启动期报错 exit 2, 提示配置冲突 |
| IBM 内网拦截海外 RSS | 对应源标记 "blocked", stderr 提示, 继续 |

---

## 11. 复用现有代码清单

现有 scripts/ 下几乎**没有** news 相关代码, 此命令基本新写. 可复用:

| 目标 `q-news/lib/` | 来源 |
|---|---|
| `sector_map.py` 初始数据 | akshare `stock_board_concept_cons_em` 自动拉一次 → sector_map.yaml |
| `stock_names.py` | 复制 `core/stock_names.py` |
| `lib/providers/*` | **代码可参考 q-fin 同结构**, 不 import (L2 独立). 各 provider 实现走 anthropic SDK / openai SDK / duckduckgo-search 库 |

需**新写**:
- `news_fetcher.py`
- `news_normalizer.py`
- `rule_engine.py`
- `llm_reasoner.py` (走 providers.llm 抽象)
- `budget_guard.py` (读 providers pricing)
- `market_check.py`
- `scoring.py`
- `lib/providers/*` (与 q-fin 同结构, 各自一份)
- knowledge_base.yaml 初版 (先写 10-20 条手动规则种子)
- sector_map.yaml 初版 (akshare 自动拉)

---

## 12. Smoke Test 验收

1. `q-news --help` 显示 `--paid` 三档说明
2. `q-news --dry-run` 输出"规则覆盖率 X%, 预估 --paid 成本 $Y"
3. `q-news --top 5` 默认 free, 纯规则, 无 API 调用, mode=free
4. `q-news --paid --top 5` 含 LLM 兜底, `meta.total_cost_usd` <= daily_usd_cap, mode=standard
5. `q-news --paid=deep --top 5` 含 verdict_model 升级 + web_search, mode=deep
6. 拔掉 ANTHROPIC_API_KEY env, `q-news --paid` 自动降级 free, stderr 提示
7. `q-news --input <(echo '{"code":"002409"}')` 反向查个股新闻 (free 模式)
8. `echo '{"code":"002409"}' | q-news` 管道 (free 模式)
9. `cost_log.jsonl` 持续累加 (`--paid` 跑后)
10. `q-news --paid --budget 0.01` 超低预算触发中途降级 (meta.mode=partial_standard)
11. 改 `config.providers.llm.active: openai_compat` + 配 DeepSeek base_url + 加 DEEPSEEK_API_KEY env → `q-news --paid` 用 DeepSeek 跑通, meta.providers.llm = "openai_compat"
12. `q-news --paid --search anthropic_tool --llm openai_compat` 启动期报错 (anthropic_tool 要求 llm=anthropic)

---

## 13. 已确认决策 (用户已拍板)

| # | 决策 |
|---|---|
| N1 | 默认 free, `--paid` 启用 standard ($0.20/run), `--paid=deep` 启用 deep ($0.45/run, +web search) |
| N2 | LLM/Search provider 抽象, 切供应商改 config.providers 一字段 (3 LLM + 2 Search 起步) |
| N3 | 默认 search = duckduckgo (free), 默认 llm = anthropic (用户已有 $50 余额) |
| N4 | OpenAI 兼容 (DeepSeek/Moonshot/通义/豆包) 在 config 预置注释模板 |
| N5 | knowledge_base.yaml 初版 10-20 条种子规则; 未匹配事件写到 `logs/unmatched_events.jsonl` 供人工补 |
| N6 | sector_map.yaml 初版从 akshare `stock_board_concept_cons_em` 自动拉一次, 用户手工合并/精简 |
| N7 | deep 模式增加 web_search 验证关键论点 (与 q-fin 对齐) |
| N8 | 个股新闻模式 (`--input`) code 上限 100, 超过 stderr 提示 |
| N9 | 同一事件多股按 sector 内市占率 / 市值 / 相关度排, 无数据按市值降序 |
| N10 | 历史回溯 V0 不做 |
| N11 | q-news 和 q-fin cost_log **不共享** (L2 独立), env 共用 `~/sentry/quant/.env` |
| N12 | 三命令统一 `--paid` flag |
| N13 | **q-news 当前不接 IBM 内网测试** (用户后续真用时自己测 `curl https://push2.eastmoney.com/` / `curl https://cn.reuters.com/rss/`) |

---

## 14. V0 开发顺序 (仅供参考, 用户可调)

估时 ~4h (加 providers 抽象):

1. (30min) 目录骨架 + `news_fetcher` akshare 拉新闻 smoke (free)
2. (30min) `sector_map.yaml` 从 akshare 拉一次, 人工精简
3. (30min) `knowledge_base.yaml` 写 10 条种子规则
4. (45min) `rule_engine` + `scoring` + free 模式跑通 (无 LLM, 已可用)
5. (45min) `lib/providers/` LLM 抽象 (anthropic + openai_compat + dummy + factory)
6. (30min) `lib/providers/` Search 抽象 (duckduckgo + anthropic_tool + factory)
7. (30min) `llm_reasoner` 接 providers + prompt cache → `--paid` standard 模式跑通
8. (15min) `budget_guard` 读 provider pricing
9. (15min) `--paid=deep` 升级链 + web_search 验证集成
10. (15min) `market_check` 集成
11. (15min) main.py 串起来 + smoke test (free / --paid / --paid=deep 各一遍)
