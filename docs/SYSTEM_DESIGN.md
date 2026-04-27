# 选股系统设计 v2 — 疯牛家族扩展

设计日期: 2026-04-24
状态: **草案 / 待用户拍板**

---

## 0. 总览

### 设计目标
在已有的 `q-seed-hongjing / q-seed-litong` 基础上，扩展出**可组合、可调度、可发布**的选股工作流，捕捉"超级牛股"。延续用户哲学："抓 1 只就够，不要普遍 alpha"。

### 四件套架构

```
       ┌─────────────────────────────────────────────────────┐
       │                  数据层 / 知识层                     │
       │  本地 TDX (data/tdx, data/tdx_fin) + akshare +       │
       │  baostock + Web 数据源 + 推理知识库                  │
       └─────────────────────────────────────────────────────┘
                              ↓
       ┌──────────┬──────────┬──────────┐
       │ q-seed-* │  q-fin   │  q-news  │   ← 三种独立扫描器
       │  形态    │  基本面  │  事件    │
       └──────────┴──────────┴──────────┘
                              ↓
       ┌─────────────────────────────────────────────────────┐
       │   target list (统一 JSON Lines 格式)                 │
       │   可作为另一个 q-xx 的 stdin                         │
       └─────────────────────────────────────────────────────┘
                              ↓
       ┌──────────────────────┐    ┌────────────────────────┐
       │   q-publish 发布     │    │ q-sched 调度          │
       │   - 企业微信         │    │  - cron 定时          │
       │   - Dashboard        │    │  - 管道串联           │
       └──────────────────────┘    └────────────────────────┘
```

---

## 1. 通用 IO 标准 (核心)

所有 q-xx 命令**必须**遵守同一套 IO 协议，这是管道能否工作的关键。

### 1.1 输出格式 — JSON Lines (`.jsonl`)

每行一个候选股 JSON：

```json
{
  "code": "605389",
  "name": "长龄液压",
  "source": "q-seed-hongjing",         // 哪个 q-xx 产生的
  "scan_date": "2026-04-24",            // 扫描时点
  "score": 5.57,                        // 评分（越小/越大根据 source 定义，必须文档说明）
  "score_type": "knn_distance",         // 评分类型
  "tags": ["AI", "算力", "半导体"],     // 命中的题材方向
  "evidence": [                         // 触发原因证据链
    {"type": "kline_pattern", "detail": "wave+pierce, dist=5.57"},
    {"type": "shareholder_change", "detail": "核芯听涛 26.42% 新进"},
    {"type": "concept_upgrade", "detail": "胡康桥/核芯互联入主, 模拟芯片转型"}
  ],
  "metadata": {                         // 自由扩展字段
    "sig_date": "2026-04-22",
    "entry_price": 84.79,
    "kline_safety": "🟢",
    "vwap60_dev": 0.128,
    "...": "..."
  },
  "priority": "high"                    // high / medium / low
}
```

**为什么用 JSON Lines 而非 CSV**:
- 字段不固定（每个 source 有自己的 metadata）
- 易于管道流式处理
- 易于 dashboard 渲染
- 仍可用 `pandas.read_json(lines=True)` 转 DataFrame 看

### 1.2 输入接受

- 默认: 全市场扫描
- `--input <file.jsonl>`: 仅在 input list 内的股票上扫描
- `stdin`: 通过管道 `q-fin | q-seed-hongjing` 接受上一步的 list

### 1.3 命令行参数约定 (统一)

```
q-xx [子命令] \
  --input <file>          # 输入 list (默认: 全市场)
  --start YYYY-MM-DD      # 时间区间起
  --end   YYYY-MM-DD      # 时间区间止
  --top N                 # 输出前 N 条
  --output <file>         # 输出文件 (默认: stdout)
  --format jsonl|csv|md   # 输出格式
  --tags AI,算力          # 仅输出含这些 tag 的
  --priority high         # 仅输出 high 优先级
```

---

## 2. q-seed-* 系列 (已有, 改造)

### 现状
- `q-seed-hongjing`: 严格 HH/Pierce, 模板 = 宏景 301396 × 3
- `q-seed-litong`: loose HH/Pierce, 模板 = 利通 603629 × 4
- 输出: `logs/similar_to_*.csv`
- 全市场扫描，~15 分钟

### 改造点
1. 输出改为 JSON Lines (兼容统一标准)
2. 接受 `--input` 缩小扫描范围（管道用）
3. `--top` 控制输出数量
4. tags 字段: 从主营业务/股东命中的 8 个方向自动标注

### 未来扩展
- 当用户发现新模板（如未来抓到第 3 只超级牛股）→ 加 `q-seed-<name>`
- 不合并模板（用户已确认这一原则）

