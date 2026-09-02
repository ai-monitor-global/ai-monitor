"""Shared data layer, LLM plumbing, and the validation gate for AI Monitor.

Every pass (update_data / reverify / discover) writes through apply_patches()
here, so provenance capture, sanity gates and the changelog cannot be bypassed
by a single misbehaving prompt.

`import anthropic` is deliberately lazy so that migration, validation and the
offline self-test run on a machine with no SDK installed.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(REPO_DIR, "data.json")

# Sources and product names are routinely Chinese; a cp1252 console would raise
# UnicodeEncodeError mid-run and lose the report of what was applied.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not a tty
        pass

SCHEMA_VERSION = 2
MODEL = os.environ.get("CLAUDE_MODEL") or "claude-opus-5"
STALE_DAYS_DEFAULT = 120
CHANGELOG_MAX = 250   # a full 30-entity backfill produces ~110 entries
REVIEW_QUEUE_MAX = 60

# --------------------------------------------------------------------------
# Taxonomy. The frontend reads these out of data.json (meta.categories /
# meta.regions) and only renders a chip for a category that actually has
# members, so declaring a spare bucket here costs nothing.
# --------------------------------------------------------------------------
CATEGORIES = {
    "coding":     {"label": "Coding",        "fg": "#1d4ed8", "bg": "#dbeafe"},
    "search":     {"label": "Search",        "fg": "#15803d", "bg": "#dcfce7"},
    "creative":   {"label": "Creative",      "fg": "#9d174d", "bg": "#fce7f3"},
    "voice":      {"label": "Voice",         "fg": "#065f46", "bg": "#d1fae5"},
    "consumer":   {"label": "Consumer",      "fg": "#991b1b", "bg": "#fee2e2"},
    "enterprise": {"label": "Enterprise",    "fg": "#6d28d9", "bg": "#ede9fe"},
    "finance":    {"label": "Finance",       "fg": "#0f766e", "bg": "#ccfbf1"},
    "legal":      {"label": "Legal",         "fg": "#92400e", "bg": "#fef3c7"},
    "health":     {"label": "Health",        "fg": "#0e7490", "bg": "#cffafe"},
    "gtm":        {"label": "Marketing/GTM", "fg": "#c2410c", "bg": "#ffedd5"},
    "support":    {"label": "Support",       "fg": "#4338ca", "bg": "#e0e7ff"},
    "hr":         {"label": "HR/招聘", "fg": "#7e22ce", "bg": "#f3e8ff"},
    "security":   {"label": "Security",      "fg": "#b91c1c", "bg": "#ffe4e6"},
    "other":      {"label": "Other",         "fg": "#374151", "bg": "#f3f4f6"},
}

REGIONS = {
    "US": {"label": "\U0001F1FA\U0001F1F8 美国", "fg": "#1d4ed8", "bg": "#dbeafe"},
    "CN": {"label": "\U0001F1E8\U0001F1F3 中国", "fg": "#991b1b", "bg": "#fee2e2"},
    "EU": {"label": "\U0001F1EA\U0001F1FA 欧洲", "fg": "#6d28d9", "bg": "#ede9fe"},
}

# --------------------------------------------------------------------------
# Patchable fields. `m` is computed (recompute_momentum) and never patchable;
# `prov` is written by this module only.
# --------------------------------------------------------------------------
NUMERIC_FIELDS = {
    "models": {"arr", "arrg", "tokM", "tokG", "trainPerRun", "runsPerYear",
               "val", "valPending"},
    "apps":   {"arr", "arrg", "mau", "maug", "val", "valPending"},
}
QUALITATIVE_FIELDS = {
    "models": {"uc", "region", "listed", "parent"},
    "apps":   {"uc", "cat", "stage", "biz", "ti", "ownModel", "listed", "parent"},
}

# "EXCHANGE:TICKER" for a public company, "" for private. For a listed company
# `val` is its current market cap, which moves daily - so these are exempt from
# the recheck cooldown and refreshed every run.
LISTED_RE = re.compile(r"^[A-Za-z.]{2,10}:[A-Za-z0-9.\-]{1,10}$")

# Fields whose freshness the dashboard surfaces per-cell. Section-specific:
# a model provider has no ownModel, and treating it as a missing tracked field
# marked every model "never verified" forever.
PROV_TRACKED_BY_SECTION = {
    "models": ("arr", "val"),
    "apps":   ("arr", "val", "ownModel"),
}
PROV_TRACKED = ("arr", "val", "ownModel")  # union, for whitelist checks


def prov_fields(section: str) -> tuple:
    return PROV_TRACKED_BY_SECTION.get(section, PROV_TRACKED)


def section_of(data: dict, entity: dict) -> str:
    return "models" if any(e is entity for e in data.get("models", [])) else "apps"

BOUNDS = {
    "arr":         (0, 500000),   # $M
    "val":         (0, 5000),     # $B, last CLOSED round / market cap
    "valPending":  (0, 5000),     # $B, reported but unclosed round
    "mau":         (0, 5000),     # M users
    "tokM":        (0, 100000),   # T tokens / month
    "trainPerRun": (0, 10000),    # T tokens / run
    "runsPerYear": (0, 100),
    "arrg":        (-100, 5000),  # %
    "maug":        (-100, 5000),  # %
    "tokG":        (-100, 5000),  # %
}
# Level fields get the >5x / <0.2x magnitude gate. Growth *rates* legitimately
# swing by more than 5x (40% -> 400% is a real year), so they only get bounds.
LEVEL_FIELDS = {"arr", "val", "mau", "tokM", "trainPerRun"}
MAGNITUDE_MAX_RATIO = 5.0
MONEY_FIELDS = {"arr", "val"}

ENUMS = {
    "stage":  {"pmf", "growth", "scale"},
    "ti":     {"low", "med", "high", "ultra"},
    "biz":    {"B2B", "B2C", "B2B+B2C", "B2C+B2B"},
    "region": set(REGIONS),
    "cat":    set(CATEGORIES),
}
OWN_MODEL_STATUS = {"none", "hybrid", "primary"}

# `parent` names the company a lab sits inside ("Meta", "Baidu"), else "".
# An embedded lab has no valuation of its own. This is enforced in code
# rather than only requested in a prompt, because asking produced Baidu's
# entire $32.9B market cap on the ERNIE row.

# v1 lumped every vertical into a single `vertical` bucket. v2 splits it, so a
# blanket rename is impossible - these are its three members at the time of
# the split. Anything else unrecognised lands in `other` for reverify to fix.
VERTICAL_SPLIT = {"Harvey": "legal", "Sierra": "support", "Abridge": "health"}

# A source that quotes a non-USD figure must show its conversion, or the number
# lands in the review queue. This is the gate that would have caught Zhipu's
# `val: 55.9` (an RMB figure read as USD $B).
CURRENCY_RE = re.compile(
    "人民币|RMB|CNY|亿元|万元|(?<!美)元"
    "|￥|¥|€|EUR|欧元"
)
CONVERTED_RE = re.compile(
    "USD|美元|US\\$|\\$|汇率|exchange rate|converted|折合",
    re.I,
)


def today() -> date:
    return date.today()


# --------------------------------------------------------------------------
# Load / migrate / save
# --------------------------------------------------------------------------
def load(path: str = DATA_FILE) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return migrate(json.load(f))


SNAPSHOT_MAX = 160  # ~3 years of weekly points


def take_snapshot(data: dict) -> None:
    """One aggregate point per save-day: the tracked universe's totals.

    The quarterly series was hand-typed history no script could move; this is
    the opposite - derived from per-entity sourced values on every run, so it
    is weekly by construction and always on the same basis as the table.
    Coverage growth shows up as a step, so counts are stored with each point.
    """
    models, apps = active(data, "models"), active(data, "apps")
    point = {
        "date": str(today()),
        "mARR": round(sum(e.get("arr") or 0 for e in models) / 1000, 2),  # $B
        "aARR": round(sum(e.get("arr") or 0 for e in apps) / 1000, 2),    # $B
        "tok":  round(sum(e.get("tokM") or 0 for e in models), 1),        # T/mo
        "nM": len(models), "nA": len(apps),
        "nMrev": sum(1 for e in models if e.get("arr") is not None),
    }
    snaps = data.setdefault("snapshots", [])
    if snaps and snaps[-1]["date"] == point["date"]:
        snaps[-1] = point  # several runs a day collapse to the latest
    else:
        snaps.append(point)
    del snaps[:-SNAPSHOT_MAX]


def save(data: dict, path: str = DATA_FILE) -> None:
    take_snapshot(data)
    trim(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def trim(data: dict) -> None:
    meta = data.setdefault("meta", {})
    if len(meta.get("changelog", [])) > CHANGELOG_MAX:
        meta["changelog"] = meta["changelog"][-CHANGELOG_MAX:]
    if len(meta.get("review_queue", [])) > REVIEW_QUEUE_MAX:
        meta["review_queue"] = meta["review_queue"][-REVIEW_QUEUE_MAX:]


def migrate(data: dict) -> dict:
    """Bring a v1 file up to schema v2. Idempotent: safe to run every pass."""
    meta = data.setdefault("meta", {})
    meta["schema"] = SCHEMA_VERSION
    meta.setdefault("last_updated", str(today()))
    meta.setdefault("last_run", meta["last_updated"])
    meta.setdefault("stale_days", STALE_DAYS_DEFAULT)
    meta["categories"] = CATEGORIES
    meta["regions"] = REGIONS
    meta.setdefault("changelog", [])
    meta.setdefault("review_queue", [])
    runs = meta.setdefault("runs", {})
    for name in ("incremental", "reverify", "discovery", "progress"):
        runs.setdefault(name, {"at": None, "ok": None, "error": None})

    data.setdefault("candidates", [])
    data.setdefault("models", [])
    data.setdefault("apps", [])

    # The historical quarterly series used to be literal arrays inside
    # index.html, which meant no script could ever move them. Seed once.
    if "series" not in data:
        data["series"] = {
            "qtr": ["Q1'23", "Q2'23", "Q3'23", "Q4'23", "Q1'24", "Q2'24", "Q3'24",
                    "Q4'24", "Q1'25", "Q2'25", "Q3'25", "Q4'25", "Q1'26"],
            "mARR":  [0.7, 1.2, 1.9, 2.8, 4.5, 7.0, 10.0, 14.5, 21.0, 30.0, 38.5, 47.0, 48.5],
            "aARR":  [0.06, 0.15, 0.28, 0.6, 1.1, 2.0, 3.4, 5.2, 7.8, 10.5, 13.5, 17.0, 18.5],
            "tokUS": [0.4, 0.8, 1.4, 2.4, 4.0, 6.5, 10.0, 15.0, 22.0, 30.0, 38.0, 45.0, 49.0],
            "tokCN": [0.02, 0.05, 0.12, 0.25, 0.6, 1.2, 2.2, 3.8, 6.0, 8.5, 11.5, 14.5, 17.0],
        }

    # The token series is an early estimate on a different basis from the
    # sourced per-provider tokM (they disagree by ~80x), so the panel stays
    # withdrawn rather than showing the page contradicting itself. Set this
    # true once there is a sourced historical series.
    data["series"].setdefault("tokens_enabled", True)

    for section, entity in iter_entities(data):
        entity.setdefault("retired", False)
        entity.setdefault("listed", "")
        entity.setdefault("parent", "")
        entity.setdefault("valPending", None)
        entity.setdefault("prov", {})
        # prov.checked (when we last verified) is distinct from prov.as_of
        # (when the source reported it). Seed it from the entity's checked_at
        # for provenance written before that distinction existed.
        stamped = entity.get("checked_at")
        if stamped:
            for prov in (entity.get("prov") or {}).values():
                if isinstance(prov, dict) and not prov.get("checked"):
                    prov["checked"] = stamped
        if section == "apps":
            if "ownModel" not in entity:
                had = bool(entity.get("selfModel"))
                entity["ownModel"] = {
                    "status": "primary" if had else "none",
                    "tokenShare": None,
                    "models": [],
                }
            entity.pop("selfModel", None)
            if entity.get("cat") not in CATEGORIES:
                entity["cat"] = VERTICAL_SPLIT.get(entity.get("name"), "other")
    return data


def iter_entities(data: dict):
    for section in ("models", "apps"):
        for entity in data.get(section, []):
            yield section, entity


def active(data: dict, section: str) -> list:
    return [e for e in data.get(section, []) if not e.get("retired")]


# --------------------------------------------------------------------------
# Universe -> prompt. Derived from data.json, never hardcoded, so the roster
# and the researched list can no longer drift apart.
# --------------------------------------------------------------------------
def universe_block(data: dict) -> str:
    models = ", ".join(e["name"] for e in active(data, "models"))
    apps = ", ".join(e["name"] for e in active(data, "apps"))
    return "MODELS: {}\nAPPS: {}".format(models, apps)


def resolve(rows: list, name: str):
    """Exact -> casefold-exact -> *unique* substring. Never a silent wrong row."""
    if not name:
        return None, "empty name"
    for row in rows:
        if row["name"] == name:
            return row, None
    key = name.casefold().strip()
    for row in rows:
        if row["name"].casefold().strip() == key:
            return row, None
    hits = [r for r in rows
            if key in r["name"].casefold() or r["name"].casefold() in key]
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, "no entity matches {!r}".format(name)
    return None, "{!r} is ambiguous: {}".format(name, [h["name"] for h in hits])


# --------------------------------------------------------------------------
# The validation gate
# --------------------------------------------------------------------------
def _parse_day(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _check_own_model(value):
    if not isinstance(value, dict):
        return "ownModel must be an object"
    status = value.get("status")
    if status not in OWN_MODEL_STATUS:
        return "ownModel.status must be one of {}".format(sorted(OWN_MODEL_STATUS))
    share = value.get("tokenShare")
    if share is not None:
        if isinstance(share, bool) or not isinstance(share, (int, float)) \
                or not 0 <= share <= 100:
            return "ownModel.tokenShare must be null or 0-100"
        if status == "none" and share > 0:
            return "ownModel.status=none contradicts tokenShare>0"
    names = value.get("models", [])
    if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
        return "ownModel.models must be a list of strings"
    return None


def _failures(section: str, entity: dict, field: str, new, patch: dict):
    """Every reason this patch should not land, as a list of (reason, code).

    Deliberately collects instead of returning on the first hit: the backfill
    may waive the "magnitude" code, and short-circuiting would let a waived
    magnitude failure hide a currency or bounds failure that runs after it.
    """
    out = []

    def bad(reason, code="other"):
        out.append((reason, code))

    if field not in NUMERIC_FIELDS[section] | QUALITATIVE_FIELDS[section]:
        bad("field {!r} is not patchable on {}".format(field, section))
        return out  # nothing else is meaningful for an unknown field

    source = str(patch.get("source") or "").strip()
    if not source:
        bad("missing source")
    if str(patch.get("conf", "")).lower() not in ("high", "medium"):
        bad("confidence {!r} is not high/medium".format(patch.get("conf")))

    as_of = _parse_day(patch.get("as_of"))
    if as_of is None:
        bad("as_of {!r} is not YYYY-MM-DD".format(patch.get("as_of")))
    else:
        if as_of > today() + timedelta(days=1):
            bad("as_of {} is in the future".format(as_of))
        prior = _parse_day((entity.get("prov", {}).get(field) or {}).get("as_of"))
        if prior and as_of < prior:
            bad("as_of {} is older than the value already on file ({})".format(
                as_of, prior), "backdate")

    if field in NUMERIC_FIELDS[section]:
        if isinstance(new, bool) or not isinstance(new, (int, float)):
            bad("{} must be a number, got {!r}".format(field, new))
            return out
        low, high = BOUNDS.get(field, (-math.inf, math.inf))
        if not low <= new <= high:
            bad("{}={} is outside the plausible range [{}, {}]".format(
                field, new, low, high))
        if field == "val" and str(entity.get("parent") or "").strip():
            bad("{} sits inside {}, which has no valuation of its own - "
                "do not record the parent's".format(
                    entity.get("name"), entity.get("parent")))
        if field in MONEY_FIELDS and CURRENCY_RE.search(source) \
                and not CONVERTED_RE.search(source):
            bad("source quotes a non-USD figure with no conversion shown; "
                "{} must be USD ({})".format(
                    field, "$M" if field == "arr" else "$B"))
        old = entity.get(field)
        if field in LEVEL_FIELDS and isinstance(old, (int, float)) \
                and not isinstance(old, bool) and old > 0 and new > 0:
            ratio = new / old
            if ratio > MAGNITUDE_MAX_RATIO or ratio < 1 / MAGNITUDE_MAX_RATIO:
                bad("{} {} -> {} is a {:.1f}x jump (>{:g}x needs a human)".format(
                    field, old, new, ratio, MAGNITUDE_MAX_RATIO), "magnitude")
        return out

    # qualitative
    if field == "ownModel":
        problem = _check_own_model(new)
        if problem:
            bad(problem)
        return out
    if field == "parent":
        if not isinstance(new, str):
            bad("parent must be a string")
        elif new.strip() and entity.get("val") is not None:
            bad("parent={!r} but val is set; an embedded lab has no "
                "valuation of its own".format(new))
        return out
    if field == "listed":
        if not isinstance(new, str):
            bad("listed must be a string")
        elif new.strip() and not LISTED_RE.match(new.strip()):
            bad("listed must look like 'HKEX:2513' or be an empty string, "
                "got {!r}".format(new))
        return out
    if not isinstance(new, str) or not new.strip():
        bad("{} must be a non-empty string".format(field))
        return out
    if field in ENUMS and new not in ENUMS[field]:
        bad("{}={!r} is not one of {}".format(field, new, sorted(ENUMS[field])))
    return out


def apply_patches(data: dict, patches: list, pass_name: str, dry_run: bool = False,
                  allow_magnitude: bool = False):
    """Validate, then apply. Returns (applied, rejected) as printable strings.

    allow_magnitude=True waives the two correction-class gates - the >5x
    magnitude gate and the as_of-monotonicity gate - because a deliberate
    correction of a wrong entry may be both a large jump and carry an OLDER
    as_of than the bad record it replaces (a mis-recorded unclosed round has a
    newer date than the real closed one). Only `--all` backfills and explicit
    `--force` runs set it; such patches are flagged `forced` in the changelog.
    All other gates (source, currency, bounds, enums, parent) still apply.
    """
    meta = data.setdefault("meta", {})
    stamp = str(today())
    applied, rejected = [], []

    for patch in patches or []:
        section = "models" if patch.get("section") == "models" else "apps"
        name, field = patch.get("name"), patch.get("field")
        new = patch.get("new_value")

        entity, err = resolve(data.get(section, []), name or "")
        if err:
            rejected.append("{}.{}: {}".format(name, field, err))
            _queue(meta, stamp, name, field, new, err, patch, pass_name)
            continue

        failures = _failures(section, entity, field, new, patch)
        waived = ([f for f in failures if f[1] in ("magnitude", "backdate")]
                  if allow_magnitude else [])
        blocking = [f for f in failures if f not in waived]
        if blocking:
            reason = "; ".join(r for r, _ in blocking)
            rejected.append("{}.{}: {}".format(entity["name"], field, reason))
            _queue(meta, stamp, entity["name"], field, new, reason, patch, pass_name)
            continue
        forced = bool(waived)
        if forced:
            print("  ! FORCED past magnitude gate: {}.{} -> {} ({})".format(
                entity["name"], field, new, patch.get("source")), file=sys.stderr)

        old = entity.get(field)
        if old == new:
            continue
        if not dry_run:
            entity[field] = new
            entity.setdefault("prov", {})[field] = {
                "as_of": str(_parse_day(patch["as_of"])),
                "checked": stamp,
                "source": str(patch["source"]).strip(),
                "url": str(patch.get("url") or "").strip() or None,
                "conf": str(patch["conf"]).lower(),
            }
            meta.setdefault("changelog", []).append({
                "date": stamp, "pass": pass_name, "section": section,
                "entity": entity["name"], "field": field,
                "old": old, "new": new,
                "source": str(patch["source"]).strip(),
                "conf": str(patch["conf"]).lower(),
                "forced": True if forced else None,
            })
        applied.append("{} {}.{}: {} -> {}{} ({})".format(
            section[:-1].upper(), entity["name"], field, old, new,
            " [FORCED]" if forced else "", patch["source"]))
    return applied, rejected


def apply_confirmations(data: dict, entity: dict, items: list, pass_name: str,
                        dry_run: bool = False):
    """Record provenance for fields that were checked and found already correct.

    Without this the freshness system never converges: a value that is right
    gets no source, so it reads as unverified forever, and reverify_targets
    keeps re-picking the same entities instead of rotating onward.
    """
    meta = data.setdefault("meta", {})
    stamp = str(today())
    confirmed, refused = [], []
    for item in items or []:
        field = item.get("field")
        if field not in PROV_TRACKED and field not in (
                NUMERIC_FIELDS["models"] | NUMERIC_FIELDS["apps"]
                | QUALITATIVE_FIELDS["models"] | QUALITATIVE_FIELDS["apps"]):
            refused.append("{}: unknown field {!r}".format(entity["name"], field))
            continue
        if field not in entity:
            refused.append("{}: {} is not set on this entity".format(
                entity["name"], field))
            continue
        source = str(item.get("source") or "").strip()
        as_of = _parse_day(item.get("as_of"))
        conf = str(item.get("conf", "")).lower()
        if not source or as_of is None or conf not in ("high", "medium"):
            refused.append("{}.{}: confirmation needs source + valid as_of + "
                           "conf".format(entity["name"], field))
            continue
        if as_of > today() + timedelta(days=1):
            refused.append("{}.{}: as_of {} is in the future".format(
                entity["name"], field, as_of))
            continue
        existing = (entity.get("prov") or {}).get(field) or {}
        prior = _parse_day(existing.get("as_of"))
        if prior and as_of < prior:
            # keep the better as_of, but still record that we looked today
            if not dry_run and existing:
                existing["checked"] = stamp
            confirmed.append("{}.{} re-checked ({})".format(
                entity["name"], field, source))
            continue
        if not dry_run:
            entity.setdefault("prov", {})[field] = {
                "as_of": str(as_of), "checked": stamp, "source": source,
                "url": str(item.get("url") or "").strip() or None,
                "conf": conf,
            }
        confirmed.append("{}.{} confirmed unchanged ({})".format(
            entity["name"], field, source))
    if refused:
        for line in refused:
            _queue(meta, stamp, entity["name"], "confirmation", None, line,
                   {}, pass_name)
    return confirmed, refused


def _queue(meta, stamp, name, field, proposed, reason, patch, pass_name):
    meta.setdefault("review_queue", []).append({
        "date": stamp, "pass": pass_name, "entity": name, "field": field,
        "proposed": proposed, "reason": reason,
        "source": str(patch.get("source") or "").strip() or None,
        "url": str(patch.get("url") or "").strip() or None,
    })


# --------------------------------------------------------------------------
# Momentum. `m` is the dashboard's default sort key; it used to be a
# hand-typed number no script could touch, which does not survive going from
# 20 tracked apps to ~45. Now it is derived, with m_manual as an override.
# --------------------------------------------------------------------------
def _pct_ranks(values):
    known = sorted(v for v in values if v is not None)
    out = []
    for v in values:
        if v is None or not known:
            out.append(None)
        elif len(known) == 1:
            out.append(0.5)
        else:
            below = sum(1 for k in known if k < v)
            equal = sum(1 for k in known if k == v)
            out.append((below + (equal - 1) / 2) / (len(known) - 1))
    return out


def recompute_momentum(data: dict) -> None:
    for section in ("models", "apps"):
        rows = active(data, section)
        if not rows:
            continue
        growth = _pct_ranks([e.get("arrg") if _num(e.get("arrg")) else None
                             for e in rows])
        scale = _pct_ranks([math.log10(e["arr"]) if _num(e.get("arr")) else None
                            for e in rows])
        # Growth adjusted against valuation: arrg / (val*1000/arr). Aligns the
        # ranking with "look at growth-adjusted valuation, not static PE".
        adjusted = _pct_ranks([
            (e["arrg"] * e["arr"] / (e["val"] * 1000))
            if _num(e.get("arrg")) and _num(e.get("arr")) and _num(e.get("val"))
            else None
            for e in rows
        ])
        for i, entity in enumerate(rows):
            if entity.get("m_manual") is not None:
                entity["m"] = int(entity["m_manual"])
                continue
            parts = [(0.40, growth[i]), (0.30, scale[i]), (0.30, adjusted[i])]
            weight = sum(w for w, v in parts if v is not None)
            if not weight:
                continue
            score = sum(w * v for w, v in parts if v is not None) / weight
            entity["m"] = max(0, min(100, round(100 * score)))


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------
def oldest_checked(entity: dict, section: str = "apps"):
    """When did we last *verify* every tracked field? None = not all verified.

    Distinct from `as_of`, which is when the source reported the figure. A
    valuation last disclosed in 2022 can be both genuinely old and perfectly
    up to date - it is the latest that exists. Staleness is about how long
    since we looked, not how old the underlying fact is.
    """
    # A field with no value needs no source: Google Gemini has no standalone
    # valuation, so demanding one would mark it unverified forever.
    fields = [f for f in prov_fields(section) if entity.get(f) is not None]
    if not fields:
        return None
    days = []
    for field in fields:
        prov = (entity.get("prov") or {}).get(field) or {}
        day = _parse_day(prov.get("checked")) or _parse_day(prov.get("as_of"))
        if day:
            days.append(day)
    if len(days) < len(fields):
        return None
    return min(days)


RECHECK_COOLDOWN_DAYS = 21


def reverify_targets(data: dict, k: int):
    """Stalest first. Names above $500M ARR get a tighter 28-day SLA.

    A cooldown on `checked_at` keeps the queue rotating: some figures simply
    are not publicly sourceable, and without it those entities would score
    9999 forever and monopolise every run. The cooldown is relaxed only if it
    would otherwise leave the run with nothing to do.
    """
    scored = []
    for index, (section, entity) in enumerate(iter_entities(data)):
        if entity.get("retired"):
            continue
        last = oldest_checked(entity, section)
        age = 9999 if last is None else (today() - last).days
        big = (entity.get("arr") or 0) >= 500
        effective = age * 2.0 if (big and age >= 28) else float(age)
        checked = _parse_day(entity.get("checked_at"))
        # A listed company's `val` is its market cap and moves daily, so it is
        # never "recently enough" checked.
        cooling = bool(checked and not str(entity.get("listed") or "").strip()
                       and (today() - checked).days < RECHECK_COOLDOWN_DAYS)
        scored.append((cooling, -effective, index, section, entity))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(s, e) for _, _, _, s, e in scored[:k]]


# --------------------------------------------------------------------------
# Run bookkeeping. meta.last_updated only moves when something actually
# landed, so the "数据截至" badge can no longer claim freshness after a
# failed or empty run.
# --------------------------------------------------------------------------
def record_run(data: dict, pass_name: str, ok: bool, error=None, **extra) -> None:
    meta = data.setdefault("meta", {})
    entry = {"at": str(today()), "ok": bool(ok),
             "error": (str(error)[:200] if error else None)}
    entry.update(extra)
    meta.setdefault("runs", {})[pass_name] = entry
    meta["last_run"] = str(today())


def note_updates(data: dict, applied: list) -> None:
    meta = data.setdefault("meta", {})
    if not applied:
        return
    meta["last_updated"] = str(today())
    meta["update_notes"] = "{}: {} 项已核实更新 — {}".format(
        today(), len(applied),
        "; ".join(a.split(" (")[0] for a in applied[:6]))


# --------------------------------------------------------------------------
# LLM plumbing
# --------------------------------------------------------------------------
WEB_SEARCH_TOOL_TYPE = "web_search_20260209"


def _web_search(max_uses: int) -> dict:
    return {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": max_uses}


def _extract_json(blocks) -> dict:
    """output_config.format guarantees valid JSON in a text block, but web
    search interleaves commentary, so try the last block, then the join."""
    texts = [b.text for b in blocks
             if getattr(b, "type", None) == "text" and getattr(b, "text", "")]
    candidates = ([texts[-1]] if texts else []) + ["".join(texts)]
    for candidate in candidates:
        s = re.sub(r"^```(?:json)?\s*", "", candidate.strip())
        s = re.sub(r"\s*```$", "", s).strip()
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            start, end = s.find("{"), s.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(s[start:end + 1])
                except json.JSONDecodeError:
                    pass
    kinds = [getattr(b, "type", "?") for b in blocks]
    raise ValueError("no JSON object in response (blocks={})".format(kinds))


def _check_search_errors(blocks) -> None:
    """Server-tool failures come back HTTP 200: on success `.content` is a
    list of results, on error it is a single error object."""
    for block in blocks:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        if isinstance(content, list):
            continue
        code = getattr(content, "error_code", None) or getattr(content, "type", "unknown")
        raise RuntimeError("web_search failed: {}".format(code))


def ask(system: str, user: str, schema: dict, max_uses: int = 8,
        max_tokens: int = 12000, effort: str = "high") -> dict:
    """One structured, web-searching call. Raises on refusal or tool failure."""
    import anthropic  # lazy: offline tooling must not need the SDK

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    # Escape hatch: if constraining the response format ever conflicts with the
    # server-side search tool, set DISABLE_STRUCTURED_OUTPUT=1 to fall back to
    # asking for JSON in the prompt. _extract_json handles both, and the
    # validation gate is unaffected either way.
    output_config = {"effort": effort}
    if os.environ.get("DISABLE_STRUCTURED_OUTPUT") in (None, "", "0"):
        output_config["format"] = {"type": "json_schema", "schema": schema}
    else:
        system = system + ("\n\nReturn ONLY a JSON object matching this schema, "
                           "with no prose and no markdown fences:\n"
                           + json.dumps(schema, ensure_ascii=False))

    response = anthropic.Anthropic(api_key=api_key).messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        output_config=output_config,
        tools=[_web_search(max_uses)],
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        raise RuntimeError("model refused the request ({})".format(
            getattr(detail, "category", "?")))
    _check_search_errors(response.content)
    usage = response.usage
    print("  [{}] in={} out={} stop={}".format(
        MODEL, usage.input_tokens, usage.output_tokens, response.stop_reason),
        file=sys.stderr)
    return _extract_json(response.content)


# --------------------------------------------------------------------------
# Response schemas.
#
# Structured outputs compile the schema into a closed grammar, so every
# subschema needs a concrete `type`. A single `new_value` that could be a
# number, a string or an object has no such type - that is a 400, not a
# warning. So patches are split by value type instead, and "unknown" is
# encoded as an empty string rather than a nullable/union type (neither
# `["number","null"]` nor `anyOf` is used anywhere here).
# --------------------------------------------------------------------------
_PROV_PROPS = {
    "as_of":  {"type": "string", "description": "YYYY-MM-DD the figure was reported"},
    "source": {"type": "string", "description": "publication + date; state the FX conversion if the figure was not quoted in USD"},
    "url":    {"type": "string", "description": "link to the source, or an empty string"},
    "conf":   {"type": "string", "enum": ["high", "medium"]},
}
OPTIONAL_NUMBER = {
    "type": "string",
    "description": "a plain number as text, or an empty string when unknown/unsourced",
}


def _patch_item(value_schema: dict, with_section: bool) -> dict:
    props = {"name": {"type": "string",
                      "description": "exact name as it appears in the dataset"},
             "field": {"type": "string"},
             "new_value": value_schema}
    if with_section:
        props["section"] = {"type": "string", "enum": ["models", "apps"]}
    props.update(_PROV_PROPS)
    return {"type": "object", "properties": props,
            "required": sorted(props), "additionalProperties": False}


def _own_item(with_section: bool) -> dict:
    props = {
        "name": {"type": "string"},
        "status": {"type": "string", "enum": sorted(OWN_MODEL_STATUS)},
        "token_share": dict(OPTIONAL_NUMBER,
                            description="share of token calls served by its own "
                                        "models, 0-100 as text; empty if undisclosed"),
        "models": {"type": "array", "items": {"type": "string"}},
    }
    if with_section:
        props["section"] = {"type": "string", "enum": ["models", "apps"]}
    props.update(_PROV_PROPS)
    return {"type": "object", "properties": props,
            "required": sorted(props), "additionalProperties": False}


def patch_properties(with_section: bool = True) -> dict:
    """The three patch arrays every pass shares."""
    return {
        "metric_patches": {
            "type": "array",
            "description": "numeric fields: arr, arrg, val, mau, maug, tokM, "
                           "tokG, trainPerRun, runsPerYear",
            "items": _patch_item({"type": "number"}, with_section),
        },
        "text_patches": {
            "type": "array",
            "description": "text/enum fields: uc, cat, stage, biz, ti, region",
            "items": _patch_item({"type": "string"}, with_section),
        },
        "own_model_patches": {
            "type": "array",
            "description": "own-model status for an app (apps only)",
            "items": _own_item(with_section),
        },
    }


def opt_number(raw):
    """'' -> None, '70' -> 70.0. Used for every optional numeric in a response."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text.lower() in ("null", "none", "n/a", "unknown", "-"):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def flatten_patches(result: dict, section: str = None) -> list:
    """Fold the three typed arrays back into one uniform patch list."""
    out = []
    for key in ("metric_patches", "text_patches"):
        for item in result.get(key) or []:
            patch = dict(item)
            if section:
                patch["section"] = section
            out.append(patch)
    for item in result.get("own_model_patches") or []:
        patch = {k: item.get(k) for k in ("name", "as_of", "source", "url", "conf")}
        patch["section"] = section or item.get("section") or "apps"
        patch["field"] = "ownModel"
        patch["new_value"] = {
            "status": item.get("status"),
            "tokenShare": opt_number(item.get("token_share")),
            "models": item.get("models") or [],
        }
        out.append(patch)
    return out

