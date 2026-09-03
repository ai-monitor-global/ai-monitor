"""The single write-entry for the subscription routine.

The cloud routine does the research (on subscription quota, no API key) and
writes ONE json file with everything it found; this script pushes all of it
through the same gates the API passes use. The routine never edits data.json
directly - every lesson in this repo's history says the constraint must live
in code, not in a prompt.

  python apply.py changes.json [--dry-run]

changes.json shape (all sections optional):
{
  "pass": "routine-weekly",
  "force": false,                       // waive 5x + backdate gates (corrections)
  "patches": [                          // value changes, gate-native shape
    {"section":"models","name":"Zhipu AI","field":"arr","new_value":1600,
     "as_of":"2026-08-31","source":"...","url":"...","conf":"high"}
  ],
  "confirmations": [                    // checked, found already correct
    {"section":"apps","name":"Cursor","field":"val",
     "as_of":"2026-08-15","source":"...","url":"...","conf":"high"}
  ],
  "candidates": [ ... ],                // discover-style app candidates
  "retire": [ {"name":"...","reason":"...","source":"...","url":"..."} ],
  "ai_progress": { "enterprise":[...],"models":[...],"infra_invest":[...],
                   "takeaway":"..." }
}

Exit code 0 = applied and data.json validates. Anything else = fix before
committing. Rejected items are parked in meta.review_queue, never lost.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import common
import discover
import validate


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    dry = "--dry-run" in sys.argv

    with open(args[0], "r", encoding="utf-8") as f:
        changes = json.load(f)
    pass_name = str(changes.get("pass") or "routine")
    force = bool(changes.get("force"))

    data = common.load()
    report = {"applied": [], "rejected": [], "confirmed": [],
              "promoted": [], "queued": [], "skipped": []}

    # ---- value patches, through the full gate --------------------------
    applied, rejected = common.apply_patches(
        data, changes.get("patches") or [], pass_name,
        dry_run=dry, allow_magnitude=force)
    report["applied"] += applied
    report["rejected"] += rejected

    # ---- confirmations (provenance for already-correct values) ---------
    per_entity = {}
    for item in changes.get("confirmations") or []:
        section = "models" if item.get("section") == "models" else "apps"
        entity, err = common.resolve(data.get(section, []), item.get("name") or "")
        if not entity:
            report["rejected"].append("confirmation {}: {}".format(
                item.get("name"), err))
            continue
        per_entity.setdefault(id(entity), (entity, []))[1].append(item)
    for entity, items in per_entity.values():
        ok, refused = common.apply_confirmations(
            data, entity, items, pass_name, dry_run=dry)
        report["confirmed"] += ok
        report["rejected"] += refused

    # a reviewed entity counts as checked, so the rotation moves on
    if not dry:
        touched = {p.get("name") for p in changes.get("patches") or []}
        touched |= {c.get("name") for c in changes.get("confirmations") or []}
        for _, entity in common.iter_entities(data):
            if entity["name"] in touched:
                entity["checked_at"] = str(common.today())

    # ---- new-company candidates, through the CRITERIA gate -------------
    for raw in changes.get("candidates") or []:
        cand = discover.normalise(raw)
        verdict, reason = discover.gate(data, cand)
        label = "{} ({})".format(cand.get("name"), reason)
        if verdict == "promote":
            report["promoted"].append(label)
            if not dry:
                data["apps"].append(discover.to_entity(cand))
        elif verdict == "queue":
            report["queued"].append(label)
            if not dry:
                data.setdefault("candidates", []).append({
                    "name": cand.get("name"), "cat": cand.get("cat"),
                    "uc": cand.get("uc"), "why": cand.get("why"),
                    "arr": cand.get("arr"), "val": cand.get("val"),
                    "source": cand.get("source"), "url": cand.get("url"),
                    "as_of": cand.get("as_of"),
                    "found_at": str(common.today()),
                    "reason": reason, "verdict": "pending",
                })
        else:
            report["skipped"].append(label)

    # ---- retirement ------------------------------------------------------
    # Policy for full autonomy: an ACQUISITION is a `parent` patch, never a
    # retire (the Cursor/SpaceX precedent). A retire item is for shutdown /
    # ceased independent operation; with confirmed=true and >=2 sources named
    # in `source` it applies directly, otherwise it queues for the next run's
    # adjudication.
    for item in changes.get("retire") or []:
        for section in ("models", "apps"):
            entity, _ = common.resolve(data[section], item.get("name") or "")
            if not entity or entity.get("retired"):
                continue
            src = str(item.get("source") or "").strip()
            if item.get("confirmed") and src and str(item.get("reason") or "").strip():
                if not dry:
                    entity["retired"] = True
                    entity["checked_at"] = str(common.today())
                    data["meta"].setdefault("changelog", []).append({
                        "date": str(common.today()), "pass": pass_name,
                        "section": section, "entity": entity["name"],
                        "field": "retired", "old": False, "new": True,
                        "source": "{}｜{}".format(item.get("reason"), src),
                        "conf": "high"})
                report["applied"].append("{} 已下架（{}）".format(
                    entity["name"], item.get("reason")))
            else:
                common._queue(data["meta"], str(common.today()), entity["name"],
                              "retired", True,
                              "提议下架: {}".format(item.get("reason")),
                              item, pass_name)
                report["queued"].append("{} (下架提议证据不足，进队列待下轮仲裁)".format(
                    entity["name"]))
            break

    # ---- weekly AI progress block ---------------------------------------
    prog = changes.get("ai_progress")
    if isinstance(prog, dict):
        for key in ("enterprise", "models", "infra_invest"):
            if not isinstance(prog.get(key), list):
                prog[key] = []
        prog["week_of"] = str(common.today())
        prog["generated_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC")
        prog["model"] = "subscription-routine"
        if not dry:
            data["ai_progress"] = prog
        report["applied"].append("ai_progress: {} 条".format(
            sum(len(prog[k]) for k in ("enterprise", "models", "infra_invest"))))

    common.recompute_momentum(data)
    common.note_updates(data, report["applied"])
    common.record_run(data, pass_name, ok=True,
                      applied=len(report["applied"]),
                      rejected=len(report["rejected"]),
                      confirmed=len(report["confirmed"]),
                      promoted=len(report["promoted"]),
                      queued=len(report["queued"]))
    if not dry:
        common.save(data)

    for key, mark in (("applied", "+"), ("confirmed", "="), ("promoted", "++"),
                      ("queued", "?"), ("skipped", "."), ("rejected", "-")):
        for line in report[key]:
            print("  {} {}".format(mark, line))
    print("\napplied {} / confirmed {} / promoted {} / queued {} / "
          "skipped {} / rejected {}{}".format(
              len(report["applied"]), len(report["confirmed"]),
              len(report["promoted"]), len(report["queued"]),
              len(report["skipped"]), len(report["rejected"]),
              "  [DRY RUN]" if dry else ""))

    errors, warnings = validate.check(
        data if dry else json.load(open(common.DATA_FILE, encoding="utf-8")))
    for w in warnings:
        print("WARN  {}".format(w))
    for e in errors:
        print("ERROR {}".format(e))
    if errors:
        print("\ndata.json failed validation - do NOT commit; fix and re-run")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
