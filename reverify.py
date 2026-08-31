"""Rotating full re-verification - the pass that actually keeps data fresh.

update_data.py can only see the last 7 days, so a figure that went stale in
March is invisible to it forever. This pass asks, with no time window at all,
"what is X's ARR / valuation / own-model status *today*", for the K entities
whose provenance is oldest. One API call per entity, so a single bad company
cannot corrupt the batch.

  python reverify.py [-k 10] [--dry-run]
  python reverify.py --all            # one-time backfill of the whole roster
  python reverify.py --only "Kimi"    # a single entity
"""
from __future__ import annotations

import json
import sys

import common

PASS = "reverify"
DEFAULT_K = 10

SYSTEM = """You are a financial data analyst re-verifying one company's entry in an
AI industry monitor dashboard, from scratch.

This is NOT a news sweep. Ignore how recently something was reported: find the
most recent credible figure that exists, whatever its date, and give that date
in `as_of`. A two-year-old number that is still the latest disclosure is the
right answer, and its `as_of` is two years ago.

Rules:
- Every patch needs a real source (Bloomberg, The Information, Reuters, CNBC,
  TechCrunch, Sacra, a company announcement, an official blog) plus the date
  that source reported it.
- When a company's OWN official disclosure conflicts with a media estimate,
  use the official figure, and say in `notes` what the media number was and
  why you did not use it. Do not prefer the larger number.
- {units}
- Emit a patch ONLY where your verified value differs from the current value
  shown below. If a field is already right, leave it out.
- If you cannot verify a field at all, leave it out. Never guess to fill a gap.
- Numeric fields go in `metric_patches`, text/enum fields in `text_patches`,
  own-model status in `own_model_patches`. An array with nothing to say stays
  empty. Echo `name` exactly as given."""

USER = """Today is {today}. Re-verify this entry.

section: {section}
name: {name}

Current stored values (verify each; patch only what is actually different):
{current}

Provenance currently on file (may be missing or old):
{prov}

Establish, as of today:
1. Latest ARR / annualised revenue -> `arr` ($M)
2. Latest valuation, and only from a CLOSED round or a completed secondary /
   listed market cap -> `val` ($B). An unclosed or rumoured round is not a
   valuation; skip the field and note it.
3. Year-over-year revenue growth -> `arrg` (%)
{extra_fields}
Also sanity-check the descriptive fields: `uc` should name the company's
current flagship products in Chinese, short (under ~30 chars) - if it still
names a product generation that has been superseded, patch it."""

APP_EXTRA = """4. Own-model status -> one entry in `own_model_patches`: `status` is "none"
   (all third-party API), "hybrid" (own model shipped but most traffic still
   third-party), or "primary" (most inference now runs on models it trained
   itself); `models` lists their names; `token_share` is the disclosed share of
   token calls served by its own models, as text, or an empty string if it has
   never been disclosed. Only emit this if the status differs from what is
   stored above. Search specifically for a disclosed share ("X% of tokens",
   "majority of requests", "most of our inference") - that share is the single
   most useful number here, so look for it before settling for an empty
   token_share. Use "primary" when the company says its own models serve most
   inference, not merely that they exist.
7. Patchable fields here are only: arr, arrg, mau, maug, val, uc, cat, stage,
   biz, ti, ownModel. `tokM`/`tokG` do not exist on an app.
5. Monthly active users -> `mau` (millions), and `maug` (%)
6. `cat` (one of: {cats}), `stage` (pmf|growth|scale), `biz`
   (B2B|B2C|B2B+B2C|B2C+B2B), `ti` token intensity (low|med|high|ultra)
"""
MODEL_EXTRA = """4. Monthly inference token volume -> `tokM` (trillions/month), `tokG` (%)
5. `region` (US|CN|EU)

For a model provider, `arr` means annualised revenue from serving models -
model API plus first-party model subscriptions. It EXCLUDES compute or
infrastructure leasing, cloud/hosting resale, hardware, and any unrelated
revenue of a parent company or sibling division. If the only figure you can
find is a blended segment that mixes model revenue with compute leasing,
do NOT patch `arr`; say so in `notes` instead.
Patchable fields here are only: arr, arrg, tokM, tokG, trainPerRun,
runsPerYear, val, uc, region. `mau` does not exist on a model provider.
"""

