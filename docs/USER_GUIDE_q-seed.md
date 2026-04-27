# q-seed 用户手册

**v1.0.0** · 形态相似度选股 (疯牛种子: hongjing 短期波浪 + litong 长期 W 底)

永远 free, 不调 LLM, 不联网. 100% 本地计算 (TDX 日线 + 模板定义).

---

## 1. 部署

### 一次性安装

```bash
cd ~/sentry/quant/q-seed

# 创建独立 venv (L2 独立, 不依赖父项目)
python3 -m venv .venv

# 装依赖 (国内推荐清华镜像)
.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
# 或: .venv/bin/pip install -r requirements.txt

# 入口脚本可执行
chmod +x ./q-seed

# (可选) 加到 PATH 方便随处调用
ln -s ~/sentry/quant/q-seed/q-seed ~/.local/bin/q-seed
```

### 数据依赖

q-seed 读取 (硬要求):
- `~/sentry/quant/data/tdx/{sh,sz,bj}/lday/*.day` — TDX 日线二进制
- `~/sentry/quant/data/stock_names.csv` — 代码 → 名称 cache (首次跑会自动生成)

确保你已跑过 `q-sync` 同步 TDX 数据.

依赖包: `pandas / numpy / pyyaml` (3 个, requirements.txt 里).

---

## 2. 启动方法

```bash
# 默认: 全市场 hongjing+litong 双扫, TOP 30, balance_per_template
q-seed

# 限定模板
q-seed --template hongjing
q-seed --template litong
q-seed --template both         # 默认

# 限定输入候选池 (跳过全市场扫描, 只扫指定 code)
q-seed --input candidates.jsonl
echo '{"code":"605389"}' | q-seed       # stdin pipe
q-seed --input - < candidates.jsonl     # stdin 显式

# 调输出数量
q-seed --top 50

# 时间窗口过滤 (Sig 信号日期)
q-seed --since 2026-04-01
q-seed --until 2026-04-15

# 输出格式
q-seed --format jsonl       # 默认
q-seed --format md
q-seed --format both        # JSON + 配套 .md

# 自定义输出文件名
q-seed --output result.jsonl

# 自定义 config
q-seed --config my-config.yaml
```

完整参数: `q-seed --help`.

---

## 3. 输入

### 默认 (无 --input): 全市场扫描

`data/tdx/` 下所有 .day 文件 (~11000 只 A股) 扫一遍. 耗时 ~5 分钟.

### --input 文件 / stdin: 限定 code 集合

每行一个 JSON 对象, 必须含 `code` 字段:

```jsonl
{"code":"605389","name":"长龄液压"}
{"code":"301396"}
{"code":"603629","extra":"任何额外字段会被忽略"}
```

模板自身 code (301396 hongjing, 603629 litong) 会自动加入扫描范围用于 KNN 基准计算, 但**不会**出现在最终输出 (除非你也在输入里给了它们).

### 管道用法

```bash
# 自己 grep 一些 code 喂进来
grep -h '"code"' my_watchlist.jsonl | q-seed --top 10

# 从其他 q-* 输出
q-fin --input list.jsonl | q-seed --top 5    # 反向: 给基本面好的票测形态
```

---

## 4. 输出 JSON Lines Schema

每行一个候选股:

```json
{
  "code": "605389",
  "name": "长龄液压",
  "scan_date": "2026-04-25",
  "source": "q-seed",
  "rank": 18,
  "score": 5.51,
  "score_type": "knn_distance_asc",
  "templates_matched": ["hongjing"],
  "best_template": "hongjing",
  "best_dist": 5.51,

  "details": {
    "hongjing": {
      "rank": 3,
      "dist": 5.51,
      "sig_date": "2026-04-22",
      "entry": 79.30,
      "n_waves": 2,
      "spike_ratio": 0.5,
      "amp_shrinkage": 0.32,
      "pierce_mean": 0.041,
      "is_20cm": false
    }
  },

  "kline": {
    "current_price": 84.79,
    "current_date": "2026-04-25",
    "vwap20": 74.85, "vwap20_dev": 0.133,
    "vwap60": 75.17, "vwap60_dev": 0.128,
    "vwap120": 70.10, "vwap120_dev": 0.21,
    "high60": 85.55, "low60": 66.10,
    "ret60": 0.071, "ret120": 0.307,
    "amplitude60": 0.295,
    "kline_safety": "🟡"
  },

  "meta": {
    "scanner_version": "q-seed v1.0.0",
    "scan_duration_ms": 4523
  }
}
```

**字段说明**:
- `score`: KNN 欧氏距离 (越小越像模板, 不是百分制评分)
- `templates_matched`: 命中的模板数组. 双命中 (`["hongjing","litong"]`) = 强信号
- `best_template` / `best_dist`: 多模板里距离最小的
- `details.<template>`: 每模板的具体特征 (amp_shrinkage 三角收缩 / pierce_mean 击穿率 / is_20cm 20% 涨停)
- `kline.kline_safety` 4 档:
  - 🔴 追高区 (现价 > VWAP60 × 1.30)
  - 🟡 临界 (1.10~1.30)
  - 🟢 健康区 (0.90~1.10)
  - ⚪ 被遗忘 (< 0.90)

### 自动备份

