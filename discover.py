"""Monthly coverage discovery - the pass that lets the roster grow.

Before this existed the updater could only mutate entities that were already
in data.json, so a newly important company could never enter the dashboard no
matter how big it got. This pass looks for what is missing, then applies the
written gate in CRITERIA.md: clears the hard bar -> promoted automatically,
grey zone -> parked in `candidates` for a human call.

  python discover.py [--dry-run]
  python discover.py --seed    # wider first sweep, to fill the known gaps
"""
from __future__ import annotations

import sys

import common

PASS = "discovery"

# CRITERIA.md thresholds. Keep the doc and these constants in sync.
HARD_ARR, HARD_VAL = 30.0, 2.0     # $M ARR / $B valuation -> auto-promote
GREY_ARR, GREY_VAL = 10.0, 0.5     # -> candidates queue
CONCENTRATION_LIMIT = 0.25

# Verticals the current roster covers thinly or not at all.
FOCUS = ("finance", "legal", "health", "gtm", "support", "hr", "security",
         "coding", "enterprise")
# Named explicitly because they are known gaps worth resolving on the first run.
SEED_HINTS = "Rogo, Clay, Hebbia, AlphaSense, Legora, EvenUp, OpenEvidence, " \
             "Ambience, Decagon, Cresta, Replit, Lovable, Windsurf, Mercor, 11x"

SYSTEM = """You are a research analyst deciding which AI-native companies belong on an
AI industry monitor dashboard.

Inclusion bar:
- AI-native: the product's core value comes from LLMs or generative models. A
  traditional software product with an AI feature bolted on does NOT qualify.
- Independent company, not a feature or division of a megacap.
- Materially sized: annualised revenue at or above ${hard_arr}M, OR a closed
  funding round in the last 12 months at or above ${hard_val}B.

Rules:
- Every company needs a real source (Bloomberg, The Information, Reuters,
  TechCrunch, Sacra, a company announcement) with the date it reported the
  figure.
- {units}
- `arr` is mandatory - a company whose revenue you cannot source at all is not
  yet evidence of anything. Leave it out entirely rather than guessing.
- Do not pad. Returning three well-sourced companies is better than twelve
  half-sourced ones. An empty list is a valid answer.
- Do not propose anything already in the tracked universe below."""

USER = """Today is {today}.

Already tracked (do NOT propose any of these):
{universe}

Find AI-native applications NOT in that list which clear the inclusion bar.
Weight your search toward the verticals this dashboard currently covers thinly
or not at all: {focus}.
{hints}
For each company return: exact `name`; `uc` = a short Chinese description of
what it does (under ~30 chars); `cat` from {cats}; `stage` (pmf|growth|scale);
`biz` (B2B|B2C|B2B+B2C|B2C+B2B); `ti` token intensity (low|med|high|ultra);
`arr` ($M, mandatory); `val` ($B, null if no closed round); `arrg` (%, null if
unknown); `mau` (millions, null if unknown); `maug` (%, null if unknown);
`ownModel` = {{"status": "none"|"hybrid"|"primary", "tokenShare": <0-100 or
null>, "models": [...]}}; `aiNative` and `independent` booleans; `why` = one
Chinese sentence on why it matters; and `source` / `url` / `as_of` / `conf`.

Separately, in `retire`, list any company in the tracked universe above that
has been acquired and no longer reports independently, or has shut down."""

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "name":  {"type": "string"},
        "uc":    {"type": "string"},
        "cat":   {"type": "string", "enum": sorted(common.CATEGORIES)},
        "stage": {"type": "string", "enum": sorted(common.ENUMS["stage"])},
        "biz":   {"type": "string", "enum": sorted(common.ENUMS["biz"])},
        "ti":    {"type": "string", "enum": sorted(common.ENUMS["ti"])},
        "arr":   {"type": "number"},
        "val":   {"type": ["number", "null"]},
        "arrg":  {"type": ["number", "null"]},
        "mau":   {"type": ["number", "null"]},
        "maug":  {"type": ["number", "null"]},
        "ownModel": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": sorted(common.OWN_MODEL_STATUS)},
                "tokenShare": {"type": ["number", "null"]},
                "models": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "tokenShare", "models"],
            "additionalProperties": False,
        },
        "aiNative":    {"type": "boolean"},
        "independent": {"type": "boolean"},
        "why":    {"type": "string"},
        "source": {"type": "string"},
        "url":    {"type": "string"},
        "as_of":  {"type": "string"},
        "conf":   {"type": "string", "enum": ["high", "medium"]},
    },
    "required": ["name", "uc", "cat", "stage", "biz", "ti", "arr", "val", "arrg",
                 "mau", "maug", "ownModel", "aiNative", "independent", "why",
                 "source", "url", "as_of", "conf"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": CANDIDATE_SCHEMA},
        "retire": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reason": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["name", "reason", "source", "url"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "description": "Chinese, max 200 chars"},
    },
    "required": ["candidates", "retire", "summary"],
    "additionalProperties": False,
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def gate(data: dict, cand: dict):
    """Return (verdict, reason) where verdict is promote / queue / skip."""
    name = str(cand.get("name") or "").strip()
    if not name:
        return "skip", "no name"

    for section in ("apps", "models"):
        existing, _ = common.resolve(data[section], name)
        if existing:
            return "skip", "already tracked as {}".format(existing["name"])
    for other in data.get("candidates") or []:
        if other.get("name", "").casefold() == name.casefold():
            return "skip", "already in the candidates queue"

    arr, val = cand.get("arr"), cand.get("val")
    if not cand.get("aiNative"):
        return "queue", "not clearly AI-native"
    if not cand.get("independent"):
        return "queue", "not an independent company"
    if not str(cand.get("source") or "").strip() or \
            common._parse_day(cand.get("as_of")) is None:
        return "skip", "unsourced or undated"

    hard = (_num(arr) and arr >= HARD_ARR) or (_num(val) and val >= HARD_VAL)
    grey = (_num(arr) and arr >= GREY_ARR) or (_num(val) and val >= GREY_VAL)
    if not hard:
        return ("queue", "grey zone") if grey else ("skip", "below the grey zone")
    if not (_num(arr) and arr > 0):
        # val cleared the bar but the frontend sums arr, so it cannot be null.
        return "queue", "clears the valuation bar but ARR is unsourced"
    if cand.get("cat") not in common.CATEGORIES:
        return "queue", "unknown category {!r}".format(cand.get("cat"))
    bad = common._check_own_model(cand.get("ownModel"))
    if bad:
        return "queue", bad
    return "promote", "ARR ${}M / val ${}B clears the hard bar".format(arr, val)


