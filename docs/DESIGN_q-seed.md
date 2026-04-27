# q-seed 设计文档

**命令**: `q-seed` — 波浪形态相似度选股 (疯牛种子)
**版本**: v2 草案 / 待用户确认
**遵守协议**: L2 独立性 · JSON Lines 输出 · 结果尽量保全

---

## 1. 定位与职责

抽出 K 线数据, 过滤出与两个标杆股 (宏景科技 301396 / 利通电子 603629) 主升浪前形态最像的 A 股活跃候选. 形态判定基于波浪 (Wave) + 刺破启动点 (Pierce) + 三角收敛 + 尖头度等 60+ 维特征.

**单一职责**: 不做基本面, 不做新闻. 只输出"K 线形态像谁".

---

## 2. L2 独立目录结构

```
~/sentry/quant/q-seed/
├── q-seed                   # bash 入口 (链到 $PATH)
├── main.py                  # 主入口, 解析参数 → 调用 lib
├── config.yaml              # 模板定义 + KNN 参数 + 特征开关
├── requirements.txt         # pandas, numpy, pyyaml, baostock (可选)
├── .venv/                   # 独立虚拟环境, uv/venv 创建
├── lib/
│   ├── __init__.py
│   ├── tdx_loader.py        # 复制自 core/, 读 data/tdx/ 二进制
│   ├── data_loader.py       # 复制自 core/, load_daily()
│   ├── mytt.py              # 复制自 core/ 指标库
│   ├── stock_names.py       # 复制自 core/
│   ├── wave_model.py        # 复制自 scripts/train_wave_model.py 的 WaveModel/特征抽取
│   ├── similar_knn.py       # 复制自 scripts/find_similar_to_template.py 的 KNN 逻辑
│   └── kline_snapshot.py    # 新写: 生成 vwap/ret/amplitude 快照字段 (dashboard 用)
├── logs/                    # 输出目录 (本地)
└── README.md                # 使用说明
```

**L2 要点**:
- `lib/` 里的代码是 **复制** 不是 symlink, 后续 `scripts/` 改了 q-seed 不受影响.
- 独立 venv 避免 quant 根目录 venv 被污染.
- 不依赖任何外部模块 (除 config + .env).

---

## 3. 配置文件 `config.yaml`

```yaml
# q-seed/config.yaml

# 数据源
data:
  tdx_dir: "/home/wyatt/sentry/quant/data/tdx"       # 日线 TDX 目录 (绝对路径)
  parquet_fallback_dir: "/home/wyatt/sentry/quant/data/daily"
  stock_names_csv: "/home/wyatt/sentry/quant/data/stock_names.csv"

# 模板定义 (硬编码, 不允许用户随便改)
templates:
  hongjing:
    code: "301396"
    sig_dates: ["2024-09-30", "2025-12-09", "2026-01-12"]
    mode: "strict"            # HH/HL 严格递增, 每次 Pierce
  litong:
    code: "603629"
    sig_dates: ["2025-12-10", "2026-01-14", "2026-01-27", "2026-02-02"]
    mode: "loose"             # HH/Pierce 各允许 1 次例外

# 形态参数 (见 CLAUDE.md 疯牛种子 spec)
wave_params:
  launch_ret: 0.05             # 启动日涨幅阈值
  launch_vol_mul: 2.0          # 放量倍数
  vol_win: 20                  # 量均期
  pullback_win: 15             # 回踩窗口
  lookback: 60                 # 波浪回看天数
  min_wave: 2                  # 最少 launch 次数
  spike_win: 5                 # 尖头判定窗口
  spike_skip: 5                # 三角窗口起点偏移
  price_center: 80             # 入场价中点 (距 80 元越近 KNN 越像)
  recent_days: 60              # 候选触发最近 N 日

# 过滤
filter:
  skip_st: true                # 剔除 ST 股
  min_listing_days: 130        # 最低上市交易日
  exclude_suspended: true      # 当日停牌剔除
  board_whitelist: ["00", "60", "30", "68"]

# K 线安全性 4 档阈值 (kline_safety 字段判定)
kline_safety:
  vwap_window: 60              # 用 VWAP60 作为基准
  thresholds:
    red:    1.30               # 现价 >= VWAP60 × 1.30 → 🔴 追高
    yellow: 1.10               # 1.10~1.30 → 🟡 临界
    green:  0.90               # 0.90~1.10 → 🟢 健康
    # < 0.90 → ⚪ 被遗忘

# 输出
output:
  default_top: 30              # 不传 --top 时默认取 TOP 30
  jsonl_dir: "logs"            # 相对 q-seed/ 的输出目录
  md_companion: true           # 同时生成 .md 人类可读版
  retention_days: 30           # 自动删除 N 天前的输出

# 降级/边界
fallback:
  on_missing_data: "skip"      # skip / error
  on_template_corrupt: "error" # 模板股票无数据时直接报错
```

