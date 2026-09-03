# Routine Prompt — AI Monitor 周更 routine 的真理副本

> ⚠️ 本文件是**线上 routine 的版本化副本 + 运维说明**。routine 每次运行实时读它，
> 所以改研究行为 = 改这个文件，push 即生效，**不需要动线上 routine**。
> 线上那段 prompt 是极简 bootstrap（见下方），基本永远不用改。

---

## 当前 routine 身份

| 项 | 值 |
|---|---|
| 名称 | AI Monitor Weekly |
| Cron | `0 13 * * 0`（周日 UTC 13:00 = 北京周日 21:00） |
| Model | Opus（研究质量优先） |
| 仓库 | `ai-monitor-global/ai-monitor`（沙箱自动 clone，git 直推无需 token） |
| 管理页 | https://claude.ai/code/routines |

## 工作机制

```
   周日 UTC 13:00                        周日 UTC 14:30
        │                                     │
        ▼                                     ▼
  云端 routine（订阅额度）            GitHub Actions cron
  联网研究：轮转复核 + 周增量          只跑 fetch_openrouter.py
  + (每月第一周)覆盖发现 + 进展周报    （纯 HTTP，零 Claude 成本）
        │                                     │
        ▼                                     │
  写 changes.json                             │
        │                                     │
        ▼                                     │
  python apply.py changes.json  ←—— 一切改动必须过这里的校验闸门
  （量级/币种/父子/日期/枚举…拒绝项进 review_queue，绝不静默丢）
        │                                     │
        ▼                                     ▼
  python validate.py 全绿 → git push origin HEAD:main
        │
        ▼
  GitHub Pages 重建 → https://ai-monitor-global.github.io/ai-monitor/
```

**本任务是完全自动的：没有任何环节等待人类。** 被闸门拦下的条目由你按下方
「仲裁协议」当场或下周处理；28 天未决的自动过期（进 changelog 留痕）。

**三条铁律（历史上每一条都对应真实事故）：**
1. **绝不手改 `data.json`** —— 研究结果一律写 `changes.json` 交给 `apply.py`。
   RMB 当美元、GMV 当 ARR、母公司估值漏进子业务行，全是靠代码闸门拦住的。
2. **apply.py 或 validate.py 报错 → 修输入重试，绝不绕过、绝不直接编辑数据。**
   被拒的条目自动进 `meta.review_queue`，由仲裁协议处理，绝不静默丢失。
3. **单条 patch 的 `"force": true` 只有两个合法来源**：(a) 通过了仲裁协议的条目，
   (b) 明知库存值口径性错误的定点修正。force 写在**那一条 patch 里**（不用文件级
   开关，避免豁免同批其他条目），来源里必须写清仲裁依据。它只豁免 >5x 量级闸门
   和日期倒退闸门，币种/边界/枚举/父子闸门永远有效。

**仲裁协议（原人工裁决的机械化版）：**
一条 patch 被量级或日期闸门拦下时，对**这一条**做专项二次核实，通过标准是**同时**满足：
- **≥3 个互相独立的来源**给出一致的新值（转载同一篇稿的不算独立）；
- 能**指出库存值具体错在哪**（口径错误如 pre/post-money 混淆、GMV 当 ARR、
  期间误算，或日期记错、币种未换算）——"新值更常见"不构成理由。
通过 → 该条加 `"force": true` 单独重新提交（可以在同一次运行里）。
不通过 → 留在 review_queue，**下周运行的步骤 2.5 自动重试**；28 天后自动过期。

## 一次性设置（创建 routine 时必须做对）

1. **把 `ai-monitor-global/ai-monitor` 加进 routine 的 sources（授权仓库列表）**。
   沙箱的 git 代理只允许推送授权列表内的仓库 —— 首跑（2026-09-02）就是因为漏了
   这一步，研究全部完成、commit 停在临时沙箱里推不出去。
2. Cron `0 13 * * 0`，Model 选 Opus，prompt 用下方 bootstrap。

## 改 routine 怎么办

