# Work 任务正文

---

运行一轮 AI Monitor Weekly，唯一目标为 ai-monitor-global/ai-monitor 的 main，交付网页 https://ai-monitor-global.github.io/ai-monitor/ 。用户授权按仓库契约更新业务数据和本轮运行记录，并直接提交 main。

在当前 Work 云端直接联网研究、运行 Python/Git 和完成发布，不创建 Dot/Codex cloud worker，不使用 cloud_threads，不依赖本地电脑、固定工作目录或旧聊天附件。通过已连接的 GitHub 确认访问能力，获取带历史的干净最新 main。完整阅读 AGENTS.md、RUNBOOK.md、ROUTINE_PROMPT.md（含步骤、研究规则和仲裁协议）、CRITERIA.md、automation/task.json，以及 apply.py 文件头。

用 python3 work_pipeline.py prepare --out .run/request.json 完成自检、轮转名单和本周去重；已成功的周次只核验发布，不重复研究。到期则按完整契约做存量仲裁、约十家全量复核、全池七天增量扫描、月初覆盖发现和三主题进展周报。真实打开核实来源；无法核实保持原值并记缺口，不把缺数据当零值或未变化。

将结果写成 automation/research-bundle.schema.json 规定的 .run/research.json，用 work_pipeline.py apply 交给原有 apply.py/validate.py 闸门。不得手改 data.json、改校验器放行或使用文件级 force。按 RUNBOOK 检查并发变更，只提交 data.json 和本轮 runs/ 记录；禁止 force push。不要运行 API 后备或 fetch_openrouter.py，OpenRouter 由现有 GitHub Actions 负责。

读回提交 SHA，核对 Pages 部署，并用 work_pipeline.py check-site 验证线上数据。只把实际验证的步骤报告成功；权限、安全审核、运行工具或来源整体不可用时，准确报告失败阶段，保留合法中间结果，不绕过拒绝。不得自行改动本任务日程、其他任务、Claude 配置或通知渠道。

最终在当前任务对话用中文简报：日期/窗口、复核范围、应用/拒绝/确认数量、重点变化及来源缺口、仲裁与新入池/候选/下架、assistant 三个转折点、周报要点、commit SHA、Pages 状态和网页链接。不要直接发送邮件、Telegram、飞书等外部消息。
