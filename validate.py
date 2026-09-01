"""Offline checks for data.json. No API key, no network.

  python validate.py             # structural + sanity check, exit 1 on error
  python validate.py --selftest  # prove the validation gate rejects bad patches

CI runs this before the commit step, so a malformed data.json never reaches
GitHub Pages.
"""
from __future__ import annotations

import copy
import json
import os
import sys

import common

# The frontend sums these, so they may never be null.
# A model provider may have no separable revenue at all (Qwen inside
# Alibaba, Llama inside Meta): excluding those would leave the landscape
# view with holes where the biggest players belong.
REQUIRED_NUMERIC = {"models": (), "apps": ("arr",)}
# Everything else may legitimately be unknown on a newly discovered company.
OPTIONAL_NUMERIC = {
    # tokM is optional: hardly any provider discloses monthly token volume,
    # and requiring it kept DeepSeek out of the dashboard entirely.
    "models": ("arr", "arrg", "tokM", "tokG", "trainPerRun", "runsPerYear", "val"),
    "apps":   ("arrg", "mau", "maug", "val"),
}
CONCENTRATION_LIMIT = 0.25


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(data: dict):
    errors, warnings = [], []
    meta = data.get("meta") or {}

    if meta.get("schema") != common.SCHEMA_VERSION:
        errors.append("meta.schema is {!r}, expected {}".format(
            meta.get("schema"), common.SCHEMA_VERSION))
    for key in ("categories", "regions", "runs", "changelog", "review_queue",
                "stale_days", "last_updated", "last_run"):
        if key not in meta:
            errors.append("meta.{} is missing".format(key))

    categories = meta.get("categories") or {}
    regions = meta.get("regions") or {}

    for section in ("models", "apps"):
        rows = data.get(section)
        if not isinstance(rows, list) or not rows:
            errors.append("{} is empty or not a list".format(section))
            continue
        seen = set()
        for entity in rows:
            name = entity.get("name")
            tag = "{}[{}]".format(section, name)
            if not isinstance(name, str) or not name.strip():
                errors.append("{}: bad name".format(tag))
                continue
            if name in seen:
                errors.append("{}: duplicate name".format(tag))
            seen.add(name)

            if not isinstance(entity.get("uc"), str) or not entity["uc"].strip():
                errors.append("{}.uc must be a non-empty string".format(tag))
            if not isinstance(entity.get("retired"), bool):
                errors.append("{}.retired must be a bool".format(tag))
            if not isinstance(entity.get("prov"), dict):
                errors.append("{}.prov must be an object".format(tag))
            if str(entity.get("parent") or "").strip() and entity.get("val") is not None:
                errors.append("{}: parent={!r} but val={} - an embedded lab must not carry its parent's valuation".format(
                    tag, entity.get("parent"), entity.get("val")))
            if "selfModel" in entity:
                errors.append("{}: legacy selfModel survived migration".format(tag))
            m = entity.get("m")
            if not _is_num(m) or not 0 <= m <= 100:
                errors.append("{}.m must be 0-100, got {!r}".format(tag, m))

            for field in REQUIRED_NUMERIC[section]:
                if not _is_num(entity.get(field)):
                    errors.append("{}.{} is required and must be a number "
                                  "(got {!r})".format(tag, field, entity.get(field)))
            for field in OPTIONAL_NUMERIC[section]:
                v = entity.get(field)
                if v is not None and not _is_num(v):
                    errors.append("{}.{} must be a number or null, got {!r}".format(
                        tag, field, v))
            for field in REQUIRED_NUMERIC[section] + OPTIONAL_NUMERIC[section]:
                v = entity.get(field)
                if _is_num(v) and field in common.BOUNDS:
                    low, high = common.BOUNDS[field]
                    if not low <= v <= high:
                        errors.append("{}.{}={} is outside [{}, {}]".format(
                            tag, field, v, low, high))

            if section == "apps":
                if entity.get("cat") not in categories:
                    errors.append("{}.cat={!r} is not in meta.categories".format(
                        tag, entity.get("cat")))
                for field in ("stage", "ti", "biz"):
                    if entity.get(field) not in common.ENUMS.get(field, set()):
                        errors.append("{}.{}={!r} is not a valid value".format(
                            tag, field, entity.get(field)))
                bad = common._check_own_model(entity.get("ownModel"))
                if bad:
                    errors.append("{}: {}".format(tag, bad))
            else:
                if entity.get("region") not in regions:
                    errors.append("{}.region={!r} is not in meta.regions".format(
                        tag, entity.get("region")))

            for field, prov in (entity.get("prov") or {}).items():
                if not isinstance(prov, dict):
                    errors.append("{}.prov.{} must be an object".format(tag, field))
                    continue
                if common._parse_day(prov.get("as_of")) is None:
                    errors.append("{}.prov.{}.as_of={!r} is not YYYY-MM-DD".format(
                        tag, field, prov.get("as_of")))
                if not str(prov.get("source") or "").strip():
                    errors.append("{}.prov.{}.source is empty".format(tag, field))
                if str(prov.get("conf", "")).lower() not in ("high", "medium"):
                    errors.append("{}.prov.{}.conf={!r} is not high/medium".format(
                        tag, field, prov.get("conf")))
            missing = [f for f in common.prov_fields(section)
                       if entity.get(f) is not None
                       and not (entity.get("prov") or {}).get(f)]
            if missing:
                warnings.append("{}: no provenance yet for {}".format(
                    tag, ", ".join(missing)))

    series = data.get("series") or {}
    lengths = {k: len(v) for k, v in series.items() if isinstance(v, list)}
    if len(set(lengths.values())) > 1:
        errors.append("series arrays have mismatched lengths: {}".format(lengths))

    for cand in data.get("candidates") or []:
        if not str(cand.get("name") or "").strip():
            errors.append("candidate with no name")
        if cand.get("verdict") not in ("pending", "approved", "rejected"):
            errors.append("candidate {!r}: bad verdict {!r}".format(
                cand.get("name"), cand.get("verdict")))

    apps = common.active(data, "apps")
    if apps:
        counts = {}
        for app in apps:
            counts[app["cat"]] = counts.get(app["cat"], 0) + 1
        for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if n / len(apps) > CONCENTRATION_LIMIT:
                warnings.append("category concentration: {} is {}/{} = {:.0%} "
                                "(limit {:.0%})".format(cat, n, len(apps),
                                                        n / len(apps),
                                                        CONCENTRATION_LIMIT))
    queue = (data.get("meta") or {}).get("review_queue") or []
    if queue:
        warnings.append("{} patch(es) parked in meta.review_queue".format(len(queue)))
    return errors, warnings


