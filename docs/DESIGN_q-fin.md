# q-fin 设计文档 (v2 修订)

**命令**: `q-fin` — 事件驱动题材股检索器 (借壳/控制权变更/主业转型)
**版本**: v2 修订 / 待用户确认
**遵守协议**: L2 独立性 · JSON Lines 输出 · 结果尽量保全 · 默认 free, `--paid` 显式启用 token · LLM/Search provider 抽象

---

## 1. 定位与职责

**找出正在发生"公司本身质变"的 A 股**, 典型例子:
- 长龄液压 605389 (核芯互联借壳, 传统液压 → 芯片)
- 天迈科技 300807 (启明创投入主, 停牌重组)
- 上纬新材 688585 (智元机器人借壳, 人形机器人)

**与其他命令的分工**:
- `q-seed` = K 线形态像谁 (技术面)
- `q-fin` = 公司**内部**在变什么 (内生事件: 借壳 / 实控人变更 / 主业转型 / 概念升级)
- `q-news` = **外部**事件如何传导到哪些公司 (地缘/政策/行业新闻驱动)

**核心流程**: **两层流水线** — 先撒网 (Layer 1 关键词检索, 免费), 再收网 (Layer 2 深度确认 + 递归实体调查, 可选付费).

---

## 2. 两层流水线

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1 撒网: 关键词检索 (候选池 20~100 只, 完全免费)        │
│                                                              │
│  A. akshare 公告关键词匹配                                   │
│     - 事件类: 控制权变更/借壳/要约收购/协议转让/重大资产重组 │
│     - 转型类: 经营范围变更/公司更名/主业转型/跨界            │
│     - 股东类: 股东大额新进 >= 5%                             │
│  B. (可选) web search 补充 akshare 外爆料                    │
│     触发: --paid=deep                                         │
└─────────────────────────────────────────────────────────────┘
                         ↓ 候选池 (code list)
┌─────────────────────────────────────────────────────────────┐
│ Layer 2 收网: 深度确认 + 递归实体调查 (精选 5~15 只)         │
│                                                              │
│  (以下全部基于 akshare/baostock, 默认免费)                   │
│  ① 公告原文深挖 (announcements_90d)                          │
│  ② 十大流通股东结构 (shareholders)                            │
│  ③ 股东户数历史 (holder_count_history)                        │
│  ④ 概念/业务变化 (concept_status)                             │
│  ⑤ 基本面 YoY / 财报 (fundamentals)                           │
│  ⑥ K 线安全性 (kline_safety, 本地 TDX)                        │
│  ⑦ q-seed 交叉验证 (是否在 hongjing/litong 候选)              │
│                                                              │
│  (下面要 token, --paid / --paid=deep 才跑)                   │
│  ⑧ 递归实体调查 (推荐 --paid+)                                │
│     新股东 → GP/实控人 → 背景 → 资本圈关联                   │
│     多跳深度可配 (standard 默认 2, deep 默认 3)              │
│  ⑨ LLM 综合结论 + ⭐ 评级 (--paid+)                           │
└─────────────────────────────────────────────────────────────┘
                         ↓
                 JSON Lines 输出 (TOP N, 每行含完整调查链)
```

---

## 3. 三档模式 (`--paid` flag)

| 档 | 启用方式 | 含义 | 成本 | 典型用途 |
|---|---|---|---|---|
| free (默认) | 不加任何 flag | Layer 1 akshare 公告 + Layer 2 ①~⑦ | **$0** | 日常扫描, 只给事实数据, 自己人肉 review |
| standard | `--paid` | free + Layer 2 ⑧⑨ (递归实体调查 2 层 + ⭐ 结论) | ~$0.20/run | 想要"入主方是谁, 背景如何, 事件质量"时用 |
| deep | `--paid=deep` | standard + Layer 1 web search 扩展 + 递归 3 层 | ~$0.45/run | 偶尔 / 周末深度扫描, 不要每天跑 |

**关键约定**:
- **默认 free**: 裸跑 `q-fin` 永远不花钱, 除非显式 `--paid`
- **`--paid` 生效的 LLM/search provider** 由 config `providers.llm.active` / `providers.search.active` 决定, 不绑定任何一家
- **三命令 (`q-seed` / `q-fin` / `q-news`) 统一 `--paid` 语义** (q-seed 无此参数, 永远 free)

---

## 4. Layer 1 — 关键词库 (config 化)

### 4.1 `config/keywords.yaml`

```yaml
# Layer 1 关键词库, 用户可自行增减
events:
  控制权变更:
    patterns:
      - "控制权变更"
      - "实际控制人变更"
      - "要约收购"
      - "协议转让"
      - "股份转让协议"
    severity: 5
    category: "借壳类"

  借壳重组:
    patterns:
      - "重大资产重组"
      - "发行股份购买资产"
      - "资产置换"
      - "借壳上市"
      - "作价购买"
    severity: 5
    category: "借壳类"

  主业转型:
    patterns:
      - "经营范围变更"
      - "新增业务"
      - "公司名称变更"
      - "简称变更"
      - "主营业务调整"
    severity: 3
    category: "转型类"

  跨界布局:
    patterns:
      - "设立子公司"
      - "对外投资"                   # 含噪, 要后续过滤
      - "战略合作"
      - "收购子公司"
    severity: 2
    category: "转型类"
    noise_filter: true               # 需二次 LLM 或人工过滤

