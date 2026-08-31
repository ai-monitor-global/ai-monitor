"""Weekly incremental pass: what changed in the last 7 days.

This is the news-driven differ. It is deliberately narrow - it only catches
things that were *reported this week*. Correcting a number that went stale
months ago is reverify.py's job, not this one's.

  python update_data.py [--dry-run]
"""
from __future__ import annotations

import sys

import common

PASS = "incremental"
WINDOW_DAYS = 7

SYSTEM = """You are a financial data analyst maintaining an AI industry monitor dashboard.

Rules:
- Only report a change you can attribute to a credible source (Bloomberg, The
  Information, Reuters, CNBC, TechCrunch, Sacra, a company announcement, an
  official blog). No speculation, no "reportedly in talks" as a confirmed figure.
- A round that is rumoured or unclosed is NOT a valuation change. Say so in the
  summary instead of patching `val`.
- {units}
- Use the exact entity name from the dataset in `name`, and set `section` to
  "models" or "apps" to match where it appears.
- Emit nothing rather than something you are unsure of. An empty patch list is
  a perfectly good answer."""

USER = """Today is {today}. Search for AI company news from the past {window} days
that changes any tracked metric below.

Tracked universe (patch only these; do not invent new companies here):
{universe}

Patchable fields
  models: arr, arrg, tokM, tokG, trainPerRun, runsPerYear, val
  apps:   arr, arrg, mau, maug, val, uc, cat, stage, biz, ti, ownModel

Look for: closed funding rounds, ARR/revenue milestones, valuation marks,
token-volume disclosures, and a launch that means an app now serves a
meaningful share of its traffic from its own model (patch `ownModel`).

`ownModel` value shape: {{"status": "none"|"hybrid"|"primary", "tokenShare":
<0-100 or null>, "models": ["name", ...]}} - "primary" means most inference is
now its own model."""

SCHEMA = {
    "type": "object",
    "properties": {
        "has_updates": {"type": "boolean"},
        "summary": {"type": "string", "description": "max 200 chars, Chinese"},
        "patches": {"type": "array", "items": common.PATCH_SCHEMA},
    },
    "required": ["has_updates", "summary", "patches"],
    "additionalProperties": False,
}


def main() -> int:
    dry = "--dry-run" in sys.argv
    data = common.load()
    print("=== {} pass: {} (model={}){} ===".format(
        PASS, common.today(), common.MODEL, " [DRY RUN]" if dry else ""))

    try:
        result = common.ask(
            system=SYSTEM.format(units=common.UNITS_RULE),
            user=USER.format(today=common.today(), window=WINDOW_DAYS,
                             universe=common.universe_block(data)),
            schema=SCHEMA, max_uses=12, max_tokens=12000)
    except Exception as exc:  # noqa: BLE001 - any failure must stay visible
        print("FAILED: {}".format(exc), file=sys.stderr)
        common.record_run(data, PASS, ok=False, error=exc)
        if not dry:
            common.save(data)
        return 1

    patches = result.get("patches") or []
    applied, rejected = common.apply_patches(data, patches, PASS, dry_run=dry)
    common.recompute_momentum(data)
    common.note_updates(data, applied)
    common.record_run(data, PASS, ok=True, applied=len(applied),
                      rejected=len(rejected), proposed=len(patches))
    if not dry:
        common.save(data)

    print("\nsummary: {}".format(result.get("summary", "")))
    print("proposed {} / applied {} / rejected {}".format(
        len(patches), len(applied), len(rejected)))
    for line in applied:
        print("  + {}".format(line))
    for line in rejected:
        print("  - {}".format(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
