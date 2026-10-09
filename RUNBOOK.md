# Work 周更与迁移手册

研究契约：`ROUTINE_PROMPT.md` 和 `CRITERIA.md`。目标仅为 `ai-monitor-global/ai-monitor` 的 `main`。
日程保持周日 13:00 UTC（北京时间 21:00）；研究日期和文件名使用 UTC。
网页：https://ai-monitor-global.github.io/ai-monitor/

## 执行入口

1. 在当前云端获取带 Git 历史的仓库副本。确认 origin、main 和干净工作区，快进同步最新远端；不要覆盖其他未提交工作，不依赖旧会话目录。
2. 读本文件、完整 `ROUTINE_PROMPT.md`（含仲裁协议）、`CRITERIA.md`、`automation/task.json` 和 `apply.py` 文件头。执行：

   ```bash
   python3 work_pipeline.py prepare --out .run/request.json
   ```

   prepare 自动跑原有自检/数据校验，生成轮转名单、全池名单、存量队列、七天新闻窗口及月度发现标志。全量轮转复核不受七天窗口限制。正常运行不能用 `--as-of`；该选项仅供离线测试。
3. `needs_research=false` 说明本周已有成功研究，不重复执行，检查已有提交和网页。每周以 UTC 周日起算；依 `meta.runs` 的研究记录去重，不根据可被 OpenRouter 更新的 `meta.last_run` 判断。迁移前的 `routine-weekly` 记录同样有效。API 后备只有增量、复核、周报三个 pass 均成功，才算完成研究。
4. 到期则按仓库完整规则真实联网研究，写 `.run/research.json`，接口见 `automation/research-bundle.schema.json`。`changes` 保留原有 patches / confirmations / candidates / retire / ai_progress 格式；附覆盖情况和本轮已打开的来源。查不到与未变化分开处理；不以搜索摘要、标题或“没有找到”冒充已核实的数字。未完成的公司/字段必须列为 unverified，不能用批量 confirmation 掩盖。
5. 所有输入生成后执行：

   ```bash
   python3 work_pipeline.py apply --request .run/request.json --bundle .run/research.json
   python3 validate.py
   ```

   wrapper 不调用模型，内部仍用原有 apply.py 和数据闸门；它拒绝过时基准、跨日请求、重复周次和文件级 force，并在脚本失败时恢复原 data.json。已知实际模型名可用 `--model-label` 记录；无法读取则用 `unreported`。不要把后备通道的 `CLAUDE_MODEL` 当作 Work 实际模型。
6. 查看 apply 报告和 diff。被拒条目依原仲裁协议处理，合法待核项保留 review_queue。若需要修正输入后重新 apply：先将候选 bundle 和报告留在 .run/，确认未提交的改动只有本轮 data.json 与本轮 ledger，恢复这两项到 prepare 的干净基准，再重跑；不要清理任何他人改动，不对已发布周次执行这种恢复。
7. 仅提交 `data.json` 和本轮 `runs/<UTC周日起日>.json`，提交信息 `chore(routine): weekly update <UTC日期>`。提交前重新读取远端 main：若不等于 request.base_commit，保留候选输入，获取干净新基准并重新 prepare、复核轮转名单和字段，再重新 apply。即使只是 OpenRouter 更新，也不能盲合并或把旧数据整份覆盖上去。
8. 使用普通 Git 的 fast-forward 推送，或已授权 GitHub 工具原子写入：基于准确 base tree 创建两项变更，一次 commit，以 `expected_sha=base_commit`、`force=false` 更新 main。禁止强推。遇权限/安全审核拒绝即报告受阻步骤，不切换工具或凭据绕过。
9. 回读远端 SHA 和提交内容，核对该 SHA 对应的 Pages 工作流。随后从与最新远端对齐的副本执行：

   ```bash
   python3 work_pipeline.py check-site
   ```

   该命令比较线上和仓库完整 data.json 的规范化哈希，并单独报告最后研究成功日。若远端已前进，先确认研究提交仍是其祖先、相关研究数据未被覆盖，再核验最新部署。Pages 未完成或网页读不到时，回报“提交成功，发布待核验”，不重复研究、不以空提交催部署。
10. 在当前 Work 任务对话用中文简要回执：UTC 日期和七天窗口、复核公司、应用/拒绝/确认数量、重点变化、仲裁和覆盖缺口、assistant 三个转折点、周报要点、SHA、Pages 状态、网页链接。不要新增或直接发送邮件、Telegram、飞书等通知。

## 状态与恢复

业务数据、溯源、轮转进度、review_queue 和各 pass 结果仍保存在 `data.json`。
`runs/` 保存原始研究 bundle、覆盖记录、基准提交、校验报告和数据指纹，便于审计与迁移。
它的 `validated_for_publication` 仅表示校验完成，提交和网页是否发布须另行验证。
`.run/` 是可丢弃的中间文件，不存凭据、不进 Git。任务不需要持久化 runner。

写入前失败：远端不前进，下次从最新仓库重做未完成工作。写入后失败：先核验部署，本周已有研究则跳过重复研究。窗口仍是过去七天；若漏跑超过一周，明确报告缺失期，不能声称已覆盖。

现有 GitHub 工作流继续负责 OpenRouter 和周一看门狗，日程不变。看门狗改用 `work_pipeline.py freshness --max-days 2`，只认成功研究，OpenRouter 无法掩盖研究停跑。通知沿用 GitHub 现有设置；检查失败不等于已发送邮件。cron / openrouter-only / validate-only 不再安装或调用 Anthropic，API 后备仍保留为手动入口。

## 换模型或迁往 GitHub Actions

Work 仅负责调度、联网研究及授权发布；规则、结构化输入输出、业务闸门和状态均在仓库。
未来接入新模型，只需实现“读取 request → 网页研究 → 输出 research bundle”；prepare/apply/校验/去重/看门狗继续复用。不把模型名、订阅令牌或特定云环境 ID 写进业务规则。

迁至 GitHub 托管临时 runner 时，新增模型和调度适配器，配置当时受支持的无人值守认证、联网、超时与额度；无需另维护服务器。不能假定订阅登录凭据永久有效。此仓库公开，认证只放平台凭据存储；可用私有控制仓库执行研究，再限定权限发布到这里。

切换先验证新执行器，再暂停旧调度器、启用新调度器。维持一个研究调度器；OpenRouter 工作流是独立数据职责。首次真实定时触发须单独验收，交互式测试通过不能替代自动触发成功的证据。
