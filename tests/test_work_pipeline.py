"""Offline integration tests; all synthetic data stays in temporary repositories."""
import copy
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("work_pipeline", SOURCE / "work_pipeline.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class WorkPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for source in SOURCE.glob("*.py"):
            shutil.copy(source, self.root)
        shutil.copy(SOURCE / ".gitignore", self.root)
        shutil.copytree(SOURCE / "automation", self.root / "automation")
        self.day = pipeline.today()
        self.old = self.day - timedelta(days=14)
        self.base = json.loads((SOURCE / "data.json").read_text())
        self.base["meta"]["last_run"] = str(self.day)
        self.base["meta"]["runs"] = {
            "routine-weekly": {"at": str(self.old), "ok": True},
            "openrouter": {"at": str(self.day), "ok": True},
        }
        pipeline.write(self.root / "data.json", self.base)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Offline test")
        self.git("config", "user.email", "offline-test@example.invalid")
        self.commit("fixture")
        self.root_patch = patch.object(pipeline, "ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def bundle(self, request):
        url = "https://example.invalid/offline-test-only"
        target = request["rotation"][0]
        rotation = []
        for t in request["rotation"]:
            checked = ["uc"] if t == target else []
            rotation.append({"section": t["section"], "name": t["name"], "checked_fields": checked,
                             "unverified_fields": [f for f in t["fields_to_review"] if f not in checked],
                             "note": "Synthetic offline fixture; not a real coverage claim."})
        coverage = {stage: {"status": "limited", "note": "Offline fixture only."} for stage in pipeline.STAGES}
        coverage["rotation"] = rotation
        return {
            "schema_version": 1, "run_id": request["run_id"],
            "window_start": request["window_start"], "window_end": request["window_end"],
            "changes": {"pass": "routine-weekly", "confirmations": [{
                "section": target["section"], "name": target["name"], "field": "uc",
                "as_of": str(self.day), "source": "Offline synthetic fixture", "url": url, "conf": "high"}],
                "ai_progress": {"enterprise": [], "models": [], "infra_invest": [], "takeaway": ""}},
            "coverage": coverage,
            "evidence": [{"url": url, "read_at": datetime.now(timezone.utc).isoformat(),
                          "note": "Synthetic; never publish."}],
            "limitations": ["This test is not research."]}

    def stage(self, request, bundle):
        rp, bp = self.root / ".run/request.json", self.root / ".run/research.json"
        pipeline.write(rp, request)
        pipeline.write(bp, bundle)
        return rp, bp

    def test_openrouter_cannot_mask_stale_research(self):
        request = pipeline.prepare()
        self.assertTrue(request["needs_research"])
        self.assertEqual(request["last_successful_research"], str(self.old))
        with self.assertRaisesRegex(ValueError, "OpenRouter does not reset"):
            pipeline.freshness(2)

    def test_same_week_legacy_success_skips_without_mutation(self):
        self.base["meta"]["runs"]["routine-weekly"]["at"] = str(self.day)
        pipeline.write(self.root / "data.json", self.base)
        self.commit("successful legacy research")
        request = pipeline.prepare()
        self.assertFalse(request["needs_research"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(pipeline.freshness(2)["status"], "research_fresh")

    def test_api_fallback_requires_all_research_passes(self):
        data = copy.deepcopy(self.base)
        data["meta"]["runs"]["reverify"] = {"at": str(self.day), "ok": True}
        self.assertEqual(pipeline.last_research(data), self.old)
        for key in ("incremental", "progress"):
            data["meta"]["runs"][key] = {"at": str(self.day), "ok": True}
        self.assertEqual(pipeline.last_research(data), self.day)
        data["meta"]["runs"]["progress"]["ok"] = False
        self.assertEqual(pipeline.last_research(data), self.old)

    def test_apply_uses_existing_gate_and_leaves_openrouter_untouched(self):
        request = pipeline.prepare()
        result = pipeline.apply(*self.stage(request, self.bundle(request)))
        after = pipeline.read(self.root / "data.json")
        self.assertEqual(after.get("openrouter"), self.base.get("openrouter"))
        self.assertEqual(after["meta"]["runs"]["openrouter"], self.base["meta"]["runs"]["openrouter"])
        self.assertGreaterEqual(result["result"]["confirmed"], 1)
        ledger = pipeline.read(self.root / "runs" / (request["run_id"] + ".json"))
        self.assertEqual(ledger["data_sha256"], pipeline.digest(after))
        self.assertEqual(ledger["status"], "validated_for_publication")
        self.commit("offline result")
        self.assertFalse(pipeline.prepare()["needs_research"])

    def test_concurrent_commit_blocks_stale_input(self):
        request = pipeline.prepare()
        paths = self.stage(request, self.bundle(request))
        (self.root / "other.txt").write_text("Concurrent legitimate work")
        self.commit("other change")
        with self.assertRaisesRegex(ValueError, "Base changed"):
            pipeline.apply(*paths)
        self.assertEqual(pipeline.read(self.root / "data.json"), self.base)

    def test_whole_file_force_and_unopened_sources_are_rejected(self):
        request = pipeline.prepare()
        for reason in ("force", "unopened"):
            bundle = self.bundle(request)
            if reason == "force":
                bundle["changes"]["force"] = True
                message = "file-level force"
            else:
                bundle["changes"]["confirmations"][0]["url"] = "https://example.invalid/not-opened"
                message = "actually opened"
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, message):
                pipeline.apply(*self.stage(request, bundle))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_failure_after_mutation_restores_original_data(self):
        (self.root / "apply.py").write_text("from pathlib import Path\nPath('data.json').write_text('{}')\nraise SystemExit(1)\n")
        self.commit("fixture with failing writer")
        request = pipeline.prepare()
        before = (self.root / "data.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "apply.py"):
            pipeline.apply(*self.stage(request, self.bundle(request)))
        self.assertEqual((self.root / "data.json").read_bytes(), before)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_no_evidence_and_missing_coverage_cannot_advance_state(self):
        request = pipeline.prepare()
        for missing in ("evidence", "rotation"):
            bundle = self.bundle(request)
            if missing == "evidence":
                bundle["evidence"] = []
            else:
                bundle["coverage"]["rotation"].pop()
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                pipeline.apply(*self.stage(request, bundle))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_midweek_success_does_not_skip_next_saturday_or_rewrite_history(self):
        self.base["meta"]["runs"]["routine-weekly"]["at"] = "2026-10-06"
        pipeline.write(self.root / "data.json", self.base)
        legacy_ledger = self.root / "runs/2026-10-04.json"
        pipeline.write(legacy_ledger, {"run_id": "2026-10-04", "status": "legacy fixture"})
        self.commit("midweek success in legacy Sunday cycle")
        before_data = (self.root / "data.json").read_bytes()
        before_ledger = legacy_ledger.read_bytes()
        with patch.object(pipeline, "today", return_value=date(2026, 10, 10)):
            request = pipeline.prepare()
        self.assertEqual(request["run_id"], "2026-10-10")
        self.assertTrue(request["needs_research"])
        self.assertEqual((self.root / "data.json").read_bytes(), before_data)
        self.assertEqual(legacy_ledger.read_bytes(), before_ledger)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_sunday_reuses_successful_saturday_cycle(self):
        self.base["meta"]["runs"]["routine-weekly"]["at"] = "2026-10-10"
        pipeline.write(self.root / "data.json", self.base)
        self.commit("successful Saturday research")
        with patch.object(pipeline, "today", return_value=date(2026, 10, 11)):
            request = pipeline.prepare()
        self.assertEqual(request["run_id"], "2026-10-10")
        self.assertFalse(request["needs_research"])
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_monthly_discovery_uses_first_saturday_cycle(self):
        self.base["meta"]["runs"]["routine-weekly"]["at"] = "2026-09-01"
        pipeline.write(self.root / "data.json", self.base)
        self.commit("older successful research")
        cases = [
            (date(2026, 10, 3), "2026-10-03", True),
            (date(2026, 10, 10), "2026-10-10", False),
            (date(2026, 11, 1), "2026-10-31", False),
            (date(2026, 11, 7), "2026-11-07", True),
        ]
        for day, run_id, due in cases:
            with self.subTest(day=day), patch.object(pipeline, "today", return_value=day):
                request = pipeline.prepare()
            self.assertEqual(request["run_id"], run_id)
            self.assertEqual(request["monthly_discovery_due"], due)

    def test_sunday_anchored_request_is_rejected_before_mutation(self):
        self.base["meta"]["runs"]["routine-weekly"]["at"] = "2026-09-20"
        pipeline.write(self.root / "data.json", self.base)
        self.commit("older successful research")
        with patch.object(pipeline, "today", return_value=date(2026, 10, 6)):
            request = pipeline.prepare()
            request["run_id"] = "2026-10-04"
            with self.assertRaisesRegex(ValueError, "Stale weekly run ID"):
                pipeline.apply(*self.stage(request, self.bundle(request)))
        self.assertEqual(pipeline.read(self.root / "data.json"), self.base)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_monday_watchdog_accepts_saturday_but_rejects_thursday(self):
        for last, age in (("2026-10-10", 2), ("2026-10-08", 4)):
            self.base["meta"]["runs"]["routine-weekly"]["at"] = last
            pipeline.write(self.root / "data.json", self.base)
            with self.subTest(last=last), patch.object(pipeline, "today", return_value=date(2026, 10, 12)):
                if age == 2:
                    result = pipeline.freshness(2)
                    self.assertEqual(result["status"], "research_fresh")
                    self.assertEqual(result["age_days"], 2)
                else:
                    with self.assertRaisesRegex(ValueError, "age=4, maximum=2 days"):
                        pipeline.freshness(2)


if __name__ == "__main__":
    unittest.main()