- **改研究口径/规则/节奏内容** → 只改本文件（及 `CRITERIA.md`），push 即生效。
- **改数据闸门/字段口径** → 改 `common.py`（闸门）+ `CRITERIA.md`（文档）。
- **改调度时间/模型** → https://claude.ai/code/routines 编辑，或 Claude Code 里 `/schedule`。
- API 版脚本（`reverify.py` 等）保留作 workflow_dispatch 后备，研究规则若有大改，
  记得同步它们的 prompt 常量（低优先级，后备通道而已）。

## 线上 prompt（bootstrap · 与线上一致）

```
调度：每周日 UTC 13:00（北京时间周日 21:00）运行。

你是 AI Monitor 周更 routine。仓库 ai-monitor-global/ai-monitor 已在沙箱中
（若无：git clone https://github.com/ai-monitor-global/ai-monitor && cd ai-monitor）。

执行：
1. git pull 确保最新；
2. 打开 ROUTINE_PROMPT.md，从「## 步骤」一节开始严格执行到底；
   研究口径以其「## 研究规则」和 CRITERIA.md 为准。

铁律：绝不直接编辑 data.json —— 一切改动写成 changes.json 用
`python apply.py changes.json` 提交；apply 或 validate 报错就修复输入重试，
不许绕过闸门。完成后 git 直推 main，并输出一段中文运行摘要
（本周核了谁、改了什么、拒了什么、新入池谁）。
```

---

## 步骤

1. **准备**：`git pull`；`python validate.py --selftest` 必须全绿（闸门自检坏了就停下报告，别继续）。
2.5 **仲裁存量队列**：读 `data.json` 的 `meta.review_queue`，对每条跑一遍上方
   仲裁协议（通过→单条 force 落地；证据仍不足→原样留下等过期）。队列通常 0-2 条，
   几分钟的事，别跳过。
2. **拿本周轮转名单**：`python reverify.py --list -k 10` → JSON：本周要全量复核的 ~10 家
   （溯源最旧优先；上市公司每周必在；每家附当前值和已有溯源）。
3. **逐家全量复核**（联网搜索，按「研究规则」）：对名单里每家核
   `arr / val / valPending / arrg`，apps 加 `mau/maug/ownModel/cat/stage/biz/ti`，
   models 加 `tokM/tokG/region`，所有实体核 `uc/listed/parent`。
   - 值变了 → 写进 `patches`；核过没变 → **必须**写进 `confirmations`（否则页面
     会把正确的值标成"待复核"，轮转也不前进）；查不到 → 两边都不写，摘要里说明。
4. **周增量扫描**：搜过去 7 天全池相关新闻（名单 = data.json 里全部未 retired 实体），
   已交割融资/官宣 ARR 里程碑/自有模型进展 → 追加进 `patches`。
5. **每月第一个周日加跑覆盖发现**：按 `CRITERIA.md` 门槛找不在池内的达标公司
   （重点：validate 警告里占比过低的垂直），写进 `candidates`。
   **收购/停运政策（全自动，无需人批）**：
   - 池内公司**被收购** → 不下架。提交 `parent=收购方` 的 patch（≥2 独立来源确认
     已交割），系统会自动清掉 val/valPending/listed —— Cursor/SpaceX 先例即此政策。
   - 池内公司**停运或不再独立经营** → `retire` 条目加 `"confirmed": true`，
     `source` 里列 ≥2 个独立来源，直接生效；证据不足就不加 confirmed，进队列下轮再核。
6. **进展周报**：过去 7 天三主题（企业端应用 enterprise / 模型与训练范式 models /
   AI Infra 投资视角 infra_invest，各 3-5 条，投资视角 2-4 条），全中文、每条带
   来源+日期+URL、宁缺毋滥，写进 `ai_progress`（结构见 apply.py 文件头注释）。
7. **提交**：把以上全部写成一个 `changes.json`（结构见 `apply.py` 文件头），
   `python apply.py changes.json`。看输出：`-` 开头是被拒的，检查是不是自己
   口径/来源写错，能修则修了重跑；确属闸门该拦的（如 >5x 需人工）就留在
   review_queue。**退出码非 0 = 数据没过校验，必须处理，禁止 commit。**
