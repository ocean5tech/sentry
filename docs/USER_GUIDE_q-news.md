# q-news 用户手册

**v1.0.0** · 事件驱动新闻 → A 股推理

默认 free (规则引擎, 0 成本); `--paid` 启用 LLM 兜底未命中规则的事件 (V0 仅占位, V1.5 落地).

核心 alpha: **`concept_status.已启动` 字段** — 找利好但 q-seed 还没命中 (没启动) 的标的.

---

## 1. 部署

```bash
cd ~/sentry/quant/q-news

python3 -m venv .venv
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
chmod +x ./q-news

# (可选) 加 PATH
ln -s ~/sentry/quant/q-news/q-news ~/.local/bin/q-news
```

依赖: `pandas / numpy / pyyaml / akshare / feedparser / anthropic / openai / python-dotenv`.

### 数据依赖

- `~/sentry/quant/q-seed/logs/*.jsonl` — **必须有最新 q-seed 输出**, concept_status "已启动" 判定要读
- `~/sentry/quant/q-fin/logs/*.jsonl` — 可选, 反查 fundamentals
- akshare 接口 (网络) — 拉新闻
- 新华网 RSS (网络) — 政策面

---

## 2. 启动方法

### Free (默认, 0 成本, 永远不烧 token)

```bash
# 默认: 拉 24h 新闻 + 跑规则 + 输出 TOP 30
q-news

# 调时间窗口
q-news --window-hours 48
q-news --since 2026-04-25 --until 2026-04-26

# 限定数据源
q-news --sources akshare:cls,rss:新华网-经济

# tag 过滤 (输出含命中此 tag 的)
q-news --tags AI_算力,半导体_国产替代

# 反查个股新闻 (q-seed 候选 → 看新闻)
q-seed --top 30 | q-news --top 10
echo '{"code":"605389"}' | q-news

# 输出格式
q-news --format jsonl              # 默认
q-news --format md
q-news --format both --output result
```

### Paid (V1.5)

```bash
# V0 占位, V1.5 实现 LLM 兜底未命中规则的事件
q-news --paid
q-news --paid=deep --budget 0.10
```

V0 paid 当前等价 free, 因为 q-news 主要价值在规则库, LLM 兜底留给后续提速.

完整参数: `q-news --help`.

---

## 3. 输入

### 默认: 全市场拉新闻

无 `--input`: 从 5 个 akshare 接口 + 5 个新华网 RSS 拉, 24h 内的新闻 + dedupe.

### `--input` / stdin: 反查个股新闻

```jsonl
{"code":"605389"}
{"code":"301396"}
```

走 akshare `stock_news_em(symbol)` 反查每只股票的新闻.

### `--news-file` 测试模式

跳过 fetcher, 直接读静态 jsonl (用于离线测试 / 复现 case):

```bash
q-news --news-file my_news.jsonl --top 10
```

每行 jsonl 必须含: `event_id, source, title, content, publish_time` (其他字段可选).

---

## 4. 输出 JSON Lines Schema

**核心 alpha 在 `concept_status.已启动` 字段** — 这是 q-news 给你 vs q-seed/q-fin 不一样的信息.