---

## 3. q-fin (基本面变化扫描)

### 范围
找"基本面正在变化"的股票。已有部分实现，需统一。

### 子模块（建议拆成子命令）

| 子命令 | 功能 | 现状 |
|---|---|---|
| `q-fin shareholders` | 十大流通股东大额新进（产业资本入主） | 已有 deep_analyze 部分实现 |
| `q-fin pivot` | 经营范围变更 / 改名 / 战略转型 | 已有 scan_business_pivot.py |
| `q-fin hot-news` | 公告含 AI/算力/机器人等热门关键词 | 已有 scan_hot_keyword_announce.py |
| `q-fin growth` | 净利 YoY > 50% 且加速 | 已有 baostock 财报，未独立 |
| `q-fin dragon` | 龙虎榜异动（机构席位/游资） | **未做** |

### 数据源
- baostock: 财报、行业、市值
- akshare: 公告（cninfo）、十大流通股东、龙虎榜
- 本地 TDX 财报 (`data/tdx_fin/`)

### 输出示例
```json
{"code":"605389","name":"长龄液压","source":"q-fin shareholders",
 "tags":["AI","半导体"],
 "evidence":[{"type":"new_holder","detail":"核芯听涛 26.42% 新进 (产业资本)"}],
 "score":0.90,"score_type":"event_strength"}
```

---

## 4. q-news (新闻事件驱动) — 重点+难点

### 设计目标
**多层推理**：新闻事件 → 受影响行业 → 期货/原料 → 受益股

例：霍尔木兹海峡封锁 → 原油/氩气/天然气期货 → 华特气体（氩气供应）

### ❗ 关键难点 + 待解决问题

#### 4.1 数据源 (待用户拍板)

| 候选 | 优点 | 缺点 |
|---|---|---|
| **彭博 Bloomberg API** | 权威、覆盖全 | 付费，单位订阅了吗？ |
| **akshare 财联社/新浪/东财** | 免费 | 噪音多，分类粗 |
| **海关总署** customs.gov.cn | 官方 | 无 API，需爬 |
| **期货交易所** 上期所/大商所/郑商所 | 官方价格 | 实时性差 |
| **Reuters / Twitter / X** | 全球事件 | API 收费、IBM 内网代理可能拦 |

⚠️ **IBM 内网代理问题**: 之前 baostock 走 TCP 才能用，akshare 多数 HTTP 调用 OK。但 Bloomberg/Twitter 等海外 API 在内网很可能被拦。

**Q1: 数据源选哪几个？**

#### 4.2 推理引擎 (核心难题)

例子推理链：
```
[输入] 霍尔木兹海峡封锁
   ↓ 一层推理：地缘 → 物流影响
影响：中东原油运输受阻
   ↓ 二层推理：商品 → 产业链
原油上涨 → 炼化成本上升 → 但氩气是炼化副产品 → 氩气供应减少
   ↓ 三层推理：原料 → A 股
氩气短缺 → A 股氩气供应商受益（华特气体、和远气体）
```

#### 4.3 实现方案 (3 选 1，需用户拍板)

**方案 A: 纯 LLM 推理**
- 每条新闻调用 Claude API 做多层推理
- 优点：覆盖任何事件，灵活
- 缺点：贵（每条 1k tokens × 50 美元 ≈ 1 万次调用）、不可复用、慢
- **风险**: 单事件 10 美元成本（多层调用）

**方案 B: 规则引擎 + 知识库**
- 维护 YAML 知识库:
  ```yaml
  events:
    - trigger: "霍尔木兹|波斯湾|伊朗"
      keywords: ["封锁","紧张","冲突"]
      first_layer: ["原油","天然气","液化气"]
      second_layer:
        原油: ["中海油服","海油工程"]
        氩气: ["华特气体","和远气体"]   # 副产品逻辑预设
  ```
- 优点：免费、可控、可复用
- 缺点：覆盖有限（只命中知识库里的）、需要持续维护

**方案 C: 混合 (推荐)**
- 规则引擎兜底常见事件
- LLM 仅在新事件时调用，结果**写回知识库**（自我进化）
- 节省成本同时保留灵活

**Q2: 选 A / B / C？我推荐 C，但 LLM 部分调用频率/上限怎么定？**

#### 4.4 输出示例

```json
{"code":"002409","name":"华特气体","source":"q-news",
 "tags":["地缘事件","氩气","原油链"],
 "evidence":[
   {"type":"news","detail":"2026-04-24 路透社: 伊朗扣押油轮"},
   {"type":"reasoning","detail":"地缘紧张 → 中东原油受阻 → 氩气副产品供应减少"},
   {"type":"market_check","detail":"WTI 原油期货涨 4.2%, 氩气现货价 +8%"}
 ],
 "score":0.85,"priority":"high","metadata":{"event_date":"2026-04-24","layers":3}}
```