# --------------------------------------------------------------------------
# Self-test: the gate has to reject these, not merely "usually" reject them.
# --------------------------------------------------------------------------
def _fixture():
    return {
        "meta": {},
        "models": [{"name": "Zhipu AI", "uc": "GLM", "region": "CN", "arr": 150,
                    "val": 55.9, "tokM": 2.5, "m": 70, "retired": False,
                    "prov": {"val": {"as_of": "2026-05-01", "source": "x",
                                     "url": None, "conf": "high"}}}],
        "apps": [{"name": "Cursor", "uc": "IDE", "cat": "coding", "stage": "scale",
                  "arr": 4000, "arrg": 1100, "mau": 3, "maug": 200, "ti": "high",
                  "biz": "B2B+B2C", "val": 60, "m": 97, "retired": False,
                  "ownModel": {"status": "none", "tokenShare": None, "models": []},
                  "prov": {}}],
    }


def _patch(**kw):
    base = {"section": "apps", "name": "Cursor", "field": "arr", "new_value": 4200,
            "as_of": str(common.today()), "source": "The Information 2026-08-20",
            "url": "https://example.com", "conf": "high"}
    base.update(kw)
    return base


CASES = [
    ("magnitude gate: >5x valuation jump",
     _patch(section="models", name="Zhipu AI", field="val", new_value=300), False),
    ("currency gate: RMB with no conversion",
     _patch(section="models", name="Zhipu AI", field="val", new_value=56,
            source="第一财经 2026-08-01，估值 400 亿元"), False),
    ("currency gate passes once conversion is shown",
     _patch(section="models", name="Zhipu AI", field="val", new_value=20,
            source="Reuters 2026-08-01, RMB 142B converted at 7.1 = $20B"), True),
    ("bounds: valuation above $5T",
     _patch(field="val", new_value=9999), False),
    ("unknown field is not patchable",
     _patch(field="m", new_value=99), False),
    ("selfModel is gone, not patchable",
     _patch(field="selfModel", new_value=True), False),
    ("missing source",
     _patch(source="   "), False),
    ("low confidence",
     _patch(conf="low"), False),
    ("malformed as_of",
     _patch(as_of="August 2026"), False),
    ("as_of in the future",
     _patch(as_of=str(common.today().replace(year=common.today().year + 1))), False),
    ("as_of older than the value on file",
     _patch(section="models", name="Zhipu AI", field="val", new_value=6,
            as_of="2026-01-01",
            source="Reuters 2026-01-01, $6B"), False),
    ("ambiguous name is queued, never silently applied",
     _patch(name="a"), False),
    ("unknown name is queued",
     _patch(name="Rogo"), False),
    ("non-numeric value for a metric field",
     _patch(field="arr", new_value="4200"), False),
    ("bad enum value",
     _patch(field="stage", new_value="unicorn"), False),
    ("ownModel contradicts itself",
     _patch(field="ownModel",
            new_value={"status": "none", "tokenShare": 70, "models": []}), False),
    ("valid ownModel upgrade applies",
     _patch(field="ownModel",
            new_value={"status": "primary", "tokenShare": 70,
                       "models": ["Composer"]},
            source="Cursor blog 2026-08-20"), True),
    ("growth rate may swing more than 5x",
     _patch(field="arrg", new_value=200), True),
    ("plain metric update applies", _patch(), True),
]