```json
{
  "code": "002409",
  "name": "雅克科技",
  "scan_date": "2026-04-26",
  "source": "q-news",
  "mode": "free",
  "rank": 1,
  "score": 9.08,
  "tags": ["稀有气体", "半导体材料"],

  "triggered_events": [
    {
      "event_id": "akshare-cls-abc123",
      "title": "OPEC+ 决定 5 月起减产 200 万桶/日",
      "source": "akshare:cls",
      "publish_time": "2026-04-26T10:30:00",
      "url": null,
      "concept": "稀有气体",
      "role": "龙头",
      "reasoning_chain": [
        {"layer":1, "type":"news",     "content":"OPEC+ 减产", "by":"akshare/rss"},
        {"layer":2, "type":"impact",   "content":"原油价格上升 → 炼化产能利用率下降", "by":"rule", "weight":0.92},
        {"layer":3, "type":"analysis", "content":"炼化副产稀有气体减少 → 供给紧张", "by":"rule", "weight":0.78},
        {"layer":4, "type":"concept",  "content":"稀有气体板块", "by":"rule"}
      ],
      "reasoning_method": "rule_engine",
      "rule": {
        "id": "opec_减产",
        "confidence": 0.77,
        "direction": "bullish",
        "priority": 1
      }
    }
  ],

  "concept_status": {
    "concept": "稀有气体",
    "已启动": false,
    "stocks_in_concept": 2,
    "active_in_qseed": 0,
    "active_pct": 0.0,
    "verdict": "🟢 未启动 (0/2 = 0% 在 q-seed) — 利好可能未 price in",
    "active_codes": []
  },

  "q_seed_crosscheck": {
    "is_in_q_seed": false
  },

  "q_fin_crosscheck": {
    "has_q_fin_record": false
  },

  "meta": {
    "scanner_version": "q-news v1.0.0",
    "mode": "free",
    "events_scanned": 446,
    "events_matched": 26,
    "events_unmatched": 420,
    "sources_used": ["akshare:cls", "akshare:em", "rss:新华网-经济"],
    "scan_duration_ms": 48123
  }
}
```

### 字段说明

| 字段 | 含义 |
|---|---|
| `concept_status.已启动` | true=该概念里 ≥50% 股票在 q-seed top 30 (已炒作) / false=未启动 (机会) / null=q-seed 数据缺失 |
| `concept_status.active_pct` | 概念内 q-seed 命中比例 |
| `triggered_events[*].reasoning_chain` | 5 层因果链 (news → impact → analysis → concept → 个股) |
| `triggered_events[*].rule.confidence` | 规则推理置信度 |
| `triggered_events[*].rule.direction` | bullish (利好) / bearish (利空) / neutral |
| `q_seed_crosscheck.is_in_q_seed` | 个股是否在 q-seed top |
| `q_fin_crosscheck.has_q_fin_record` | 个股是否在 q-fin 输出过 |
| `score` | 综合评分 (rule conf + 概念未启动 bonus + 利好 bonus + qseed未启动 bonus + 基本面) |
| `meta.events_unmatched` | 未命中规则的事件数 (写到 logs/unmatched_events.jsonl) |

### 自动备份

每次跑写到 `~/sentry/quant/q-news/logs/q-news_top<N>_YYYYMMDD_HHMM.jsonl` + `.md`. 30 天滚动.

未命中规则的事件写到 `~/sentry/quant/q-news/logs/unmatched_events.jsonl` 持续追加, 供你后续看哪些规则需要补.

---

## 5. 配置文件

主 config: `config.yaml`
知识库: `config/knowledge_base.yaml` (推理规则)
概念库: `config/sector_map.yaml` (概念 → 股票)

### 5.1 数据源 `config.yaml > sources`

```yaml
sources:
  akshare:
    enabled: true
    interfaces:
      - {name: "akshare:cls",   func: "stock_info_global_cls",  weight: 1.0}   # 财联社快讯
      - {name: "akshare:em",    func: "stock_info_global_em",   weight: 1.0}   # 东财快讯
      - {name: "akshare:cjzc",  func: "stock_info_cjzc_em",     weight: 0.8}   # 财经资讯
      - {name: "akshare:cctv",  func: "news_cctv",              weight: 0.7, args: {date: "today"}}
    individual_news_func: "stock_news_em"

  rss:
    enabled: true
    timeout_seconds: 10
    feeds:
      - {name: "rss:新华网-时政", url: "http://www.news.cn/politics/news_politics.xml", tags: [政策], weight: 0.9}
      - {name: "rss:新华网-经济", url: "http://www.news.cn/fortune/news_fortune.xml",   tags: [财经], weight: 1.0}
      - {name: "rss:新华网-科技", url: "http://www.news.cn/tech/news_tech.xml",         tags: [科技], weight: 0.9}
      - {name: "rss:新华网-国际", url: "http://www.news.cn/world/news_world.xml",       tags: [国际], weight: 0.7}
      - {name: "rss:新华网-军事", url: "http://www.news.cn/mil/news_mil.xml",           tags: [军工], weight: 0.7}
```

