"""Tests for bc_walk.py — checking the fetched folders and batching their cases. Standard library only.

    python3 -m unittest discover -s scripts/tests -v

They must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions.
"""

import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import bc_run  # noqa: E402
import bc_walk  # noqa: E402

SITE = "acme.testrail.io"
CONFIG = {
    "version": 2,
    "requirements": {"provider": "jama", "base_url": "https://acme.jamacloud.com", "project_id": 7},
    "test_cases": {
        "provider": "testrail",
        "base_url": "https://acme.testrail.io",
        "project_id": 3,
        "suite_id": 9,
        "authoring": {"requirement_field": "custom_jama_req_id"},
    },
}


def case(cid, title, section, steps=None, custom=None):
    raw = {
        "section_path": section,
        "steps": steps if steps is not None else [{"content": f"Open {title}", "expected": "It shows"}],
    }
    if custom:
        raw["custom"] = custom
    return {
        "type": "test_case",
        "id": f"TC-{cid}",
        "title": title,
        "description": "Preconditions: logged in",
        "feature": section.split("/")[-1],
        "status": "",
        "linked_ids": [],
        "raw_fields": raw,
    }


def folder_file(cases, section, **overrides):
    """A file as testrail_get_canonical_cases writes it with section and save_to."""
    payload = {
        "kind": "test_cases",
        "source": "testrail",
        "site": SITE,
        "written_at": "2026-11-12T12:00:00+00:00",
        "project_id": 3,
        "suite_id": 9,
        "requirement_field": "custom_jama_req_id",
        "filter": {"requirement_ids": [], "section": section, "unlinked": False},
        "summary": f"{len(cases)} of 40 cases in suite 9 (under folder {section!r})",
        "warnings": [],
        "cases_scanned": 40,
        "count": len(cases),
        "test_cases": cases,
    }
    payload.update(overrides)
    return payload


SETTINGS = [case(1, "Settings opens", "App/Settings"), case(2, "Settings error banner", "App/Settings/Errors")]
LOGIN = [case(3, "Login succeeds", "App/Login"), case(4, "Login fails", "App/Login")]


class WalkRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"BC_HOME": str(Path(self.tmp.name, "bc-home"))})
        env.start()
        self.addCleanup(env.stop)
        self.project = Path(self.tmp.name, "project")
        (self.project / ".buddy-council").mkdir(parents=True)
        self.configure(CONFIG)
        self.run = Path(bc_run.cmd_new(str(self.project), "gather-locators", "")["run_dir"])

    def configure(self, config):
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(config))

    def save(self, folder, payload):
        path = Path(bc_walk.folder_file(self.run, folder))
        path.write_text(json.dumps(payload))
        return path

    def check(self, *folders):
        return bc_walk.cmd_check_cases(str(self.run), list(folders))

    def entry(self, result, folder):
        return next(f for f in result["folders"] if f["folder"] == folder)


class FolderFiles(WalkRunTest):
    def test_each_folder_has_its_own_file_name_the_connector_accepts(self):
        folders = ["App/Settings", "App Settings", "app/settings", "Ärger/" + "x" * 80, "日本"]
        names = [Path(bc_walk.folder_file(self.run, f)).name for f in folders]
        self.assertEqual(len(set(names)), len(names), "folders that slug alike still get distinct files")
        for name in names:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9_-]{0,63}\.json$", "testrail save_to accepts only this shape")
        self.assertEqual(Path(bc_walk.folder_file(self.run, "App/Settings")).parent, self.run)

    def test_a_folder_path_is_normalized_before_it_is_named(self):
        self.assertEqual(bc_walk.folder_file(self.run, " /App / Settings/ "), bc_walk.folder_file(self.run, "App/Settings"))


