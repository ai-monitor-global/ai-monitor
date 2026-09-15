# 纳入标准与字段口径

这份文档是 `discover.py` 的判定依据，也是「为什么这家在、那家不在」的唯一成文答案。
改门槛请同时改 `discover.py` 顶部的常量（`HARD_ARR` / `HARD_VAL` / `GREY_ARR` / `GREY_VAL`）。

---

## 1. 准入门槛（混合门槛制）

### 硬门槛 —— 自动入池
三条同时满足：

1. **AI-native**：产品的核心价值来自大模型或生成式模型。传统软件加一个 AI 功能**不算**。
2. **独立公司**：不是大厂的一个功能或事业部。
3. **有体量**：年化收入 ≥ **$30M**（`arr`），**或**过去 12 个月有**已完成**的融资/二级交易估值 ≥ **$2B**（`val`）。

外加一条工程约束：`arr` 必须有可信来源。前端要把 `arr` 加总，它不能为空 ——
只有估值达标但收入完全查不到的公司，进候选队列而不是主表。

### 例外：个人助理（`assistant`）类别
C 端个人助理形态的 agent（Instinct、Meta Muse、OpenClaw、Manus）在 2026-09 整体处于
**融资跑在商业化前面**的阶段：Instinct 以 $2.5B 估值完成 B 轮时仍是邀请制、无定价、无收入。
按 ARR 口径这一类永远进不了主表，所以单独设口径（`discover.gate` 与 `validate.py` 同步实现）：

- **`arr` 可空**：没公布过价格就没有收入可查，`null` 是正确值，**禁止填 0 或估算**
  （0 会污染总 ARR 和平均增速）。前端合计会注明"其中 N 家有收入数据"。
- **自动入池**：已交割轮次估值 ≥ **$2B** 即达标，不再要求 `arr` 有来源；
  估值 $0.5B–$2B 进 `candidates`。
- **头部补位**：赛道头部但无独立估值的，由操作侧按"该赛道头部"灰区规则手工入池，不走自动发现：
  大厂内嵌产品（Meta Muse）标 `parent`，估值永远留空；开源自托管项目（OpenClaw）标 `access: oss`。
- **不重复计数**：ChatGPT / Gemini / Claude 的助理形态**不单列**——它们的算力已计入
  Foundation Models 层，再进 App 层会双重计算。
- **主指标是活跃用户，不是 ARR**：这一类真正有信息量的是三个转折点——邀请制→公开
  （`access`）、首次公布定价（`uc`，并重估 `stage`）、基座模型披露（`ownModel`）。
  在用户数被披露之前，`access` 比 `mau` 信息量更大。

### 灰区 —— 进 `candidates` 队列等人裁决
任一条成立即入队：

- `arr` 在 **$10M–$30M** 之间；或 `val` 在 **$0.5B–$2B** 之间
- AI-native 或独立性判断不确定
- 该赛道目前覆盖为 0，而它是这个赛道的头部

队列里的条目带 `verdict: "pending"`，看板「候选池」tab 直接可见。批了就手工搬进 `apps[]`，
否决就把 `verdict` 改成 `rejected`。

### 出池与收购（全自动政策）
- **被收购** → **不下架**：标 `parent=收购方`（系统自动清 val/valPending/listed，
  子公司无独立估值），继续追踪 ARR / 自有模型。先例：Cursor/SpaceX（2026-08-14
  交割，2026-09-02 定策）。
- **停运 / 不再独立经营**（≥2 个独立来源确认）→ 标 `retired: true`。不删除数据，
  保留历史护 track record。
- 连续两次全量复核 `arr` 跌幅 > 50% → 同上，retire。
- 证据不足时进 `meta.review_queue`，由下周运行按仲裁协议（见 ROUTINE_PROMPT.md）
  自动重核；28 天未决自动过期。

### 组成守恒
- 任一 `cat` 占活跃 app 总数 ≤ **25%**（`validate.py` 超限会告警）
- 每个已定义的垂直至少覆盖 1 家头部
- **宁缺毋滥**：不为了凑满某个赛道硬塞不达标的公司

---

## 2. 赛道分类（`cat`）

| cat | 含义 |
|---|---|
| `coding` | 编程 / 软件工程 Agent |
| `search` | 搜索与问答 |
| `creative` | 图像 / 视频 / 音乐 / 剪辑生成 |
| `voice` | 语音合成与语音 Agent |
| `consumer` | 泛消费级应用 |
| `assistant` | C 端个人助理形态的 agent：替用户执行任务（订票、购物、发邮件），可经消息/电话调用。主指标为用户数与供给状态，口径见 §1 例外 |
| `enterprise` | 横向企业生产力（知识库、文档、会议） |
| `finance` | 金融 / 投研 / 会计 |
| `legal` | 法律 |
| `health` | 医疗 |
| `gtm` | Marketing / Sales / GTM |
| `support` | 客服与客户成功 |
| `hr` | 招聘与人力 |
| `security` | 安全 |
| `other` | 尚无专属桶的垂直 |