加新闻源: 新增 interfaces 项 (akshare 函数名) 或 feeds 项 (RSS URL). 见 `~/sentry/quant/docs/SOURCES_q-news.md` 已实测的可用清单.

### 5.2 规则库 `config/knowledge_base.yaml`

V0 含 20 条规则 (油价/美联储/政策/出口管制/地缘/AI/航天/创新药/机器人/新能源/借壳).

加新规则:

```yaml
rules:
  你的规则名:
    triggers: ["关键词1", "关键词2", "关键词3"]      # ≥2 个命中视为激活
    direction: bullish | bearish | neutral
    target_sectors: [稀有气体]                       # 必须在 sector_map 里有定义
    reasoning_chain:
      - {layer: 2, type: impact,   content: "直接影响", weight: 0.9}
      - {layer: 3, type: analysis, content: "衍生分析", weight: 0.8}
    confidence_base: 0.7
    priority: 1
    examples: ["可选, 一个真实例子"]
```

### 5.3 概念库 `config/sector_map.yaml`

V0 含 28 个概念骨架 (8 方向: AI/算力, 半导体, 商业航天, 矿产, 稀土, 贵金属, 创新药, 小行业冠军). **stocks 字段需用户填**.

```yaml
AI_算力:
  description: "AI 大模型 + 算力基础设施"
  priority: 1
  synonyms: [人工智能, GPU, 算力, 大模型]
  stocks:
    - {code: "300308", name: "中际旭创", role: "龙头"}
    - {code: "300394", name: "天孚通信", role: "梯队"}
    # role: 龙头 / 梯队 / 卫星
```

stocks 为空时, q-news 会输出 event-level (code=null) 记录, 提示"概念股待用户填". 填了后自动命中.

### 5.4 概念启动判定 `config.yaml > concept_status`

```yaml
concept_status:
  active_pct_threshold: 0.5        # ≥50% 概念股在 q-seed top → "已启动"
  qseed_topn_threshold: 30         # 取 q-seed TOP N 作参考
```

调高 `active_pct_threshold` (例 0.6) 让"已启动"更严, 调低 (0.3) 让 q-news 更倾向标"已启动" (减少假"未启动").

### 5.5 评分权重 `config.yaml > scoring.weights`

```yaml
scoring:
  weights:
    rule_confidence: 0.4              # 规则置信度
    concept_inactive_bonus: 0.3       # 概念未启动 → +bonus (alpha 来源!)
    direction_bullish_bonus: 0.1      # 利好加分
    qseed_inactive_bonus: 0.1         # 个股 q-seed 未命中 +bonus
    fundamentals_yoy_bonus: 0.1       # yoy_ni > 0 +bonus
```

### 5.6 输出均衡 `config.yaml > output.balance_per_concept`

```yaml
output:
  balance_per_concept: true        # 默认开. 防单一概念占满 TOP N
```

**为何**: 一条新闻 (例 "卫星组网 300 颗") 可能命中多个概念 (商业航天 + 卫星导航), 每个概念又展开到 5 只股票, 不限制时 TOP 10 容易被单一概念占满.

**算法**: 每概念上限 = `⌈top/3⌉`. `--top 10` 每概念最多 3 只, 名额不够时 leftover 按 score 补齐.

**实测对比** (q-news --top 10, 同一卫星新闻):

```
修复前: 商业航天 5 + 卫星导航 5 = 10 (单一新闻全占)
修复后: 商业航天 3 + 卫星导航 3 + 半导体_国产替代 3 + 模拟芯片 1 = 10 (均衡)
```

关闭: `output.balance_per_concept: false` 回到全局 score 排序.

### 5.7 Provider 抽象 (复用 q-fin 同结构, V1.5 paid 模式用)

与 q-fin 结构一致, L2 独立各自一份. 见 `USER_GUIDE_q-fin.md` §5.4.

---

## 6. 典型工作流

### 日常: 早盘看今日新闻命中

```bash
q-news --top 10
# 输出含: 事件标题 + 推理链 + 概念是否启动
```