SCHEMA = {
    "type": "object",
    "properties": dict(common.patch_properties(with_section=False), **{
        "name": {"type": "string"},
        "notes": {"type": "string", "description": "what you could not verify, and why; Chinese"},
    }),
    "required": ["name", "notes", "metric_patches", "text_patches",
                 "own_model_patches"],
    "additionalProperties": False,
}

SHOWN_FIELDS = ("uc", "arr", "arrg", "val", "mau", "maug", "tokM", "tokG",
                "cat", "stage", "biz", "ti", "region", "ownModel")


def _snapshot(entity: dict) -> str:
    rows = []
    for field in SHOWN_FIELDS:
        if field in entity:
            rows.append("  {}: {}".format(
                field, json.dumps(entity[field], ensure_ascii=False)))
    return "\n".join(rows)


def _prov(entity: dict) -> str:
    prov = entity.get("prov") or {}
    if not prov:
        return "  (none - this entry has never been verified)"
    return "\n".join(
        "  {}: as_of {} via {}".format(f, p.get("as_of"), p.get("source"))
        for f, p in sorted(prov.items()))


def _arg(flag: str, default):
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return default


def main() -> int:
    dry = "--dry-run" in sys.argv
    backfill = "--all" in sys.argv
    only = _arg("--only", None)
    k = int(_arg("-k", DEFAULT_K))

    data = common.load()

    if only:
        targets = []
        for section in ("models", "apps"):
            entity, err = common.resolve(data[section], only)
            if entity:
                targets = [(section, entity)]
                break
        if not targets:
            print("no entity matches {!r}".format(only), file=sys.stderr)
            return 1
    elif backfill:
        targets = [(s, e) for s, e in common.iter_entities(data)
                   if not e.get("retired")]
    else:
        targets = common.reverify_targets(data, k)

    print("=== {} pass: {} (model={}){}{} ===".format(
        PASS, common.today(), common.MODEL,
        " [DRY RUN]" if dry else "", " [BACKFILL]" if backfill else ""))
    print("{} entit{} queued: {}".format(
        len(targets), "y" if len(targets) == 1 else "ies",
        ", ".join(e["name"] for _, e in targets)))

    all_patches, failures = [], []
    for section, entity in targets:
        print("\n-- {} ({})".format(entity["name"], section))
        extra = (APP_EXTRA.format(cats="|".join(sorted(common.CATEGORIES)))
                 if section == "apps" else MODEL_EXTRA)
        try:
            result = common.ask(
                system=SYSTEM.format(units=common.UNITS_RULE),
                user=USER.format(today=common.today(), section=section,
                                 name=entity["name"], current=_snapshot(entity),
                                 prov=_prov(entity), extra_fields=extra),
                schema=SCHEMA, max_uses=8, max_tokens=8000)
        except Exception as exc:  # noqa: BLE001
            print("   FAILED: {}".format(exc), file=sys.stderr)
            failures.append("{}: {}".format(entity["name"], exc))
            continue
        found = common.flatten_patches(result, section=section)
        # The model is told which entity it is looking at, but pin the identity
        # anyway so a mislabelled patch can never hit the wrong row.
        for patch in found:
            patch["section"] = section
            patch["name"] = entity["name"]
        print("   {} patch(es); notes: {}".format(len(found), result.get("notes", "")))
        all_patches.extend(found)

    applied, rejected = common.apply_patches(
        data, all_patches, PASS, dry_run=dry, allow_magnitude=backfill)
    common.recompute_momentum(data)
    common.note_updates(data, applied)
    common.record_run(data, PASS, ok=bool(targets) and len(failures) < len(targets),
                      error="; ".join(failures) if failures else None,
                      checked=len(targets) - len(failures), failed=len(failures),
                      applied=len(applied), rejected=len(rejected))
    if not dry:
        common.save(data)

    print("\nchecked {} / failed {} / applied {} / rejected {}".format(
        len(targets) - len(failures), len(failures), len(applied), len(rejected)))
    for line in applied:
        print("  + {}".format(line))
    for line in rejected:
        print("  - {}".format(line))

    # A single flaky company should not turn the whole run red; a total
    # wipeout should.
    return 1 if targets and len(failures) == len(targets) else 0


if __name__ == "__main__":
    sys.exit(main())