分类表存在 `data.json` 的 `meta.categories`（由 `common.py` 的 `CATEGORIES` 写入），
前端从数据读取标签和配色，**只渲染有成员的赛道**。所以新增一个垂直只需改 `common.py`
一处，不用动 `index.html`。

模型层只按 `region`（US / CN / EU）分组，存在 `meta.regions`。

---

## 3. 字段口径

单位是绝对的，任何非美元来源必须换算并在 `source` 里写明汇率，否则会被币种闸门拦进
`meta.review_queue`。

| 字段 | 含义 | 单位 | 可空 |
|---|---|---|---|
| `name` | 展示名，也是 patch 的匹配键 | — | 否 |
| `uc` | 当前旗舰产品 / 一句话业务，中文，≤ ~30 字 | — | 否 |
| `arr` | Apps: ARR；Models: 年化 API 收入 | USD **$M** | **否**（前端要加总）；`assistant` 类别例外，可空 |
| `arrg` | 收入同比增速 | % | 是 |
| `val` | 最新**已完成**轮次/二级/市值估值。传闻或未 close 的轮次不算 | USD **$B** | 是 |
| `mau` | 月活 | 百万 | 是 |
| `maug` | 月活增速 | % | 是 |
| `tokM` | 月度推理 token 量（仅 models） | 万亿 T/月 | **否**（前端要加总） |
| `tokG` | token 量增速 | % | 是 |
| `trainPerRun` / `runsPerYear` | 单次训练量 / 年训练次数（仅 models） | T / 次 | 是 |
| `ti` | Token 密度 `low\|med\|high\|ultra` | — | 否 |
| `stage` | `pmf\|growth\|scale` | — | 否 |
| `biz` | `B2B\|B2C\|B2B+B2C\|B2C+B2B` | — | 否 |
| `access` | 供给状态 `invite\|waitlist\|ga\|oss`（邀请制 / 排队 / 公开可用 / 开源自托管）。只在 `assistant` 类别维护，其余为 `null` | — | 是 |
| `region` | `US\|CN\|EU`（仅 models） | — | 否 |
| `ownModel` | 见下 | — | 否（apps） |
| `m` | Momentum 0-100，**计算值** | — | 否 |
| `m_manual` | 手动钉住 momentum，设了就覆盖计算值 | — | 是 |
| `retired` | 已出池 | bool | 否 |
| `prov` | 逐字段溯源，见下 | — | 否 |

### `ownModel`（取代旧的布尔 `selfModel`）
```json
{ "status": "none | hybrid | primary | unknown",
  "tokenShare": 70,
  "models": ["Composer"] }
```
- `none` —— 全部依赖第三方 API
- `hybrid` —— 自有模型已上线，但主要流量仍走第三方
- `primary` —— 多数推理已跑在自训模型上
- `unknown` —— 公司从未披露基座模型，也没有可信报道能判断（Instinct 官网只说模型"经过训练会使用手机和电脑"）。
  这是事实本身，**不要压成 `none`**——那是把推断当事实写进数据源
- `tokenShare` —— 自有模型占 token 调用的百分比，未披露则 `null`
- 约束：`status: none` / `unknown` 与 `tokenShare > 0` 互相矛盾，会被校验层拒绝

### `prov`（溯源）
`arr` / `val` / `ownModel` 三个字段的新鲜度会在页面上逐格显示：
```json
"prov": { "arr": { "as_of": "2026-08-12", "source": "The Information 2026-08-12",
                   "url": "https://…", "conf": "high" } }
```
- `as_of` —— 来源**报道该数字**的日期，不是抓取日期
- `conf` —— 只接受 `high` / `medium`
- 超过 `meta.stale_days`（默认 120 天）该格变灰并打「待复核」角标

### Momentum 怎么算
`m` 曾经是手写死值，任何脚本都改不了；覆盖从 20 家扩到 45 家时手编就是臆造，所以改成计算值：

```
m = round(100 × (0.40×pct_rank(arrg)
                + 0.30×pct_rank(log10 arr)
                + 0.30×pct_rank(arrg ÷ (val×1000/arr))))
```

分位在各自的 section（models / apps）内计算；缺项按剩余权重归一化。
第三项是**增速调整后的估值**分位 —— 贵不贵要跟增速一起看，不是静态倍数一刀切。

---

## 4. 数据来源
- **Foundation Model 收入**：年化 API 收入，来源为融资公告、Bloomberg、The Information
- **AI App ARR**：年化经常性收入，来源为 Sacra、公司公开发言、媒体报道
- **Token 消耗量**：按 API 定价反推 + 官方披露校验
- **C 端用户数（`mau`）**：只认公司披露（财报 / 官方博客 / 正式声明，conf high）或三方数据商
  （Sensor Tower / Appfigures / data.ai，conf medium，且只对有应用商店 App 的产品有效——
  Instinct 跑在 iMessage/WhatsApp/电话上、OpenClaw 自托管，三方数据对它们结构性失明）。
  媒体转述或创始人推文里的定性说法不入数，留空
- 所有数据为研究性估算，**不构成投资建议**
