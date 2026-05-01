# Sentry Quant — A股量化选股系统

**Status**: V1.6 (2026-05-01)
**Owner**: wooyoo@gmail.com
**Last update**: 2026-05-01

围绕 A 股 (~11000 只) 的形态/规则/事件驱动选股。单进程 + 文件存储，无 DB，无后端服务。

---

## 当前能力

```
形态相似度   q-seed          (KNN, 6模板, 永远 free)
          ↓
科创突破    kechuang_breakout (规则引擎, 科创+创业板, 含解禁期过滤)
          ↓
布林带策略  boll_support      (三入场点: 下沿/突破/回踩, 含抗跌+解禁期过滤)
          ↓
基本面深挖  q-fin            (free / --paid 含 ⭐verdict + 实体调查链 + MA专项搜索)
          ↓
新闻驱动   q-news            (cninfo公告 + RSS, --paid LLM利多/利空)
          ↓
新股追踪   q-ipo-watch        (近7天新股, baostock名称补全)
          ↓
推送       q-push             → 企业微信群机器人 (每股一条, 含入场/止损/目标/重组分析)
双向对话   q-chatbot          → 企微自建应用 (Flask + cloudflare tunnel)
          ↓
可视化     q-dashboard        → 单页4板块 http://localhost:8501
```

---

## 14 个 q-* 命令

| 命令 | 职责 | 状态 | 成本 |
|------|------|------|------|
| `q-seed` | 形态 KNN (6模板) | ✅ | $0 |
| `q-fin` | 公告+股东+实体调查+MA搜索+⭐verdict | ✅ | $0 / `--paid` ~$0.01/股 |
| `q-news` | cninfo公告+新闻+LLM利多/利空 | ✅ | $0 / `--paid` 自动 |
| `q-kechuang-batch` | 科创/创业板平台突破 cron 20:30 | ✅ | ~$0.01/次 |
| `q-boll-batch` | 布林带三信号选股 cron 20:45 | ✅ | ~$0.01/次 |
| `q-ipo-watch` | 近7天新股扫描 cron 21:00 | ✅ | $0 |
| `q-chatbot-start` | 企微双向对话服务 | ✅ | DeepSeek 路由约$0.0001/条 |
| `q-sync` | 同步 TDX 日线 zip | ✅ | — |
| `q-sync-fin` | 同步 TDX 财报 zip | ✅ | — |
| `q-clean` | 清理旧 logs/cache | ✅ | — |
| `q-push` | stdin JSONL → 企业微信 webhook | ✅ | — |
| `q-dashboard` | Streamlit 单页4板块 :8501 | ✅ | — |
| `q-backtest-fast` | walk-forward 多周期回测 | ✅ | — |
| `q-pick-today` | 主流程编排 (q-seed+q-fin+push) | ✅ | ~$0.05/次 |

---

## Cron 时间表（工作日）

| 时间 | 脚本 | 内容 |
|------|------|------|
| 20:00 | q-pick-today-batch | 形态选股+q-fin+推送 |
| 20:15 | q-news-daily-batch | 公告热点+推送 |
| 20:30 | q-kechuang-batch | 科创突破+推送 Top3 |
| 20:45 | q-boll-batch | 布林带三信号+q-fin+推送 Top5 |
| 21:00 | q-ipo-watch | 新股缓存 |

---

## 布林带策略三入场点 (boll_support, 2026-05-01 新增)

针对科创板(688) + 创业板(300/301)，含抗跌性过滤（大盘跌时跑赢指数 >1%）+ 解禁期过滤。

| 信号 | 条件 | 意义 | 止损 |
|------|------|------|------|
| A 下沿开仓 | 收盘在布林下轨25%以内，横盘(<15%) | 低风险建仓，均值回归逻辑 | 破下轨离场 |
| B 上沿突破 | 今日首次放量(>1.5x)突破上轨 | 趋势启动，追涨有据 | 跌回上轨离场 |
| C 回踩上沿 | 近15日曾突破上轨，现缩量回踩 | 突破确认，最优入场 | 破上轨支撑离场 |

批处理配置：A Top5 + C Top2 + B Top3 → q-fin → 推综合 Top5

---

## 解禁期过滤 (lockup_filter, 2026-05-01 新增)

A股主要锁定期节点（从IPO日起算）：180天 / 365天 / 1095天，前后30天窗口内跳过。
已集成到：`kechuang_breakout` + `boll_support`

---

## q-fin MA专项搜索 (2026-05-01 新增)

检测到重组/收购类公告时，自动触发双向搜索：
- 方向A：`"{name} 收购标的 资产注入 重组方向"` → 找公司在买什么
- 方向B：`"{name} 入主方 收购方 要约 受让方"` → 找谁在买公司

LLM 提取：收购标的、标的业务、交易方式、重组方向，输出到推送消息。

验证案例：688531 日联科技 → 搜索到"收购上海菲莱测试技术，发行股份+可转债+现金，布局半导体检测全链"（高置信）

---

## q-push 推送格式升级 (2026-05-01)

boll_support 股票推送现含：
- 布林带区间（下轨～上轨，宽度%）
- 当前位置（0%=下轨, 100%=上轨）
- 量比 + 抗跌超额（大盘跌日平均跑赢%）
- T+1 入场参考价 / 止损位 / 目标价
- 【重组/注资调查】块：入主方、收购标的、交易方式、重组方向

---

## q-fin 国资控盘分析 (2026-05-01)

- 国资股东识别扩展：含南方电网、国家电网、中央汇金、财政部等央企前缀
- `state_owned_pct` 字段：十大流通股东中国资合计持股%
- LLM prompt 指引：国资高持股 = 流通盘小 = 高控盘 = 利多因子（不再误判为流动性风险）
- verdict 新增 `control_degree` 字段：高控盘/中控盘/低控盘

---

## 回测数据（2025-08 ~ 2026-04）

| 模板 | 80天胜率 | 均收益 | dist阈值 |
|------|---------|--------|---------|
| xiangnong | 89% | +61% | <5.0 |
| yunnange | 78% | +32% | 无 |
| hongjing | 56% | +19% | <5.0 |
| lanqi | 55% | +17% | 无 |
| fujing | 49% | +31% | <5.0 |

再次起爆胜率 **87%** vs 首次 56%

---

## LLM / 搜索 配置

| 用途 | Provider | 费用 |
|------|---------|------|
| 主 LLM | DeepSeek (openai_compat) | ~$0.14/1M tokens |
| 搜索 | DuckDuckGo → Tavily fallback | DDG免费, Tavily 1000次/月免费 |
| 意图路由 | DeepSeek | ~$0.0001/条 |

---

## 企业微信配置

| 类型 | 用途 | Key |
|------|------|-----|
| 群机器人 webhook | q-push 推送 | WECHAT_WEBHOOK_URL |
| 自建应用「福宝抓股」| q-chatbot 双向 | WECHAT_CORP_ID / AGENT_ID / CORP_SECRET |

---

## 设计原则

- **无 DB**：所有状态存 parquet/jsonl 文件
- **无 Docker**：直接跑在 WSL2 Python venv
- **无 threading 竞争**：各批处理顺序执行，cron 错峰15分钟
- **成本控制**：free 模式可用，付费仅在明确场景触发（实测10只<$0.015）
- **pilot 定位**：凭据/技术债后续重写时统一处理