class CheckCases(WalkRunTest):
    def test_matching_files_give_one_fetch_line_per_folder(self):
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings"))
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        result = self.check("App/Settings", "App/Login")
        self.assertEqual(result["refetch"], [])
        self.assertEqual(
            result["lines"],
            ["Fetch: App/Settings → 2 cases from TestRail suite 9", "Fetch: App/Login → 2 cases from TestRail suite 9"],
        )
        settings = self.entry(result, "App/Settings")
        self.assertEqual((settings["status"], settings["cases"]), ("ok", 2))
        self.assertEqual(settings["file"], bc_walk.folder_file(self.run, "App/Settings"))
        self.assertTrue((self.run / "check-cases.json").is_file(), "batch reads the folders from here")

    def test_a_wrong_site_project_suite_or_field_is_listed_never_trusted(self):
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings", site="other.testrail.io"))
        self.save("App/Login", folder_file(LOGIN, "App/Login", project_id=4, suite_id=10,
                                           requirement_field="custom_refs"))
        result = self.check("App/Settings", "App/Login")
        self.assertEqual(result["refetch"], ["App/Settings", "App/Login"])
        settings, login = self.entry(result, "App/Settings"), self.entry(result, "App/Login")
        self.assertEqual(settings["status"], "mismatch")
        self.assertIn("other.testrail.io", " ".join(settings["problems"]))
        self.assertIn("project 4 suite 10", " ".join(login["problems"]))
        self.assertIn("custom_refs", " ".join(login["problems"]))
        self.assertTrue(result["lines"][0].startswith("Fetch: App/Settings → MISMATCH"), result["lines"][0])

    def test_the_recorded_section_must_be_exactly_the_folder(self):
        self.save("App/Settings", folder_file(SETTINGS, "Settings"))
        self.save("App/Login", folder_file(LOGIN, "App/Login", filter={"requirement_ids": ["REQ-1"], "section": "App/Login", "unlinked": False}))
        result = self.check("App/Settings", "App/Login")
        self.assertEqual(result["refetch"], ["App/Settings", "App/Login"])
        self.assertIn("'Settings'", " ".join(self.entry(result, "App/Settings")["problems"]))
        self.assertIn("requirement_ids", " ".join(self.entry(result, "App/Login")["problems"]))

    def test_a_missing_file_is_a_failed_fetch_with_the_path_to_save_to(self):
        result = self.check("App/Settings")
        entry = self.entry(result, "App/Settings")
        self.assertEqual(entry["status"], "missing")
        self.assertEqual(result["refetch"], ["App/Settings"])
        self.assertIn(Path(entry["file"]).name, result["lines"][0])
        self.assertTrue(result["lines"][0].startswith("Fetch: App/Settings → FAILED"))

    def test_the_count_must_match_the_cases_saved(self):
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings", count=5))
        entry = self.entry(self.check("App/Settings"), "App/Settings")
        self.assertEqual(entry["status"], "mismatch")
        self.assertIn("5", " ".join(entry["problems"]))

    def test_a_file_not_written_by_the_connector_is_a_mismatch(self):
        Path(bc_walk.folder_file(self.run, "App/Settings")).write_text("{not json")
        self.save("App/Login", {"kind": "requirements", "source": "jama"})
        result = self.check("App/Settings", "App/Login")
        self.assertEqual(result["refetch"], ["App/Settings", "App/Login"])

    def test_an_empty_folder_is_reported_not_refetched(self):
        warning = "No folder matches section 'App/Gone'."
        self.save("App/Gone", folder_file([], "App/Gone", warnings=[warning]))
        result = self.check("App/Gone")
        self.assertEqual(result["refetch"], [])
        self.assertEqual(self.entry(result, "App/Gone")["status"], "empty")
        self.assertEqual(result["lines"], ["Fetch: App/Gone → no cases in App/Gone"])
        self.assertEqual(result["warnings"], [f"App/Gone: {warning}"], "the connector's warnings are passed on")

    def test_cases_outside_the_folder_are_counted_and_left_out(self):
        # A bare name matches a folder of that name at any depth, so the connector may return cases elsewhere.
        stray = case(9, "Other settings", "Legacy/Settings")
        self.save("Settings", folder_file([case(1, "Settings opens", "Settings"), stray], "Settings"))
        entry = self.entry(self.check("Settings"), "Settings")
        self.assertEqual((entry["status"], entry["cases"], entry["outside"]), ("ok", 1, 1))

    def test_a_folder_given_twice_is_checked_once(self):
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        self.assertEqual(len(self.check("App/Login", " App/Login/ ")["folders"]), 1)

    def test_a_project_without_testrail_is_refused(self):
        self.configure({**CONFIG, "test_cases": {"provider": "none"}})
        with self.assertRaises(bc_run.RunError):
            self.check("App/Login")

    def test_no_folder_is_refused(self):
        with self.assertRaises(bc_run.RunError):
            self.check(" / ")