每次跑都写到 `~/sentry/quant/q-seed/logs/q-seed_top<N>_YYYYMMDD_HHMM.jsonl` + 同名 .md (人读). 保留 30 天.

---

## 5. 配置文件 `config.yaml`

位置: `~/sentry/quant/q-seed/config.yaml`. 命令行 `--config` 可覆盖.

```yaml
# 数据路径 (绝对路径)
data:
  tdx_dir: "/home/wyatt/sentry/quant/data/tdx"
  parquet_fallback_dir: "/home/wyatt/sentry/quant/data/daily"
  stock_names_csv: "/home/wyatt/sentry/quant/data/stock_names.csv"

# 模板定义 (如果你想加新的标杆股, 在这加)
templates:
  hongjing:
    code: "301396"
    name: "宏景科技"
    sig_dates: ["2024-09-30", "2025-12-09", "2026-01-12"]
    mode: "strict"           # strict 严格 HH/Pierce
  litong:
    code: "603629"
    name: "利通电子"
    sig_dates: ["2025-12-10", "2026-01-14", "2026-01-27", "2026-02-02"]
    mode: "loose"            # loose 允许 1 次 HH/Pierce 例外

# 波浪算法参数 (硬编码 → config)
wave_params:
  launch_ret: 0.05               # 启动涨幅阈值
  launch_vol_mul: 2.0            # 放量倍数 (vs 前 20 日均量)
  vol_window: 20
  pullback_window: 15
  lookback: 60                   # 波浪回看天数
  min_wave: 2                    # 最少 launch 次数
  spike_window: 5                # 尖头判定窗口
  spike_skip: 5                  # 三角窗口起点偏移
  price_center: 80               # 入场价中点 (60-100 区间)
  recent_days: 60                # 候选最近 N 日

# 过滤规则
filter:
  skip_st: true
  min_listing_days: 60
  exclude_suspended: true
  board_whitelist: ["00", "60", "30", "68"]   # 主板/创业板/科创板, 不要北交所(82)

# K 线安全 4 档阈值
kline_safety:
  vwap_window: 60                # 用 VWAP60 作基准
  thresholds:
    red: 1.30                    # >= 1.30 → 🔴
    yellow: 1.10                 # 1.10~1.30 → 🟡
    green: 0.90                  # 0.90~1.10 → 🟢
    # < 0.90 → ⚪

# 输出
output:
  default_top: 30                # 不传 --top 时默认值
  jsonl_dir: "logs"
  md_companion: true
  retention_days: 30
  balance_per_template: true     # both 模板时强制对半 (15+15)
  min_per_template: 10           # 某一边至少 N 个

# 降级
fallback:
  on_missing_data: "skip_silent" # skip 该 code, 不报错
  on_template_corrupt: "die"     # 模板自身数据有问题直接退出
```

### 调参常见场景

- **想看更多 hongjing 候选** (litong 太宽松占位多): 改 `output.balance_per_template: true` (默认就是), 调 `min_per_template`
- **kline_safety 阈值不合口味** (你觉得 1.10 不该算 🟡): 调 `kline_safety.thresholds.yellow`
- **想加第三个标杆股** (例 寒武纪 688256 主升浪前): 在 templates 下加新条目, 给 mode=strict 或 loose
- **想扫科创板 + 创业板就行**: `filter.board_whitelist: ["30", "68"]`
- **波浪算法太严**: `wave_params.launch_ret: 0.04` (放宽到 4% 启动)

---

## 6. 典型工作流

```bash
# 早盘看每日 TOP 30 候选
q-seed > /tmp/today.jsonl

# 只看你 watchlist 里的票
cat watchlist.jsonl | q-seed --top 5

# 看强信号 (双命中)
q-seed | jq 'select(.templates_matched | length > 1)'

# 与 q-fin 联用: 形态像 → 基本面有故事
q-seed --top 50 | q-fin --paid --top 10
```

---

## 7. 常见问题

| 问题 | 排查 |
|---|---|
| `data.tdx_dir not found` | 检查 `~/sentry/quant/data/tdx/` 是否存在; 跑 `q-sync` |
| 输出空 | 跑 `q-seed --template hongjing --top 5` 看是不是 both 合并问题 |
| 跑得慢 (>10min) | 全市场首次正常 ~5min; >10min 检查磁盘/CPU; 用 `--input` 缩范围 |
| 长龄液压不在 TOP 30 | 是 hongjing 模式下 #18, 默认 both 时 hongjing/litong 各 15 个会进 |
| 看 SLOW 测试 | `RUN_SLOW=1 bash tests/run_tests.sh` |

---

## 8. 实施细节 (开发者)

- DESIGN: `~/sentry/quant/docs/DESIGN_q-seed.md`
- 测试: `~/sentry/quant/q-seed/tests/{TEST_PLAN_q-seed.md, run_tests.sh}` (57/57 PASS)
- 核心算法: `lib/wave_model.py` (波浪检测+特征) + `lib/similar_knn.py` (z-score+欧氏距离)
- 复用自父项目: `lib/core/{tdx_loader,data_loader,mytt,stock_names}.py` (lazy import)
- 实施踩坑教训 (q-fin/q-news 复用): `~/.claude/projects/-home-wyatt-sentry/memory/feedback_qseed_lessons.md`
