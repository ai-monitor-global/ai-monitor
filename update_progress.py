"""Weekly "AI 进展" research module.

Unchanged in purpose: three themes of last-7-days developments written into
data.json under `ai_progress`. Now goes through common.py for the API call, so
it shares the current web-search tool version, structured output, and the
meta.runs bookkeeping that surfaces failures on the page.

  python update_progress.py [--dry-run]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import common

PASS = "progress"

SYSTEM = """你是一位严谨的 AI 行业分析师，为投资人 Xiaoxiao 服务。语气直接、像投研同事——不堆术语、不客套、不用 emoji 装饰正文。
铁律：
- 只写过去 7 天内、有可信来源(官方公告、arXiv、The Information、Bloomberg、官方博客、知名媒体)的真实进展；不臆测、不凑数，宁缺毋滥。
- 每条都要给出来源名称+日期，能给 URL 就给。
- 投资视角部分要具体到层/标的，并说清楚逻辑，但明确这是研究观察、非投资建议。
- 全部用中文。"""

USER = """今天是 {today}。请联网搜索过去 7 天最重要的 AI 行业进展，按以下三个主题整理，每个主题给 3-5 条高质量内容（投资视角给 2-4 个层/机会）。

主题一 企业端应用推广(enterprise)：企业 AI 的新用例、大企业落地 AI 的真实反馈与效果、企业端 AI 进展的具体实例(谁、做了什么、效果如何)。
主题二 模型与训练范式(models)：学术界/业界在讨论的 Training 新范式(如新的后训练/RL/数据/架构思路)，以及最新 SOTA 模型的评价与横向对比(谁更强在哪)。kind 取 training | sota | eval。
主题三 AI Infra 投资视角(infra_invest)：新的 AI 基础设施层投资机会，重点关注 AI Agent 编排层、Model Router 层(按任务路由不同模型)等；给出该层的投资逻辑(thesis)与代表标的/玩家(players)。

没有内容的数组留空，不要编造。"""

ITEM_BASE = {
    "source": {"type": "string"},
    "url": {"type": "string"},
}

SCHEMA = {
    "type": "object",
    "properties": {
        "enterprise": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": dict(ITEM_BASE, **{
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "example": {"type": "string"},
                    "company": {"type": "string"},
                    "date": {"type": "string"},
                }),
                "required": ["title", "summary", "example", "company", "date",
                             "source", "url"],
                "additionalProperties": False,
            },
        },
        "models": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": dict(ITEM_BASE, **{
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "kind": {"type": "string", "enum": ["training", "sota", "eval"]},
                    "date": {"type": "string"},
                }),
                "required": ["title", "summary", "kind", "date", "source", "url"],
                "additionalProperties": False,
            },
        },
        "infra_invest": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": dict(ITEM_BASE, **{
                    "layer": {"type": "string"},
                    "thesis": {"type": "string"},
                    "players": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                }),
                "required": ["layer", "thesis", "players", "summary", "source", "url"],
                "additionalProperties": False,
            },
        },
        "takeaway": {"type": "string", "description": "一句话本周综述"},
    },
    "required": ["enterprise", "models", "infra_invest", "takeaway"],
    "additionalProperties": False,
}


def main() -> int:
    dry = "--dry-run" in sys.argv
    data = common.load()
    print("=== {} pass: {} (model={}){} ===".format(
        PASS, common.today(), common.MODEL, " [DRY RUN]" if dry else ""))

    try:
        prog = common.ask(system=SYSTEM,
                          user=USER.format(today=common.today()),
                          schema=SCHEMA, max_uses=14, max_tokens=16000)
    except Exception as exc:  # noqa: BLE001
        # Keep the previous week's block rather than blanking the section.
        print("FAILED: {}".format(exc), file=sys.stderr)
        common.record_run(data, PASS, ok=False, error=exc)
        if not dry:
            common.save(data)
        return 1

    prog["week_of"] = str(common.today())
    prog["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prog["model"] = common.MODEL
    for key in ("enterprise", "models", "infra_invest"):
        if not isinstance(prog.get(key), list):
            prog[key] = []

    counts = {k: len(prog[k]) for k in ("enterprise", "models", "infra_invest")}
    if not dry:
        data["ai_progress"] = prog
        common.record_run(data, PASS, ok=True, **counts)
        common.save(data)
    else:
        common.record_run(data, PASS, ok=True, **counts)

    print("ai_progress written: {}".format(counts))
    print("takeaway: {}".format(prog.get("takeaway", "")[:200]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
