# Sentry Quant — A股量化选股系统

**Status**: V1.5 (2026-05-01)
**Owner**: wooyoo@gmail.com
**Last update**: 2026-05-01

围绕 A 股 (~11000 只) 的形态/规则/事件驱动选股。单进程 + 文件存储，无 DB，无后端服务。

---

## 当前能力

```
形态相似度   q-seed          (KNN, 6模板, 永远 free)
          ↓
科创突破    kechuang_breakout (规则引擎, 科创+创业板)
          ↓
基本面深挖  q-fin            (free / --paid 含 ⭐verdict + 实体调查链)
          ↓
新闻驱动   q-news            (cninfo公告 + RSS, --paid LLM利多/利空)
          ↓
新股追踪   q-ipo-watch        (近7天新股, baostock名称补全)
          ↓
推送       q-push             → 企业微信群机器人 (每股一条)
双向对话   q-chatbot          → 企微自建应用 (Flask + cloudflare tunnel)
          ↓
可视化     q-dashboard        → 单页4板块 http://localhost:8501
```

---

## 13 个 q-* 命令

| 命令 | 职责 | 状态 | 成本 |
|------|------|------|------|
| `q-seed` | 形态 KNN (5模板, litong已停用) | ✅ | $0 |
| `q-fin` | 公告+股东+实体调查+⭐verdict | ✅ | $0 / `--paid` ~$0.10/股 |
| `q-news` | cninfo公告+新闻+LLM利多/利空 | ✅ | $0 / `--paid` 自动 |
| `q-kechuang-batch` | 科创/创业板平台突破 cron 20:30 | ✅ | ~$0.10/次 |
| `q-ipo-watch` | 近7天新股扫描 cron 20:45 | ✅ | $0 |
| `q-chatbot-start` | 企微双向对话服务 | ✅ | DeepSeek 路由约$0.0001/条 |
| `q-sync` | 同步 TDX 日线 zip | ✅ | — |
| `q-sync-fin` | 同步 TDX 财报 zip | ✅ | — |
| `q-clean` | 清理旧 logs/cache | ✅ | — |
| `q-push` | stdin JSONL → 企业微信 webhook | ✅ | — |
| `q-dashboard` | Streamlit 单页4板块 :8501 | ✅ | — |
| `q-backtest-fast` | walk-forward 多周期回测 | ✅ | — |
| `q-pick-today` | 主流程编排 (q-seed+q-fin+push) | ✅ | ~$0.15/次 |

---

## Cron 时间表（工作日）

| 时间 | 脚本 | 内容 |
|------|------|------|
| 20:00 | q-pick-today-batch | 形态选股+q-fin+推送 |
| 20:15 | q-news-daily-batch | 公告热点+推送 |
| 20:30 | q-kechuang-batch | 科创突破+推送 |
| 20:45 | q-ipo-watch | 新股缓存 |

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

q-chatbot 需要 cloudflare tunnel 提供公网回调URL，
每次重启URL变化需更新企微后台（待配置 Named Tunnel 固定）

---

## 设计原则

- **无 DB**：所有状态存 parquet/jsonl 文件
- **无 Docker**：直接跑在 WSL2 Python venv
- **无 threading 竞争**：各批处理顺序执行，cron 错峰15分钟
- **成本控制**：free 模式可用，付费仅在明确场景触发
- **pilot 定位**：凭据/技术债后续重写时统一处理