# The backfill override: it may cross the magnitude gate (that is its whole
# job) but must not become a way around any other gate.
FORCE_CASES = [
    ("backfill override lands the 10x RMB correction",
     _patch(section="models", name="Zhipu AI", field="val", new_value=5.6,
            source="Reuters 2026-08-01, RMB 40B converted at 7.1 = $5.6B"), True),
    ("backfill override does NOT bypass the currency gate",
     _patch(section="models", name="Zhipu AI", field="val", new_value=5.6,
            source="第一财经 2026-08-01，估值 400 亿元"), False),
    ("backfill override does NOT bypass bounds",
     _patch(section="models", name="Zhipu AI", field="val", new_value=9999), False),
]


def selftest() -> int:
    failures = 0
    for label, patch, should_apply in CASES:
        data = common.migrate(_fixture())
        applied, rejected = common.apply_patches(data, [patch], "selftest")
        ok = (len(applied) == 1 and not rejected) if should_apply \
            else (not applied and len(rejected) == 1)
        queued = len((data["meta"].get("review_queue") or []))
        if not should_apply and queued != 1:
            ok = False
        print("{} {}".format("PASS" if ok else "FAIL", label))
        if not ok:
            failures += 1
            print("      applied={} rejected={} queued={}".format(
                applied, rejected, queued))

    for label, patch, should_apply in FORCE_CASES:
        data = common.migrate(_fixture())
        applied, rejected = common.apply_patches(
            data, [patch], "selftest", allow_magnitude=True)
        ok = (len(applied) == 1 and not rejected) if should_apply \
            else (not applied and len(rejected) == 1)
        if should_apply:
            entry = (data["meta"].get("changelog") or [{}])[-1]
            ok = ok and entry.get("forced") is True
        print("{} {}".format("PASS" if ok else "FAIL", label))
        if not ok:
            failures += 1
            print("      applied={} rejected={}".format(applied, rejected))

    # confirmations record provenance without touching the value - the thing
    # that makes the freshness rotation converge instead of re-picking the
    # same entities forever
    data = common.migrate(_fixture())
    app = data["apps"][0]
    before = app["val"]
    ok, refused = common.apply_confirmations(data, app, [
        {"field": "val", "as_of": str(common.today()), "source": "CNBC",
         "url": "", "conf": "high"},
        {"field": "nope", "as_of": str(common.today()), "source": "x",
         "url": "", "conf": "high"},
        {"field": "arr", "as_of": "not-a-date", "source": "x", "url": "",
         "conf": "high"},
        {"field": "mau", "as_of": str(common.today()), "source": "  ",
         "url": "", "conf": "high"},
    ], "selftest")
    conf_checks = [
        ("confirmation records provenance", bool(app["prov"].get("val"))),
        ("confirmation leaves the value alone", app["val"] == before),
        ("confirmation does not touch the changelog",
         not data["meta"].get("changelog")),
        ("bad confirmations refused", len(ok) == 1 and len(refused) == 3),
    ]
    data["apps"][0]["prov"]["val"]["as_of"] = str(common.today())
    common.apply_confirmations(data, app, [
        {"field": "val", "as_of": "2020-01-01", "source": "STALE", "url": "",
         "conf": "high"}], "selftest")
    conf_checks.append(("an older confirmation cannot overwrite a fresher one",
                        app["prov"]["val"]["source"] != "STALE"))
    for label, ok_flag in conf_checks:
        print("{} {}".format("PASS" if ok_flag else "FAIL", label))
        failures += 0 if ok_flag else 1

    # momentum: derived, respects m_manual, survives missing inputs
    data = common.migrate(_fixture())
    data["apps"].append({"name": "Sparse", "uc": "?", "cat": "other",
                         "stage": "pmf", "arr": 10, "arrg": None, "mau": None,
                         "maug": None, "ti": "low", "biz": "B2B", "val": None,
                         "m": 0, "retired": False,
                         "ownModel": {"status": "none", "tokenShare": None,
                                      "models": []}, "prov": {}})
    data["apps"][0]["m_manual"] = 42
    common.recompute_momentum(data)
    checks = [
        ("m_manual pins momentum", data["apps"][0]["m"] == 42),
        ("sparse row still scores", isinstance(data["apps"][1]["m"], int)),
    ]
    for label, ok in checks:
        print("{} {}".format("PASS" if ok else "FAIL", label))
        failures += 0 if ok else 1

    # the schema lint must catch every shape that has actually broken
    for label, bad in BAD_SCHEMAS:
        found = lint_schema(bad)
        ok = bool(found)
        print("{} lint catches: {}".format("PASS" if ok else "FAIL", label))
        failures += 0 if ok else 1
    for name, schema in _live_schemas():
        found = lint_schema(schema)
        ok = not found
        print("{} lint clean: {} schema".format("PASS" if ok else "FAIL", name))
        if not ok:
            failures += 1
            for p in found:
                print("      {}".format(p))

    total = (len(CASES) + len(FORCE_CASES) + len(checks) + len(conf_checks)
             + len(BAD_SCHEMAS) + len(_live_schemas()))
    print("\n{} / {} self-test checks passed".format(total - failures, total))
    return 1 if failures else 0


