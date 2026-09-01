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
`arr` ($M, a real number, mandatory); `val` ($B); `arrg` (%); `mau`
(millions); `maug` (%) - these four are text: give the number as text, or an
EMPTY STRING when you could not source it. Never write "null" or a guess.
`ownModel` = {{"status": "none"|"hybrid"|"primary", "token_share": "<0-100 as
text, empty if undisclosed>", "models": [...]}}; `aiNative` and `independent`
booleans; `why` = one Chinese sentence on why it matters; and `source` / `url`
/ `as_of` / `conf`.

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
        # Unknowns are empty strings, not nulls: a union/nullable type has no
        # single concrete type and structured outputs rejects it.
        "arr":   {"type": "number", "description": "annualised revenue in $M; mandatory"},
        "val":   dict(common.OPTIONAL_NUMBER, description="valuation in $B from a CLOSED round; empty if none"),
        "arrg":  dict(common.OPTIONAL_NUMBER, description="revenue YoY growth %; empty if unknown"),
        "mau":   dict(common.OPTIONAL_NUMBER, description="monthly actives in millions; empty if unknown"),
        "maug":  dict(common.OPTIONAL_NUMBER, description="MAU growth %; empty if unknown"),
        "ownModel": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": sorted(common.OWN_MODEL_STATUS)},
                "token_share": dict(common.OPTIONAL_NUMBER, description="0-100 as text; empty if undisclosed"),
                "models": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "token_share", "models"],
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


def normalise(cand: dict) -> dict:
    """Coerce the response's empty-string-means-unknown numerics into real
    numbers/None, and rename token_share to the stored `tokenShare`."""
    out = dict(cand)
    for field in ("arr", "val", "arrg", "mau", "maug",
                  "tokM", "tokG", "trainPerRun", "runsPerYear"):
        if field in out:
            out[field] = common.opt_number(out.get(field))
    own = dict(out.get("ownModel") or {})
    if "token_share" in own:
        own["tokenShare"] = common.opt_number(own.pop("token_share"))
    own.setdefault("status", "none")
    own.setdefault("tokenShare", None)
    own.setdefault("models", [])
    out["ownModel"] = own
    return out


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


# --------------------------------------------------------------------------
# Explicit adds. Discovery only searches for AI-native *applications*, so a
# model provider could never enter the roster no matter how important it got -
# which is how DeepSeek stayed missing. This researches named entities in
# either section and appends them with full provenance.
# --------------------------------------------------------------------------
MODEL_SEEDS = "DeepSeek, Alibaba Qwen, ByteDance Doubao, Meta (Llama), " \
              "Amazon (Nova), Baidu (ERNIE), Tencent (Hunyuan)"

ADD_SYSTEM = """You are a research analyst adding one company to an AI industry monitor
dashboard. Establish its current figures from scratch.

Rules:
- Every figure needs a real source (Bloomberg, The Information, Reuters,
  TechCrunch, Sacra, a company announcement, an official filing) with the date
  it reported the figure.
- RECENCY WINS: if several valuations or revenue figures exist, use the most
  recent by date, not the largest and not the most repeated.
- If the company is publicly LISTED, `val` is its CURRENT MARKET CAP and
  `listed` is "EXCHANGE:TICKER". Never use a pre-IPO round for a listed
  company.
- If the subject is a model, product line or division INSIDE a larger company
  (e.g. Doubao inside ByteDance, Nova inside Amazon, Llama inside Meta), it has
  no valuation of its own: leave `val` and `listed` empty. Do NOT put the
  parent company's valuation or market cap there - that is the parent's number,
  not this row's. The same applies to `arr`: report only revenue from serving
  models, never the parent's total.
- {units}
- For a model provider, `arr` is annualised revenue from serving models (API +
  first-party model subscriptions). It EXCLUDES compute/infrastructure leasing,
  cloud resale, hardware, and unrelated parent-company revenue. If only a
  blended segment figure exists, leave `arr` empty and explain in `notes`.
- Leave a field empty rather than guessing."""

ADD_USER = """Today is {today}. Research this company for the "{section}" table.

name: {name}

Return, with sources:
- `uc`: current flagship products, in Chinese, under ~30 chars
- `arr` ($M) and `arrg` (%)
- `val` ($B) and `listed`
{fields}
If this company is already better known under a different exact name, use the
name most readers would recognise."""

MODEL_ADD_FIELDS = """- `region` (US|CN|EU)
- `tokM` (trillions of tokens served per month) and `tokG` (%)
- `trainPerRun` (trillions of tokens per training run) and `runsPerYear`
"""
APP_ADD_FIELDS = """- `cat` (one of: {cats}), `stage` (pmf|growth|scale),
  `biz` (B2B|B2C|B2B+B2C|B2C+B2B), `ti` (low|med|high|ultra)
- `mau` (millions) and `maug` (%)
- `ownModel`: status (none|hybrid|primary), token_share, models
"""


def _add_schema(section: str) -> dict:
    props = {
        "name":   {"type": "string"},
        "uc":     {"type": "string"},
        "arr":    dict(common.OPTIONAL_NUMBER, description="$M; empty if unsourceable"),
        "arrg":   common.OPTIONAL_NUMBER,
        "val":    dict(common.OPTIONAL_NUMBER, description="$B; market cap if listed"),
        "listed": {"type": "string", "description": "EXCHANGE:TICKER, or empty if private"},
        "notes":  {"type": "string"},
        "source": {"type": "string"},
        "url":    {"type": "string"},
        "as_of":  {"type": "string"},
        "conf":   {"type": "string", "enum": ["high", "medium"]},
    }
    if section == "models":
        props.update({
            "region":      {"type": "string", "enum": sorted(common.REGIONS)},
            "tokM":        common.OPTIONAL_NUMBER,
            "tokG":        common.OPTIONAL_NUMBER,
            "trainPerRun": common.OPTIONAL_NUMBER,
            "runsPerYear": common.OPTIONAL_NUMBER,
        })
    else:
        props.update({
            "cat":   {"type": "string", "enum": sorted(common.CATEGORIES)},
            "stage": {"type": "string", "enum": sorted(common.ENUMS["stage"])},
            "biz":   {"type": "string", "enum": sorted(common.ENUMS["biz"])},
            "ti":    {"type": "string", "enum": sorted(common.ENUMS["ti"])},
            "mau":   common.OPTIONAL_NUMBER,
            "maug":  common.OPTIONAL_NUMBER,
            "ownModel": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": sorted(common.OWN_MODEL_STATUS)},
                    "token_share": common.OPTIONAL_NUMBER,
                    "models": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "token_share", "models"],
                "additionalProperties": False,
            },
        })
    return {"type": "object", "properties": props,
            "required": sorted(props), "additionalProperties": False}


