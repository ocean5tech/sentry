# q-fin 用户手册

**v1.0.0** · 事件驱动题材股检索器 (借壳 / 控制权变更 / 主业转型)

默认 free (不烧 token); `--paid` 启用 LLM 递归实体调查 + ⭐ verdict.

---

## 1. 部署

### 一次性安装

```bash
cd ~/sentry/quant/q-fin

# 创建独立 venv (L2 独立)
python3 -m venv .venv

# 装依赖 (国内推荐清华镜像)
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 入口可执行
chmod +x ./q-fin

# (可选) 加 PATH
ln -s ~/sentry/quant/q-fin/q-fin ~/.local/bin/q-fin
```

### 数据依赖

q-fin 读取:
- `~/sentry/quant/data/tdx/{sh,sz,bj}/lday/*.day` — TDX 日线 (kline_safety 用)
- `~/sentry/quant/data/tdx_fin/gpcw*.dat` — TDX 财报二进制 (fundamentals/YoY 用; 你定期 rsync 自 `tdxfin0423` 文件夹)
- `~/sentry/quant/data/stock_names.csv` — 代码 → 名称 (q-seed 同款)
- `~/sentry/quant/q-seed/logs/*.jsonl` — q-seed 输出 (q_seed_crosscheck 模块用)
- akshare 接口 (网络) — 公告 / 十大流通 / 户数变化

依赖包: `pandas / numpy / pyyaml / akshare / baostock / tqdm / anthropic / openai / duckduckgo-search / python-dotenv`.

### --paid 凭据

```bash
# 在 ~/sentry/quant/.env (chmod 600)
ANTHROPIC_API_KEY=sk-ant-xxx
# 可选: 切 DeepSeek / 通义 / Moonshot 时
# DEEPSEEK_API_KEY=sk-xxx
# OPENAI_API_KEY=sk-xxx
```

q-fin 启动时 (仅 --paid 模式) 自动 load .env. **free 模式不需要任何 API key**.

---

## 2. 启动方法

### Free (默认, 0 成本)

```bash
# 不带 --paid: 永远不烧钱
echo '{"code":"605389"}' | q-fin --top 1

# 给 input 文件
q-fin --input candidates.jsonl --top 20

# 与 q-seed 联用
q-seed --top 50 | q-fin --top 20
```

### Paid (启用 LLM 实体调查 + ⭐ verdict)

```bash
# 默认 standard 档 (Haiku entity, Sonnet verdict, max_uses=1, ~$0.05/只)
q-seed --top 10 | q-fin --paid --budget 1.00 --top 5

# Deep 档 (max_depth=3, max_uses 由 config, ~$0.10+/只)
q-fin --input list.jsonl --paid=deep --top 3 --budget 0.50

# 临时切 LLM provider (例: 试用 DeepSeek 省钱)
q-fin --paid --llm openai_compat --top 5 --budget 0.10

# 限定 per-stock 预算 (默认 0.10)
q-fin --paid --entity-budget-per-stock 0.20 --top 5
```

### Dry-run (估成本不真跑)

```bash
q-fin --paid --dry-run --input list.jsonl --top 10
# stderr 会打印 "Estimated --paid cost: $X.XX"
```

完整参数: `q-fin --help`.

---

## 3. 输入

### 主流: --input 文件 / stdin

每行 JSON, 必须含 `code`:

```jsonl
{"code":"605389"}
{"code":"301396","name":"任何额外字段被忽略"}
```

### V0 不支持裸跑全市场

V0 q-fin 必须给 input. 想全市场扫: 先 `q-seed > /tmp/all.jsonl` 再 `q-fin --input /tmp/all.jsonl`.

未来 V1 会加 Layer 1 全市场关键词撒网 (akshare 公告匹配).

---

## 4. 输出 JSON Lines Schema

