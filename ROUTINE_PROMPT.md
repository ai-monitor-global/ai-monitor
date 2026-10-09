# AI Monitor Weekly — 跨模型研究契约

本文件保存研究步骤、研究规则和仲裁协议；`CRITERIA.md` 保存纳入标准与字段口径，代码闸门保持原实现。
当前由 Work 云端直接研究、运行脚本并授权提交，日程为周日 13:00 UTC（北京时间 21:00）。
具体执行、去重、并发保护和发布核验见 `RUNBOOK.md`；定时任务正文为 `automation/WORK_PROMPT.md`。

研究过程不绑定模型、API key、特定云环境 ID 或固定目录。更换模型只调整运行平台；更换调度器复用同一份研究契约、结构化输出和仓库状态。修改研究规则时只改本文件及关联标准，任务每次从最新 main 读取。

OpenRouter 继续由现有 GitHub Actions 更新，API 版研究脚本保留为手动后备。Work 不调用这些模型 API，不负责 OpenRouter 拉取，也不更改其日程。研究结果经统一入口调用原有 apply.py/validate.py，提交 main 后由 Pages 发布。

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

## 步骤

1. **准备**：按 RUNBOOK 获取干净最新 main，运行 `python3 work_pipeline.py prepare --out .run/request.json`；内部原有自检必须全绿。已完成的周次只核验发布。
2.5 **仲裁存量队列**：读 `data.json` 的 `meta.review_queue`，对每条跑一遍上方
   仲裁协议（通过→单条 force 落地；证据仍不足→原样留下等过期）。队列通常 0-2 条，
   几分钟的事，别跳过。
2. **拿本周轮转名单**：使用 prepare 请求中的 rotation（来自 `python3 reverify.py --list -k 10`）：本周要全量复核的 ~10 家
   （溯源最旧优先；上市公司每周必在；每家附当前值和已有溯源）。
3. **逐家全量复核**（联网搜索，按「研究规则」）：对名单里每家核
   `arr / val / valPending / arrg`，apps 加 `mau/maug/ownModel/cat/stage/biz/ti`
   （`assistant` 类别再加 `access`，且 `arr` 查不到是常态，见研究规则 6），
   models 加 `tokM/tokG/region`，所有实体核 `uc/listed/parent`。
   - 值变了 → 写进 `patches`；核过没变 → **必须**写进 `confirmations`（否则页面
     会把正确的值标成"待复核"，轮转也不前进）；查不到 → 两边都不写，摘要里说明。
4. **周增量扫描**：搜过去 7 天全池相关新闻（名单 = data.json 里全部未 retired 实体），
   已交割融资/官宣 ARR 里程碑/自有模型进展 → 追加进 `patches`。
   `assistant` 类别（个人助理）另盯三个转折点：邀请制→公开（patch `access`）、
   首次公布定价（patch `uc` 带上定价，并重估 `stage`）、基座模型披露（patch `ownModel`）。
   这三件事即使没有任何数字变化也要落成 patch 并在摘要点名——它们比这类的 ARR 重要。
5. **每月第一个周日加跑覆盖发现**：按 `CRITERIA.md` 门槛找不在池内的达标公司
   （重点：validate 警告里占比过低的垂直），写进 `candidates`。
   **收购/停运政策（全自动，无需人批）**：
   - 池内公司**被收购** → 不下架。提交 `parent=收购方` 的 patch（≥2 独立来源确认
     已交割），系统会自动清掉 val/valPending/listed —— Cursor/SpaceX 先例即此政策。
   - 池内公司**停运或不再独立经营** → `retire` 条目加 `"confirmed": true`，
     `source` 里列 ≥2 个独立来源，直接生效；证据不足就不加 confirmed，进队列下轮再核。
   - **`assistant` 类别入池口径不同**（CRITERIA.md §1 例外）：已交割估值 ≥ $2B 即达标，
     候选的 `arr` 可填 0（系统落库为 null）；$0.5–2B 进 `candidates`；可以顺带给 `access`。
     ChatGPT / Gemini / Claude 的助理形态**不单列**——算力已计入模型层，会双重计算。
6. **进展周报**：过去 7 天三主题（企业端应用 enterprise / 模型与训练范式 models /
   AI Infra 投资视角 infra_invest，各 3-5 条，投资视角 2-4 条），全中文、每条带
   来源+日期+URL、宁缺毋滥，写进 `ai_progress`（结构见 apply.py 文件头注释）。
