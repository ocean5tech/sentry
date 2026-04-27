# q-news 新闻源验证报告

**测试日期**: 2026-04-26 IBM 内网环境
**测试方法**: 实际 HTTP 请求 (10s timeout, 桌面 UA), akshare Python 调用

---

## ✅ 推荐 V0 用 (4 层)

### 第 1 层: 实时快讯 (核心, akshare 全免费)

| 接口 | 行数/拉 | 特点 |
|---|---|---|
| `stock_info_global_cls` | 20 | 财联社快讯, A股本土最快, 含技术面/产业链分析 |
| `stock_info_global_em` | 200 | 东财快讯, 信息密度大, 含 AI/光模块/新材料等深度 |
| `stock_info_cjzc_em` | 400 | 东财财经资讯, 历史回看充分 (财经早餐/政策汇总) |

3 接口合并去重后 ≈ 400-500 条/拉. 24h 增量 ~50-150 条新闻.

### 第 2 层: 政策权威 (新华网真 RSS, **5 个栏目都通**)

| 栏目 | URL |
|---|---|
| 时政 | `http://www.news.cn/politics/news_politics.xml` |
| 经济 | `http://www.news.cn/fortune/news_fortune.xml` |
| 科技 | `http://www.news.cn/tech/news_tech.xml` |
| 国际 | `http://www.news.cn/world/news_world.xml` |
| 军事 | `http://www.news.cn/mil/news_mil.xml` |

XML feed 标准 RSS 2.0, 无需 cookie. 用作宏观/政策权威源.

### 第 3 层: 个股反向查 (按需)

`akshare.stock_news_em(symbol="605389")` — 输入 code 列表时反查个股新闻.

`q-news --input <(q-seed --top 30)` 串联用法.

### 第 4 层: 宏观数据 (月度, 知识库基础)

替代海关 (海关网站 412 拦). akshare 提供:
- `macro_china_exports_yoy` 出口同比
- `macro_china_imports_yoy` 进口同比
- `macro_china_trade_balance` 贸易差额
- `macro_china_cpi` / `macro_china_pmi` / `macro_china_gdp` 等

不是"新闻"是"数据点", 月度更新, 用于喂给 LLM 推理时提供宏观背景.

---

## ❌ 不推荐 (内网拦/已确认无解)

| 源 | 状态 | 备注 |
|---|---|---|
| 海关总署 customs.gov.cn | HTTP 412 反爬 | 多 UA 尝试失败. 海关一手数据 V0 不做, 用 akshare macro 替代 |
| RSSHub 全系 (rsshub.app) | HTTP 403 | 内网拦 (大概率 GFW + IBM proxy). 第三方聚合方案凉了 |
| Reuters 中文 RSS | HTTP 401 | 需登录/付费 |
| Bloomberg China | HTTP 403 | 内网拦 |
| 同花顺 RSS / 央视新闻 RSS | HTTP 404 | 接口下线 |
| 雪球热门 | HTTP 404 | 改版 |
| 搜狐财经 | 503 (代理拦) | |

---

## 🟡 通但暂不用 (HTML 爬虫成本高)

这些站通, 但**没有官方 RSS, 要爬 HTML**, V0 不做 (爬虫本身复杂):

- 央行 pbc.gov.cn / 证监会 csrc.gov.cn / 工信部 miit / 发改委 ndrc / 商务部 mofcom
- 央视新闻 / 央广网 / 网易财经 / 第一财经 / 21经济网 / 国家统计局
- 巨潮资讯 (q-fin 已通过 akshare 接入, 不重复)

V1 如果发现 akshare cls/em 漏了重要政策, 再加 HTML 爬虫.

---

## 内网/网络细节

- 多数源响应 1-9s, 基本能用
- 财新 caixin.com 通但 RSS 路径变了 (/rss/economy.xml 返回 HTML)
- BBC 中文 RSS 通 (国际视角备选)
- 央广 cnr.cn /rss/cj.xml 返回 HTML 不是 XML — 路径过期

---

## V0 实施清单

```yaml
# q-news/config/news_sources.yaml (草案)
sources:
  akshare:
    enabled: true
    interfaces:
      - stock_info_global_cls         # 财联社
      - stock_info_global_em          # 东财
      - stock_info_cjzc_em            # 东财财经资讯
      - news_cctv                     # 新闻联播 (日级)
      - news_economic_baidu           # 经济日历
    individual_news_iface: stock_news_em  # 反向查个股新闻

  rss:
    enabled: true
    timeout_seconds: 10
    feeds:
      - {name: "新华网-时政", url: "http://www.news.cn/politics/news_politics.xml", tags: [政策, 政治]}
      - {name: "新华网-经济", url: "http://www.news.cn/fortune/news_fortune.xml", tags: [财经, 经济]}
      - {name: "新华网-科技", url: "http://www.news.cn/tech/news_tech.xml", tags: [科技, 创新]}
      - {name: "新华网-国际", url: "http://www.news.cn/world/news_world.xml", tags: [国际, 地缘]}
      - {name: "新华网-军事", url: "http://www.news.cn/mil/news_mil.xml", tags: [军工, 国防]}
    # 失败处理: timeout / HTTP 错误 → 标 blocked, 继续跑其他源

  macro:                              # 月度数据点 (非新闻)
    enabled: true
    interfaces:
      - macro_china_exports_yoy       # 替代海关一手
      - macro_china_imports_yoy
      - macro_china_trade_balance
      - macro_china_cpi_monthly
      - macro_china_pmi_monthly
    refresh: monthly                  # 月度自动拉, cache 长 ttl
```