UNITS_RULE = (
    "Units are absolute: arr in USD millions ($M), val in USD billions ($B), "
    "mau in millions of users, tokM in trillions of tokens per month, "
    "arrg/maug/tokG in percent. If a source quotes RMB, EUR or any non-USD "
    "figure you MUST convert to USD and say so in `source` (e.g. 'Reuters "
    "2026-07-02, RMB 40B converted at 7.1 = $5.6B'). A figure whose currency "
    "you cannot establish must be omitted, not guessed. Never emit a field you "
    "could not source; omitting is always correct. "
    "`arr` means CURRENT RUN-RATE ARR: the company's own stated ARR, or its "
    "most recent single month x12 / single quarter x4. For a company growing "
    "several hundred percent a year, a half-year or full-year total averaged "
    "into a rate badly understates the present pace - NEVER compute arr as "
    "H1x2 or FYx1 when any more recent run-rate exists. Before converting any "
    "revenue figure, resolve WHAT PERIOD it covers (one month? a quarter? "
    "cumulative year-to-date?); if the period is ambiguous, do not use it - a "
    "monthly figure written into an annual field is a 12x error. State the "
    "period and the x12/x4 arithmetic in `source`. METRIC BEFORE RECENCY: "
    "a cumulative year-to-date total is NOT an ARR and can never fill `arr`, "
    "no matter how recent - first keep only figures that are the right metric "
    "for the field, THEN pick the newest among those. A newer figure of the "
    "wrong metric never beats an older figure of the right one."
)