class Batch(WalkRunTest):
    def batch(self):
        return bc_walk.cmd_batch(str(self.run))

    def batches(self):
        return json.loads((self.run / "batches.json").read_text())["batches"]

    def test_batch_needs_check_cases_first(self):
        with self.assertRaises(bc_run.RunError):
            self.batch()

    def test_batches_are_small_grouped_by_folder_and_cover_every_case_once(self):
        long_steps = [{"content": "Tap the thing " + "x" * 400, "expected": "It shows"} for _ in range(10)]
        many = [case(100 + i, f"Settings case {i}", "App/Settings", steps=long_steps) for i in range(12)]
        self.save("App/Settings", folder_file(many, "App/Settings"))
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        self.check("App/Settings", "App/Login")
        result = self.batch()
        batches = self.batches()
        self.assertGreater(len(batches), 2, "the settings cases do not fit one batch")
        seen = []
        for batch in batches:
            path = Path(batch["file"])
            self.assertEqual(path.parent, self.run / "batches")
            size = len(path.read_bytes())
            self.assertEqual(batch["bytes"], size)
            self.assertLessEqual(size, bc_run.PART_BYTES)
            text = path.read_text(encoding="utf-8")
            self.assertIn(batch["folder"], text)
            for cid in batch["case_ids"]:
                self.assertIn(cid, text)
            seen += batch["case_ids"]
        self.assertEqual(sorted(seen), sorted([c["id"] for c in many + LOGIN]))
        self.assertEqual({b["folder"] for b in batches if "TC-3" in b["case_ids"]}, {"App/Login"})
        self.assertEqual(len({b["id"] for b in batches}), len(batches))
        printed = result["batches"]
        self.assertEqual([b["id"] for b in printed], [b["id"] for b in batches])
        self.assertNotIn("case_ids", printed[0], "the printed list stays compact")
        self.assertEqual(result["cases"], len(seen))

    def test_folders_that_failed_the_check_are_left_out_and_named(self):
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings"))
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        self.save("App/Gone", folder_file([], "App/Gone"))
        self.check("App/Settings", "App/Login", "App/Gone")
        # A file changed after the check is checked again, never trusted.
        self.save("App/Login", folder_file(LOGIN, "App/Login", suite_id=10))
        result = self.batch()
        self.assertEqual({b["folder"] for b in self.batches()}, {"App/Settings"})
        left = {entry["folder"]: entry["reason"] for entry in result["left_out"]}
        self.assertEqual(set(left), {"App/Login", "App/Gone"})
        self.assertIn("suite 10", left["App/Login"])
        self.assertIn("no cases", left["App/Gone"])

    def test_a_case_in_two_requested_folders_is_batched_once_under_the_deeper(self):
        self.save("App", folder_file(SETTINGS + LOGIN, "App"))
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings"))
        self.check("App", "App/Settings")
        result = self.batch()
        by_folder = {}
        for batch in self.batches():
            by_folder.setdefault(batch["folder"], []).extend(batch["case_ids"])
        self.assertEqual(sorted(by_folder["App/Settings"]), ["TC-1", "TC-2"])
        self.assertEqual(sorted(by_folder["App"]), ["TC-3", "TC-4"])
        self.assertEqual(result["cases"], 4)

    def test_a_case_too_big_for_one_batch_is_split_and_reported(self):
        huge = case(7, "Huge case", "App/Settings",
                    steps=[{"content": f"Step {i} " + "y" * 900, "expected": "ok"} for i in range(40)])
        self.save("App/Settings", folder_file([huge, SETTINGS[0]], "App/Settings"))
        self.check("App/Settings")
        result = self.batch()
        holding = [b for b in self.batches() if "TC-7" in b["case_ids"]]
        self.assertGreater(len(holding), 1)
        self.assertTrue(all(b["case_ids"] == ["TC-7"] for b in holding))
        self.assertTrue(all(b["bytes"] <= bc_run.PART_BYTES for b in self.batches()))
        self.assertEqual(result["split_cases"], ["TC-7"])

    def test_a_rerun_replaces_the_old_batches(self):
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings"))
        self.check("App/Settings")
        self.batch()
        stale = self.run / "batches" / "batch-999.md"
        stale.write_text("old")
        self.batch()
        self.assertFalse(stale.exists())

    def test_nothing_to_batch_is_an_error(self):
        self.save("App/Gone", folder_file([], "App/Gone"))
        self.check("App/Gone")
        with self.assertRaises(bc_run.RunError):
            self.batch()


class CommandLine(WalkRunTest):
    def call(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc_walk.main(list(args))
        return code, json.loads(out.getvalue())

    def test_both_subcommands_print_one_json_object(self):
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        code, checked = self.call("check-cases", str(self.run), "--folder", "App/Login")
        self.assertEqual((code, checked["ok"]), (0, True))
        code, batched = self.call("batch", str(self.run))
        self.assertEqual((code, batched["ok"], len(batched["batches"])), (0, True, 1))

    def test_an_error_is_one_json_object_and_exit_1(self):
        code, result = self.call("batch", str(self.run))
        self.assertEqual((code, result["ok"]), (1, False))
        code, result = self.call("batch", str(Path(self.tmp.name, "not-a-run")))
        self.assertEqual((code, result["ok"]), (1, False))

    def test_the_script_runs_on_its_own(self):
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "bc_walk.py"), "check-cases", str(self.run), "--folder", "App/Login"],
            capture_output=True, text=True, env={**os.environ, "BC_HOME": str(Path(self.tmp.name, "bc-home"))},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(json.loads(done.stdout)["ok"])
        self.assertTrue(re.search(r"Fetch: App/Login → 2 cases", done.stdout))


if __name__ == "__main__":
    unittest.main()
