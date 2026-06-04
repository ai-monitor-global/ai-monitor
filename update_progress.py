"""
Weekly "AI 进展" research module for the AI Native App Monitor.

Runs after update_data.py. Calls Claude with the web_search built-in tool to
research the most important AI developments of the past 7 days across three
themes, then writes a structured `ai_progress` block into data.json:

  1) 企业端应用推广  — enterprise AI new use cases / big-co feedback / concrete examples
  2) 模型与训练范式  — new training paradigms (academia) + latest SOTA model evals/compares
  3) AI Infra 投资视角 — infra-layer opportunities (Agent orchestration, Model Router, ...)

On any failure it preserves the previous ai_progress and records meta.progress_error,
so the dashboard never blanks. Needs ANTHROPIC_API_KEY; CLAUDE_MODEL overrides model.
"""
import json
import os
import re
import sys
from datetime import date, datetime, timezone
import anthropic

DATA_FILE = "data.json"
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-opus-4-8"

SYSTEM_PROMPT = """你是一位严谨的 AI 行业分析师，为投资人 Xiaoxiao 服务。语气直接、像投研同事——不堆术语、不客套、不用 emoji 装饰正文。
铁律：
- 只写过去 7 天内、有可信来源(官方公告、arXiv、The Information、Bloomberg、官方博客、知名媒体)的真实进展；不臆测、不凑数，宁缺毋滥。
- 每条都要给出来源名称+日期，能给 URL 就给。
- 投资视角部分要具体到层/标的，并说清楚逻辑，但明确这是研究观察、非投资建议。
- 全部用中文。只返回合法 JSON，不要 markdown 代码围栏、不要任何额外说明文字。"""

USER_PROMPT = """今天是 {today}。请联网搜索过去 7 天最重要的 AI 行业进展，按以下三个主题整理，每个主题给 3-5 条高质量内容（投资视角给 2-4 个层/机会）。

主题一 企业端应用推广(enterprise)：企业 AI 的新用例、大企业落地 AI 的真实反馈与效果、企业端 AI 进展的具体实例(谁、做了什么、效果如何)。
主题二 模型与训练范式(models)：学术界/业界在讨论的 Training 新范式(如新的后训练/RL/数据/架构思路)，以及最新 SOTA 模型的评价与横向对比(谁更强在哪)。kind 取 training | sota | eval。
主题三 AI Infra 投资视角(infra_invest)：新的 AI 基础设施层投资机会，重点关注 AI Agent 编排层、Model Router 层(按任务路由不同模型)等;给出该层的投资逻辑(thesis)与代表标的/玩家(players)。

只返回如下 JSON(没有内容的数组留空，不要编造)：
{{
  "enterprise":   [{{"title":"", "summary":"", "example":"", "company":"", "source":"", "date":"YYYY-MM-DD", "url":""}}],
  "models":       [{{"title":"", "summary":"", "kind":"training|sota|eval", "source":"", "date":"YYYY-MM-DD", "url":""}}],
  "infra_invest": [{{"layer":"", "thesis":"", "players":["",""], "summary":"", "source":"", "url":""}}],
  "takeaway": "一句话本周综述"
}}"""


def extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating fences or surrounding prose."""
    s = re.sub(r"^```(?:json)?\s*", "", text.strip())
    s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise ValueError(f"No JSON object found. First 500 chars: {s[:500]!r}")


def call_claude(today_str: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=MODEL,
        max_tokens=12000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}],
        messages=[{"role": "user", "content": USER_PROMPT.format(today=today_str)}],
        extra_headers={"anthropic-beta": "web-search-2025-03-05"},
    )
    print(f"stop_reason: {response.stop_reason}")
    text = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    ).strip()
    if not text:
        raise ValueError(f"No text block. stop_reason={response.stop_reason}")
    return extract_json(text)


def main():
    today_str = str(date.today())
    print(f"=== AI Monitor weekly progress: {today_str} (model={MODEL}) ===")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; keeping existing ai_progress.", file=sys.stderr)
        return

    try:
        prog = call_claude(today_str)
    except Exception as e:  # noqa: BLE001
        print(f"progress generation error: {e}", file=sys.stderr)
        data.setdefault("meta", {})["progress_error"] = f"{today_str}: {str(e)[:150]}"
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        sys.exit(1)

    prog["week_of"] = today_str
    prog["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prog["model"] = MODEL
    # normalise expected arrays so the frontend never sees undefined
    for k in ("enterprise", "models", "infra_invest"):
        if not isinstance(prog.get(k), list):
            prog[k] = []
    data["ai_progress"] = prog
    data.get("meta", {}).pop("progress_error", None)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    counts = {k: len(prog[k]) for k in ("enterprise", "models", "infra_invest")}
    print(f"ai_progress written: {counts}")
    print("takeaway:", prog.get("takeaway", "")[:200])


if __name__ == "__main__":
    main()
