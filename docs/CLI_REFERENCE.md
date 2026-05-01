# CLI 参数手册

所有命令均在 `$PATH`，位于 `~/sentry/quant/scripts/`。

---

## q-pick-today — 今日形态信号主流程

```bash
q-pick-today [选项]
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--date YYYY-MM-DD` | 今天 | 目标交易日 |
| `--skip-sync` | 否 | 跳过 rsync（假设 data/tdx 已是最新） |
| `--skip-fin` | 否 | 跳过 q-fin 深度调研（省 token） |
| `--skip-push` | 否 | 只打印不推送企业微信 |
| `--budget USD` | config 值 | 覆盖每日 q-fin 总预算 |
| `--config PATH` | q-pick-today/config.yaml | 配置文件路径 |

**示例：**
```bash
q-pick-today                          # 正常运行，今日数据
q-pick-today --skip-push              # 测试：打印消息但不推送
q-pick-today --date 2026-04-28        # 指定日期复盘
q-pick-today --skip-fin --skip-sync   # 纯形态扫描，不花 LLM 费用
```

---

## q-news — 新闻/公告扫描

```bash
q-news [选项]
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--paid [standard\|deep]` | free | 启用 LLM（cninfo+板块推理）; 不加=纯规则引擎 |
| `--push` | 否 | 分析完成后自动推企业微信 |
| `--top N` | 30 | 输出 TOP N 新闻事件 |
| `--window-hours N` | 24 | 拉最近 N 小时新闻 |
| `--since / --until` | — | 精确时间范围 ISO8601 |
| `--sources SRC` | 全部 | 限定数据源，逗号分隔，如 `akshare:cls,rss:新华网-经济` |
| `--format jsonl\|md\|both` | jsonl | 输出格式 |
| `--output PATH` | 自动 | 输出文件路径 |
| `--no-cache` | 否 | 跳过 cache，强制重新拉取 |

**示例：**
```bash
q-news                                # free 模式，纯规则引擎
q-news --paid                         # 付费：cninfo 7天公告 + LLM 分析
q-news --paid --push                  # 分析后自动推企业微信
q-news --paid=deep --push             # 深度模式（更多递归层）
q-news --window-hours 48              # 拉最近 48h 新闻
q-news --sources akshare:cls          # 只拉财联社
```

---

## q-backtest-fast — walk-forward 回测

```bash
q-backtest-fast [选项]
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--start YYYY-MM-DD` | 2025-08-01 | 回测起始日 |
| `--end YYYY-MM-DD` | 2026-04-22 | 回测结束日 |
| `--step N` | 1 | 每 N 个交易日取一个样本点 |
| `--hold-days 5,20,80` | 5,10,20,40,80 | 持仓天数列表（逗号分隔） |
| `--top N` | 5 | 每模板 top N 候选 |
| `--templates LIST` | 全部6个 | 逗号分隔模板名 |
| `--win-pct F` | 0.05 | 胜率阈值（+5%） |
| `--max-drawdown-allowed F` | 0.07 | 持仓体验好最大浮亏（7%） |
| `--output PATH` | 自动 | 输出 jsonl 路径 |

**示例：**
```bash
# 单日扫描（查看今天有哪些候选）
q-backtest-fast --start 2026-04-30 --end 2026-04-30

# 完整回测（带 dist 阈值，约 6 分钟）
q-backtest-fast --start 2025-08-01 --end 2026-04-01 --step 1 --hold-days 20,80

# 只跑 yunnange 和 xiangnong 两个高胜率模板
q-backtest-fast --templates yunnange,xiangnong --hold-days 80
```

---

## q-fin — 深度事件调研

```bash
# 从 stdin 读 JSONL（每行含 code + name）
echo '{"code":"605389","name":"长龄液压","scan_date":"2026-04-30"}' | q-fin --paid
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--paid [standard\|deep]` | free | 启用 LLM；free=只拉结构化数据不分析 |
| `--top N` | 15 | 分析 top N 只股票 |
| `--max-depth N` | config | 实体调查递归深度（1-3） |
| `--budget USD` | 0.50 | 覆盖每日 LLM 预算上限 |
| `--llm NAME` | config.active | 临时覆盖 LLM 提供商（anthropic/deepseek） |
| `--output PATH` | 自动 | 输出 jsonl 路径 |

**示例：**
```bash
# 从 q-backtest 输出接管（通过 stdin）
cat /tmp/picks.jsonl | q-fin --paid --max-depth 2

# 单股深度调研
echo '{"code":"300532","name":"今天国际"}' | q-fin --paid=deep

# 临时切回 Anthropic（需要 web_search 功能）
echo '{"code":"605389","name":"长龄液压"}' | q-fin --paid --llm anthropic
```

---

## q-seed-hongjing / q-seed-litong — 形态扫描

```bash
q-seed-hongjing [TOP_N] [--skip-scan] [--as-of-date DATE]
q-seed-litong   [TOP_N] [--skip-scan] [--as-of-date DATE]
```

| 参数 | 说明 |
|---|---|
| `TOP_N`（位置参数） | 输出 top N 候选，默认 30 |
| `--skip-scan` | 跳过全市场扫描，直接用上次结果拉基本面 |
| `--as-of-date DATE` | 历史回测用，限制 K 线不超过该日期（防前视偏差） |

**示例：**
```bash
q-seed-hongjing           # top 30
q-seed-hongjing 50        # top 50
q-seed-litong --skip-scan # 只拉基本面，不重新扫描
```

---

## q-sync / q-sync-fin — 数据同步

```bash
q-sync [--from-zip PATH] [--status]
q-sync-fin [--from-zip PATH]
```

| 参数 | 说明 |
|---|---|
| `--from-zip PATH` | 直接从本地 zip 解压（绕过下载，IBM 内网用） |
| `--status` | 仅查看当前数据最新日期 |

**示例：**
```bash
# 从 Windows 侧手动下载的 zip 同步
q-sync --from-zip /mnt/c/Users/.../hsjday0430.zip
q-sync-fin --from-zip /mnt/c/Users/.../tdxfin0430.zip

# 查看当前数据日期
q-sync --status
```

---

## q-dashboard — 可视化面板

```bash
q-dashboard          # 启动 Streamlit，监听 :8501
```

浏览器打开 `http://localhost:8501` 或 `http://localhost:8501?date=2026-04-28`（跳转到指定日）。

---

## q-clean — 清理过期文件

```bash
q-clean [--apply] [--quiet]
```

| 参数 | 说明 |
|---|---|
| `--apply` | 真正删除（不加只 dry-run 显示将删什么） |
| `--quiet` | 减少输出 |

**示例：**
```bash
q-clean          # 预览将删除的文件
q-clean --apply  # 真正清理
```

---

## is_trading_day — 交易日判断

```bash
is_trading_day [YYYYMMDD]   # 省略=今天
# 退出码 0=交易日, 1=非交易日
```

```bash
is_trading_day && echo "今天开市" || echo "今天休市"
is_trading_day 20260501    # 检查五一: 输出"非交易日"
```
