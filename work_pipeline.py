"""Portable weekly research adapter: stdlib only; business gates remain in apply.py."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PASS = "routine-weekly"
STAGES = ("queue", "incremental", "discovery", "progress")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def today():
    return datetime.now(timezone.utc).date()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def clean():
    require(not git("status", "--porcelain", "--untracked-files=all"),
            "Dirty worktree: preserve other work; keep this run's inputs in ignored .run/.")


def script(*args):
    # common.today() is date.today(); enforce the task's UTC business date.
    result = subprocess.run([sys.executable, *args], cwd=ROOT, env={**os.environ, "TZ": "UTC"},
                            capture_output=True, text=True)
    require(result.returncode == 0, f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def last_research(data):
    """OpenRouter, failed passes and arbitrary meta.last_run never prove research success."""
    runs = data.get("meta", {}).get("runs", {})
    dates = []
    for name, entry in runs.items():
        if (name == PASS or name.startswith(PASS + "-")) and entry.get("ok") is True:
            dates.append(date.fromisoformat(entry["at"]))
    # Preserve support for the existing manual API fallback only when all
    # three required research passes have succeeded, not an isolated reverify.
    entries = [runs.get(k, {}) for k in ("incremental", "reverify", "progress")]
    if all(e.get("ok") is True and e.get("at") for e in entries):
        dates.append(min(date.fromisoformat(e["at"]) for e in entries))
    return max(dates) if dates else None


def prepare(as_of=None):
    clean()
    cfg, data = read(ROOT / "automation/task.json"), read(ROOT / "data.json")
    require(cfg["timezone"] == "UTC" and cfg["pass_name"] == PASS, "Unsupported business date/pass configuration.")
    day = date.fromisoformat(as_of) if as_of else today()
    require(day <= today(), "Cannot prepare a future research run.")
    week = day - timedelta(days=(day.weekday() + 1) % 7)
    last = last_research(data)
    require(last is None or last <= day, "Stored research date is in the future; investigate.")
    script("validate.py", "--selftest")
    validation = script("validate.py")
    targets = json.loads(script("reverify.py", "--list", "-k", str(cfg["rotation_size"])))
    for target in targets:
        fields = ["arr", "val", "valPending", "arrg", "uc", "listed", "parent"]
        fields += (["tokM", "tokG", "region"] if target["section"] == "models"
                   else ["mau", "maug", "ownModel", "cat", "stage", "biz", "ti"])
        if target["current"].get("cat") == "assistant":
            fields.append("access")
        target["fields_to_review"] = fields
    return {
        "schema_version": 1, "run_id": str(week), "as_of": str(day), "timezone": "UTC",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "needs_research": last is None or last < week,
        "last_successful_research": str(last) if last else None,
        "window_start": str(day - timedelta(days=cfg["lookback_days"] - 1)), "window_end": str(day),
        "monthly_discovery_due": week.day <= 7,
        "base_commit": git("rev-parse", "HEAD"), "base_data_sha256": digest(data),
        "rotation": targets, "review_queue": data.get("meta", {}).get("review_queue", []),
        "universe": [{"section": s, "name": e["name"]} for s in ("models", "apps")
                     for e in data[s] if not e.get("retired")],
        "validation": validation.strip(),
    }


def text(value):
    return isinstance(value, str) and bool(value.strip())


def https(value):
    if not isinstance(value, str):
        return False
    url = urlsplit(value)
    return url.scheme == "https" and bool(url.netloc) and not url.username and not url.password


def check_bundle(request, bundle):
    require(isinstance(bundle, dict) and bundle.get("schema_version") == 1, "Bundle schema must be v1.")
    require(set(bundle) == {"schema_version", "run_id", "window_start", "window_end", "changes", "coverage", "evidence", "limitations"},
            "Unexpected or missing research bundle fields.")
    require(bundle.get("run_id") == request["run_id"], "Bundle belongs to another weekly run.")
    for key in ("window_start", "window_end"):
        require(bundle.get(key) == request[key], f"Bundle {key} differs from prepared window.")
    sources = bundle.get("evidence")
    require(isinstance(sources, list) and sources, "No opened source evidence: do not mark failed research as success.")
    urls = set()
    for source in sources:
        require(isinstance(source, dict) and https(source.get("url")) and text(source.get("note")),
                "Each evidence item needs an opened HTTPS URL and verification note.")
        at = datetime.fromisoformat(source.get("read_at", "").replace("Z", "+00:00"))
        require(at.tzinfo is not None and at <= datetime.now(timezone.utc) + timedelta(minutes=10),
                "Evidence read_at must be an actual timestamp with timezone, not in the future.")
        require(at.date() >= date.fromisoformat(request["window_start"]), "Evidence must be read during this observation period.")
        urls.add(source["url"])
    coverage = bundle.get("coverage", {})
    targets = {(x["section"], x["name"]): set(x["fields_to_review"]) for x in request["rotation"]}
    rows = coverage.get("rotation", [])
    require(len(rows) == len(targets), "Account for every rotation target, including unverified fields.")
    seen, checked_count = set(), 0
    for row in rows:
        key = (row.get("section"), row.get("name"))
        require(key in targets and key not in seen and text(row.get("note")), "Invalid/duplicate rotation coverage.")
        seen.add(key)
        checked, missing = row.get("checked_fields"), row.get("unverified_fields")
        require(isinstance(checked, list) and isinstance(missing, list), "Coverage field lists are required.")
        require(not set(checked) & set(missing) and set(checked) | set(missing) == targets[key],
                f"{key}: account for exactly the requested fields without overlap.")
        checked_count += len(checked)
    require(checked_count > 0, "No rotation fields verified; do not advance research success.")
    for stage in STAGES:
        entry = coverage.get(stage, {})
        allowed = {"completed", "limited"}
        if stage == "discovery" and not request["monthly_discovery_due"]:
            allowed.add("not_due")
        require(entry.get("status") in allowed and text(entry.get("note")), f"Missing {stage} coverage.")
    require(isinstance(bundle.get("limitations"), list) and all(text(x) for x in bundle["limitations"]),
            "limitations must be a list; list actual gaps, or use [].")
    changes = bundle.get("changes", {})
    require(changes.get("pass") == PASS and changes.get("force", False) is False,
            "Keep pass=routine-weekly; file-level force is prohibited. Per-patch arbitration rules remain in force.")
    require(set(changes) <= {"pass", "force", "patches", "confirmations", "candidates", "retire", "ai_progress"},
            "Unknown changes fields; use the existing apply.py interface.")
    for key in ("patches", "confirmations", "candidates", "retire"):
        rows = changes.get(key, [])
        require(isinstance(rows, list), f"changes.{key} must be an array.")
        for row in rows:
            require(isinstance(row, dict) and row.get("url") in urls, f"{key} item lacks an actually opened source.")
    progress = changes.get("ai_progress")
    require(isinstance(progress, dict), "Include the weekly ai_progress object.")
    require(isinstance(progress.get("takeaway"), str), "ai_progress.takeaway must be a string; do not invent an insight.")
    for key, cap in (("enterprise", 5), ("models", 5), ("infra_invest", 4)):
        rows = progress.get(key)
        require(isinstance(rows, list) and len(rows) <= cap, f"Invalid progress list: {key}.")
        for row in rows:
            require(isinstance(row, dict) and row.get("url") in urls and text(row.get("source"))
                    and text(row.get("title")), f"{key}: missing verified source/title.")
            require(request["window_start"] <= str(date.fromisoformat(row.get("date", ""))) <= request["window_end"],
                    f"{key}: news date outside the seven-day window.")
    require(changes.get("patches") or changes.get("confirmations"),
            "A completed weekly recheck needs verified patches or confirmations, not only a date update.")


def apply(request_path, bundle_path, model_label="unreported"):
    clean()
    request, bundle, base = read(request_path), read(bundle_path), read(ROOT / "data.json")
    require(request.get("needs_research") is True, "This week is already covered; verify publication instead.")
    require(request["base_commit"] == git("rev-parse", "HEAD") and request["base_data_sha256"] == digest(base),
            "Base changed: sync, prepare and review inputs again; never merge data.json blindly.")
    require(request["as_of"] == str(today()) and request["timezone"] == "UTC", "Re-prepare after the UTC business date changes.")
    require(request["run_id"] == str(today() - timedelta(days=(today().weekday() + 1) % 7)), "Stale weekly run ID.")
    last = last_research(base)
    require(last is None or str(last) < request["run_id"], "This week's research is already committed.")
    check_bundle(request, bundle)
    ledger = ROOT / "runs" / (request["run_id"] + ".json")
    require(not ledger.exists(), "This run's ledger already exists; inspect its publication.")
    payload = ROOT / ".run/changes.json"
    write(payload, bundle["changes"])
    before = (ROOT / "data.json").read_bytes()
    try:
        report = script("apply.py", str(payload))
        validation = script("validate.py")
        current = read(ROOT / "data.json")
        write(ledger, {
            "schema_version": 1, "run_id": request["run_id"], "window_start": request["window_start"],
            "window_end": request["window_end"], "base_commit": request["base_commit"],
            "data_sha256": digest(current), "model_label": model_label,
            "validated_at": datetime.now(timezone.utc).isoformat(), "status": "validated_for_publication",
            "publication_note": "This record proves validation only. Verify the remote commit and live Pages data separately.",
            "result": current["meta"]["runs"][PASS], "bundle": bundle,
            "apply_report": report, "validation": validation.strip(),
        })
    except Exception:
        (ROOT / "data.json").write_bytes(before)
        if ledger.exists():
            ledger.unlink()
        raise
    return {"status": "validated_for_publication", "run_id": request["run_id"],
            "data_sha256": digest(current), "apply_report": report,
            "result": current["meta"]["runs"][PASS],
            "allowed_paths": ["data.json", ledger.relative_to(ROOT).as_posix()],
            "commit_message": f"chore(routine): weekly update {request['as_of']}"}


def freshness(max_days, as_of=None):
    last = last_research(read(ROOT / "data.json"))
    day = date.fromisoformat(as_of) if as_of else today()
    age = (day - last).days if last else None
    require(age is not None and 0 <= age <= max_days,
            f"Research watchdog: last successful research={last}, age={age}, maximum={max_days} days. OpenRouter does not reset this clock.")
    return {"status": "research_fresh", "last_successful_research": str(last), "age_days": age}


def check_site():
    data = read(ROOT / "data.json")
    site = read(ROOT / "automation/task.json")["site_url"].rstrip("/") + "/"
    fingerprint = digest(data)
    req = Request(site + "data.json?verify=" + fingerprint,
                  headers={"Cache-Control": "no-cache", "User-Agent": "AI-Monitor-Publication-Check/1.0"})
    with urlopen(req, timeout=30) as response:
        published = json.load(response)
    require(digest(published) == fingerprint, "Live data does not match this checkout; inspect deployment and newer commits.")
    return {"status": "published_data_matches", "site_url": site, "data_sha256": fingerprint,
            "last_successful_research": str(last_research(published)), "last_run": published["meta"].get("last_run")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--out", required=True)
    prep.add_argument("--as-of", help="Offline tests only; normal runs use actual UTC date.")
    ap = sub.add_parser("apply")
    ap.add_argument("--request", required=True)
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--model-label", default="unreported")
    fresh = sub.add_parser("freshness")
    fresh.add_argument("--max-days", type=int, default=2)
    sub.add_parser("check-site")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.as_of)
            write(args.out, result)
            result = {k: v for k, v in result.items() if k not in {"rotation", "universe", "review_queue"}}
            result["request_path"] = args.out
        elif args.command == "apply":
            result = apply(args.request, args.bundle, args.model_label)
        elif args.command == "freshness":
            result = freshness(args.max_days)
        else:
            result = check_site()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