```json
{
  "code": "605389",
  "name": "长龄液压",
  "scan_date": "2026-04-26",
  "source": "q-fin",
  "mode": "standard",
  "rank": 1,
  "score": 30.5,
  "score_type": "weighted_layer1+layer2",

  "verdict": {
    "rating": 5,
    "stars": "⭐⭐⭐⭐⭐",
    "one_liner": "教科书级芯片产业资本借壳",
    "theme_hardness": "硬",
    "entry_suggestion": "等回踩 VWAP20 附近再入",
    "key_risks": ["要约收购已完成, 涨幅已大", "..."],
    "themes": ["AI", "算力", "半导体"],
    "verdict_model": "claude-sonnet-4-6",
    "verdict_provider": "anthropic",
    "verdict_cost_usd": 0.011
  },

  "entity_research": {
    "mode": "standard",
    "llm_provider": "anthropic",
    "search_provider": "duckduckgo",
    "max_depth_used": 2,
    "budget_used_usd": 0.097,
    "chain": {
      "entity": "无锡核芯听涛科技合伙企业",
      "depth": 0,
      "_source": "llm",
      "_evidence_used": true,
      "_search_calls": 1,
      "identity": "私募合伙企业(SPV持股载体)",
      "business": "持股 29.99%, 芯片产业资本胡康桥/许兰涛的融资工具",
      "key_persons": ["胡康桥", "许兰涛", "核芯互联"],
      "sub": [
        {
          "entity": "胡康桥",
          "depth": 1,
          "_source": "llm",
          "identity": "自然人, 芯片产业资本家",
          "business": "清华电子本科, 莱斯大学硕士, AMD 出身, 2017 创核芯互联",
          "sub": []
        }
      ]
    }
  },

  "layer1_triggers": {
    "matched_events": [
      {"category": "控制权变更", "keyword": "要约收购", "ann_date": "2025-07-10"}
    ],
    "matched_shareholder_signal": {
      "new_entry_pct": 26.42,
      "entity_type": "合伙企业",
      "triggered_rule": "first_new_entry_pct_10"
    },
    "severity_sum": 50
  },

  "announcements_90d": {
    "total": 48,
    "by_category": {"控制权变更": 17, "业绩": 4, "...": 27},
    "key_titles": [{"date":"2025-07-10","title":"...","category":"控制权变更"}],
    "risk_flags": ["要约收购未完成"]
  },

  "shareholders": {
    "report_date": "2025-12-31",
    "top10_free": [{"rank":1,"name":"无锡核芯听涛...","pct":26.42,"is_partnership":true}],
    "new_entries_count": 4,
    "top10_concentration_pct": 78.5,
    "major_new_entry": {
      "name": "无锡核芯听涛科技合伙企业",
      "pct": 26.42,
      "type": "产业资本",
      "warning_emoji": "🔥",
      "detection": "partnership_top_holder"
    }
  },

  "holder_count_history": [
    {"date":"2026-02-10","count":8058,"chg_pct":4.5}
  ],

  "concept_status": {
    "name_changed": false,
    "scope_change_recent": true,
    "new_business_keywords": ["芯片", "半导体"],
    "is_pivot_story": true
  },

  "fundamentals": {
    "industry": "...",
    "data_source": "tdx_gpcw",
    "period": "2025-12-31",
    "src_file": "gpcw20251231.dat",
    "eps": 0.88,
    "revenue": 450000000,
    "net_profit": 186000000,
    "total_assets": 1530000000,
    "yoy_net_profit": 40.46,
    "yoy_revenue": 21.58,
    "net_margin": 0.413,
    "market_cap_yi": 15.3
  },

  "kline": {
    "current_price": 84.79,
    "vwap20_dev": 0.133,
    "vwap60_dev": 0.128,
    "ret60": 0.071,
    "kline_safety": "🟡"
  },

  "q_seed_crosscheck": {
    "is_in_q_seed": true,
    "matched_templates": ["hongjing"],
    "best_template": "hongjing",
    "best_dist": 5.51,
    "best_rank": 18,
    "crosscheck_bonus": 0.5
  },

  "tags": ["AI", "算力", "半导体"],

  "meta": {
    "scanner_version": "q-fin v1.0.0",
    "mode": "standard",
    "providers": {"llm": "anthropic", "search": "duckduckgo"},
    "scan_duration_ms": 22133,
    "total_cost_usd": 0.097,
    "budget_remaining_usd": 0.003
  }
}
```