8. **推送**：`git add data.json && git commit -m "chore(routine): weekly update $(date -u +%F)" && git push origin HEAD:main`
   （push 被拒就 `git pull --rebase --autostash` 后重推）。
9. **两层验证**：等 1-2 分钟后抓
   https://ai-monitor-global.github.io/ai-monitor/data.json 确认 `meta.last_run`
   已是今天（数据层）；有条件的话再看页面无红色横幅（渲染层）。
10. **摘要**：中文输出——核了哪几家、应用/拒绝/确认各几条、重点数字变化
    （±30% 以上的点名）、仲裁了什么及依据、新入池/候选/下架、进展周报 takeaway。
    摘要是运行日志，不是请示：**不要写"待你确认/等你裁决"** —— 没有人在等着批。
    真正的异常出口只有两个：run 失败（Actions 看门狗会变红发邮件）和页面横幅。

## 研究规则（与 `common.py` 闸门同源，闸门为准）

1. **`arr` = 当前 run-rate ARR**：公司自述 ARR、或最近单月×12 / 单季×4。
   **禁止**用 H1×2 / 全年÷1 当 ARR（高增速公司会低估数倍）。任何收入数字先搞清
   **覆盖什么期间**再换算；期间不明就不用。**累计数（YTD）永远不是 ARR**。
   市场/人力平台的**总流水 GMV 不是 ARR**，要取净收入（抽成后）。
2. **先指标后新旧**：先筛掉指标不对的数字，再在同指标里取日期最新的。
   错误指标的新数字打不过正确指标的旧数字。
3. **官方 vs 媒体**：同指标同期间冲突时用官方；但官方滚动收入 ≠ 当前 ARR，
   压不住更新的可信 run-rate。**上市公司第一步先搜业绩会/业绩演示材料**
   （"<公司> 业绩会 ARR"），公司往往在那里而非财报正文披露 ARR。
4. **估值口径**：`val` = 最近**已交割**轮次 / 已完成二级 / 上市公司**实时市值**
   （同时把 `listed` 写成 `"HKEX:2513"` 格式）。已报道但未交割的轮次写 `valPending`，
   不进 `val`。**有 `parent` 的内嵌实体（Gemini/Doubao/Qwen/Llama/Nova/ERNIE/Hunyuan，以及 2026-08 被 SpaceX 收购后的 Cursor）没有自己的估值，val/valPending/listed 永远留空**，arr 只算该业务自身收入。
5. **来源纪律**：每个数字带 来源名+报道日期+URL+conf(high/medium)；`arr`/`val`
   至少两个独立近期来源交叉；中国公司必搜中文媒体（36氪/晚点/虎嗅/科创板日报/财新）；
   非美元一律换算并在 source 里写明汇率；查不到就空着，**绝不编数**。

## 注意事项

- **沙箱的 WebFetch 被网络策略全量拦截**（sacra.com、CNBC、公司官网、curl 一律
  403），研究只能靠 WebSearch 的结果摘要（首跑实测）。因此：同一个数字换 2-3 个
  不同关键词搜索来交叉，摘要间冲突时在 source 里写明取舍；仅有单一摘要支撑的
  数字 conf 一律 medium。这不改变"绝不编数"铁律。
- **push 被拒时的正确行为**（首跑已按此执行，保持）：不绕过、不找替代凭据；把
  changes.json 和 commit patch 作为文件发到会话里留底，然后报告"需要把仓库加进
  routine sources"。研究成果可在有权限的环境用 apply.py 重放，不必重跑。
- 沙箱 egress 代理会拦部分外网（如飞书 403）。**OpenRouter 数据不归 routine 管**——
  Actions cron（周日 UTC 14:30）用仓库 secret 自己拉，routine 不要碰 `fetch_openrouter.py`。
- 一次运行的研究预算把轮转 10 家做扎实优先，增量扫描其次；宁可少核两家，
  不要浅核十家。
- `python` 环境：apply/validate/reverify --list 都不需要 anthropic 包，裸 python3 即可。
- 后备通道：routine 挂了可在 Actions 手动跑 `weekly`（API 版全流程，花 API 额度），
  见 README「一次性回填」一节的模式说明。