# --------------------------------------------------------------------------
# Schema preflight. An invalid response schema is rejected by the API before
# any inference happens, so this costs ~nothing and catches the class of bug
# that otherwise only shows up 30 wasted search calls into a backfill.
# --------------------------------------------------------------------------
def lint_schema(node, path=""):
    """Mirror of the rules structured outputs enforces: every subschema needs
    one concrete `type`, no unions, objects fully `required` and closed.

    The live 400 this was written for:
      output_config.format.schema: Invalid schema: Schema type is missing for
      schema: {'description': 'number for metric fields, ...'}
    """
    problems = []
    if not isinstance(node, dict):
        return problems
    if any(k in node for k in ("properties", "items", "enum", "type",
                               "description", "additionalProperties")):
        kind = node.get("type")
        if kind is None:
            problems.append("{}: no `type`".format(path or "<root>"))
        elif not isinstance(kind, str):
            problems.append("{}: `type` is a union {!r}".format(path or "<root>", kind))
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                problems.append("{}: uses {}".format(path or "<root>", key))
        if node.get("type") == "object":
            props = node.get("properties") or {}
            missing = sorted(set(props) - set(node.get("required") or []))
            if missing:
                problems.append("{}: not in `required`: {}".format(
                    path or "<root>", missing))
            if node.get("additionalProperties") is not False:
                problems.append("{}: additionalProperties is not False".format(
                    path or "<root>"))
    for name, sub in (node.get("properties") or {}).items():
        problems += lint_schema(sub, "{}.{}".format(path, name))
    if isinstance(node.get("items"), dict):
        problems += lint_schema(node["items"], path + "[]")
    return problems