7. **校验候选**：把以上内容写为 `automation/research-bundle.schema.json` 规定的 `.run/research.json`，changes 子对象保持 apply.py 文件头格式，并附实际来源和覆盖缺口。执行 `python3 work_pipeline.py apply --request .run/request.json --bundle .run/research.json`，内部仍经原有 apply.py 数据闸门。被拒条目按仲裁协议处理，不改校验器强行通过，不启用文件级 force。退出码非零禁止提交；修正输入后的重试按 RUNBOOK 处理本轮未提交候选。
8. **提交与推送**：按 RUNBOOK 再查远端基准，只提交 data.json 与本轮 runs/ 记录，使用包含 UTC 日期的提交信息。并发变化时重新准备、复核并应用输入，不对 data.json 盲目 rebase/autostash 合并，绝不强推。
9. **发布验证**：分别核对远端 SHA、对应 Pages 工作流和 `python3 work_pipeline.py check-site` 的线上数据哈希。页面渲染检查有条件才执行；数据验证与视觉验证分开报告。
10. **摘要**：中文输出——核了哪几家、应用/拒绝/确认各几条、重点数字变化
    （±30% 以上的点名）、仲裁了什么及依据、新入池/候选/下架、`assistant` 类别三个
    转折点有无触发（转公开 / 首次定价 / 基座模型披露；没有就一句"无"）、进展周报 takeaway。
    摘要是运行日志，不是请示：**不要写"待你确认/等你裁决"** —— 没有人在等着批。
    异常必须在当前 Work 任务回执中说明；现有 Actions 看门狗另按 GitHub 的通知设置告警，不能仅凭检查失败声称邮件已发送。

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
6. **`assistant`（个人助理）类别口径**：C 端个人 agent（Instinct / Meta Muse / OpenClaw / Manus）
   整体 pre-revenue，**活跃用户是主指标，ARR 不是**。对这一类：
   - **用户数**：专门搜披露的 MAU / DAU / WAU / 注册数 / waitlist 规模。找到 → patch `mau`
     （百万）和 `maug`（对上一次披露值的增幅），`as_of` 写数字所指的日期而非报道日期，
     `source` 写清口径（MAU 还是注册数）。公司披露/财报 = high；三方数据商
     （Sensor Tower / Appfigures / data.ai）= medium。媒体转述、创始人推文里的定性说法
     （"早期用户在规划公路旅行"）**不构成数字**，不提交，摘要里写"仍未披露"。
   - **绝不估算、绝不插值**用户数。查不到就让 `mau` 留 null——null 是正确答案，猜测不是。
   - **结构性失明**：Instinct 跑在 iMessage/WhatsApp/电话上、OpenClaw 自托管，都没有应用商店
     入口，Sensor Tower 一类下载数据看不见它们，**不要拿应用商店数据顶替一个没有 App 的产品**；
     只认公司披露。Meta Muse 藏在 Meta 整体口径里，除非财报单列否则也没有数字。
     OpenClaw 的 GitHub star / 下载量是装机代理，不是活跃用户，不写进 `mau`。
   - **`arr` 留空**：没公布过价格就没有收入可查，`null` 正确，**禁止填 0**。Meta Muse 已有
     $20/$100 订阅档但 Meta 不会单独披露收入，同样留空。这一类的 `arr` 只有公司自述时才填。
   - **三个转折点比数字重要**：邀请制→公开（`access`: invite/waitlist → ga）、首次定价
     （`uc` 带上价格并重估 `stage`）、基座模型披露（`ownModel`: unknown → none/hybrid/primary）。
     `ownModel.status = unknown` 表示"公司没说、信源不足以判断"，是事实不是缺省——
     没有可信信源前**不要**把它改成 none 或 primary。
   - `access` 只在这一类维护，其他类别保持 null，不要去补。

## 注意事项

- 以当前 Work 的实际联网能力为准，不沿用旧平台“WebFetch 全部 403”的假设。必须真正打开来源；搜索摘要用于发现。来源打不开就尝试其他公开独立来源，仍不能核实时保留原值、记录缺口；整体检索不可用不得刷新成功日期。
- GitHub 写入遇权限或安全审核拒绝时停止受阻步骤，不换工具或凭据绕过；保留合法研究 bundle 并报告具体错误，不推测是旧平台 routine sources 配置造成的。
- OpenRouter 数据继续由现有 GitHub Actions 负责，Work 不运行 fetch_openrouter.py。
- 一次运行的研究预算把轮转 10 家做扎实优先，增量扫描其次；宁可少核两家，
  不要浅核十家。
- **`assistant` 类别的空列是预期状态，不是抓取失败**：四家里目前没有一家有可用的活跃
  用户数（原因见研究规则 6），这几列会先空一段时间。它们的价值在**从空变成有数字的那一刻**
  ——那意味着公司自己觉得用户量到了值得说的量级。对 Instinct 而言"转公开"和"首次披露用户数"
  大概率是同一事件。不要为了填满列而放松来源标准。
- Python 环境：Work 入口及 apply/validate/reverify --list 只用标准库，不需要 anthropic 包。
- 后备通道：routine 挂了可在 Actions 手动跑 `weekly`（API 版全流程，花 API 额度），
  见 README「一次性回填」一节的模式说明。

## 分析与写作风格（Crystal 偏好，2026-10-09）

本节规范分析文字怎么写；本任务既有的输出格式、字段、围栏、数据口径、质量闸门与禁止事项优先，不得为了风格删减或改动。

尤其在投资研究和深入分析中，请以形成判断、支持决策为目标。结论先行，明确给出你更倾向的观点、最可能的情景及其决策含义，再解释关键依据。围绕真正决定结果的核心因素展开，区分不同证据的重要性和可信度，避免机械地等量罗列 pros and cons，也不要在每个观点之后附加大量限制、反驳和 hedging comments。只突出可能实质改变结论或影响决策的关键风险，并说明什么新证据会让你改变判断。比较多个选项时，尽量给出明确排序和取舍。保留分析深度与事实准确性，独立判断，不迎合我；证据不足时明确指出具体缺口，避免泛泛而谈的免责声明。