### Free 模式与 Paid 模式差异

| 字段 | Free | Paid |
|---|---|---|
| `verdict` | `null` | 含 ⭐ rating/one_liner/risks |
| `entity_research` | `null` | 含完整 chain |
| `meta.providers` | 不存在 | `{llm, search}` |
| `meta.total_cost_usd` | 不存在 | 含真实开销 |
| 其他 layer2 字段 | 全有 | 全有 (paid 不影响) |

### 自动备份

每次跑写到 `~/sentry/quant/q-fin/logs/q-fin_top<N>_YYYYMMDD_HHMM.jsonl` + `.md`. 30 天滚动.

---

## 5. 配置文件 `config.yaml`

位置: `~/sentry/quant/q-fin/config.yaml`. 完整段落 + 注释见文件本身, 这里说调参常见场景.

### 5.1 数据路径 + 数据源

```yaml
data:
  tdx_dir: "/home/wyatt/sentry/quant/data/tdx"
  tdx_fin_dir: "/home/wyatt/sentry/quant/data/tdx_fin"
  q_seed_logs_dir: "/home/wyatt/sentry/quant/q-seed/logs"
  cache_dir: "./data_cache"
  cache_ttl_hours: 24

sources:
  tdx_fin:
    enabled: true
    load_recent_periods: 8           # 加载最近 8 期 (2 年)
    use_unverified_fields: false     # 仅 verified=true 字段对外
  akshare:
    enabled: true
    rate_limit_per_minute: 60
    timeout_seconds: 15
    retry_times: 2
    interfaces:                      # 接口名 config 化, 方便 akshare 升级时改
      announcements: "stock_zh_a_disclosure_report_cninfo"
      shareholders_top10: "stock_gdfx_free_top_10_em"
      holder_count_history: "stock_zh_a_gdhs_detail_em"
```

### 5.2 关键词库 `config/keywords.yaml`

```yaml
events:
  控制权变更:
    patterns: ["控制权变更","实际控制人变更","要约收购","协议转让"]
    severity: 5
  借壳重组:
    patterns: ["重大资产重组","借壳上市"]
    severity: 5
  主业转型:
    patterns: ["经营范围变更","公司名称变更"]
    severity: 3

shareholders:
  large_new_entry_pct: 0.05            # >= 5% 算大额新进
  partnership_weight: 1.5              # 合伙企业额外权重 (产业资本特征)

hot_tags:
  AI_算力: ["AI","算力","GPU","大模型"]
  半导体: ["芯片","半导体","模拟芯片"]
  机器人: ["人形机器人","具身智能"]
  # ... 8 方向
```

### 5.3 财报字段映射 `config/gpcw_field_map.yaml`

TDX gpcw 二进制每股 584 个 float32 字段, 4 个**已交叉验证** (茅台+长龄+平安, 误差<5%):

```yaml
fields:
  - {name: eps,           idx: 0,  unit: yuan_per_share, verified: true}
  - {name: revenue,       idx: 73, unit: yuan,           verified: true}
  - {name: net_profit,    idx: 94, unit: yuan,           verified: true}
  - {name: total_assets,  idx: 39, unit: yuan,           verified: true}
```

**用前请校准其他字段** — 社区索引表里的 ROE/毛利率位置在我们的数据上对不上.

### 5.4 Provider 抽象层

```yaml
providers:
  llm:
    active: "anthropic"               # 切 deepseek/moonshot/openai 改这一字段
    anthropic:
      api_key_env: "ANTHROPIC_API_KEY"
      default_model: "claude-haiku-4-5-20251001"   # entity_research 用
      verdict_model: "claude-sonnet-4-6"           # TOP 5 verdict 升级用
      verdict_model_top_n: 5
      pricing:
        haiku:  {input: 1.0, output: 5.0}          # $/MTok
        sonnet: {input: 3.0, output: 15.0}
    openai_compat:                    # 一类覆盖 OpenAI/DeepSeek/通义/Moonshot/豆包
      api_key_env: "OPENAI_API_KEY"
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4o-mini"
      pricing:
        default: {input: 0.15, output: 0.60}
      # 切 DeepSeek 示例 (复制并改 active="openai_compat"):
      # base_url: "https://api.deepseek.com/v1"
      # api_key_env: "DEEPSEEK_API_KEY"
      # default_model: "deepseek-chat"
      # pricing.default: {input: 0.27, output: 1.10}
    dummy: {pricing: {default: {input: 0, output: 0}}}
    dummy_priced: {pricing: {default: {input: 1.0, output: 5.0}}}

  search:
    active: "duckduckgo"              # 当 llm=anthropic 时实际走内置 web_search tool
    duckduckgo:
      retries: 2
      retry_sleep_seconds: 3
    anthropic_tool: {requires_llm: "anthropic", cost_per_call_usd: 0.01}
    dummy: {}
```