# The two shapes that actually broke, kept as regression fixtures.
BAD_SCHEMAS = [
    ("untyped property (the real 400)", {
        "type": "object",
        "properties": {"new_value": {"description": "number or string or object"}},
        "required": ["new_value"], "additionalProperties": False}),
    ("union type", {
        "type": "object",
        "properties": {"val": {"type": ["number", "null"]}},
        "required": ["val"], "additionalProperties": False}),
    ("property missing from required", {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
        "required": ["a"], "additionalProperties": False}),
    ("open object", {
        "type": "object",
        "properties": {"a": {"type": "string"}}, "required": ["a"]}),
    ("untyped nested inside an array", {
        "type": "object",
        "properties": {"xs": {"type": "array", "items": {
            "type": "object", "properties": {"v": {"description": "?"}},
            "required": ["v"], "additionalProperties": False}}},
        "required": ["xs"], "additionalProperties": False}),
]
def _live_schemas():
    import update_data
    import reverify
    import discover
    import update_progress
    return [("update_data", update_data.SCHEMA),
            ("reverify", reverify.SCHEMA),
            ("discover", discover.SCHEMA),
            ("update_progress", update_progress.SCHEMA)]


def schema_check() -> int:
    schemas = _live_schemas()
    failures = 0
    for name, schema in schemas:
        local = lint_schema(schema)
        print("{} {} (local lint)".format("PASS" if not local else "FAIL", name))
        for p in local:
            print("      {}".format(p))
            failures += 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("\nno ANTHROPIC_API_KEY: skipped the live API check")
        return 1 if failures else 0

    import anthropic
    client = anthropic.Anthropic()
    for name, schema in schemas:
        try:
            # Mirror the real request's parameter combination (thinking +
            # effort + format + the search tool), minus any actual searching,
            # so the preflight tests what the passes actually send.
            client.messages.create(
                model=common.MODEL, max_tokens=2048,
                thinking={"type": "adaptive"},
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": schema}},
                tools=[common._web_search(1)],
                messages=[{"role": "user", "content":
                           "Do not search. Reply with a minimal object of "
                           "empty strings, zeros and empty arrays."}],
            )
            print("PASS {} (API accepted the schema)".format(name))
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "Invalid schema" in text or "invalid_request_error" in text:
                print("FAIL {} (API rejected the schema)\n      {}".format(name, text[:400]))
                failures += 1
            else:
                # a rate limit or transient error is not a schema verdict
                print("WARN {} (inconclusive): {}".format(name, text[:160]))
    return 1 if failures else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--schema-check" in sys.argv:
        return schema_check()
    with open(common.DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    errors, warnings = check(data)
    for w in warnings:
        print("WARN  {}".format(w))
    for e in errors:
        print("ERROR {}".format(e))
    print("\n{} error(s), {} warning(s)".format(len(errors), len(warnings)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
