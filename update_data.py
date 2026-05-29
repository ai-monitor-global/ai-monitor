"""
Daily data updater for AI Native App Monitor.
Uses Anthropic SDK with web_search built-in tool.
Fix: use client.messages.create + extra_headers for web-search beta.
"""
import json
import os
import sys
import re
from datetime import date, timedelta
import anthropic

DATA_FILE = "data.json"
MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a financial data analyst maintaining an AI industry monitor dashboard.
Search for the latest news about AI company funding, valuations, and revenue milestones,
then return a structured JSON patch with ONLY confirmed, sourced changes.

Rules:
- Only update a field if you find a credible source (Bloomberg, TechCrunch, CNBC, Sacra, The Information, company announcement).
- Do NOT speculate. If unsure, skip the field.
- Return ONLY valid JSON, no prose, no markdown fences.
"""

USER_PROMPT = """Today is {today}. Search the web for AI company news from the past 7 days.

Focus on:
MODELS: OpenAI, Anthropic, Google Gemini, xAI/Grok, Mistral AI, Cohere, Kimi/Moonshot, MiniMax, Zhipu AI
APPS: Cursor/Anysphere, Perplexity, Character.ai, ElevenLabs, Midjourney, Runway, Harvey, Glean, Sierra, Cognition, HeyGen, Suno, Luma AI

Look for: new funding rounds, ARR/revenue milestones, valuation updates.

Return JSON (omit sections with no confirmed updates):
{{
  "has_updates": true or false,
  "update_notes": "Brief summary max 200 chars",
  "model_patches": [
    {{"name": "exact name in dataset", "field": "arr|val|arrg|tokM", "new_value": <number>, "source": "source + date", "confidence": "high|medium"}}
  ],
  "app_patches": [
    {{"name": "exact name in dataset", "field": "arr|val|arrg|mau", "new_value": <number>, "source": "source + date", "confidence": "high|medium"}}
  ]
}}

arr unit: $M. val unit: $B. arrg unit: %. Include only high/medium confidence changes with verifiable sources.
"""


def call_claude(today_str: str) -> dict:
    """Call Claude with web_search using correct beta header via extra_headers."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    # Correct usage: client.messages.create + extra_headers for web-search beta
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": USER_PROMPT.format(today=today_str)}],
        extra_headers={"anthropic-beta": "web-search-2025-03-05"},
    )

    print(f"stop_reason: {response.stop_reason}")
    print(f"blocks: {[getattr(b, 'type', '?') for b in response.content]}")

    # Concatenate ALL text blocks. With web_search the model emits text across
    # many blocks (commentary between searches + a final answer that may itself
    # span multiple blocks or be followed by a trailing remark), so keeping only
    # the last block hands json.loads a fragment.
    text = "".join(
        block.text for block in response.content
        if getattr(block, "type", None) == "text"
    ).strip()

    if not text:
        raise ValueError(
            f"No text block found. stop_reason={response.stop_reason}, "
            f"blocks={[getattr(b,'type','?') for b in response.content]}"
        )

    return extract_json(text)


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
        raise ValueError(f"No JSON object found in response. First 500 chars: {s[:500]!r}")


def apply_patches(data: dict, patch: dict) -> tuple:
    changes = []
    today = str(date.today())

    for mp in patch.get("model_patches", []):
        name, field, val = mp.get("name"), mp.get("field"), mp.get("new_value")
        if not all([name, field, val is not None]):
            continue
        for m in data["models"]:
            if name.lower() in m["name"].lower():
                old = m.get(field)
                m[field] = val
                changes.append(f"MODEL {m['name']}.{field}: {old} -> {val} ({mp.get('source','')})")
                break

    for ap in patch.get("app_patches", []):
        name, field, val = ap.get("name"), ap.get("field"), ap.get("new_value")
        if not all([name, field, val is not None]):
            continue
        for a in data["apps"]:
            if name.lower() in a["name"].lower():
                old = a.get(field)
                a[field] = val
                changes.append(f"APP {a['name']}.{field}: {old} -> {val} ({ap.get('source','')})")
                break

    data["meta"]["last_updated"] = today
    data["meta"]["next_update"] = str(date.today() + timedelta(days=1))
    if patch.get("update_notes"):
        data["meta"]["update_notes"] = f"{today}: {patch['update_notes']}"

    return data, changes


def main():
    today_str = str(date.today())
    print(f"=== AI Monitor daily update: {today_str} ===")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    print("Calling Claude API with web_search (extra_headers beta)...")
    try:
        patch = call_claude(today_str)
    except Exception as e:
        print(f"Claude API error: {e}", file=sys.stderr)
        data["meta"]["last_updated"] = today_str
        data["meta"]["next_update"] = str(date.today() + timedelta(days=1))
        data["meta"]["update_notes"] = f"{today_str}: Error: {str(e)[:150]}"
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        sys.exit(1)  # Exit with error so GitHub Actions marks it as failed

    if not patch.get("has_updates"):
        print("No confirmed updates today.")
        data["meta"]["last_updated"] = today_str
        data["meta"]["next_update"] = str(date.today() + timedelta(days=1))
        data["meta"]["update_notes"] = f"{today_str}: No new confirmed data points found."
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return

    updated_data, changes = apply_patches(data, patch)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(updated_data, f, ensure_ascii=False, indent=2)

    print(f"Applied {len(changes)} changes:")
    for c in changes:
        print(f"  {c}")
    print("data.json updated successfully.")


if __name__ == "__main__":
    main()