### 5.5 实体调查参数

```yaml
entity_research:
  max_depth_standard: 2               # --paid 默认深度
  max_depth_deep: 3                   # --paid=deep 深度
  fanout_per_layer: 2                 # 每层最多追 N 个 link
  budget_per_stock_usd: 0.10          # per 股票硬上限
  prefer_entity_hints: true           # 本地 hints 命中跳过 LLM
  min_shareholder_pct_for_research: 0.03   # major_new_entry pct >= 3% 才查
  web_search_max_uses: 1              # 1=经济($0.05/只), 2=完整($0.10/只)
```

### 5.6 预算保护

```yaml
budget:
  daily_usd_cap: 0.50                 # run 级别上限 (--budget 命令行可覆盖)
  on_exceeded: "fallback_free"        # fallback_free / error / warn_continue
  cost_log: "./data_cache/cost_log.jsonl"
```

### 5.7 实体提示库 `config/entity_hints.yaml`

跑过的实体自动追加, 下次 0 LLM 成本命中. 用户可手维:

```yaml
partnerships:
  "无锡核芯听涛科技合伙企业(普通合伙)":
    identity: "私募合伙企业 (SPV 持股载体)"
    gp: "核芯互联科技"
    controller: "胡康桥"
    confirmed: true                   # 用户手维标 confirmed, 永远命中 hints

companies:
  "核芯互联科技":
    identity: "模拟芯片设计公司"
    business: "国产替代模拟/混合信号芯片"

persons:
  "胡康桥":
    identity: "自然人, 芯片产业资本家"
    background: "清华电子本科, 莱斯大学硕士, AMD 出身"
```

### 5.8 调参常见场景

- **想看更多公告类型**: 编辑 `config/keywords.yaml` 加新 `events:` 段
- **8 方向 tag 不够**: 改 `config/keywords.yaml` 的 `hot_tags`
- **省钱**: `entity_research.web_search_max_uses: 1` (默认就是); 或者 `mode.default: "free"` 后只手动 `--paid`
- **追深一点**: `--paid=deep` + `--max-depth 3` + `--budget 0.30`
- **换 LLM 用 DeepSeek**: 编辑 config providers.llm.active="openai_compat", 复制 deepseek 示例段, 加 `DEEPSEEK_API_KEY` 到 .env
- **把已知合伙企业预填 hints** 让 q-fin 不查: 编辑 `config/entity_hints.yaml` 手动加, 标 `confirmed: true`

---

## 6. 典型工作流

### 日常: 免费扫 q-seed 候选

```bash
q-seed --top 30 | q-fin --top 10 > /tmp/today.jsonl
# 看长龄风格的票 (要 score 高 + safety 不红)
jq 'select(.score > 10 and .kline.kline_safety != "🔴") | {code, name, score}' /tmp/today.jsonl
```

### 周末深扫: 给 TOP 候选烧 token 出 ⭐

```bash
# 限 $1 总预算, TOP 5 升 Sonnet 出 verdict
q-seed --top 30 | q-fin --paid --budget 1.00 --top 5
```

### 单只深度查 (像 0424 调研长龄)

```bash
echo '{"code":"605389"}' | q-fin --paid=deep --budget 0.50 --top 1 | jq .
```

### 切 DeepSeek 省钱

```bash
# 编辑 config 切完, .env 加 DEEPSEEK_API_KEY
q-fin --paid --llm openai_compat --top 5 --budget 0.20
```