# 股东变动阈值
shareholders:
  large_new_entry_pct: 0.05          # >= 5% 认为大额新进
  first_new_entry_pct: 0.10          # >= 10% 认为可能借壳
  partnership_weight: 1.5            # 合伙企业给额外权重 (产业资本常见形式)

# 热门方向 (命中加分)
hot_tags:
  AI_算力: ["AI", "算力", "GPU", "服务器", "大模型", "智算"]
  半导体: ["芯片", "半导体", "模拟芯片", "存储", "EDA"]
  机器人: ["人形机器人", "机器人", "具身智能", "扫地机器人"]
  商业航天: ["卫星", "火箭", "商业航天", "遥感"]
  创新药: ["创新药", "ADC", "PD-1", "单抗"]
  稀土: ["稀土", "磁材", "钕铁硼"]
  贵金属: ["黄金", "白银", "铂金"]
  其他: []                            # 用户 8 方向之一: 小行业冠军, 无法自动

# 排除 (避免噪音)
exclusions:
  - name_contains: ["ST", "*ST"]     # ST 股默认排除, --include-st 打开
    severity_downgrade: 1
  - announcement_contains: ["更正公告", "关于公告的更正"]
    skip: true
```

### 4.2 Layer 1 执行逻辑

```python
# pseudo
candidates = []
for stock in all_a_share:                    # ~5300 只
    anns = akshare.get_announcements_90d(stock.code)
    hits = match_keywords(anns, keywords.events)
    if hits:
        candidates.append({
            code, name,
            triggered_events: [...],
            severity_sum: sum(hits.severity),
            first_hit_date: min(hits.date)
        })
for stock in all_a_share:
    top10 = akshare.shareholders_top10(stock.code)
    new_entries = [s for s in top10 if s.change == "新进" and s.pct >= 5%]
    if new_entries:
        append or merge into candidates

# 如果 --paid=deep: web_search("A股 最近 控制权变更 借壳 实控人变更")
# 把爆料命中的新公司代码补进 candidates