---

## 4. CLI 参数

```
q-seed [OPTIONS]

可选参数:
  --top N                   输出 TOP N (默认读 config.output.default_top=30)
  --template {hongjing|litong|both}
                            只跑某一个模板 (默认 both, 合并去重后按最小距离排序)
  --input <file|->          输入 JSON Lines (或 -) 限定扫描范围到这些 code
                            不给 → 全市场 (~11000 只)
  --since YYYY-MM-DD        只输出 sig_date >= 此日期的候选 (默认 recent_days 参数)
  --until YYYY-MM-DD        只输出 sig_date <= 此日期的候选
  --format {jsonl|md|both}  输出格式 (默认 jsonl, stdout)
  --output <file>           输出文件 (默认 stdout, 同时自动写 logs/ 备份)
  --config <path>           指定 config.yaml (默认 ./config.yaml)
  --no-fundamentals         关闭基本面追加字段 (默认关闭, q-fin 负责这个)
  -h, --help
```

**默认行为** (不带任何参数):
1. 读 `config.yaml`
2. 跑 hongjing + litong 两个模板, 合并去重
3. 按 `best_dist` 升序取 TOP 30
4. stdout 打印 JSON Lines, 同时写 `logs/q-seed_top30_<YYYYMMDD_HHMM>.jsonl` + `.md`

**管道示例**:
```bash
q-seed                                  # 独立跑, 输出 TOP 30
q-seed --template hongjing --top 60     # 只跑宏景, TOP 60
q-fin --top 100 | q-seed --top 30       # 上游 q-fin 的基本面 TOP 100 里筛形态
q-seed | q-fin | q-news                 # 三连击 (下游只读 code)
```

---

## 5. 输入规约

**3 种输入模式** (优先级从高到低):

1. `--input <file.jsonl>`: 读文件, 提取每行的 `code` 字段, 限定扫描范围.
2. stdin 有数据 (管道来): 读 stdin JSON Lines, 同上.
3. 默认: 全市场扫描 (从 tdx_dir 下所有 .day 文件推导 code 列表).

**stdin 判定**: `sys.stdin.isatty()` 为 False 视作管道输入.

**非法输入处理**:
- stdin 不是合法 JSON Lines → 报错退出 (`exit 2`)
- `code` 字段缺失 → 该行忽略, stderr warning
- 输入 code 在 TDX 找不到 → 该 code 忽略, stderr warning

---

## 6. 输出 JSON Lines Schema

**核心原则**: 每行自包含, dashboard 可直接渲染, 不需重调 akshare / baostock.

每行一个候选股:

```json
{
  "code": "605389",
  "name": "长龄液压",
  "scan_date": "2026-04-24",
  "source": "q-seed",
  "rank": 3,
  "score": 5.57,
  "score_type": "knn_distance_asc",

  "templates_matched": ["hongjing"],
  "best_template": "hongjing",
  "best_dist": 5.57,

  "details": {
    "hongjing": {
      "rank": 3,
      "dist": 5.57,
      "sig_date": "2026-04-22",
      "entry": 84.79,
      "n_waves": 3,
      "spike_ratio": 1.0,
      "triangle_strict": 0,
      "amp_shrinkage": -1.43,
      "pierce_mean": 0.05,
      "pierce_max": 0.11,
      "amp_mean": 0.18,
      "price_cum_ret": 0.42,
      "is_20cm": 0,
      "is_st": false
    },
    "litong": null
  },

  "kline": {
    "current_price": 84.79,
    "current_date": "2026-04-22",
    "vwap20": 74.85,   "vwap20_dev": 0.133,
    "vwap60": 75.17,   "vwap60_dev": 0.128,
    "vwap120": 71.72,  "vwap120_dev": 0.182,
    "high60": 85.55,   "low60": 66.10,
    "high120": 85.55,  "low120": 59.80,
    "ret5": 0.073, "ret20": 0.141, "ret60": 0.071, "ret120": 0.307,
    "amplitude_5d": 0.207,
    "volume_ratio_5d_20d": 1.85,
    "kline_safety": "🟢"
  },

  "meta": {
    "scanner_version": "q-seed v2.0.0",
    "config_hash": "sha256:…前 12 位",
    "scan_duration_ms": 812433
  }
}
```

