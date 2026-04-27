# 使用说明 · A股量化选股预警系统 V0

---

## 当前功能

| 功能 | 说明 |
|------|------|
| 全量拉取 | 一次性下载全市场约 5300 只股票近 3 年前复权日线 |
| 增量更新 | 每日补全最新数据，只拉尚未入库的部分 |
| 策略扫描 | 两个策略扫描全市场，命中的股票打印并写入日志 |

### 策略说明

**策略一：30日内首次涨停**
- 今日涨停（收盘价 ≥ 昨收 × 1.097，且收盘 = 最高）
- 过去 29 个交易日内没有出现过涨停

**策略二：涨停后回调 5%+**
- 过去 20 个交易日内至少出现过一次涨停
- 当前价相对该 20 日最高价回撤 ≥ 5%

---

## 目录结构

```
~/sentry/quant/
├── scripts/
│   ├── pull-full.sh     ← 首次全量拉取
│   ├── pull-update.sh   ← 每日增量更新
│   └── scan.sh          ← 盘后扫描选股
├── core/
│   ├── data_loader.py   # 数据层：akshare → parquet
│   ├── scanner.py       # 策略层：读 parquet → 命中列表
│   └── mytt.py          # 通达信指标函数库
├── data/daily/          # 每只股票一个 .parquet（拉取后生成）
└── logs/                # pull_full.log + v0_YYYYMMDD.csv
```

---

## 命令速查

```bash
# 首次全量拉取（后台运行，约 60-90 分钟）
bash ~/sentry/quant/scripts/pull-full.sh

# 查看拉取进度
bash ~/sentry/quant/scripts/pull-full.sh --status

# 每日增量更新（盘后 15:30+ 运行，约 10-20 分钟）
bash ~/sentry/quant/scripts/pull-update.sh

# 盘后选股扫描
bash ~/sentry/quant/scripts/scan.sh
```

---

## 第一次使用流程

### 第 1 步：启动全量数据拉取

```bash
bash ~/sentry/quant/scripts/pull-full.sh
```

输出示例：
```
启动全量数据拉取（后台运行）...
日志：/home/wyatt/sentry/quant/logs/pull_full.log
查看进度：bash ~/sentry/quant/scripts/pull-full.sh --status
已启动，PID=12345
完成后日志末尾会出现：Done. ok=XXXX failed=XX
```

去做其他事，60-90 分钟后回来检查进度：

```bash
bash ~/sentry/quant/scripts/pull-full.sh --status
```

出现 `Done. ok=5XXX failed=XX` 表示完成。

---

### 第 2 步：盘后运行扫描（每天 15:30+ 重复）

```bash
bash ~/sentry/quant/scripts/pull-update.sh && bash ~/sentry/quant/scripts/scan.sh
```

扫描输出示例：
```
============================================================
A股量化选股 V0  |  20260417
============================================================
扫描中，请稍候...

命中 3 条信号：

策略               代码       名称           现价
--------------------------------------------------
30日首次涨停       002XXX     某某科技        18.76
涨停后回调5%+      300XXX     某某新材        12.34
涨停后回调5%+      688XXX     某某半导体       45.20

结果已写入: logs/v0_20260417.csv
```

---

### 第 3 步：人工验证信号（跑 1-2 周）

每次扫描后 `logs/v0_YYYYMMDD.csv` 新增一批记录，手动填写次日和三日涨跌：

| 日期 | 策略 | 代码 | 名称 | 触发价 | 次日涨跌% | 三日涨跌% | 备注 |
|------|------|------|------|--------|-----------|-----------|------|
| 20260417 | 30日首次涨停 | 002XXX | 某某科技 | 18.76 | **+3.2** | **+5.1** | |

**胜率满意（自定义标准）→ 通知开发者进入 V1（微信推送）。**

---

## 常见问题

**拉取中途断了怎么办？**
重新运行 `pull-full.sh`，已下载的股票会跳过，从断点继续。

**某只股票报错？**
停牌/退市股票偶发报错，扫描时自动跳过，不影响整体。

**WSL 时间不对？**
```bash
sudo hwclock -s
```