---

## 5. 调度系统 (q-sched)

### 三种调度模式

#### 5.1 配置文件定时
`~/sentry/quant/config/sched.yaml`:
```yaml
jobs:
  daily_seed:
    cron: "30 16 * * 1-5"          # 工作日 16:30
    pipeline:
      - q-seed-hongjing --top 30
      - q-seed-litong --top 30
      - q-publish --to wechat,dashboard --priority high

  weekly_fin_pivot:
    cron: "0 20 * * 5"              # 周五 20:00
    pipeline:
      - q-fin pivot --top 50
      - q-fin shareholders --input -  # 接受上一步 stdout
      - q-publish --to dashboard

  realtime_news:
    cron: "*/30 9-15 * * 1-5"       # 工作日盘中每半小时
    pipeline:
      - q-news --hot
      - q-publish --to wechat --priority high  # 仅高优紧急
```

#### 5.2 手动管道 (Unix 风格)
```bash
q-fin pivot --top 100 | q-seed-hongjing --top 20 --start 2026-01-01
# 含义：基本面变化的票 → 再用宏景形态二次确认
```

#### 5.3 命令式调用
```bash
q-sched run daily_seed   # 立即跑一次
q-sched list             # 列出所有 job 和最后执行时间
q-sched logs daily_seed  # 看某 job 历史 log
```

### 实现选择 (待拍板)

**Q3: 调度系统用哪个？**
- A. **cron + bash + jsonl 文件**（最简单，0 依赖）
- B. **systemd timer** （Linux 原生，状态可见，需要 sudo）
- C. **Prefect / Airflow**（功能强但重，对单机+1 用户过度）

我推荐 A（甚至连 q-sched 命令都不用建，直接用 crontab）。

---

## 6. 发布系统 (q-publish)

### 6.1 渠道 A: 企业微信

#### 接入方式 (待拍板)
- **方式 1: 群机器人 webhook** （最简单，5 分钟接通）
  ```
  POST https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
  ```
  优点: 无需自建应用、无 access_token 管理
  缺点: 只能往群发，不能 1v1

- **方式 2: 自建应用 + access_token** （需企业微信管理权限）
  优点: 可 1v1 发给指定人、消息更丰富（卡片、图文）
  缺点: 需要管理后台配置

**Q4: 企业微信用方式 1 还是 2？方式 1 你需要先建个机器人拿 webhook URL 给我**

#### 消息格式（建议）
- 文本卡片 (Markdown 模式):
```
## 🐂 疯牛家族日报 2026-04-24

### 🏆 本日 TOP 5 (新增加 ⭐)
1. ⭐ 605389 长龄液压 84.79 [hongjing dist 5.57]
   AI/算力 | 实控人变更核芯互联 | K线🟢
2. ⭐ 002869 金溢科技 ... 
   ...

### 📊 状态变化
- 002463 沪电股份 排名: 29 → 31 (轻微下降)

[详细报告] dashboard.url
```

### 6.2 渠道 B: Dashboard

#### 选型 (待拍板)
- **A. Streamlit** (Python, 部署简单, 可交互)
- **B. 静态 HTML + Chart.js / TradingView** (无服务器, GitHub Pages 部署)
- **C. Grafana + 时序数据库** (专业但重)
- **D. 简易 Markdown 表格** (本地浏览, 0 部署)

我推荐 **A. Streamlit**（中庸之道）。功能：
- 主表（按 priority/source/score 排序）
- 与昨日 diff（新增🆕、消失❌、排名升降）
- 单股点开详细 evidence + K 线图
- 历史扫描记录回看

**Q5: Dashboard 选哪个？部署在哪里（本地/云端）？**

---

## 7. AI 基座 (Claude Code 订阅 + $50 API)

### 现状
- 用户有 Claude Code 订阅（按月 $20/$100/$200，有交互配额）
- $50 API 余额 (按需调用付费)

### 调用策略

| 场景 | 用 LLM？ | 替代 |
|---|---|---|
| K 线形态匹配 | ❌ | KNN + 数学 |
| 财报数值解析 | ❌ | baostock 直接给 |
| 公告关键词命中 | ❌ | 字符串 grep |
| **公告语义理解（"是否真转型"）** | ✅ 偶尔 | 规则做不好 |
| **新闻事件 → 受影响产业链推理** | ✅ 重度 | LLM 强项 |
| **跨数据源去噪 / 优先级排序** | ⚠️ 可选 | 启发式规则 |

