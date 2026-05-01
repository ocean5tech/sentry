# 配置文件指南

所有配置均为 YAML，禁止硬编码在脚本里（密钥除外，见 `.env`）。

---

## 密钥文件 `.env`

位置：`~/sentry/quant/.env`

```bash
ANTHROPIC_API_KEY=sk-ant-...       # Claude API（可选，切回 Anthropic 用）
DEEPSEEK_API_KEY=sk-...            # DeepSeek API（当前主力 LLM）
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
TAVILY_API_KEY=tvly-dev-...        # 搜索备用（DDG 失败时自动切换）
```

> 切换 LLM：修改 `q-fin/config.yaml` 和 `q-news/config.yaml` 里的 `providers.llm.active`
> - `deepseek`：便宜（~$0.27/M input），无 web_search tool
> - `anthropic`：贵（~$1.0/M input），支持实时联网搜索

---

## q-pick-today/config.yaml

控制每日形态信号流程：

```yaml
templates:
  - yunnange    # 80d胜率78%  ← 优先
  - lanqi       # 55%
  - hongjing    # 56% (dist<5)
  - fujing      # 49%
  - xiangnong   # 89% (n小)
  # litong 已停用

data:
  windows_dir_pattern: "/mnt/c/Users/201744672/Downloads/hsjday{mmdd}"
  local_tdx:  "/home/wyatt/sentry/quant/data/tdx"

q_fin:
  enabled: true
  threshold_stars: 4          # 仅 ⭐⭐⭐⭐+ 跑 q-fin
  budget_per_stock: 0.15      # 每只 $0.15 上限
  daily_total_budget: 1.00    # 每日总预算
  max_depth: 3                # 实体调查递归层数

star_thresholds:              # 评级规则: dist + 模板胜率联合判断
  - {min_stars: 5, max_dist: 5.0, min_win80: 0.50}
  - {min_stars: 4, max_dist: 7.0, min_win80: 0.45}
  - {min_stars: 3, max_dist: 10.0, min_win80: 0.0}
```

**常用调整：**
- 想少花钱：把 `threshold_stars: 4` 改成 `5`，只给最强信号用 q-fin
- 想多看几只：把 `max_dist` 放宽（如 8.0），会出现更多候选但质量下降
- 临时关 q-fin：`enabled: false`

---

## q-backtest/config.yaml

控制回测参数：

```yaml
defaults:
  start_date: "2025-08-01"
  end_date:   "2026-04-22"
  hold_days: 20
  win_pct: 0.05           # 胜率阈值: +5%
  top_per_template: 5     # 每模板取 top N

dist_thresholds:          # 每模板 dist 上限（回测验证最优值）
  hongjing:  5.0          # dist<5: 56% win vs 42% 全量
  xiangnong: 5.0          # dist<5: 89% win vs 27-35%
  fujing:    5.0          # dist<5: avg +29% vs +14%
  yunnange:  ~            # 不限: 78% 无论 dist
  lanqi:     ~            # 不限
  litong:    ~            # 已停用
```

**调整建议：**
- 不确定某个 dist 阈值是否合适 → 用 `q-backtest-fast` 跑对比看数字

---

## q-fin/config.yaml

主要关注：

```yaml
providers:
  llm:
    active: "deepseek"        # 改成 "anthropic" 切回联网搜索
  search:
    active: "duckduckgo"      # 主搜索；DDG失败自动切 Tavily

entity_research:
  max_depth_standard: 2       # --paid 模式: 调查2层
  max_depth_deep: 3           # --paid=deep 模式: 调查3层
  budget_per_stock_usd: 0.10  # 每只股 LLM 预算
  web_search_max_uses: 1      # 每次 LLM 调用最多搜几次

budget:
  daily_usd_cap: 0.50         # 每日总预算
  on_exceeded: "fallback_free" # 超出后降级为 free 模式
```

---

## q-news/config.yaml

主要关注：

```yaml
sources:
  akshare:
    interfaces:
      - {name: "akshare:cls",  func: "stock_info_global_cls",  weight: 1.0}  # 财联社
      - {name: "akshare:em",   func: "stock_info_global_em",   weight: 1.0}  # 东方财富
      - {name: "akshare:cctv", func: "news_cctv", weight: 0.7}              # 央视
  rss:
    feeds:
      - {name: "rss:新华网-经济", url: "http://www.news.cn/fortune/..."}
      # 可以自行增减 RSS 源

budget:
  daily_usd_cap: 0.50
  per_stock_cap_usd: 0.05    # cninfo 每条公告分析上限

mode:
  default_window_hours: 24   # 每次拉最近 N 小时新闻
```

**调整建议：**
- 新闻太多噪音：提高规则引擎 `triggers_min_match: 2` → `3`
- 想覆盖更多板块：在 `config/knowledge_base.yaml` 增加关键词规则
- 想增加 cninfo 扫描天数：修改 `cninfo_scanner.py` 的 `n_days=7` 参数

---

## q-seed/config.yaml

模板定义（一般不需要修改）：

```yaml
templates:
  hongjing:
    code: "301396"
    sig_dates: ["2024-09-30", "2025-12-09", "2026-01-12"]
    mode: strict
  yunnange:
    code: "002428"
    sig_dates: ["2026-02-27"]
    mode: loose
  # ... 其他4个模板

wave_params:
  launch_ret: 0.05           # 起爆日涨幅阈值 5%
  launch_vol_mul: 2.0        # 放量倍数
  lookback: 60               # 回溯窗口天数
  use_market_features: true  # 加入大盘趋势特征
```

**何时需要修改：**
- 添加新模板：增加一个模板 block，指定 code 和 sig_dates
- 调整起爆定义：修改 `launch_ret`（降低=更多信号但噪声多）

---

## 日志保留策略（q-clean）

在 `q-clean/config.yaml` 中：

```yaml
retention:
  backup_days: 3            # data/backup/: 保留 3 天
  daily_parquet_days: 180   # data/daily/*.parquet: 保留 180 天
  logs_days: 30             # q-*/logs/*.jsonl: 保留 30 天
  cache_fin_days: 7         # q-fin/data_cache: 保留 7 天
  cache_news_days: 1        # q-news/data_cache: 保留 1 天
```