**字段保全原则**:
- `details.<template>` 里保留所有形态子分量 (n_waves / pierce / amp / triangle 等), 不只给 dist — dashboard 可用这些做进一步筛选.
- `kline` 字段预计算好 vwap/return/dev, 省掉下游重算.
- `templates_matched` 数组 → 双命中时可直接识别 (两个模板都在里面的票 = 强信号).
- `meta` 带 scanner 版本和 config hash, 利于复现.

**kline_safety 4 档**:
- 🔴 现价 >= VWAP60 × 1.3 (追高区)
- 🟡 VWAP60 × 1.1 ~ 1.3 (临界)
- 🟢 VWAP60 × 0.9 ~ 1.1 (健康)
- ⚪ 现价 < VWAP60 × 0.9 (被遗忘, 看业绩再说)

---

## 7. Markdown 伴生格式 (--format md)

调试/人肉 review 用. 一张表:

```
# q-seed TOP 30 · 2026-04-24

| # | code | name | 模板 | dist | sig_date | entry | safety | ret60 | 备注 |
|---|------|------|------|------|----------|-------|--------|-------|------|
| 1 | 002869 | 金溢科技 | hongjing | 5.13 | ... | ... | 🟢 | +8% | n_waves=3 |
...
```

---

## 8. 失败模式

| 场景 | 行为 |
|---|---|
| TDX 目录不存在 | `exit 1`, stderr "data.tdx_dir not found" |
| 模板股票无数据 | `exit 2`, config.fallback.on_template_corrupt 决定 |
| stdin 非法 JSON | `exit 2`, stderr 具体哪行哪列 |
| KNN 算出来全是 inf | 该模板输出空, stderr warning, 不影响另一模板 |
| 全市场扫描无候选 | stdout 空, exit 0 (正常), stderr "no candidates in recent_days=60" |
| Ctrl-C | 捕获, 部分结果写 logs/, stderr "interrupted, partial saved" |

---

## 9. 复用现有代码清单

| 目标 `q-seed/lib/` 文件 | 来源 | 改动 |
|---|---|---|
| `tdx_loader.py` | `core/tdx_loader.py` | 直接复制 |
| `data_loader.py` | `core/data_loader.py` | 改绝对路径读 config |
| `mytt.py` | `core/mytt.py` | 直接复制 |
| `stock_names.py` | `core/stock_names.py` | 直接复制 |
| `wave_model.py` | `scripts/train_wave_model.py` 的 class/函数 | 抽出 WaveModel + 特征抽取 函数 |
| `similar_knn.py` | `scripts/find_similar_to_template.py` 的主逻辑 | 去掉 argparse, 改函数签名 |
| `kline_snapshot.py` | 新写 | - |

**注**: `~/sentry/quant/q-seed/lib/core/` (0424 session 已建的) 可以保留, 只是要补 `wave_model.py` / `similar_knn.py` / `kline_snapshot.py` + 写 `main.py` + `config.yaml` + bash 入口.

---

## 10. Smoke Test 验收

1. `q-seed --help` 显示参数说明.
2. `q-seed --top 5` 输出 5 行 JSON Lines, 每行含完整 schema, 可 `jq '.code'` 提取.
3. `q-seed --template hongjing --top 5 --format md` 生成 markdown 表.
4. `echo '{"code":"301396"}' | q-seed --top 1` 能处理 stdin 输入.
5. `q-seed > /tmp/seed.jsonl && cat /tmp/seed.jsonl | q-seed --top 10` 管道自回路成立.
6. 耗时: 全市场 ≤ 15 分钟 (~11k 只 × 两模板); `--input` 300 只 ≤ 30 秒.
7. `logs/q-seed_top30_YYYYMMDD_HHMM.jsonl` 自动落盘.

---

## 11. Token 使用说明

**q-seed 永远 free** — 不调 LLM, 不联网, 无 `--paid` 参数. 所有逻辑 (KNN / 形态识别 / K 线快照) 纯本地计算, 依赖 TDX 日线 + 模板定义.

三命令统一约定:
| 命令 | 默认 | `--paid` | `--paid=deep` |
|---|---|---|---|
| `q-seed` | free | (无此参数) | (无此参数) |
| `q-fin` | free | standard (LLM 递归 2 层) | deep (+web search + 递归 3 层) |
| `q-news` | free (规则) | standard (+Haiku 兜底) | deep (+Sonnet + web search 验证) |

## 12. 已确认决策 (不再开放)

- ✅ 全市场扫描 27 min 可接受, 24h cache 必做
- ✅ `both` 模板双命中用 `min(dist)` 排序, `templates_matched` 数组自然体现
- ✅ `kline_safety` 阈值先硬编码
- ✅ `meta.config_hash` 以后要复现再加
- ✅ 0424 已建的 `q-seed/lib/core/` 保留 (只用其中 tdx/data/mytt/stock_names 4 个文件)