### 成本控制原则
1. **批量调用**：一次 LLM 处理 N 条新闻（共享 system prompt）
2. **缓存**：同一新闻不重复推理（hash + Redis/sqlite）
3. **小模型先**：用 Haiku 兜底简单分类，只在必要时升 Sonnet
4. **本地兜底**：能用规则就用规则
5. **预算硬上限**: 每天 < $1 (一年 < $365)

### Claude Code 订阅 vs API 区分
- **Claude Code 订阅** (你和我聊天的额度): 适合**研究/调试**，不适合自动化批量调用
- **API ($50)**: 适合**自动化推理调用**

**Q6: q-news 推理是否接受用 API？预算多少美元/月？**

---

## 8. 实施路线 (V0/V1/V2/V3)

### V0 (~1 天) — 最小可用
1. 统一 IO 标准 (JSON Lines)
2. 改造 `q-seed-hongjing/litong` 输出 JSON Lines
3. 整合现有 `scan_business_pivot.py` + `scan_hot_keyword_announce.py` → `q-fin pivot/hot-news`
4. 写 `q-publish wechat` (仅 webhook 文本卡片)
5. cron 跑 daily

### V1 (~3-5 天) — 完整四件套
1. `q-fin shareholders/dragon`（龙虎榜接入 akshare）
2. `q-news` 知识库版（**纯规则 + 无 LLM**）
3. Streamlit Dashboard 第一版（主表 + diff）
4. `q-sched` 命令封装

### V2 (~1 周) — AI 加持
1. `q-news` LLM 推理引擎 (混合方案 C)
2. 知识库自我进化机制
3. 跨 q-xx 的"双命中"加权

### V3 (~未定) — 持续迭代
- 新模板加入 `q-seed-*`
- 历史回溯系统（看曾经命中的股后续表现）
- 移动端 / 微信小程序

---

## 9. ❗ 待用户拍板的关键问题清单

| # | 问题 | 选项 | 我的推荐 |
|---|---|---|---|
| Q1 | q-news 数据源 | 多选: akshare / Bloomberg / 海关 / 自建爬虫 | akshare 起步, Bloomberg 看你单位有没有 |
| Q2 | q-news 推理引擎 | A 纯 LLM / B 规则知识库 / C 混合 | **C** (规则兜底 + LLM 兜底) |
| Q3 | 调度系统 | A cron / B systemd / C Airflow | **A** (cron, 简单可靠) |
| Q4 | 企业微信接入 | 方式 1 群机器人 / 方式 2 自建应用 | **方式 1** (需要你建机器人拿 webhook) |
| Q5 | Dashboard | A Streamlit / B 静态 HTML / C Grafana / D Markdown | **A Streamlit** |
| Q5b | Dashboard 部署 | 本地 / 云端 | 本地起步, 公网需要看 IBM 内网政策 |
| Q6 | LLM 预算 | 每天 $X 上限 | $1/天起步 |
| Q7 | tags 字典 | 用你给的 8 方向，是否扩展到行业/概念 | 8 方向起步, 后续按需加 |
| Q8 | priority 规则 | high/medium/low 怎么判定？ | 双 source 命中 = high, 单 source = medium |
| Q9 | 数据更新 | 数据手动 sync (rsync from Windows) 还是自动? | 现状手动, 自动需要 q-sync 改造 |
| Q10 | IBM 内网限制 | akshare/Bloomberg/Twitter API 在内网能跑通吗? | **需要你测试** |
| Q11 | 时间区间含义 | --start --end 是过滤"信号触发日"还是"扫描数据范围"? | 触发日 |
| Q12 | 跑 history backfill | 是否回溯 2025 全年看 q-news/q-fin 历史命中? | 推荐做, 验证有效性 |
| Q13 | 老脚本去留 | scan_business_pivot.py 等老脚本删 / 保留 / 重构? | 重构进 q-fin 子命令 |
| Q14 | 多 user/单 user | 系统是否考虑多用户隔离 (你+家人/同事)? | 单用户起步 |
| Q15 | 失败/异常处理 | 某个 q-xx 跑挂了，要发警告到企业微信吗? | 是，建议 |

---

## 10. 关于现状的补充

- 已有 `q-seed-hongjing/litong` (CLAUDE.md 已记录)
- 已有 `data/tdx_fin/` TDX 财报数据 (本次会话刚同步, 还没用上)
- IBM 工作环境: HTTP 代理可能阻塞海外 API
- 用户偏好: Chinese 为主语言、小步迭代、避免过度工程
- V0 (data + scan CLI) 早已 ship, 后续 V1 (推送) 在用户认为种子算法足够后**已隐式解锁**

---

## 等待你的反馈

请回答 Q1-Q15 (或挑你关心的几个)。回答后我开始 V0 实施。

如果某个问题答不出来，告诉我"你来定"，我就用我推荐的默认值。