---

## 7. 成本参考表

| 调用 | 模型 | 单 call 估算 |
|---|---|---|
| entity_research (Haiku, max_uses=1) | Haiku 4.5 | ~$0.025 |
| entity_research (Haiku, max_uses=2) | Haiku 4.5 | ~$0.05 |
| verdict (Sonnet, TOP 5) | Sonnet 4.6 | ~$0.011 |
| verdict (Haiku, rank > 5) | Haiku 4.5 | ~$0.003 |

**典型 --paid 单只成本** (含 entity 1-3 calls + 1 verdict):
- max_uses=1: $0.04 ~ $0.07
- max_uses=2: $0.07 ~ $0.13

**实测案例**: 长龄液压 605389 完整跑 (max_uses=1 + Sonnet verdict): **~$0.05/只** (entity hits hints 后接近 $0).

---

## 8. 失败模式

| 场景 | 行为 |
|---|---|
| `ANTHROPIC_API_KEY` 缺 | 自动降 free, stderr 提示 |
| akshare 接口挂 (内网拦) | 该子模块字段为 null, 不退出 |
| tdx_fin 文件占位空 (期未到) | 自动跳过, 用上一期数据 |
| 预算超限 (run 级) | 剩余 stock 自动降 free, `meta.mode = "partial_standard"` |
| Web search 挂 / 拦截 | entity_research 跑无证据版 (Haiku 凭知识猜) |
| **input 含 null/empty code** | stderr warn 跳过该行, 不烧 token (e.g. q-news 输出 sector_map 待填的 event-level 记录) |
| Ctrl-C | 已完成部分写 logs/, exit 130 |
| BrokenPipe (e.g. `q-fin \| head`) | 静默 exit 0 |

---

## 9. 实施细节 (开发者)

- DESIGN: `~/sentry/quant/docs/DESIGN_q-fin.md` (~890 行)
- 测试: `~/sentry/quant/q-fin/tests/{TEST_PLAN_q-fin.md, run_tests.sh}` (65/65 PASS)
- 核心模块:
  - `lib/gpcw_loader.py` — TDX gpcw 二进制解析
  - `lib/fundamentals.py` — 跨期 + YoY 计算
  - `lib/{announcements,shareholders,holder_count,concept_change,kline_safety,q_seed_crosscheck}.py` — Layer 2
  - `lib/layer1_triggers.py` — Layer 1 派生
  - `lib/scoring.py` — 评分
  - `lib/entity_research.py` — 递归调查 + hints + auto-append (B3)
  - `lib/llm_verdict.py` — ⭐ verdict (B3)
  - `lib/budget_guard.py` — 预算保护 + cost_log
  - `lib/providers/{llm,search}_*.py` — Provider 抽象 (B2)
- 复用自父项目: `lib/core/{tdx_loader,data_loader,mytt,stock_names}.py`
- 实施踩坑教训: `~/.claude/projects/-home-wyatt-sentry/memory/feedback_qseed_lessons.md`

### 字段映射验证 + 添加新字段

```bash
# 用已知财报数字交叉验证 idx (e.g. 找 ROE 字段)
.venv/bin/python -c "
from lib.gpcw_loader import GpcwLoader
from pathlib import Path
loader = GpcwLoader(Path('data/tdx_fin'))
loader.load_recent(1)
arr = loader.get_raw_array('600519')
target = 36.94    # 茅台 2025Q3 ROE 已知值
for i, v in enumerate(arr):
    if 0.9 * target <= v <= 1.1 * target: print(f'idx={i} v={v}')
"

# 用 3 只股票交叉验证后写入 config/gpcw_field_map.yaml, 标 verified: true
```

---

## 10. 安全/凭据注意事项

- **`~/sentry/quant/.env`** 必须 chmod 600. 不要 commit (本项目当前不在 git, 但你以后可能加)
- API key 不要在 chat / Slack / Issue 里贴明文. 已贴的应到 [console.anthropic.com](https://console.anthropic.com/settings/keys) revoke 后重新生成
- `cost_log.jsonl` 保留所有真实消费记录, 用于 audit / 用量分析
