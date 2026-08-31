# AI Native App Monitor

全球 AI Native App + Foundation Model 监控 Dashboard。追踪 ARR、估值、Token 消耗、自有模型进度。
每周日 UTC 13:00（北京时间 21:00）自动更新，页面下方另有「AI 进展周报」。

**站点**：GitHub Pages（Settings → Pages → main / root）

---

## 数据怎么保持新鲜

三个 pass 各管一件事。关键在于**第二个** —— 没有它，任何落在 7 天窗口外的过期数字都永远修不回来。

| 脚本 | 频率 | 干什么 |
|---|---|---|
| `update_data.py` | 每周 | **增量**：只看过去 7 天的新闻，抓已确认的变化 |
| `reverify.py` | 每周 | **轮转全量复核**：取溯源最旧的 10 家，**不带时间窗**重新核 ARR/估值/自有模型/旗舰产品。一家一次 API 调用。约 6-7 周走完全池 |
| `discover.py` | 每月第一周 | **覆盖发现**：找不在池里但已达标的公司，按 `CRITERIA.md` 硬门槛自动入池，灰区进 `candidates` 候选池等人裁决 |
| `update_progress.py` | 每周 | 生成「AI 进展周报」（企业端 / 模型范式 / Infra 投资） |
| `validate.py` | 每次 | 离线校验；`--selftest` 用固定反例证明校验层拦得住坏数据 |
| `common.py` | — | 共享数据层：schema 迁移、名单派生、**校验闸门**、changelog、momentum |

所有写入都必须过 `common.apply_patches()`，绕不开。它会拦下：

- **量级异常**：`arr`/`val`/`mau`/`tokM` 变动超过 5 倍
- **币种未换算**：来源用人民币/欧元计价却没写换算（`val` 曾把 RMB 当 $B 记）
- 缺来源、缺 `as_of`、置信度不是 high/medium、`as_of` 比在档值更旧
- 越界值、枚举非法、`ownModel` 自相矛盾
- **名称对不上或有歧义** —— 不再静默丢弃，一律进 `meta.review_queue`

被拦的进 `meta.review_queue`（页面顶部会提示条数），生效的进 `meta.changelog`。
`meta.last_updated` **只在真的落了改动时才前进**，所以「数据截至」不会再撒谎。

---

## 一次性回填

首次上线 v2 后跑这两步，让看板当天就是对的：

1. **Actions → Weekly Data Update → Run workflow**，mode 选 `reverify-all`，先勾 `dry_run` 看一遍拟改动
   （这一步修 Kimi / MiniMax / Harvey / Zhipu 的 RMB 串单位，以及 Cursor 的自有模型状态）。
   全量回填允许**越过量级闸门**（这正是它的目的），越过的改动在 changelog 里标 `forced`，其余闸门照旧生效。
2. mode 选 `discover-seed`，补齐 finance / gtm / legal / health / support / coding 等空白垂直。

本地跑（需要 `pip install anthropic` 和 `ANTHROPIC_API_KEY`）：

```bash
python validate.py --selftest        # 不需要 key
python reverify.py --all --dry-run   # 看拟改动，不写盘
python reverify.py --only "Kimi"     # 只核一家
python discover.py --seed --dry-run
python validate.py                   # 提交前必过
```

---

## 文件

| 文件 | 说明 |
|------|------|
| `index.html` | 前端。单文件、无构建，读 `data.json` |
| `data.json` | 全部数据（schema v2） |
| `CRITERIA.md` | **纳入标准 + 赛道分类 + 字段口径**（改门槛先改这里） |
| `common.py` | 共享数据层与校验闸门 |
| `update_data.py` / `reverify.py` / `discover.py` / `update_progress.py` | 四个 pass |
| `validate.py` | 离线校验 + 校验层自检 |
| `.github/workflows/weekly-update.yml` | 定时任务与手动入口 |

### `data.json` 结构
```
meta      schema / last_updated / last_run / stale_days / categories / regions
          runs（每个 pass 的成败与错误）/ changelog / review_queue
models[]  name uc region arr arrg tokM tokG trainPerRun runsPerYear val m prov retired
apps[]    name uc cat stage arr arrg mau maug ti biz val m ownModel prov retired
candidates[]  发现但未自动入池的灰区标的
series    趋势图的季度序列（以前写死在 index.html 里，脚本改不动）
ai_progress   AI 进展周报
```

前端从 `meta.categories` / `meta.regions` 生成筛选按钮、标签和配色，**只渲染有成员的赛道**——
新增一个垂直只改 `common.py` 的 `CATEGORIES`，不用动 HTML。

---

## 配置

- Secret `ANTHROPIC_API_KEY`（必需）
- Variable `CLAUDE_MODEL`（可选，默认 `claude-opus-5`）

改更新频率：改 workflow 里的 cron（当前 `0 13 * * 0`）。
改复核节奏：改 `reverify.py` 的 `DEFAULT_K`（每周核几家）。
改入池门槛：改 `discover.py` 顶部常量，并同步 `CRITERIA.md`。

---

## 数据说明

口径与来源见 `CRITERIA.md`。所有数据为研究性估算，**不构成投资建议**。
每格数值旁的圆点即其来源与报道日期；超过 `stale_days`（默认 120 天）未复核会变灰并标「待复核」。
