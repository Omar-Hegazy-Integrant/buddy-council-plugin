"""Tests for bc_walk.py — checking the fetched folders, batching their cases, and validating and merging the
extractors' fragments into the graph. Standard library only.

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


LOGIN_FRAGMENT = {
    "batch": "batch-001",
    "conditions": [{"name": "Server error", "description": "the server answers with an error", "sources": ["TC-4"]}],
    "screens": [
        {
            "name": "Login screen",
            "sources": ["TC-3", "TC-4"],
            "states": [
                {"name": "default", "sources": ["TC-3"]},
                {"name": "Error", "requires": ["Server error"], "sources": ["TC-4"]},
            ],
            "components": [
                {"name": "Sign in", "kind": "button", "sources": ["TC-4", "TC-3"]},
                {"name": "Error banner", "kind": "banner", "states": ["Error"], "sources": ["TC-4"]},
            ],
            "labels": [
                {"text_hint": "Wrong password", "purpose": "error message", "states": ["Error"], "sources": ["TC-4"]},
            ],
        },
        {"name": "Home", "sources": ["TC-3"]},
    ],
    "entries": [{"screen": "Login", "sources": ["TC-3"]}],
    "transitions": [
        {"from": {"screen": "Login"}, "to": {"screen": "Home"}, "action": "tap", "component": "Sign in",
         "sources": ["TC-3"]},
        {"from": {"screen": "Login"}, "to": {"screen": "Login", "state": "Error"}, "action": "tap",
         "component": "Sign in", "requires": ["Server error"], "sources": ["TC-4"]},
    ],
}

SETTINGS_FRAGMENT = {
    "batch": "batch-002",
    "conditions": [{"name": "server error!", "description": "Server returns an error", "sources": ["TC-2"]}],
    "screens": [
        {"name": "Home page", "sources": ["TC-1"],
         "components": [{"name": "Settings", "kind": "tab", "sources": ["TC-1"]}]},
        {"name": "Settings", "platforms": ["ios"], "sources": ["TC-1"],
         "components": [{"name": "Save button", "kind": "button", "sources": ["TC-1"]},
                        {"name": "Units", "kind": "switch", "sources": ["TC-1"]}]},
        {"name": "Preferences", "platforms": ["android"], "sources": ["TC-2"],
         "components": [{"name": "Save", "kind": "button", "sources": ["TC-2"]},
                        {"name": "Units", "kind": "switch", "sources": ["TC-2"]}]},
    ],
    "transitions": [
        {"from": {"screen": "Home"}, "to": {"screen": "Settings"}, "action": "tap", "component": "Settings",
         "platforms": ["ios"], "sources": ["TC-1"]},
        {"from": {"screen": "Home"}, "to": {"screen": "Preferences"}, "action": "tap", "component": "Settings",
         "platforms": ["android"], "sources": ["TC-2"]},
        {"from": {"screen": "Preferences"}, "to": {"screen": "Preferences"}, "action": "external",
         "instruction": "Turn off the network", "sets": ["Server error"], "sources": ["TC-2"]},
    ],
}


class FragmentRunTest(WalkRunTest):
    """A run with two batches: batch-001 is App/Login (TC-3, TC-4), batch-002 is App/Settings (TC-1, TC-2)."""

    def setUp(self):
        super().setUp()
        self.save("App/Login", folder_file(LOGIN, "App/Login"))
        self.save("App/Settings", folder_file(SETTINGS, "App/Settings"))
        self.check("App/Login", "App/Settings")
        self.batched = bc_walk.cmd_batch(str(self.run))

    def fragment(self, batch_id, payload):
        path = self.run / "fragments" / f"{batch_id}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def validate(self, batch_id):
        return bc_walk.cmd_validate(str(self.run), batch_id)

    def merge(self, same=None):
        return bc_walk.cmd_merge(str(self.run), same)

    def graph(self):
        return json.loads((self.run / "graph.json").read_text())

    def screen(self, graph, sid):
        return next(s for s in graph["screens"] if s["id"] == sid)

    def node(self, items, nid):
        return next(i for i in items if i["id"] == nid)


class Validate(FragmentRunTest):
    def test_each_batch_names_the_fragment_file_its_extractor_writes(self):
        batches = json.loads((self.run / "batches.json").read_text())["batches"]
        self.assertEqual([b["id"] for b in self.batched["batches"]], ["batch-001", "batch-002"])
        for printed, saved in zip(self.batched["batches"], batches):
            self.assertEqual(printed["fragment"], str(self.run / "fragments" / f"{saved['id']}.json"))
            self.assertEqual(saved["fragment"], printed["fragment"])

    def test_a_good_fragment_passes(self):
        self.fragment("batch-001", LOGIN_FRAGMENT)
        result = self.validate("batch-001")
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["status"], "ok")

    def test_a_case_no_item_cites_is_a_warning(self):
        fragment = json.loads(json.dumps(LOGIN_FRAGMENT))
        fragment["conditions"] = []
        fragment["screens"] = [{"name": "Home", "sources": ["TC-3"]}]
        fragment["entries"], fragment["transitions"] = [], []
        self.fragment("batch-001", fragment)
        result = self.validate("batch-001")
        self.assertTrue(result["valid"])
        self.assertEqual(result["uncited"], ["TC-4"])

    def test_a_malformed_fragment_lists_every_error(self):
        bad = {
            "batch": "batch-009",
            "extra": 1,
            "conditions": [{"name": "Offline", "sources": ["TC-3"]}],
            "screens": [
                {"sources": ["TC-3"]},
                {"name": "Login", "platforms": ["windows"], "sources": ["TC-99"],
                 "components": [{"name": "Go", "kind": "button", "states": ["Nowhere"], "sources": ["TC-3"]}],
                 "labels": [{"text_hint": "Hi", "sources": ["TC-3"]}]},
            ],
            "transitions": [
                {"from": {"screen": "Login"}, "to": {"screen": "Mars"}, "action": "fly", "sources": ["TC-3"]},
                {"from": {"screen": "Login"}, "to": {"screen": "Login"}, "action": "tap", "sources": ["TC-3"]},
                {"from": {"screen": "Login"}, "to": {"screen": "Login"}, "action": "external",
                 "requires": ["Rain"], "sources": []},
            ],
        }
        self.fragment("batch-001", bad)
        result = self.validate("batch-001")
        self.assertFalse(result["valid"])
        errors = "\n".join(result["errors"])
        for expected in (
            "batch: is 'batch-009', not 'batch-001'",
            "extra: not in the schema",
            "conditions[0].description: missing",
            "screens[0].name: missing",
            "screens[1].platforms[0]: 'windows' is not one of ios, android",
            "screens[1].sources: TC-99 is not a case in batch-001",
            "screens[1].components[0].states: 'Nowhere' is not a state of screen 'Login'",
            "screens[1].labels[0].purpose: missing",
            "transitions[0].to: no screen 'Mars' in this fragment",
            "transitions[0].action: 'fly' is not one of tap, type, select, scroll, back, external",
            "transitions[1].component: a tap needs the component it acts on",
            "transitions[2].instruction: an external step needs an instruction for a person",
            "transitions[2].requires: no condition 'Rain' in this fragment",
            "transitions[2].sources: empty",
        ):
            self.assertIn(expected, errors)

    def test_a_fragment_that_is_not_json_or_missing_fails(self):
        self.fragment("batch-001", "{not json")
        self.assertIn("not valid JSON", self.validate("batch-001")["errors"][0])
        result = self.validate("batch-002")
        self.assertFalse(result["valid"])
        self.assertIn("no fragment", result["errors"][0])

    def test_a_batch_is_re_asked_once_then_its_cases_are_reported(self):
        self.fragment("batch-002", {"batch": "batch-002"})
        first = self.validate("batch-002")
        self.assertEqual((first["valid"], first["attempts"], first["retry"]), (False, 1, True))
        self.assertEqual(self.validate("batch-002")["attempts"], 1, "the same file again is not a new attempt")
        self.fragment("batch-002", {"batch": "batch-002", "screens": "none"})
        second = self.validate("batch-002")
        self.assertEqual((second["valid"], second["attempts"], second["retry"]), (False, 2, False))
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["case_ids"], ["TC-1", "TC-2"])
        self.assertIn("TC-1, TC-2", second["line"])

    def test_batching_again_drops_the_old_fragments(self):
        self.fragment("batch-001", LOGIN_FRAGMENT)
        self.validate("batch-001")
        bc_walk.cmd_batch(str(self.run))
        self.assertFalse((self.run / "fragments" / "batch-001.json").exists())
        self.assertEqual(self.validate("batch-001")["attempts"], 1)

    def test_an_unknown_batch_is_refused(self):
        with self.assertRaises(bc_run.RunError):
            self.validate("batch-404")


class Merge(FragmentRunTest):
    def setUp(self):
        super().setUp()
        self.fragment("batch-001", LOGIN_FRAGMENT)
        self.fragment("batch-002", SETTINGS_FRAGMENT)

    def test_fragments_merge_into_one_graph_by_normalized_name(self):
        result = self.merge()
        graph = self.graph()
        self.assertEqual(result["graph"], str(self.run / "graph.json"))
        self.assertEqual([s["id"] for s in graph["screens"]], ["home", "login", "preferences", "settings"])
        home = self.screen(graph, "home")
        self.assertEqual(home["sources"], ["TC-1", "TC-3"], "Home and Home page are one screen")
        self.assertEqual(home["name"], "Home")
        self.assertEqual([s["id"] for s in home["states"]], ["home.default"])
        self.assertEqual(graph["conditions"], [{"id": "cond.server_error", "description": "Server returns an error",
                                                "sources": ["TC-2", "TC-4"]}])
        login = self.screen(graph, "login")
        self.assertEqual([s["id"] for s in login["states"]], ["login.default", "login.error"])
        self.assertEqual(self.node(login["states"], "login.error")["requires"], ["cond.server_error"])
        sign_in = self.node(login["components"], "login.sign_in_button")
        self.assertEqual(sign_in["sources"], ["TC-3", "TC-4"])
        self.assertNotIn("only_in", sign_in, "it is in every state of its screen")
        self.assertEqual(self.node(login["components"], "login.error_banner")["only_in"], ["login.error"])
        self.assertEqual(login["labels"], [{"id": "login.label.wrong_password", "text_hint": "Wrong password",
                                            "purpose": "error message", "only_in": ["login.error"],
                                            "platforms": ["ios", "android"], "sources": ["TC-4"]}])
        self.assertIn({"id": "entry.login.default", "state": "login.default", "conditions": [],
                       "platforms": ["ios", "android"], "sources": ["TC-3"]}, graph["entries"])
        failing = [t for t in graph["transitions"] if t["to_state"] == "login.error"]
        self.assertEqual(failing, [{"from_state": "login.default", "to_state": "login.error", "action": "tap",
                                    "component": "login.sign_in_button", "requires": ["cond.server_error"],
                                    "sets": [], "clears": [], "platforms": ["ios", "android"],
                                    "sources": ["TC-4"]}])
        external = next(t for t in graph["transitions"] if t["action"] == "external")
        self.assertEqual(external["instruction"], "Turn off the network")
        self.assertEqual(external["sets"], ["cond.server_error"])
        self.assertNotIn("component", external)
        self.assertEqual(result["counts"]["screens"], 4)

    def test_platforms_default_to_both(self):
        self.merge()
        graph = self.graph()
        self.assertEqual(self.screen(graph, "home")["platforms"], ["ios", "android"])
        self.assertEqual(self.screen(graph, "settings")["platforms"], ["ios"])
        self.assertEqual(self.screen(graph, "preferences")["platforms"], ["android"])

    def test_output_is_sorted_and_the_same_whatever_the_order(self):
        self.merge()
        first = (self.run / "graph.json").read_text()
        graph = json.loads(first)
        for key in ("entries", "conditions", "screens"):
            ids = [n["id"] for n in graph[key]]
            self.assertEqual(ids, sorted(ids))
        for screen in graph["screens"]:
            for key in ("states", "components", "labels"):
                ids = [n["id"] for n in screen[key]]
                self.assertEqual(ids, sorted(ids))
        edges = [(t["from_state"], t["to_state"], t["action"]) for t in graph["transitions"]]
        self.assertEqual(edges, sorted(edges))
        shuffled = json.loads(json.dumps(LOGIN_FRAGMENT))
        for key in ("screens", "transitions", "conditions"):
            shuffled[key].reverse()
        for screen in shuffled["screens"]:
            for key in ("states", "components"):
                screen.get(key, []).reverse()
        self.fragment("batch-001", shuffled)
        self.merge()
        self.assertEqual((self.run / "graph.json").read_text(), first)

    def test_near_duplicates_are_listed_and_settled_by_the_answers(self):
        result = self.merge()
        pairs = result["near_duplicates"]
        self.assertEqual([(p["kind"], p["ids"]) for p in pairs], [("screen", ["preferences", "settings"])])
        self.assertEqual(pairs[0]["names"], ["Preferences", "Settings"])
        self.assertTrue((self.run / "near-duplicates.json").exists())

        result = self.merge(json.dumps([["settings", "preferences"]]))
        self.assertEqual(result["near_duplicates"], [])
        graph = self.graph()
        self.assertEqual([s["id"] for s in graph["screens"]], ["home", "login", "settings"])
        settings = self.screen(graph, "settings")
        self.assertEqual(settings["platforms"], ["ios", "android"])
        self.assertEqual(settings["sources"], ["TC-1", "TC-2"])
        self.assertEqual([c["id"] for c in settings["components"]], ["settings.save_button", "settings.units_switch"])
        into = [t for t in graph["transitions"] if t["from_state"] == "home.default"]
        self.assertEqual(len(into), 1, "the two platforms' taps into the screen are one transition now")
        self.assertEqual((into[0]["to_state"], into[0]["platforms"], into[0]["sources"]),
                         ("settings.default", ["ios", "android"], ["TC-1", "TC-2"]))
        external = next(t for t in graph["transitions"] if t["action"] == "external")
        self.assertEqual((external["from_state"], external["to_state"]), ("settings.default", "settings.default"))

        again = self.merge()
        self.assertEqual(again["near_duplicates"], [], "the answers are kept for the next merge")
        self.assertEqual([s["id"] for s in self.graph()["screens"]], ["home", "login", "settings"])

    def test_a_listed_pair_left_out_of_the_answers_is_kept_apart(self):
        self.merge()
        self.merge("[]")
        self.assertEqual(self.merge()["near_duplicates"], [])
        self.assertIn("preferences", [s["id"] for s in self.graph()["screens"]])

    def test_an_answer_about_a_pair_not_on_the_list_is_refused(self):
        self.merge()
        before = (self.run / "graph.json").read_text()
        for answer in ('[["home", "login"]]', '{"settings": "preferences"}', "not json"):
            with self.assertRaises(bc_run.RunError):
                self.merge(answer)
        self.assertEqual((self.run / "graph.json").read_text(), before)

    def test_near_identical_labels_on_one_state_merge(self):
        fragment = {
            "batch": "batch-001",
            "screens": [{
                "name": "Login", "sources": ["TC-3", "TC-4"],
                "states": [{"name": "default", "sources": ["TC-3"]}, {"name": "Error", "sources": ["TC-4"]}],
                "labels": [
                    {"text_hint": "Wrong password!", "purpose": "error", "states": ["Error"], "sources": ["TC-4"]},
                    {"text_hint": "wrong  password", "purpose": "error", "states": ["Error"], "sources": ["TC-3"]},
                    {"text_hint": "Wrong passwords", "purpose": "error", "states": ["Error"], "sources": ["TC-3"]},
                    {"text_hint": "Wrong passwords", "purpose": "hint", "states": ["default"], "sources": ["TC-3"]},
                ],
            }],
        }
        self.fragment("batch-001", fragment)
        (self.run / "fragments" / "batch-002.json").unlink()
        self.merge()
        login = self.screen(self.graph(), "login")
        self.assertEqual([(label["text_hint"], label["sources"]) for label in login["labels"]],
                         [("Wrong password!", ["TC-3", "TC-4"]), ("Wrong passwords", ["TC-3"])])

    def test_bad_or_missing_fragments_are_left_out_and_their_cases_named(self):
        self.fragment("batch-002", {"batch": "batch-002", "screens": "none"})
        result = self.merge()
        self.assertEqual(result["left_out"], [{"batch": "batch-002", "case_ids": ["TC-1", "TC-2"],
                                               "reason": "the fragment does not match the schema"}])
        self.assertTrue(any("TC-1, TC-2" in line for line in result["lines"]))
        self.assertEqual([s["id"] for s in self.graph()["screens"]], ["home", "login"])
        (self.run / "fragments" / "batch-001.json").unlink()
        with self.assertRaises(bc_run.RunError):
            self.merge()

    def test_a_component_with_a_different_control_per_platform_is_two_sharing_a_role(self):
        fragment = {
            "batch": "batch-001",
            "screens": [{"name": "Login", "sources": ["TC-3"], "components": [
                {"name": "Remember me", "kind": "switch", "role": "remember", "platforms": ["ios"], "sources": ["TC-3"]},
                {"name": "Remember me", "kind": "checkbox", "role": "remember", "platforms": ["android"],
                 "sources": ["TC-4"]},
            ]}],
        }
        self.fragment("batch-001", fragment)
        (self.run / "fragments" / "batch-002.json").unlink()
        result = self.merge()
        login = self.screen(self.graph(), "login")
        self.assertEqual([(c["id"], c["role"], c["platforms"]) for c in login["components"]],
                         [("login.remember_me_checkbox", "remember", ["android"]),
                          ("login.remember_me_switch", "remember", ["ios"])])
        self.assertEqual(result["near_duplicates"], [], "they are meant to be two")


class WalkCommandLine(FragmentRunTest):
    def call(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc_walk.main(list(args))
        return code, json.loads(out.getvalue())

    def test_validate_and_merge_print_one_json_object(self):
        self.fragment("batch-001", LOGIN_FRAGMENT)
        self.fragment("batch-002", SETTINGS_FRAGMENT)
        code, result = self.call("validate", str(self.run), "batch-001")
        self.assertEqual((code, result["ok"], result["valid"]), (0, True, True))
        code, result = self.call("merge", str(self.run))
        self.assertEqual((code, result["ok"]), (0, True))
        code, result = self.call("merge", str(self.run), "--same", '[["settings", "preferences"]]')
        self.assertEqual((code, result["ok"], result["counts"]["screens"]), (0, True, 3))
        code, result = self.call("merge", str(self.run), "--same", "nope")
        self.assertEqual((code, result["ok"]), (1, False))

    def test_the_schema_is_written_once_in_the_script(self):
        self.assertIn("screens", bc_walk.FRAGMENT_SCHEMA)
        self.assertEqual(bc_walk.ACTIONS, ("tap", "type", "select", "scroll", "back", "external"))


if __name__ == "__main__":
    unittest.main()
