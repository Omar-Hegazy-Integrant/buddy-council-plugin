"""Progress logs are saved by bc_run.py, one plain command at a time, so neither onboarding nor V&V prompts.

    python3 -m unittest discover -s scripts/tests -v

Standard library only; they must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions.
"""

import contextlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))

import bc_run  # noqa: E402


class Progress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name, "project")
        (self.project / ".buddy-council").mkdir(parents=True)

    def save(self, log, patch):
        return bc_run.cmd_progress(str(self.project), log, json.dumps(patch))

    def read(self, name):
        return json.loads((self.project / ".buddy-council" / name).read_text())

    def test_onboarding_features_merge_by_name_and_the_rest_is_kept(self):
        self.save("onboarding", {"mode": "interactive", "current_feature_index": 0, "features": [
            {"name": "Login", "status": "pending", "user_notes": []},
            {"name": "Alarms", "status": "pending", "user_notes": []}]})
        self.save("onboarding", {"current_step_index": 3, "features": [
            {"name": "Alarms", "status": "completed", "assessment": {"questions_asked": 4, "correct": 3}}]})
        log = self.read("onboarding-progress.json")
        self.assertEqual([(f["name"], f["status"]) for f in log["features"]], [("Login", "pending"), ("Alarms", "completed")])
        self.assertEqual(log["features"][1]["assessment"]["correct"], 3)
        self.assertEqual((log["version"], log["mode"], log["current_step_index"]), (1, "interactive", 3))
        self.assertRegex(log["last_updated"], r"^\d{4}-\d\d-\d\dT")

    def test_vnv_stories_merge_by_dev_key_and_a_redraft_replaces_the_scenarios(self):
        self.save("vnv", {"sprint": "Sprint 14", "stories": [
            {"dev_key": "PROJ-1", "state": "pending-scenario-validation",
             "scenarios": [{"id": "S1", "title": "Old"}, {"id": "S2", "title": "Gone"}]},
            {"dev_key": "PROJ-2", "state": "pending-questions"}]})
        self.save("vnv", {"stories": [{"dev_key": "PROJ-1", "scenarios": [{"id": "S1", "title": "New"}],
                                        "approved_by": None}, {"dev_key": "PROJ-3", "state": "pending-validation"}]})
        log = self.read("vnv-progress.json")
        stories = {s["dev_key"]: s for s in log["stories"]}
        self.assertEqual(list(stories), ["PROJ-1", "PROJ-2", "PROJ-3"])
        self.assertEqual(stories["PROJ-1"]["scenarios"], [{"id": "S1", "title": "New"}])
        self.assertEqual(stories["PROJ-1"]["state"], "pending-scenario-validation")
        self.assertIsNone(stories["PROJ-1"]["approved_by"])
        self.assertIn("updated_at", log)

    def test_an_apostrophe_travels_as_u0027_and_lands_as_itself(self):
        bc_run.cmd_progress(str(self.project), "vnv", '{"stories": [{"dev_key": "PROJ-1", "note": "the dev\\u0027s answer"}]}')
        self.assertEqual(self.read("vnv-progress.json")["stories"][0]["note"], "the dev's answer")

    def test_a_broken_log_is_never_overwritten(self):
        path = self.project / ".buddy-council" / "onboarding-progress.json"
        path.write_text("{broken")
        with self.assertRaises(bc_run.RunError):
            self.save("onboarding", {"current_step_index": 1})
        self.assertEqual(path.read_text(), "{broken")

    def test_a_patch_that_isnt_a_json_object_is_refused(self):
        for text in ("[1, 2]", "{'single': 'quotes'}", ""):
            with self.assertRaises(bc_run.RunError):
                bc_run.cmd_progress(str(self.project), "vnv", text)

    def test_a_folder_never_set_up_is_refused(self):
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_progress(self.tmp.name, "onboarding", "{}")

    def test_logs_are_private(self):
        self.save("vnv", {"sprint": "S"})
        mode = stat.S_IMODE((self.project / ".buddy-council" / "vnv-progress.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_notes_append_and_archive_sets_both_aside(self):
        self.save("onboarding", {"features": [{"name": "Login", "status": "in_progress"}]})
        bc_run.cmd_note(str(self.project), "Login — feature start", "Started the Login walkthrough.")
        bc_run.cmd_note(str(self.project), "Login — note", "Revisit lockout.")
        notes = (self.project / ".buddy-council" / "onboarding-notes.md").read_text()
        self.assertEqual(notes.count("\n---\n"), 2)
        self.assertRegex(notes, r"^## \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ — Login — feature start\n\nStarted")
        archived = bc_run.cmd_archive(str(self.project), "onboarding")["archived"]
        self.assertEqual(len(archived), 2)
        self.assertFalse((self.project / ".buddy-council" / "onboarding-progress.json").exists())
        self.assertTrue(all(Path(path).exists() for path in archived))

    def test_the_command_line_prints_one_json_object(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bc_run.main(["progress", "--project", str(self.project), "--log", "vnv",
                                "--patch", '{"stories": [{"dev_key": "PROJ-9"}]}'])
        self.assertEqual((code, json.loads(out.getvalue())["stories"]), (0, 1))

    def test_a_git_project_keeps_the_logs_out_of_git(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            self.skipTest("git isn't installed")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.save("vnv", {"sprint": "S"})
        exclude = (self.project / ".git" / "info" / "exclude").read_text()
        self.assertIn(".buddy-council/", exclude.splitlines())
        status = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")


if __name__ == "__main__":
    unittest.main()