### 找未启动的利好

```bash
# jq 过滤未启动的 (concept_status.已启动 = false)
q-news --top 50 | jq 'select(.concept_status."已启动" == false)'
```

### q-seed → q-news: 看 q-seed 候选股的新闻面

```bash
q-seed --top 30 | q-news --top 10
# 反查 q-seed 候选股最近的个股新闻
```

### q-news → q-fin: 新闻命中后做基本面验证

```bash
q-news --top 20 | q-fin --paid --top 5
# q-news 命中的股 → q-fin 调研入主方/基本面
```

### 三连击: 形态 + 新闻 + 基本面

```bash
q-seed --top 50 | q-fin --top 20 | q-news --top 5
# 形态像 → 基本面有事件 → 当前还有新闻驱动
```

### 周末批量

```bash
q-news --window-hours 168 --top 30 > weekend.jsonl  # 7 天
```

---

## 7. 失败模式

| 场景 | 行为 |
|---|---|
| akshare 某接口挂 | stderr warn, 该源 0 条, 继续跑其他 |
| RSS 拦/超时 | stderr "blocked", 跳过该 feed, 继续 |
| q-seed logs 不存在 | concept_status.已启动 = null, 不退出 |
| 全部源都失败 | events_scanned=0, output 空 |
| 输入非法行 | stderr warn, 跳过该行 |
| BrokenPipe (`q-news | head`) | 静默 exit 0 |
| Ctrl-C | 已 fetch 部分写 logs/, exit 130 |

---

## 8. 实施细节 (开发者)

- DESIGN: `~/sentry/quant/docs/DESIGN_q-news.md`
- 数据源验证报告: `~/sentry/quant/docs/SOURCES_q-news.md` (实测内网通达性)
- 测试: `~/sentry/quant/q-news/tests/{TEST_PLAN_q-news.md, run_tests.sh}` (31/31 PASS 含网络)
- 核心模块:
  - `lib/news_fetcher.py` — akshare + RSS 标准化 schema
  - `lib/rule_engine.py` — knowledge_base 关键词匹配 + reasoning_chain
  - `lib/concept_status.py` — q-seed crosscheck → 已启动判定 (核心 alpha)
  - `lib/q_fin_crosscheck.py` — q-fin logs 反查
  - `lib/scoring.py` — 综合评分 (含未启动 bonus)
  - `lib/cache.py` — 1h cache (新闻刷新快)
  - `lib/providers/` — LLM/Search 抽象 (复用 q-fin 模式, V1.5 paid 用)
  - `lib/budget_guard.py` — 预算保护 (V1.5 用)
- 复用自父项目: `lib/core/{tdx_loader,data_loader,mytt,stock_names}.py`

### 加自定义新闻源

例: 加上同花顺财经 RSS

```yaml
# config.yaml
rss:
  feeds:
    - {name: "rss:同花顺", url: "https://...", tags: [财经], weight: 0.8}
```

加 akshare 接口:

```yaml
sources:
  akshare:
    interfaces:
      - {name: "akshare:foo", func: "stock_news_xxx", weight: 0.7}
```

---

## 9. 已知限制 (V0)

- **海关一手数据无解** (customs.gov.cn 412 反爬), 用 akshare `macro_china_exports_yoy` 替代
- **国际新闻** (Reuters/Bloomberg) 内网拦, V0 不做
- **paid LLM 兜底** V0 占位, V1.5 实现 (会在未命中规则的高频事件上跑 Haiku)
- **历史回溯** V0 不做 (V1+)
- **sector_map 需用户手填** stocks (你在 8 方向各填 3-5 个核心标的)
- **规则库 (20 条)** 是种子, 用户用一段后看 unmatched_events.jsonl 自己补规则

---

## 10. 安全注意

- akshare/RSS 都是公网, 不需要 API key
- `--paid` (V1.5) 才需要 `~/sentry/quant/.env` 里的 `ANTHROPIC_API_KEY`
- cost_log.jsonl 当前不写 (V0 free 不烧), V1.5 paid 启用后会记账
