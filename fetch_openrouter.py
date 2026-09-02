"""OpenRouter weekly token series - an external, high-frequency market proxy.

No LLM involved: this hits OpenRouter's official datasets API (the same data
behind openrouter.ai/rankings) and aggregates complete ISO weeks. It measures
tokens routed through the OpenRouter platform (top-50 models + an "other"
bucket) - a proxy for market activity and its trend, NOT the whole market's
absolute volume.

Needs the OPENROUTER_API_KEY repo secret. Without it the pass skips cleanly
(recorded as not-configured, never as a failure), so the pipeline stays green
until the key is added and the chart lights up on the first run after.

  python fetch_openrouter.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

import common

PASS = "openrouter"
URL = ("https://openrouter.ai/api/v1/datasets/rankings-daily"
       "?start_date={start}&end_date={end}&period=day")
LOOKBACK_DAYS = 400   # bounded by whatever the API actually returns
WEEKS_KEPT = 104


def fetch_daily(key: str, days: int = LOOKBACK_DAYS) -> dict:
    """date -> platform total tokens (sum over all rows for that date)."""
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    req = urllib.request.Request(
        URL.format(start=start, end=end),
        headers={"Authorization": "Bearer " + key,
                 "User-Agent": "ai-monitor/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        rows = (json.load(resp) or {}).get("data") or []
    daily = {}
    for row in rows:
        try:
            n = float(row.get("total_tokens"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(n) or n <= 0:
            continue
        day = str(row.get("date"))[:10]
        daily[day] = daily.get(day, 0.0) + n
    return dict(sorted(daily.items()))


def to_weeks(daily: dict) -> list:
    """Complete ISO weeks only (7 data days), tokens in T, keyed by Monday.
    A partial week would always look like a crash, so it is never emitted."""
    buckets = {}
    for day_str, tokens in daily.items():
        day = date.fromisoformat(day_str)
        monday = day - timedelta(days=day.weekday())
        b = buckets.setdefault(str(monday), {"days": 0, "tok": 0.0})
        b["days"] += 1
        b["tok"] += tokens
    weeks = [{"week": wk, "tok": round(b["tok"] / 1e12, 2)}
             for wk, b in sorted(buckets.items()) if b["days"] == 7]
    return weeks[-WEEKS_KEPT:]


def main() -> int:
    data = common.load()
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        # not configured is not a failure - do not turn the dashboard red
        print("OPENROUTER_API_KEY not set; skipping. Add the repo secret to "
              "enable the weekly market-proxy series.")
        common.record_run(data, PASS, ok=None,
                          error="OPENROUTER_API_KEY not configured")
        common.save(data)
        return 0

    try:
        daily = fetch_daily(key)
        weeks = to_weeks(daily)
        if len(weeks) < 2:
            raise RuntimeError("only {} complete week(s) in the response"
                               .format(len(weeks)))
    except Exception as exc:  # noqa: BLE001 - visible, never silent
        print("FAILED: {}".format(exc), file=sys.stderr)
        common.record_run(data, PASS, ok=False, error=exc)
        common.save(data)
        return 1

    prev, last = weeks[-2], weeks[-1]
    wow = round((last["tok"] / prev["tok"] - 1) * 100, 1) if prev["tok"] else None
    data["openrouter"] = {
        "as_of": max(daily),
        "fetched": str(common.today()),
        "wow_pct": wow,
        "weekly": weeks,
        "source": "OpenRouter datasets API（rankings 同源，top50+other 全平台口径）",
        "url": "https://openrouter.ai/rankings",
    }
    common.record_run(data, PASS, ok=True, weeks=len(weeks), wow_pct=wow)
    common.save(data)
    print("openrouter: {} complete weeks; latest {} = {}T; WoW {}%".format(
        len(weeks), last["week"], last["tok"], wow))
    return 0


if __name__ == "__main__":
    sys.exit(main())