def add_entities(data: dict, names: list, section: str, dry: bool) -> int:
    required = ("arr",)   # tokM is rarely disclosed; do not gate on it
    added, skipped = [], []
    for name in names:
        name = name.strip()
        if not name:
            continue
        existing, _ = common.resolve(data.get(section, []), name)
        if existing:
            skipped.append("{} (already tracked as {})".format(name, existing["name"]))
            continue
        print("\n-- researching {} ({})".format(name, section))
        fields = (MODEL_ADD_FIELDS if section == "models"
                  else APP_ADD_FIELDS.format(cats="|".join(sorted(common.CATEGORIES))))
        try:
            got = common.ask(
                system=ADD_SYSTEM.format(units=common.UNITS_RULE),
                user=ADD_USER.format(today=common.today(), section=section,
                                     name=name, fields=fields),
                schema=_add_schema(section), max_uses=10, max_tokens=8000)
        except Exception as exc:  # noqa: BLE001
            print("   FAILED: {}".format(exc), file=sys.stderr)
            skipped.append("{} (research failed)".format(name))
            continue
        got = normalise(got)
        missing = [f for f in required if not _num(got.get(f))]
        if missing:
            skipped.append("{} (no sourceable {})".format(name, ", ".join(missing)))
            print("   skipped: could not source {}".format(", ".join(missing)))
            continue
        entity = _to_entity(got, section)
        print("   {} | arr ${}M | val {} | {}".format(
            entity["name"], entity["arr"],
            "${}B".format(entity["val"]) if entity.get("val") is not None else "n/a",
            entity.get("listed") or "private"))
        if not dry:
            data[section].append(entity)
        added.append(entity["name"])
    print("\nadded {} / skipped {}".format(len(added), len(skipped)))
    for line in skipped:
        print("  .  {}".format(line))
    return len(added)


def _to_entity(got: dict, section: str) -> dict:
    prov = {"as_of": str(common._parse_day(got.get("as_of")) or common.today()),
            "checked": str(common.today()),
            "source": str(got.get("source") or "").strip() or "seeded",
            "url": str(got.get("url") or "").strip() or None,
            "conf": str(got.get("conf") or "medium").lower()}
    entity = {
        "name": got["name"].strip(),
        "uc": (got.get("uc") or "").strip() or got["name"].strip(),
        "arr": got.get("arr"), "arrg": got.get("arrg"),
        "val": got.get("val"), "listed": str(got.get("listed") or "").strip(),
        "m": 0, "retired": False, "checked_at": str(common.today()),
        "prov": {"arr": dict(prov)},
    }
    if got.get("val") is not None:
        entity["prov"]["val"] = dict(prov)
    if section == "models":
        entity.update({
            "region": got.get("region") or "US",
            "tokM": got.get("tokM"), "tokG": got.get("tokG"),
            "trainPerRun": got.get("trainPerRun"),
            "runsPerYear": got.get("runsPerYear"),
        })
    else:
        entity.update({
            "cat": got.get("cat") or "other", "stage": got.get("stage") or "growth",
            "biz": got.get("biz") or "B2B", "ti": got.get("ti") or "med",
            "mau": got.get("mau"), "maug": got.get("maug"),
            "ownModel": got.get("ownModel") or {
                "status": "none", "tokenShare": None, "models": []},
        })
        entity["prov"]["ownModel"] = dict(prov)
    return entity


def _arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> int:
    dry = "--dry-run" in sys.argv
    seed = "--seed" in sys.argv

    if "--add" in sys.argv or "--add-model-seeds" in sys.argv:
        data = common.load()
        section = _arg("--section", "models")
        names = (MODEL_SEEDS if "--add-model-seeds" in sys.argv
                 else _arg("--add", "")).split(",")
        print("=== add pass: {} (model={}){} ===".format(
            common.today(), common.MODEL, " [DRY RUN]" if dry else ""))
        n = add_entities(data, names, section, dry)
        common.recompute_momentum(data)
        common.record_run(data, PASS, ok=True, added=n)
        if not dry:
            common.save(data)
        return 0
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
    for raw in result.get("candidates") or []:
        cand = normalise(raw)
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