# 按 severity_sum 降序排
candidates.sort(key=severity_sum, desc=True)
candidates = candidates[:100]                # 最多 100 只进 Layer 2
```

**Layer 1 耗时估算**:
- akshare 公告 per stock ≈ 0.3s, 5300 × 0.3 = 27 分钟 (全扫)
- 有 cache 则 24h 内 <5 分钟
- 可 `--layer1-limit 2000` 只扫活跃板块

---

## 5. Layer 2 — 深度确认 + 递归实体调查

### 5.1 免费部分 (①~⑦)

对每个候选跑:

| 子模块 | 数据源 | 产出字段 |
|---|---|---|
| announcements_90d | akshare stock_zh_a_disclosure_report_cninfo | 分类计数 + 关键标题 + risk_flags |
| shareholders | akshare stock_gdfx_free_top_10_em | top10_free + 新进列表 + 集中度 |
| holder_count_history | akshare stock_zh_a_gdhs_detail_em | 8 期变化 + 最新户数降幅 |
| concept_status | 从公告 infer | name_changed / scope_change / 新业务关键词 |
| fundamentals | baostock | YoY / ROE / 净利率 / 市值 |
| kline_safety | 本地 TDX | vwap/ret/amplitude + 4 档 🔴🟡🟢⚪ |
| q-seed 交叉 | 读 `~/sentry/quant/q-seed/logs/*.jsonl` 最新 | templates_matched |

### 5.2 **递归实体调查** (⑧, --paid 启用)

**这是用户明确要求的功能** — 不止"找到新股东", 还要"新股东是谁的公司, 这个人背景如何, 这样才有结论".

**算法**:

```
def recursive_entity_research(entity_name, depth, max_depth=2, budget_guard):
    """
    entity_name: 初始 = Layer 2 发现的新股东名
    depth: 当前深度 (0 = 根)
    """
    if depth >= max_depth or not budget_guard.allow():
        return {"entity": entity_name, "info": "max_depth_or_budget"}

    # 1 次 Anthropic API 调用 (含 web_search tool)
    result = llm.query(
        f"调查以下实体的真实身份和背景: {entity_name}. "
        f"如果是合伙企业, 查出 GP/LP/实控人. "
        f"如果是公司, 查出主营业务/实控人/在行业中的地位. "
        f"如果是自然人, 查出职业背景/资本关联. "
        f"用 web search 验证. 输出 JSON: {identity, business, key_persons, links_to_dig_deeper}",
        tools=["web_search"],
        model="haiku"
    )
    budget_guard.consume(result.cost_usd)

    # 递归下一跳
    sub_investigations = []
    for link in result.links_to_dig_deeper[:2]:          # 每层最多追 2 个关键人物
        sub = recursive_entity_research(link, depth+1, max_depth, budget_guard)
        sub_investigations.append(sub)

    return {
        "entity": entity_name,
        "depth": depth,
        "identity": result.identity,
        "business": result.business,
        "key_persons": result.key_persons,
        "web_sources": result.citations,
        "sub": sub_investigations
    }
```

**例子** (长龄液压, max_depth=2):

```
根 (depth=0): "无锡核芯听涛科技合伙企业"
  → LLM+web: "这是一个合伙企业, GP 是核芯互联科技有限公司, 实控人胡康桥"
  └─ 1 级 (depth=1): "核芯互联科技"
     → LLM+web: "模拟芯片设计公司, 国产替代龙头, 客户 A/B/C, 2024 营收 X 亿"
  └─ 1 级 (depth=1): "胡康桥"
     → LLM+web: "AMD/TI 背景, 中科院半导体所博士, 芯片产业资本"
```

**递归深度控制**:
- `max_depth=2` (standard 默认)
- `max_depth=3` (deep 默认)
- `max_depth=1` (standard 经济模式)
- 每层 fanout <= 2 个子调查
- 总调用数 = fanout^depth × 新股东数 ≈ 2^2 × 2 = 8 次/只
- 每次 ≈ $0.02-0.04 (含 web_search 工具)
- **per 股票预算上限**: `--entity-budget-per-stock 0.10` USD

### 5.3 LLM 综合结论 (⑨, --paid 启用)

Layer 2 全部跑完后, 一次 LLM 调用生成 **⭐ 评级 + 一句话结论 + 风险点 + 入场建议**.

**Prompt 结构**:

```
你是 A 股题材股分析师. 基于以下对 {code} {name} 的全部调查数据, 输出:
1. ⭐ 评级 (1-5, 考虑事件硬度 / 入主方质量 / K 线位置 / 已涨幅)
2. 一句话结论 (例: "教科书级芯片产业资本借壳, 类宏景但更硬核")
3. 关键风险 (例: "估值已 price in 要约价 137%")
4. 合理入场建议 (例: "等回踩 VWAP20 70 元附近")

调查数据:
- 触发事件: {triggered_events}
- 新股东: {shareholders.major_new_entry}
- 递归实体: {entity_research_chain}  ← 这是关键, 前面递归调查的结果
- K 线: {kline}
- 基本面: {fundamentals}
- 命中题材: {tags}
- q-seed 交叉: {q_seed_match}
- 风险 flags: {risk_flags}

输出 JSON: {rating, verdict, risks, entry_suggestion, theme_hardness}
```

单次 ~$0.01 (Haiku) 或 ~$0.03 (Sonnet, 仅 top 5 用 Sonnet).

---

## 6. L2 独立目录结构

```
~/sentry/quant/q-fin/
├── q-fin                       # bash 入口
├── main.py
├── config.yaml                 # 主配置 (数据源/providers/budget/输出)
├── config/
│   ├── keywords.yaml           # Layer 1 关键词库
│   ├── providers.yaml          # LLM/Search provider 定义 + 切换 (可放 config.yaml 内联)
│   └── entity_hints.yaml       # 常见合伙企业 → 已知 GP 映射 (避免重复 LLM)
├── requirements.txt            # pandas, akshare, baostock, pyyaml, anthropic, openai, duckduckgo-search, tqdm
├── .venv/
├── lib/
│   ├── __init__.py
│   ├── tdx_loader.py           # 复制自 core/
│   ├── data_loader.py          # 复制自 core/
│   ├── stock_names.py          # 复制自 core/
│   ├── keyword_scanner.py      # Layer 1 公告关键词匹配
│   ├── shareholder_scanner.py  # Layer 1 大额新进筛选
│   ├── announcements.py        # Layer 2 公告深挖
│   ├── shareholders.py         # Layer 2 股东五维
│   ├── holder_count.py         # Layer 2 户数历史
│   ├── concept_change.py       # Layer 2 概念变化推断
│   ├── fundamentals.py         # Layer 2 财报 (baostock)
│   ├── kline_safety.py         # Layer 2 本地 K 线快照
│   ├── q_seed_crosscheck.py    # Layer 2 读 q-seed 输出交叉
│   ├── entity_research.py      # Layer 2 ⑧ 递归实体调查 (核心!) — 用 providers 抽象
│   ├── llm_verdict.py          # Layer 2 ⑨ 综合结论 — 用 providers 抽象
│   ├── budget_guard.py         # Token 预算上限 + 冷却 — 读 provider pricing
│   ├── scoring.py              # Layer 1/2 排序
│   ├── tagger.py               # 8 方向 tag 归属
│   └── providers/              # LLM + Search provider 抽象 (可换供应商)
│       ├── __init__.py
│       ├── llm_base.py         # abstract LLMProvider
│       ├── llm_anthropic.py    # Claude (Haiku/Sonnet)
│       ├── llm_openai_compat.py# OpenAI / DeepSeek / Moonshot / 通义千问 (OpenAI 兼容 API 格式)
│       ├── llm_dummy.py        # fallback mock, 不联网 (test / API 挂时用)
│       ├── llm_factory.py      # 读 config.providers.llm.active → 返回实例
│       ├── search_base.py      # abstract SearchProvider
│       ├── search_duckduckgo.py# 免费, 默认
│       ├── search_anthropic_tool.py  # Claude bundled web_search tool (仅 llm=anthropic 时可用)
│       └── search_factory.py
├── data_cache/                 # akshare/search 结果 24h 缓存
│   ├── announcements/
│   ├── shareholders/
│   ├── entity/                 # 递归实体结果, 90d TTL (背景变化慢)
│   └── cost_log.jsonl          # LLM 成本日志
├── logs/                       # 输出 JSON Lines + md
└── README.md
```

**Provider 抽象层要点**:
- 所有 LLM/Search 调用走 `lib/providers/*_factory.py`, 业务代码不直接 import Anthropic/OpenAI SDK
- 换供应商: 改 `config.providers.llm.active` 一个字段, 其他不动
- `llm_openai_compat.py` 一个类覆盖 OpenAI 格式的所有 provider (OpenAI / DeepSeek / Moonshot / 通义千问 / 豆包 兼容模式), 通过 `base_url` 区分
- 3 命令**各自持有一份** `lib/providers/`, 不共享 (L2 独立要求)
- API key env 共享: 3 命令都读 `~/sentry/quant/.env` 同一份环境变量

---

## 7. 配置文件 `config.yaml`

```yaml
# q-fin/config.yaml

data:
  tdx_dir: "/home/wyatt/sentry/quant/data/tdx"
  tdx_fin_dir: "/home/wyatt/sentry/quant/data/tdx_fin"
  stock_names_csv: "/home/wyatt/sentry/quant/data/stock_names.csv"
  q_seed_logs_dir: "/home/wyatt/sentry/quant/q-seed/logs"   # 交叉验证用
  cache_dir: "./data_cache"
  cache_ttl_hours: 24
  entity_cache_ttl_days: 90                                 # 实体背景 90 天不变

# 数据源接口配置 (避免硬编码 akshare 函数名)
sources:
  akshare:
    enabled: true
    rate_limit_per_minute: 60                               # 内部限速, 避免被 ban
    timeout_seconds: 15
    retry_times: 2
    interfaces:
      announcements: "stock_zh_a_disclosure_report_cninfo"  # Layer 2 公告
      shareholders_top10: "stock_gdfx_free_top_10_em"       # 十大流通
      holder_count_history: "stock_zh_a_gdhs_detail_em"     # 股东户数变化
      stock_news: "stock_news_em"                           # (可选) 个股新闻
      board_concept: "stock_board_concept_cons_em"          # (可选) 概念板块
  baostock:
    enabled: true
    timeout_seconds: 30
    retry_times: 1
    queries:
      profitability: "query_profit_data"                    # 季度盈利能力
      growth: "query_growth_data"                           # YoY 增长
      industry: "query_stock_industry"                      # 证监会行业
      basic_info: "query_stock_basic"                       # 上市日 / 状态

# 关键词 / 股东阈值引用外部文件
keywords_file: "./config/keywords.yaml"
entity_hints_file: "./config/entity_hints.yaml"

# 默认模式 (可命令行 --paid 覆盖)
mode:
  default: "free"                                           # free / standard / deep
  layer1_candidate_limit: 100                               # Layer 1 → Layer 2 最多多少只
  layer2_top_output: 15                                     # 最终输出 TOP N

# 递归实体调查 (仅 standard/deep 生效, 即 --paid)
entity_research:
  max_depth_standard: 2                                     # --paid 时
  max_depth_deep: 3                                         # --paid=deep 时
  fanout_per_layer: 2
  budget_per_stock_usd: 0.10                                # per 股票硬上限
  prefer_entity_hints: true                                 # 本地 hints 命中跳过 LLM
  min_shareholder_pct_for_research: 0.03                    # >= 3% 才递归查

# Provider 抽象层 (LLM + Search), 切换供应商只改这里
providers:
  llm:
    active: "anthropic"                                     # anthropic / openai_compat / dummy
    anthropic:
      api_key_env: "ANTHROPIC_API_KEY"
      default_model: "claude-haiku-4-5-20251001"            # 日常 / 实体调查
      verdict_model: "claude-sonnet-4-6"                    # ⑨ 综合结论 (仅 TOP N 用)
      verdict_model_top_n: 5
      prompt_cache: true
      pricing:                                              # USD per 1M token
        haiku:  {input: 1.0, output: 5.0}
        sonnet: {input: 3.0, output: 15.0}
      web_search_bundled: true                              # 仅当 search.active=anthropic_tool 时使用
    openai_compat:                                          # 一类覆盖 OpenAI/DeepSeek/Moonshot/通义千问/豆包
      api_key_env: "OPENAI_API_KEY"                         # 用户切 provider 时改 env 名 + base_url
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4o-mini"
      verdict_model: "gpt-4o"
      verdict_model_top_n: 5
      pricing:
        default: {input: 0.15, output: 0.60}
        verdict: {input: 2.50, output: 10.00}
      # 切 DeepSeek 示例 (用户复制本段改 active 即可):
      # base_url: "https://api.deepseek.com/v1"
      # default_model: "deepseek-chat"
      # api_key_env: "DEEPSEEK_API_KEY"
      # pricing.default: {input: 0.27, output: 1.10}
      # 切 Moonshot:
      # base_url: "https://api.moonshot.cn/v1"
      # default_model: "moonshot-v1-8k"
      # 切通义千问:
      # base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
      # default_model: "qwen-turbo"
    dummy:                                                  # 测试 / API 挂时 fallback
      pricing: {default: {input: 0, output: 0}}

  search:
    active: "duckduckgo"                                    # duckduckgo (默认 free) / anthropic_tool / serpapi
    duckduckgo: {}                                          # 无 key, 直接用
    anthropic_tool:
      requires_llm: "anthropic"                             # 必须 llm.active=anthropic 才能用
      cost_per_call_usd: 0.01
    # serpapi (用户自加):
    # serpapi:
    #   api_key_env: "SERPAPI_KEY"
    #   cost_per_call_usd: 0.005

# 预算保护 (provider 无关, 读 provider pricing 自动算)
budget:
  daily_usd_cap: 0.50                                       # 硬上限 / 天
  on_exceeded: "fallback_free"                              # fallback_free / error / warn_continue
  cost_log: "./data_cache/cost_log.jsonl"

# 输出
output:
  jsonl_dir: "logs"
  md_companion: true
  retention_days: 30
  include_entity_chain: true                                # 输出里包含完整递归链

# 降级
fallback:
  on_akshare_fail: "continue"
  on_baostock_fail: "continue"
  on_llm_fail: "fallback_free"                              # 任何 LLM provider 挂自动降级
  on_search_fail: "skip_entity"                             # search 挂仅跳过 entity_research, 不退出
```

**切换 provider 的实操**:
```bash
# 例 1: 把 LLM 从 Claude 换成 DeepSeek (10x 便宜)
# 编辑 config.yaml:
#   providers.llm.active: "openai_compat"
#   providers.llm.openai_compat.base_url: "https://api.deepseek.com/v1"
#   providers.llm.openai_compat.api_key_env: "DEEPSEEK_API_KEY"
#   providers.llm.openai_compat.default_model: "deepseek-chat"
# 在 ~/sentry/quant/.env 加: DEEPSEEK_API_KEY=sk-xxx
# 完成. 业务代码无需改.

# 例 2: 想要 web search 但不付 anthropic_tool 的钱
#   providers.search.active: "duckduckgo"   (默认就是)
```

---

## 8. CLI 参数

```
q-fin [OPTIONS]

# 模式选择 (关键, 默认 free 不花钱)
  --paid                          启用付费档. 等价 --paid=standard
                                    (LLM 递归实体 2 层 + ⭐ verdict)
  --paid=deep                     深度档 (+Layer 1 web search 扩展 + 递归 3 层)
  --paid=standard                 显式 standard (与 --paid 等价)
  # 无 --paid 时永远 free, 0 成本

# Layer 1 范围
  --layer1-limit N                候选池上限 (默认 100)
  --since YYYY-MM-DD              公告窗口起 (默认 90d 前)
  --until YYYY-MM-DD              公告窗口止 (默认今天)
  --keywords <file>               自定义关键词 yaml (默认 config/keywords.yaml)
  --include-st                    打开 ST 股
  --board {main|chinext|star|all} 板块限定 (默认 all)

# Layer 2 范围
  --top N                         最终输出 TOP N (默认 15)
  --modules <a,b,c>               只跑 Layer 2 某些子模块 (ann/share/holder/concept/fund/kline/qseed/entity/verdict)

# 实体调查控制 (仅 --paid 生效)
  --max-depth N                   覆盖递归深度 (standard 默认 2, deep 默认 3)
  --entity-budget-per-stock USD   per 股票预算上限 (默认 0.10)

# 预算
  --budget USD                    覆盖 daily_usd_cap (例 --budget 0.20 限当次跑)

# Provider 临时切换 (config 已有, 命令行可覆盖)
  --llm <name>                    临时换 LLM provider (anthropic / openai_compat / dummy)
  --search <name>                 临时换 search provider (duckduckgo / anthropic_tool / serpapi)

# 输入
  --input <file|->                预给定候选池 (跳过 Layer 1)
                                  JSON Lines, 读 code 字段
                                  典型: q-seed --top 30 | q-fin
  --tags AI,算力                   只输出命中这些 tag 的

# 输出
  --format {jsonl|md|both}
  --output <file>
  --config <path>
  --no-cache
  --dry-run                       跑 Layer 1 + 估算 Layer 2 成本, 不真调 LLM
  -h, --help
```

**典型用法**:

```bash
# 日常免费扫 (最常用, 永不花钱)
q-fin                                       # 全市场关键词, Layer 2 只免费部分, 无 ⭐ verdict

# 对候选深度研究 (要 ⭐ 结论和入主方背景)
q-fin --paid                                # ~$0.20/run (用 config 默认 provider)

# 周末深扫 (加 web search 找外部爆料)
q-fin --paid=deep                           # ~$0.45/run

# 给定候选池做 deep dive (跳 Layer 1)
q-seed --top 30 | q-fin --paid --top 10

# 只要 AI 方向, Layer 1 限 50 只
q-fin --paid --tags AI,算力 --layer1-limit 50

# 测算成本不真跑
q-fin --paid --dry-run

# 临时换 provider (例: 这次试用 DeepSeek 省钱)
q-fin --paid --llm openai_compat            # 配合 config 已配的 deepseek base_url

# 限制本次预算
q-fin --paid=deep --budget 0.10             # 超 $0.10 自动降 free
```

---

## 9. 输入规约

**三种模式**:

1. `--input file.jsonl` / stdin (非 tty): 跳 Layer 1, 直接对给定 code 跑 Layer 2.
2. `--since ... --until ...` 限窗口, 其他默认: 跑 Layer 1 + Layer 2.
3. 裸跑: 默认窗口 90d, free 模式 (无 `--paid`), 输出 TOP 15.

**Layer 1 跳过条件**:
- 有 `--input` → 直接用, 不跑 Layer 1
- 没 `--input` → 必跑 Layer 1 (这就是 q-fin 的核心能力)

---

## 10. 输出 JSON Lines Schema

每行一个候选股 (TOP 15):

```json
{
  "code": "605389",
  "name": "长龄液压",
  "scan_date": "2026-04-25",
  "source": "q-fin",
  "mode": "standard",
  "rank": 1,
  "score": 4.8,
  "score_type": "star_rating_0_5",

  "verdict": {
    "rating": 5,
    "stars": "⭐⭐⭐⭐⭐",
    "one_liner": "教科书级芯片产业资本借壳 — 核芯互联真·产业资本接手, 类宏景但更硬核",
    "theme_hardness": "硬",
    "entry_suggestion": "等回踩 VWAP20 附近再入, 止损 65 元",
    "key_risks": [
      "估值已 price in 要约价 35.82 元 vs 现价 84.79 (+137%)",
      "控制权变更需监管审核, 核查工作函回复中",
      "资产注入需 1-3 年"
    ],
    "themes": ["AI", "算力", "半导体"],
    "verdict_model": "claude-sonnet-4-6",
    "verdict_provider": "anthropic",
    "verdict_cost_usd": 0.028
  },

  "layer1_triggers": {
    "matched_events": [
      {"category": "控制权变更", "keyword": "要约收购", "source": "akshare", "ann_date": "2025-07-10"},
      {"category": "控制权变更", "keyword": "协议转让", "source": "akshare", "ann_date": "2025-07-10"}
    ],
    "matched_shareholder_signal": {
      "new_entry_pct": 26.42,
      "entity_type": "合伙企业",
      "triggered_rule": "first_new_entry_pct_10"
    },
    "severity_sum": 10
  },

  "entity_research": {
    "mode": "standard",
    "llm_provider": "anthropic",
    "search_provider": "duckduckgo",
    "max_depth_used": 2,
    "budget_used_usd": 0.078,
    "chain": {
      "entity": "无锡核芯听涛科技合伙企业",
      "depth": 0,
      "identity": "私募合伙企业 (Special Purpose Vehicle)",
      "business": "持股载体, 用于产业资本入主长龄液压",
      "key_persons": ["核芯互联科技", "胡康桥"],
      "web_sources": [
        "https://www.cninfo.com.cn/...",
        "https://new.qq.com/rain/a/..."
      ],
      "sub": [
        {
          "entity": "核芯互联科技",
          "depth": 1,
          "identity": "模拟芯片设计公司 (深圳/无锡)",
          "business": "国产替代模拟/混合信号芯片, 2024 营收 ~3 亿, C 轮融资估值 30-50 亿",
          "key_persons": ["胡康桥 (董事长/实控人)"],
          "industry_position": "模拟芯片国产替代头部梯队",
          "sub": []
        },
        {
          "entity": "胡康桥",
          "depth": 1,
          "identity": "自然人, 芯片产业资本家",
          "background": "前 AMD/TI 背景, 中科院半导体所博士",
          "capital_links": ["核芯互联科技 (核心)"],
          "sub": []
        }
      ]
    },
    "conclusion": "真产业资本 (非财务投资人), 芯片资产可注入预期强"
  },

  "announcements_90d": {
    "total": 50,
    "window": {"since": "2026-01-24", "until": "2026-04-24"},
    "by_category": {"控制权变更": 2, "业绩": 4, "股权激励": 16, "境外上市": 7, "其他": 21},
    "key_titles": [
      {"date":"2025-07-10","title":"关于收到要约收购报告书摘要的公告","category":"控制权变更"},
      {"date":"2026-03-15","title":"关于增加经营范围的公告","category":"主业转型"}
    ],
    "risk_flags": ["要约收购未完成", "实控人套现减持"]
  },

  "shareholders": {
    "report_date": "2025-12-31",
    "top10_free": [
      {"rank":1,"name":"无锡核芯听涛科技合伙企业","pct":26.42,"change":"新进","is_partnership":true},
      {"rank":2,"name":"夏泽民","pct":21.58,"change":-9797921,"change_rate_pct":-25.0}
    ],
    "new_entries_count": 4,
    "top10_concentration_pct": 78.5,
    "major_new_entry": {
      "name": "无锡核芯听涛科技合伙企业",
      "pct": 26.42,
      "type": "产业资本",
      "warning_emoji": "🔥"
    }
  },

  "holder_count_history": [
    {"date":"2026-02-10","count":8058,"chg_pct":4.5,"avg_mv_wan":132.7},
    {"date":"2025-11-10","count":7710,"chg_pct":-11.2,"avg_mv_wan":151.2}
  ],

  "concept_status": {
    "name_changed": false,
    "scope_change_recent": true,
    "scope_change_date": "2026-03-15",
    "new_business_keywords": ["半导体", "芯片", "模拟芯片"],
    "is_pivot_story": true
  },

  "fundamentals": {
    "industry": "通用设备制造业 (暂)",
    "market_cap_yi": 150.2,
    "report_qtr": "2025Q3",
    "yoy_ni_pct": 18.75,
    "net_margin_pct": 16.05,
    "roe_pct": 5.26,
    "main_business": [{"segment": "液压件", "pct": 95.0}]
  },

  "kline": {
    "current_price": 84.79,
    "current_date": "2026-04-22",
    "vwap20": 74.85, "vwap20_dev": 0.133,
    "vwap60": 75.17, "vwap60_dev": 0.128,
    "high60": 85.55, "low60": 66.10,
    "ret60": 0.071, "ret120": 0.307,
    "kline_safety": "🟢"
  },

  "q_seed_crosscheck": {
    "is_in_q_seed": true,
    "matched_templates": ["hongjing"],
    "best_dist": 5.57,
    "best_rank": 3,
    "crosscheck_bonus": 0.5
  },

  "tags": ["AI", "算力", "半导体"],

  "meta": {
    "scanner_version": "q-fin v2.0.0",
    "mode": "standard",
    "providers": {"llm": "anthropic", "search": "duckduckgo"},
    "layer1_candidates_count": 67,
    "layer2_deep_dive_count": 15,
    "total_cost_usd": 0.21,
    "budget_remaining_usd": 0.29,
    "scan_duration_ms": 180432
  }
}
```

**字段保全原则**:
- `entity_research.chain` 完整保留递归树, 每层 web_sources 可复查.
- `verdict.verdict_model` + `verdict_cost_usd` 标记结论来自哪个模型、花了多少.
- `layer1_triggers.matched_events` 保留具体命中哪些关键词、公告日期, 便于复现.
- `q_seed_crosscheck` 独立段, dashboard 可展示"双命中 = 强信号".
- 免费模式下 (`mode=free`, 即无 `--paid`): `verdict` 和 `entity_research` 字段为 null, 其他 layer2 字段照常有.

---

## 11. 递归实体调查的细节 (关键模块)

### 11.1 本地 hints 优先 (省钱)

`config/entity_hints.yaml` 维护已知映射, 命中则跳过 LLM:

```yaml
partnerships:
  "无锡核芯听涛科技合伙企业":
    gp: "核芯互联科技"
    controller: "胡康桥"
    known_since: "2026-04-24"
    confirmed: true

  "启明创投":
    identity: "VC"
    focus: ["TMT", "医疗", "企业服务"]
    confirmed: true

companies:
  "核芯互联科技":
    business: "模拟芯片设计"
    industry_position: "国产替代头部"
    confirmed: true

persons:
  "胡康桥":
    background: "前 AMD/TI, 中科院半导体所博士"
```

首次查到的实体 LLM 调查后, **自动追加**到 hints (下次免费命中). 用户可人工精修.

### 11.2 成本控制

```python
# per 股票调查硬上限
if stock.entity_budget_used >= config.entity_research.budget_per_stock_usd:
    return partial_result(reason="per_stock_budget_exceeded")

# per run 硬上限
if run.total_cost_usd >= config.budget.daily_usd_cap:
    switch_to_mode("free")                                  # 降级
    log_warning("budget exceeded, remaining stocks use free mode")

# 命中 hints 免费
if entity in hints:
    return hints[entity]                                    # 0 cost
```

### 11.3 Web Search 工具 (provider 抽象)

不直接调用任何家 SDK, 走 `lib/providers/search_factory.py`. 当前支持:
- `duckduckgo` (默认 free) — `duckduckgo-search` Python 库, 无 key, 限流时自动 retry+sleep
- `anthropic_tool` — Claude API 内置 `web_search_20250305` tool, 仅 `llm.active=anthropic` 可用, 每次 ~$0.01 + token
- `serpapi` (可选, 用户自加) — 付费, ~$0.005/次, 用 HTTP

**调用统一签名**:
```python
search = get_search(config)
results = search.query(q="无锡核芯听涛 GP 实控人", max_results=5)
# 返回 [{title, url, snippet}, ...]
```

**挂接失败 fallback** (config.fallback.on_search_fail):
- `skip_entity` (默认): 仅跳过 entity_research, 其他字段照常
- 没 API key (anthropic_tool/serpapi 需) → 自动降级 duckduckgo
- duckduckgo 也挂 → entity_research 该实体标 `failed: true, reason: "search_unavailable"`

---

## 12. 失败模式

| 场景 | 行为 |
|---|---|
| LLM provider 的 API key 缺 | `--paid` / `--paid=deep` 自动降 free, stderr 提示 |
| LLM provider API 调用失败 | 按 `fallback.on_llm_fail` 处理, 默认降 free |
| akshare 某接口挂 | 该子模块置 null, evidence 不含 |
| Layer 1 无候选 | stdout 空, exit 0, stderr "no candidates with severity >= 2" |
| 预算超限 (run 内) | 剩余股票自动降 free 模式, meta.mode 标 "partial_standard" |
| Web search provider 挂 | 按 `fallback.on_search_fail` 处理, 默认 skip_entity |
| `search.active=anthropic_tool` 但 `llm.active != anthropic` | 启动期报错 exit 2, 提示配置冲突 |
| q-seed 未跑过 (logs 空) | q_seed_crosscheck.is_in_q_seed = null, 不报错 |
| stdin 非法 | exit 2 |
| Ctrl-C | 捕获, 已完成部分写 logs/, stderr "interrupted, partial saved" |

---

## 13. 复用现有代码清单

| 目标 `q-fin/lib/` | 来源 | 改动 |
|---|---|---|
| `tdx_loader.py` | `core/tdx_loader.py` | 复制 |
| `data_loader.py` | `core/data_loader.py` | 复制 |
| `stock_names.py` | `core/stock_names.py` | 复制 |
| `keyword_scanner.py` | `scripts/scan_hot_keyword_announce.py` | 主逻辑抽函数 |
| `shareholder_scanner.py` | `scripts/scan_business_pivot.py` 股东部分 | 抽函数 |
| `announcements.py` | `scripts/deep_dive_announcements.py` | 抽函数 + 分类规则外置 |
| `shareholders.py` | `scripts/deep_analyze_candidates.py` 股东段 | 抽函数 |
| `holder_count.py` | `scripts/deep_analyze_candidates.py` gdhs 段 | 抽函数 |
| `concept_change.py` | `scripts/scan_business_pivot.py` 概念段 | 抽函数 |
| `fundamentals.py` | `scripts/fetch_candidate_fundamentals.py` | 抽函数 |
| `kline_safety.py` | `scripts/deep_analyze_candidates.py` K 线段 | 抽函数 |
| `q_seed_crosscheck.py` | 新写, 读 q-seed logs/ 最新 jsonl | - |
| `entity_research.py` | **新写, 递归调用 providers.llm + providers.search** | - |
| `llm_verdict.py` | **新写, 走 providers.llm 抽象** | - |
| `budget_guard.py` | 新写, 读 providers.{llm,search}.pricing 自动算 | - |
| `scoring.py` | 新写 | - |
| `lib/providers/*` | **新写**, 见 §6 目录结构 (LLM/Search 抽象层 + 各 provider 实现) | - |

---

## 14. Smoke Test 验收

1. `q-fin --help` 显示 `--paid` 三档说明
2. `q-fin --dry-run` 跑 Layer 1 (free 模式), 打印候选池 + 估成本 $0
3. `q-fin --top 5` 输出 5 行 JSON, mode=free, verdict=null, entity_research=null, 其他字段有值
4. `q-fin --paid --top 3 --max-depth 1` 输出 3 行含 verdict + entity_research 一层
5. 拔掉 ANTHROPIC_API_KEY, `q-fin --paid` 自动降 free, stderr 提示 "llm provider unavailable"
6. `q-seed --top 30 | q-fin --top 10` 管道跳 Layer 1
7. `q-fin --paid --budget 0.01` 超低预算触发中途降级 (meta.mode=partial_standard)
8. `entity_hints.yaml` 手动加一条 → 二次运行命中 hints, cost_log 不增加该实体的 LLM 消费
9. 长龄液压 605389 用 `q-fin --paid` 跑出 `entity_research.chain` 能追到胡康桥
10. 改 `config.providers.llm.active: openai_compat` + 配 DeepSeek base_url + 加 DEEPSEEK_API_KEY env → `q-fin --paid` 用 DeepSeek 跑通, meta.providers.llm = "openai_compat"
11. `q-fin --paid --search anthropic_tool --llm openai_compat` 启动期报错 (anthropic_tool 要求 llm=anthropic)
12. `q-fin --paid --search duckduckgo --llm openai_compat` 用 DeepSeek + DDG 组合跑通

---

## 15. 已确认决策 (用户已拍板)

| # | 决策 |
|---|---|
| F1 | Layer 1 全市场公告扫 ~27 min 可接受, 24h cache 必做, 二次 <5min |
| F2 | 递归深度 standard=2 / deep=3, fanout=2, 用户可调 |
| F3 | verdict TOP 5 升 Sonnet (anthropic), 其他 Haiku |
| F4 | entity_hints.yaml 不预填, 跑过自动沉淀 |
| F5 | q-fin 和 q-news cost_log 不共享 (L2 独立), env 共用 `~/sentry/quant/.env` |
| F6 | verdict 与 conclusion 合并一次 LLM call |
| F7 | web_search 走 provider 抽象, **默认 duckduckgo (free)**, 可切 anthropic_tool / serpapi |
| F8 | Layer 1 `--board` 默认 all, severity 排序自然过滤 |
| F9 | Layer 2 `--modules` 白名单 |
| F10 | fanout 默认 2, 用户可调到 3 |
| F11 | 三命令统一 `--paid` flag, q-fin 支持 `--paid` (=standard) 和 `--paid=deep` |
| F12 | LLM/Search provider 抽象化, 3 LLM (anthropic/openai_compat/dummy) + 2 Search (duckduckgo/anthropic_tool) 起步 |
| F13 | OpenAI 兼容 (DeepSeek/Moonshot/通义/豆包) 在 config 预置注释模板, 用户切 base_url+env 即可启用 |