def to_entity(cand: dict) -> dict:
    prov = {"as_of": str(common._parse_day(cand["as_of"])),
            "source": str(cand["source"]).strip(),
            "url": str(cand.get("url") or "").strip() or None,
            "conf": str(cand["conf"]).lower()}
    entity = {
        "name": cand["name"].strip(),
        "uc": cand["uc"].strip(),
        "cat": cand["cat"],
        "stage": cand["stage"],
        "arr": cand["arr"],
        "arrg": cand.get("arrg"),
        "mau": cand.get("mau"),
        "maug": cand.get("maug"),
        "ti": cand["ti"],
        "biz": cand["biz"],
        "val": cand.get("val"),
        "m": 0,  # recompute_momentum fills this in
        "ownModel": cand["ownModel"],
        "retired": False,
        "prov": {"arr": dict(prov), "ownModel": dict(prov)},
    }
    if _num(cand.get("val")):
        entity["prov"]["val"] = dict(prov)
    return entity


def composition(data: dict):
    apps = common.active(data, "apps")
    counts = {}
    for app in apps:
        counts[app["cat"]] = counts.get(app["cat"], 0) + 1
    return [(cat, n, n / len(apps)) for cat, n in
            sorted(counts.items(), key=lambda kv: -kv[1])] if apps else []


def main() -> int:
    dry = "--dry-run" in sys.argv
    seed = "--seed" in sys.argv
    data = common.load()
    print("=== {} pass: {} (model={}){}{} ===".format(
        PASS, common.today(), common.MODEL,
        " [DRY RUN]" if dry else "", " [SEED]" if seed else ""))

    hints = ("Companies worth checking specifically, if they qualify: {}.\n"
             .format(SEED_HINTS) if seed else "")
    try:
        result = common.ask(
            system=SYSTEM.format(hard_arr=HARD_ARR, hard_val=HARD_VAL,
                                 units=common.UNITS_RULE),
            user=USER.format(today=common.today(),
                             universe=common.universe_block(data),
                             focus=", ".join(FOCUS), hints=hints,
                             cats="|".join(sorted(common.CATEGORIES))),
            schema=SCHEMA, max_uses=25 if seed else 14,
            max_tokens=16000)
    except Exception as exc:  # noqa: BLE001
        print("FAILED: {}".format(exc), file=sys.stderr)
        common.record_run(data, PASS, ok=False, error=exc)
        if not dry:
            common.save(data)
        return 1

    promoted, queued, skipped = [], [], []
    for cand in result.get("candidates") or []:
        verdict, reason = gate(data, cand)
        label = "{} ({})".format(cand.get("name"), reason)
        if verdict == "promote":
            promoted.append(label)
            if not dry:
                data["apps"].append(to_entity(cand))
        elif verdict == "queue":
            queued.append(label)
            if not dry:
                data.setdefault("candidates", []).append({
                    "name": cand.get("name"), "cat": cand.get("cat"),
                    "uc": cand.get("uc"), "why": cand.get("why"),
                    "arr": cand.get("arr"), "val": cand.get("val"),
                    "source": cand.get("source"), "url": cand.get("url"),
                    "as_of": cand.get("as_of"), "found_at": str(common.today()),
                    "reason": reason, "verdict": "pending",
                })
        else:
            skipped.append(label)

    # Retirement is never automatic - it is proposed for a human.
    for item in result.get("retire") or []:
        entity = None
        for section in ("models", "apps"):
            entity, _ = common.resolve(data[section], item.get("name") or "")
            if entity:
                break
        if entity and not entity.get("retired"):
            common._queue(data["meta"], str(common.today()), entity["name"],
                          "retired", True, "提议下架: {}".format(item.get("reason")),
                          item, PASS)

    common.recompute_momentum(data)
    if promoted:
        common.note_updates(data, ["APP {}".format(p) for p in promoted])
    common.record_run(data, PASS, ok=True, promoted=len(promoted),
                      queued=len(queued), skipped=len(skipped))
    if not dry:
        common.save(data)

    print("\nsummary: {}".format(result.get("summary", "")))
    print("promoted {} / queued {} / skipped {}".format(
        len(promoted), len(queued), len(skipped)))
    for line in promoted:
        print("  ++ {}".format(line))
    for line in queued:
        print("  ?  {}".format(line))
    for line in skipped:
        print("  .  {}".format(line))

    print("\ncomposition ({} active apps):".format(len(common.active(data, "apps"))))
    for cat, n, share in composition(data):
        flag = "  <-- over the {:.0%} limit".format(CONCENTRATION_LIMIT) \
            if share > CONCENTRATION_LIMIT else ""
        print("  {:<11} {:>3}  {:>5.1%}{}".format(cat, n, share, flag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
