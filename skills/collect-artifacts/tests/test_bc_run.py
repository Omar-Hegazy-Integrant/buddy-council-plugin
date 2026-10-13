"""Tests for bc_run.py — run folders, GitHub enrichment, and linking. Standard library only.

    python3 -m unittest discover -s skills/collect-artifacts/tests -v

They must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions.
"""

import base64
import datetime
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bc_run  # noqa: E402


def requirement(rid, title, feature, description="", **raw):
    return {
        "type": "requirement",
        "id": rid,
        "title": title,
        "description": description or f"The system shall {title.lower()}.",
        "rationale": "",
        "feature": feature,
        "status": "Approved",
        "linked_ids": [],
        "raw_fields": {"item_type": "Requirement", "feature_path": f"Product / {feature}", **raw},
    }


def case(cid, title, links="", section="Alarms", refs="", steps=None):
    return {
        "type": "test_case",
        "id": f"TC-{cid}",
        "title": title,
        "description": "Preconditions: device paired",
        "feature": section.split("/")[-1],
        "status": "",
        "linked_ids": [t.strip() for t in links.split(",") if t.strip()],
        "raw_fields": {
            "section_path": section,
            "refs": refs,
            "steps": steps if steps is not None else [{"content": "Trigger it", "expected": "It happens"}],
        },
    }


REQUIREMENTS = [
    requirement("CWA-REQ-1", "Sound an audible alarm", "Alarms"),
    requirement("CWA-REQ-2", "Show a visual alarm", "Alarms"),
    requirement("CWA-REQ-3", "Silence alarms for 5 minutes", "Alarms"),
    requirement("CWA-REQ-4", "Display heart rate", "Vitals"),
    requirement("CWA-REQ-5", "Display SpO2", "Vitals"),
]
CASES = [
    case(10, "Audible alarm sounds", "CWA-REQ-1"),
    case(11, "Visual and audible together", "cwa-req-1, CWA-REQ-2"),
    case(12, "Alarm with a stranded link", refs="CWA-REQ-3, PROJ-12"),
    case(13, "Alarm orphan, no refs"),
    case(14, "Heart rate shows", "CWA-REQ-4", section="Vitals/Heart"),
    case(15, "Legacy link", "OLD-SRS-9", section="Legacy"),
    case(16, "Elsewhere orphan", section="Login"),
]


class RunFolderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name, "project")
        (self.project / ".buddy-council").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def new_run(self, command="coverage", scope=""):
        return Path(bc_run.cmd_new(str(self.project), command, scope)["run_dir"])

    def write(self, run, name, payload):
        (run / name).write_text(json.dumps(payload))


class New(RunFolderTest):
    def test_a_run_folder_is_created_privately_with_a_manifest(self):
        result = bc_run.cmd_new(str(self.project), "Coverage!", "Alarms")
        run = Path(result["run_dir"])
        self.assertEqual(run.parent, (self.project / ".buddy-council" / "runs").resolve())
        self.assertRegex(run.name, r"^\d{8}-\d{6}-coverage$")
        self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o700)
        manifest = json.loads((run / "manifest.json").read_text())
        self.assertEqual((manifest["command"], manifest["scope"]), ("Coverage!", "Alarms"))
        self.assertEqual(result["requirements"], str(run / "requirements.json"))
        second = Path(bc_run.cmd_new(str(self.project), "coverage", "")["run_dir"])
        self.assertNotEqual(run, second, "two runs in the same second get distinct folders")

    def test_a_project_without_setup_is_refused(self):
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_new(self.tmp.name, "coverage", "")

    def test_old_runs_are_pruned_and_nothing_else_is_touched(self):
        runs = self.project / ".buddy-council" / "runs"
        runs.mkdir()
        old = [runs / f"2026010{n}-000000-coverage" for n in range(1, 9)]
        for folder in old:
            folder.mkdir()
            (folder / "requirements.json").write_text("[]")
        stale = runs / "20250101-000000-coverage"
        stale.mkdir()
        os.utime(stale, (time.time() - 30 * 86400,) * 2)
        keep_me = runs / "notes"
        keep_me.mkdir()
        outside = Path(self.tmp.name, "outside")
        outside.mkdir()
        (runs / "20991231-000000-link").symlink_to(outside)
        with mock.patch.object(bc_run, "MAX_RUN_AGE_SECONDS", 10 ** 12):
            result = bc_run.cmd_new(str(self.project), "coverage", "")
        remaining = sorted(p.name for p in runs.iterdir() if p.is_dir() and not p.is_symlink() and p.name != "notes")
        self.assertEqual(len(remaining), 5)
        self.assertIn(Path(result["run_dir"]).name, remaining)
        self.assertTrue(keep_me.exists() and outside.exists())
        bc_run.cmd_new(str(self.project), "coverage", "")
        self.assertFalse(stale.exists(), "a run older than a week goes regardless of count")


class Link(RunFolderTest):
    def run_all(self):
        run = self.new_run()
        self.write(run, "requirements.json", {"kind": "requirements", "scope": {"input": "", "kind": "all"}, "requirements": REQUIREMENTS})
        self.write(run, "test_cases.json", {"kind": "test_cases", "test_cases": CASES})
        return run, bc_run.cmd_link(str(run))

    def test_counts_untested_orphans_and_stranded_links(self):
        run, result = self.run_all()
        counts = result["counts"]
        self.assertEqual(
            {k: counts[k] for k in ("requirements", "test_cases_in_scope", "tested", "untested", "orphans", "stranded")},
            {"requirements": 5, "test_cases_in_scope": 7, "tested": 3, "untested": 2, "orphans": 3, "stranded": 1},
        )
        self.assertEqual(result["readiness"], 'Readiness: 5 requirements, 7 test cases for scope "all"')
        links = json.loads((run / "links.json").read_text())
        self.assertEqual(links["by_requirement"], {"CWA-REQ-1": ["TC-10", "TC-11"], "CWA-REQ-2": ["TC-11"], "CWA-REQ-4": ["TC-14"]})
        self.assertEqual([u["id"] for u in links["untested"]], ["CWA-REQ-3", "CWA-REQ-5"])
        self.assertTrue(links["untested"][0]["stranded"], "CWA-REQ-3 is only untested because its link is stranded")
        self.assertEqual(links["stranded"][0]["stranded_ids"], ["CWA-REQ-3"])
        self.assertEqual({o["id"]: o["feature"] for o in links["orphans"]}, {"TC-12": "Alarms", "TC-13": "Alarms", "TC-16": "No feature"})
        self.assertEqual(links["unknown_linked_ids"], [{"id": "old-srs-9", "cases": 1}])
        self.assertTrue(any("not among the fetched requirements" in w for w in links["warnings"]))
        rows = {r["feature"]: r for r in links["features"]}
        self.assertEqual((rows["Alarms"]["tested"], rows["Alarms"]["untested"], rows["Alarms"]["orphans"]), (2, 1, 2))
        self.assertEqual(rows["Vitals"]["coverage_pct"], 50.0)
        self.assertEqual(rows["No feature"]["orphans"], 1)
        manifest = json.loads((run / "manifest.json").read_text())
        self.assertEqual(manifest["readiness"], result["readiness"])

    def test_deprecated_and_deferred_requirements_are_never_untested(self):
        run = self.new_run()
        reqs = [dict(r) for r in REQUIREMENTS]
        reqs[2] = {**reqs[2], "status": "Deprecated"}
        reqs[4] = {**reqs[4], "status": "deferred to 2.0"}
        self.write(run, "requirements.json", {"requirements": reqs})
        self.write(run, "test_cases.json", {"test_cases": CASES})
        counts = bc_run.cmd_link(str(run))["counts"]
        self.assertEqual((counts["requirements"], counts["tested"], counts["untested"], counts["retired"]), (5, 3, 0, 2))
        self.assertEqual(counts["coverage_pct"], 100.0)
        links = json.loads((run / "links.json").read_text())
        self.assertEqual(links["untested"], [])
        self.assertEqual({r["feature"]: r["retired"] for r in links["features"] if r["requirements"]}, {"Alarms": 1, "Vitals": 1})

    def test_bundles_hold_verbatim_text_with_each_case_once(self):
        run, _ = self.run_all()
        alarms = (run / "bundles" / "01-alarms-1.md").read_text()
        self.assertTrue(alarms.startswith("# Feature 1 of 3: Alarms\n3 requirements · 2 tested · 1 untested · 2 orphan test cases"))
        self.assertIn("## CWA-REQ-1 — Sound an audible alarm", alarms)
        self.assertIn("The system shall sound an audible alarm.", alarms)
        self.assertEqual(alarms.count("### TC-11 — Visual and audible together"), 1)
        self.assertIn("### TC-11 — shown above, under CWA-REQ-1", alarms)
        self.assertIn("Test cases: none — UNTESTED", alarms)
        self.assertIn("Requirement ID stranded in References: CWA-REQ-3", alarms)
        self.assertIn("1. Trigger it\n   Expected: It happens", alarms)
        self.assertTrue((run / "bundles" / "03-no-feature-1.md").exists())
        self.assertEqual(stat.S_IMODE((run / "bundles" / "01-alarms-1.md").stat().st_mode), 0o600)

    def test_a_feature_scope_keeps_only_its_cases(self):
        run = self.new_run(scope="Alarms")
        alarms = [r for r in REQUIREMENTS if r["feature"] == "Alarms"]
        self.write(run, "requirements.json", {"scope": {"input": "Alarms", "kind": "feature"}, "requirements": alarms})
        self.write(run, "test_cases.json", {"test_cases": CASES})
        result = bc_run.cmd_link(str(run))
        links = json.loads((run / "links.json").read_text())
        self.assertEqual(result["counts"]["test_cases_in_scope"], 4)
        self.assertEqual(result["counts"]["test_cases_read"], 7)
        self.assertEqual(sorted(o["id"] for o in links["orphans"]), ["TC-12", "TC-13"])
        self.assertEqual(links["unknown_linked_ids"], [], "other features' links are not unknown in a scoped run")
        self.assertEqual(result["readiness"], 'Readiness: 3 requirements, 4 test cases for scope "Alarms"')

    def test_a_sub_folder_scope_finds_its_unlinked_cases(self):
        run = self.new_run(scope="Heart")
        self.write(run, "requirements.json", {"scope": {"input": "Heart", "kind": "feature"}, "requirements": [REQUIREMENTS[3]]})
        self.write(run, "test_cases.json", {"test_cases": CASES + [case(17, "Unlinked heart case", section="Vitals/Heart")]})
        bc_run.cmd_link(str(run))
        links = json.loads((run / "links.json").read_text())
        self.assertEqual([o["id"] for o in links["orphans"]], ["TC-17"])

    def test_an_excel_array_uses_the_run_scope(self):
        run = self.new_run(scope="Vitals")
        self.write(run, "requirements.json", [r for r in REQUIREMENTS if r["feature"] == "Vitals"])
        self.write(run, "test_cases.json", {"test_cases": CASES})
        result = bc_run.cmd_link(str(run))
        self.assertEqual(result["counts"]["test_cases_in_scope"], 1)
        self.assertEqual(result["readiness"], 'Readiness: 2 requirements, 1 test cases for scope "Vitals"')

    def test_missing_test_cases_are_reported_not_hidden(self):
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        result = bc_run.cmd_link(str(run))
        self.assertEqual(result["counts"]["test_cases_in_scope"], 0)
        self.assertTrue(any("test_cases.json is missing" in w for w in result["warnings"]))

    def test_missing_requirements_fail(self):
        run = self.new_run()
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_link(str(run))

    def test_large_features_split_into_parts_that_fit_one_read(self):
        run = self.new_run()
        long_text = "The pump shall stop — “safely” — when occluded. " * 40
        many = [requirement(f"CWA-REQ-{n}", f"Requirement {n}", "Pump", long_text) for n in range(1, 41)]
        cases = [case(100 + n, f"Case {n}", f"CWA-REQ-{n}", section="Pump") for n in range(1, 41)]
        self.write(run, "requirements.json", {"requirements": many})
        self.write(run, "test_cases.json", {"test_cases": cases})
        result = bc_run.cmd_link(str(run))
        self.assertGreater(result["features"][0]["parts"], 3)
        parts = sorted((run / "bundles").glob("01-pump-*.md"))
        for part in parts:
            self.assertLess(len(part.read_bytes()), bc_run.PART_BYTES + 600, part.name)
        joined = "".join(p.read_text() for p in parts)
        for n in range(1, 41):
            self.assertIn(f"## CWA-REQ-{n} — Requirement {n}\n", joined)
            self.assertIn(f"### TC-{100 + n} — Case {n}", joined)
        self.assertIn("— part 2 of", parts[1].read_text().splitlines()[0])

    def test_linked_docs_are_listed_under_their_requirement(self):
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS[:1]})
        self.write(run, "test_cases.json", {"test_cases": CASES[:1]})
        self.write(run, "docs.json", {
            "docs": [{"url": "https://github.com/o/r/blob/main/a.md", "files": ["docs/01-a-1.md", "docs/01-a-2.md"], "truncated": True, "requirement_ids": ["CWA-REQ-1"]}],
            "failed": [{"url": "https://github.com/o/r/blob/main/b.md", "error": "file_not_found", "requirement_ids": ["CWA-REQ-1"]}],
        })
        bc_run.cmd_link(str(run))
        bundle = (run / "bundles" / "01-alarms-1.md").read_text()
        self.assertIn("Linked doc: https://github.com/o/r/blob/main/a.md → docs/01-a-1.md, docs/01-a-2.md (truncated)", bundle)
        self.assertIn("Linked doc not in this run: https://github.com/o/r/blob/main/b.md (file_not_found)", bundle)
        self.assertIn("Docs linked from this feature — read them with it: docs/01-a-1.md, docs/01-a-2.md", bundle.splitlines()[2])
        links = json.loads((run / "links.json").read_text())
        self.assertEqual(links["features"][0]["docs"], ["docs/01-a-1.md", "docs/01-a-2.md"])

    def test_custom_fields_are_shown_and_noise_is_not(self):
        run = self.new_run()
        req = requirement("CWA-REQ-1", "Sound an audible alarm", "Alarms")
        req["raw_fields"].update({
            "jama_id": 2001, "url": "https://jama/x", "sequence": "1.2", "modified_date": "2026-01-01",
            "Configuration": ["iOS", "Android"], "Applicable Products": ["Clinic"], "Hazard Ref": "N/A",
            "Internal Use": "", "Notes": "n" * 600,
        })
        tc = case(10, "Audible alarm sounds", "CWA-REQ-1")
        tc["raw_fields"].update({"priority": "High", "type": "Functional",
                                 "custom": {"Applicable OS": ["iOS"], "CtQ": True, "Ext Reference": "SRS-1"}})
        self.write(run, "requirements.json", {"requirements": [req]})
        self.write(run, "test_cases.json", {"test_cases": [tc]})
        bc_run.cmd_link(str(run))
        bundle = (run / "bundles" / "01-alarms-1.md").read_text()
        self.assertIn("Fields:\n- Configuration: iOS, Android\n- Applicable Products: Clinic\n- Notes: " + "n" * 160
                      + "… [440 more characters — search the run's JSON for the ID]\n", bundle)
        for noise in ("Hazard Ref", "Internal Use", "jama_id", "sequence", "https://jama/x"):
            self.assertNotIn(noise, bundle)
        self.assertIn("Folder: Alarms · Links: CWA-REQ-1 · Priority: High · Type: Functional", bundle)
        self.assertIn("Fields:\n- Applicable OS: iOS\n- CtQ: yes\n- Ext Reference: SRS-1", bundle)

    def test_unlinked_cases_are_found_in_folders_that_contain_the_feature_name(self):
        run = self.new_run(scope="Alarms")
        alarms = [r for r in REQUIREMENTS if r["feature"] == "Alarms"]
        self.write(run, "requirements.json", {"scope": {"input": "Alarms", "kind": "feature"}, "requirements": alarms})
        extra = [case(30, "iOS alarm orphan", section="Alarms - iOS/Audible"), case(31, "Not ours", section="Alarmsound"),
                 case(32, "Settings orphan", section="Alarm Settings")]
        self.write(run, "test_cases.json", {"test_cases": CASES + extra})
        bc_run.cmd_link(str(run))
        orphans = {o["id"] for o in json.loads((run / "links.json").read_text())["orphans"]}
        self.assertEqual(orphans, {"TC-12", "TC-13", "TC-30"})

    def test_the_longest_feature_name_in_a_folder_wins(self):
        run = self.new_run()
        reqs = [requirement("R-1", "One", "Alarms"), requirement("R-2", "Two", "Alarms Settings")]
        self.write(run, "requirements.json", {"requirements": reqs})
        self.write(run, "test_cases.json", {"test_cases": [case(40, "Orphan", section="Product/Alarms Settings - iOS")]})
        bc_run.cmd_link(str(run))
        self.assertEqual(json.loads((run / "links.json").read_text())["orphans"][0]["feature"], "Alarms Settings")

    def test_relinking_replaces_old_bundles(self):
        run, _ = self.run_all()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS[3:]})
        bc_run.cmd_link(str(run))
        self.assertEqual(sorted(p.name for p in (run / "bundles").glob("*.md"))[0], "01-vitals-1.md")
        self.assertFalse((run / "bundles" / "02-vitals-1.md").exists())

    def test_only_run_folders_are_accepted(self):
        for bad in (self.tmp.name, str(self.project / ".buddy-council")):
            with self.assertRaises(bc_run.RunError):
                bc_run.cmd_link(bad)


JAMA_CONFIG = {
    "requirements": {
        "provider": "jama", "project_id": 42, "item_type_filter": ["Software Requirement"],
        "field_mapping": {"status": "Status", "rationale": "Rationale", "github_url": None},
        "roots": [{"id": 7, "path": "Product / SRS"}], "feature_inference": {"depth": 1, "folder_item_type": "Folder"},
        "enrichment": {"enabled": True, "strategy": "cli"},
    },
    "test_cases": {"provider": "testrail", "project_id": 1, "suite_id": 9, "authoring": {"requirement_field": "custom_jama_req_id"}},
}


def jama_file(reqs, scope="", **args):
    base = {"item_type_filter": ["Software Requirement"], "item_type_exclude": [], "root_ids": [7], "exclude_ids": [],
            "feature_depth": 1, "feature_item_type": "Folder", "field_mapping": {"status": "Status", "rationale": "Rationale"}}
    return {"kind": "requirements", "source": "jama", "project_id": 42, "scope": {"input": scope, "kind": "all" if not scope else "feature"},
            "summary": "3 requirements across 2 features", "args": {**base, **args}, "requirements": reqs}


def testrail_file(cases, **overrides):
    return {"kind": "test_cases", "source": "testrail", "project_id": 1, "suite_id": 9,
            "requirement_field": "custom_jama_req_id", "filter": {"requirement_ids": [], "section": "", "unlinked": False},
            "test_cases": cases, **overrides}


class Checks(RunFolderTest):
    def setUp(self):
        super().setUp()
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(JAMA_CONFIG))

    def link(self, requirements_file, cases_file, docs=None, scope=""):
        run = self.new_run(scope=scope)
        self.write(run, "requirements.json", requirements_file)
        if cases_file is not None:
            self.write(run, "test_cases.json", cases_file)
        if docs is not None:
            self.write(run, "docs.json", docs)
        return bc_run.cmd_link(str(run))

    def test_a_run_that_matches_the_config_passes_and_reports_from_the_files(self):
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), docs={"docs": [{"truncated": True}], "failed": [{"url": "u"}]})
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["lines"], [
            "Fetch: requirements → 5 from Jama project 42 (3 requirements across 2 features)",
            "Fetch: test cases → 7 read from TestRail suite 9",
            "Enrichment: fetched 1 of 2 GitHub-linked requirement docs (1 truncated)",
            'Readiness: 5 requirements, 7 test cases for scope "all"',
        ])

    def test_wrong_arguments_are_named(self):
        result = self.link(
            jama_file(REQUIREMENTS, scope="Vitals", item_type_filter=[], field_mapping={}, root_ids=[]),
            testrail_file(CASES, suite_id=10, requirement_field="custom_refs", filter={"requirement_ids": ["X-1"]}),
        )
        text = "\n".join(result["mismatches"])
        for expected in ("item_type_filter=none", "root_ids=none", "field_mapping=none", "scope 'Vitals'",
                         "suite 10", "linked through custom_refs", "were filtered"):
            self.assertIn(expected, text)

    def test_a_requirements_file_the_server_did_not_write_is_refused(self):
        result = self.link({"requirements": REQUIREMENTS}, [c for c in CASES])
        self.assertIn("requirements.json was not written by the Jama server.", result["mismatches"])
        self.assertIn("test_cases.json was not written by the TestRail server.", result["mismatches"])

    def test_missing_sources_are_listed(self):
        result = self.link(jama_file(REQUIREMENTS), None)
        self.assertEqual(result["missing"], ["test cases"])
        self.assertIn("Fetch: test cases → FAILED — no test case file in the run", result["lines"])

    def test_enrichment_that_never_ran_is_missing(self):
        reqs = [dict(r) for r in REQUIREMENTS]
        reqs[0] = {**reqs[0], "_enrichment_urls": [{"source": "github", "url": "https://github.com/o/r/blob/main/a.md"}]}
        result = self.link(jama_file(reqs), testrail_file(CASES))
        self.assertIn("enrichment", result["missing"])
        self.assertIn("Enrichment: not run — 1 GitHub-linked docs were not fetched", result["lines"])

    def test_an_excel_run_is_not_held_to_jama_arguments(self):
        config = {**JAMA_CONFIG, "requirements": {"provider": "excel", "enrichment": {"enabled": False}}}
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(config))
        result = self.link(REQUIREMENTS, testrail_file(CASES))
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["lines"][0], "Fetch: requirements → 5 from the Excel export")


def lookup_requirement(rid, description, folder, status="Approved", title=""):
    return {"type": "requirement", "id": rid, "title": title, "description": description, "rationale": "",
            "feature": folder.split(" / ")[0], "status": status, "linked_ids": [],
            "raw_fields": {"folder_path": folder, "url": f"https://jama.test/{rid}", "Configuration": ["iOS", "Android"]}}


LOOKUP_REQUIREMENTS = [
    lookup_requirement("R-1", "The App shall silence all alerts while the sensor warm-up is in progress.", "Alerts / Warm-up"),
    lookup_requirement("R-2", "The App shall raise an alert when the device starts operating on battery.", "Alerts / Battery"),
    lookup_requirement("R-3", "The App shall display the warmup countdown during the sensor warm up.", "Session / Warm-up"),
    lookup_requirement("R-4", "The App shall allow the user to mute the alarm for 30 minutes.", "Alerts / Mute"),
    lookup_requirement("R-5", "The App shall show glucose trends.", "Trends"),
    lookup_requirement("R-6", "Alarms shall not be suppressed during warm up.", "Alerts / Warm-up", status="Deprecated"),
    lookup_requirement("R-7", "The battery level is displayed when operating, on the receiver, on battery power.", "Device / Power"),
]
LOOKUP_CASES = [
    {"id": "TC-1", "title": "Alerts are silent in warm-up", "description": "", "feature": "Warm-up", "linked_ids": ["R-1"],
     "raw_fields": {"section_path": "Alerts/Warm-up", "steps": [{"content": "Start a sensor session", "expected": "No alert sounds until warm-up completes"}]}},
    {"id": "TC-2", "title": "Unlinked warm-up check", "description": "", "feature": "Misc", "linked_ids": [],
     "raw_fields": {"section_path": "Misc", "steps": [{"content": "Trigger a low glucose reading during warm-up", "expected": "The alarm is suppressed"}]}},
    {"id": "TC-3", "title": "Trends render", "description": "", "feature": "Trends", "linked_ids": ["R-5"],
     "raw_fields": {"section_path": "Trends", "steps": [{"content": "Open trends", "expected": "Trend chart shows"}]}},
]
MUTE_DURING_WARMUP = ["mute|silence|suppress|snooze", "alarm|alert|notification", "warm-up|warm up|warmup"]


class Search(RunFolderTest):
    def setUp(self):
        super().setUp()
        self.run = self.new_run(command="lookup", scope="all")
        self.write(self.run, "requirements.json", {"requirements": LOOKUP_REQUIREMENTS})
        self.write(self.run, "test_cases.json", {"test_cases": LOOKUP_CASES})

    def search(self, query="mute alarms during warm-up", concepts=MUTE_DURING_WARMUP, **kwargs):
        return bc_run.cmd_search(str(self.run), query, concepts, **kwargs)

    def text(self, result):
        return "".join((self.run / f).read_text() for f in result["files"])

    def test_word_forms_and_spellings_meet(self):
        for forms in (["mute", "muted", "muting", "mutes"], ["alarm", "alarms"], ["battery", "batteries"],
                      ["notify", "notified", "notifies"], ["reading", "readings"], ["warn", "warning", "warnings"]):
            self.assertEqual(len({tuple(bc_run.analyze(f)[0]) for f in forms}), 1, forms)
        self.assertEqual(bc_run.analyze("The patient's warm-up"), (["patient", "warm", "up"], ["warmup"]))
        self.assertEqual(bc_run.analyze("speed needed"), (["speed", "need"], []))

    def test_requirements_matching_every_concept_rank_first(self):
        result = self.search()
        self.assertEqual([t["id"] for t in result["top"][:3]], ["R-6", "R-1", "R-4"])
        self.assertEqual(result["top"][1]["matches"], ["mute", "alarm", "warm-up"])
        self.assertEqual(result["matched"]["every_concept"], 2)
        text = self.text(result)
        self.assertIn("Match: mute (silence) · alarm (alert) · warm-up — 3 of 3 concepts", text)
        self.assertIn("Status: Deprecated", text, "a retired requirement is shown with its status, never hidden")

    def test_every_spelling_of_a_hyphenated_word_matches(self):
        result = self.search("warm-up", ["warm-up"])
        self.assertEqual({t["id"] for t in result["top"]}, {"R-1", "R-3", "R-6"})

    def test_a_phrase_ranks_its_exact_wording_above_scattered_words(self):
        result = self.search("operating on battery", ["operating on battery"])
        self.assertEqual([t["id"] for t in result["top"]], ["R-2", "R-7"])

    def test_a_phrase_counts_only_when_its_words_are_close(self):
        reqs = [
            lookup_requirement("P-1", "Each alert shall play its sound once the sensor warm-up ends.", "Alerts / Sound"),
            lookup_requirement("P-2", "The warm-up of the sensor takes two hours.", "Session / Warm-up"),
            lookup_requirement("P-3", "Show a warm welcome screen. Many steps later, the sensor may be paired and set up.", "Onboarding / Welcome"),
        ]
        self.write(self.run, "requirements.json", {"requirements": reqs})
        result = self.search("sensor warm-up", ["sensor warm-up"])
        self.assertEqual([t["id"] for t in result["top"]], ["P-1", "P-2"])

    def test_the_text_is_verbatim_with_location_link_fields_and_tests(self):
        text = self.text(self.search())
        self.assertIn("### 2. R-1\nMatch:", text)
        self.assertIn("Description:\nThe App shall silence all alerts while the sensor warm-up is in progress.\n", text)
        self.assertIn("Folder: Alerts / Warm-up", text)
        self.assertIn("Fields:\n- Configuration: iOS, Android", text)
        self.assertIn("Link: https://jama.test/R-1", text)
        self.assertIn('Test cases: TC-1 "Alerts are silent in warm-up"', text)
        self.assertIn("| Alerts | 4 | Warm-up, Mute, Battery |", text)
        self.assertEqual(bc_run.where_of({"feature": "Leaf", "raw_fields": {"feature_path": "A / B / C / Leaf"}}), "B / C")

    def test_test_cases_that_mention_it_are_listed_with_their_links(self):
        text = self.text(self.search())
        self.assertIn("- TC-2 — Unlinked warm-up check · Folder: Misc · Links: none · matches:", text)
        self.assertIn("  > Expected: The alarm is suppressed", text)
        self.assertIn("Links: R-1 (#2)", text, "a linked case points at the requirement's rank")
        self.assertNotIn("TC-3", text)

    def test_a_feature_filter_narrows_requirements_and_cases(self):
        result = self.search(feature="warm up")
        self.assertEqual({t["id"] for t in result["top"]}, {"R-1", "R-3", "R-6"})
        self.assertEqual(result["searched"]["requirements"], 3)
        with self.assertRaises(bc_run.RunError) as ctx:
            self.search(feature="Billing")
        self.assertIn("Features in this run: Alerts, Device, Session, Trends", str(ctx.exception))

    def test_wordings_nothing_uses_are_reported(self):
        result = self.search("snooze alarms while charging", ["snooze", "alarm|alert", "charging|charger"])
        self.assertEqual(result["unmatched_wordings"], {"snooze": ["snooze"], "charging": ["charging", "charger"]})
        self.assertIn("No requirement matches the concept 'charging' in any of its wordings (charging, charger).", result["warnings"])

    def test_the_project_vocabulary_is_suggested(self):
        result = self.search("silence alerts during warm-up", ["silence", "alert", "warm-up"])
        self.assertIn("sensor", result["suggested_terms"])
        self.assertNotIn("silence", result["suggested_terms"])

    def test_without_concepts_the_query_words_are_used(self):
        result = bc_run.cmd_search(str(self.run), "glucose trends", [])
        self.assertEqual([c["name"] for c in result["concepts"]], ["glucose", "trends"])
        self.assertEqual(result["top"][0]["id"], "R-5")

    def test_searches_are_numbered_and_parts_fit_one_read(self):
        many = [lookup_requirement(f"R-{n}", "The App shall raise the alert during warm-up. " * 40, "Alerts / Warm-up") for n in range(10, 60)]
        self.write(self.run, "requirements.json", {"requirements": LOOKUP_REQUIREMENTS + many})
        first = self.search(top=60)
        second = self.search("alerts", ["alert"])
        self.assertTrue(all(f.startswith("search/01-") for f in first["files"]))
        self.assertTrue(all(f.startswith("search/02-") for f in second["files"]))
        self.assertGreater(len(first["files"]), 2)
        for name in first["files"]:
            self.assertLess(len((self.run / name).read_bytes()), 20_480, name)
        self.assertIn("— part 2 of", (self.run / first["files"][1]).read_text().splitlines()[0])

    def test_linked_docs_are_searched_too(self):
        (self.run / "docs").mkdir()
        (self.run / "docs" / "01-sds-1.md").write_text("# SDS\nAlerts are muted while the sensor warms up.\n")
        self.write(self.run, "docs.json", {"docs": [{"url": "https://github.com/o/r/blob/main/sds.md", "files": ["docs/01-sds-1.md"], "requirement_ids": ["R-3"]}]})
        result = self.search("mute alerts during warm-up", ["mute", "alert", "warm-up|warms up"])
        self.assertEqual(result["matched"]["doc_parts"], 1)
        self.assertIn("- docs/01-sds-1.md (https://github.com/o/r/blob/main/sds.md) — linked from R-3", self.text(result))

    def test_show_prints_requirements_and_test_cases_verbatim(self):
        text = bc_run.cmd_show(str(self.run), "r-4, C2, 3, TC-1, R-99")
        self.assertIn("## R-4\nStatus: Approved · Folder: Alerts / Mute", text)
        self.assertIn("The App shall allow the user to mute the alarm for 30 minutes.", text)
        self.assertIn("## TC-2 — Unlinked warm-up check", text)
        self.assertIn("## TC-3 — Trends render", text)
        self.assertIn("   Expected: No alert sounds until warm-up completes", text)
        self.assertTrue(text.rstrip().endswith("Not in this run: R-99"))


class Recent(RunFolderTest):
    def setUp(self):
        super().setUp()
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(JAMA_CONFIG))

    def complete_run(self, scope="", requirements=None, cases=None):
        run = self.new_run(scope=scope)
        self.write(run, "requirements.json", requirements or jama_file(REQUIREMENTS, scope=scope))
        self.write(run, "test_cases.json", cases or testrail_file(CASES))
        bc_run.cmd_link(str(run))
        return run

    def age(self, run, minutes):
        manifest = json.loads((run / "manifest.json").read_text())
        then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
        manifest["created_at"] = then.isoformat(timespec="seconds")
        (run / "manifest.json").write_text(json.dumps(manifest))

    def test_the_newest_complete_run_of_everything_is_reused(self):
        older = self.complete_run()
        newer = self.complete_run()
        self.age(older, 20)
        self.age(newer, 5)
        result = bc_run.cmd_recent(str(self.project), 30)
        self.assertEqual(result["run_dir"], str(newer))
        self.assertEqual(result["age_minutes"], 5)
        self.assertEqual(result["lines"][-1], 'Readiness: 5 requirements, 7 test cases for scope "all"')

    def test_scoped_incomplete_stale_or_mismatched_runs_are_not(self):
        scoped = self.complete_run(scope="Alarms", requirements=jama_file(REQUIREMENTS[:3], scope="Alarms"))
        missing = self.new_run()
        self.write(missing, "requirements.json", jama_file(REQUIREMENTS))
        bc_run.cmd_link(str(missing))
        stale = self.complete_run()
        self.age(stale, 45)
        wrong = self.complete_run(cases=testrail_file(CASES, suite_id=10))
        unlinked = self.new_run()
        self.write(unlinked, "requirements.json", jama_file(REQUIREMENTS))
        for run in (scoped, missing, wrong, unlinked):
            self.age(run, 1)
        result = bc_run.cmd_recent(str(self.project), 30)
        self.assertIsNone(result["run_dir"])
        self.assertIn("in the last 30 minutes", result["reason"])

    def test_a_config_change_since_the_run_stops_its_reuse(self):
        run = self.complete_run()
        self.age(run, 2)
        self.assertEqual(bc_run.cmd_recent(str(self.project), 30)["run_dir"], str(run))
        changed = {**JAMA_CONFIG, "requirements": {**JAMA_CONFIG["requirements"], "item_type_filter": ["Other"]}}
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(changed))
        self.assertIsNone(bc_run.cmd_recent(str(self.project), 30)["run_dir"])

    def test_a_project_without_runs_has_none(self):
        self.assertEqual(bc_run.cmd_recent(str(self.project), 30), {"run_dir": None, "reason": "no runs yet"})


def b64(text):
    return base64.b64encode(text.encode()).decode()


class FakeGh:
    """Stands in for `gh api`: answers from a dict of endpoint -> JSON (or an error string)."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.assertions(argv, kwargs)
        endpoint = argv[-1]
        self.calls.append(endpoint)
        answer = self.answers.get(endpoint, "gh: Not Found (HTTP 404)")
        if isinstance(answer, str):
            return subprocess.CompletedProcess(argv, 1, "", answer)
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

    @staticmethod
    def assertions(argv, kwargs):
        assert argv[:2] == ["gh", "api"], argv
        assert not any(flag in argv for flag in ("-X", "--method", "-f", "-F", "--field", "--input")), argv
        assert kwargs.get("timeout"), "every gh call is bounded"


class Enrich(RunFolderTest):
    def setUp(self):
        super().setUp()
        self.configure({"enabled": True, "strategy": "cli", "max_doc_chars": 60})
        self.run = self.new_run()
        reqs = [
            requirement("R-1", "One", "F", _enrichment_urls=None),
            requirement("R-2", "Two", "F"),
            requirement("R-3", "Three", "F"),
        ]
        reqs[0]["_enrichment_urls"] = [{"source": "github", "url": "https://github.com/o/r/blob/main/docs/sds.md?plain=1#L4"}]
        reqs[1]["_enrichment_urls"] = [
            {"source": "github", "url": "https://github.com/o/r/blob/main/docs/sds.md#L4"},
            {"source": "github", "url": "https://github.com/o/r/tree/main/docs/arch"},
        ]
        reqs[2]["_enrichment_urls"] = [
            {"source": "github", "url": "https://github.com/o/r/blob/main/gone.md"},
            {"source": "github", "url": "https://gitlab.com/o/r/blob/main/x.md"},
        ]
        self.write(self.run, "requirements.json", {"requirements": reqs})
        sds = "# SDS\n![flow](images/flow.png) <img alt=\"seq\" src=\"../seq.svg\">\n" + "x" * 100
        self.gh = FakeGh({
            "repos/o/r/contents/docs/sds.md?ref=main": {"content": b64(sds), "sha": "1", "size": len(sds)},
            "repos/o/r/contents/docs/arch?ref=main": [{"name": "README.md", "path": "docs/arch/README.md", "type": "file"}],
            "repos/o/r/contents/docs/arch/README.md?ref=main": {"content": b64("# Arch"), "sha": "2", "size": 6},
            "repos/o/r": {"full_name": "o/r"},
        })

    def configure(self, enrichment):
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps({"requirements": {"enrichment": enrichment}}))

    def test_docs_are_fetched_once_written_and_indexed(self):
        result = bc_run.cmd_enrich(str(self.run), runner=self.gh)
        self.assertEqual((result["urls"], result["fetched"]), (4, 2))
        self.assertEqual(result["line"], "Enrichment: fetched 2 of 4 GitHub-linked requirement docs (1 truncated at 60 characters)")
        self.assertEqual(self.gh.calls.count("repos/o/r/contents/docs/sds.md?ref=main"), 1, "one fetch per unique URL")
        index = json.loads((self.run / "docs.json").read_text())
        sds = index["docs"][0]
        self.assertEqual((sds["url"], sds["requirement_ids"]), ("https://github.com/o/r/blob/main/docs/sds.md#L4", ["R-1", "R-2"]))
        self.assertEqual(sds["locator"]["anchor"], "L4")
        self.assertTrue(sds["truncated"])
        self.assertEqual(sds["files"], ["docs/01-sds-1.md"])
        content = (self.run / sds["files"][0]).read_text()
        self.assertTrue(content.startswith("<!-- https://github.com/o/r/blob/main/docs/sds.md#L4 — part 1 of 1 -->\n# SDS"))
        self.assertIn("[...truncated, see https://github.com/o/r/blob/main/docs/sds.md#L4 for full document]", content)
        self.assertEqual(
            sds["referenced_images"],
            [
                {"alt": "flow", "url": "https://raw.githubusercontent.com/o/r/main/docs/images/flow.png"},
                {"alt": "seq", "url": "https://raw.githubusercontent.com/o/r/main/seq.svg"},
            ],
        )
        self.assertTrue((self.run / index["docs"][1]["files"][0]).read_text().endswith("\n# Arch"))
        failures = {f["url"]: f["error"] for f in index["failed"]}
        self.assertEqual(failures, {"https://github.com/o/r/blob/main/gone.md": "file_not_found", "https://gitlab.com/o/r/blob/main/x.md": "unsupported_url"})

    def test_a_long_doc_is_split_into_parts_that_fit_one_read(self):
        self.configure({"enabled": True, "strategy": "cli", "max_doc_chars": 10 ** 6})
        long_doc = "".join(f"Line {n}: the pump shall stop — “safely”.\n" for n in range(2000))
        self.gh.answers["repos/o/r/contents/docs/sds.md?ref=main"] = {"content": b64(long_doc), "sha": "1", "size": 1}
        bc_run.cmd_enrich(str(self.run), runner=self.gh)
        files = json.loads((self.run / "docs.json").read_text())["docs"][0]["files"]
        self.assertGreater(len(files), 3)
        texts = [(self.run / f).read_text() for f in files]
        for text in texts:
            self.assertLessEqual(len(text.encode("utf-8")), bc_run.PART_BYTES)
        body = "".join(t.split("\n", 1)[1] for t in texts)
        self.assertEqual(body, long_doc, "the parts put back together are the doc, verbatim")
        self.assertTrue(texts[1].startswith(f"<!-- https://github.com/o/r/blob/main/docs/sds.md#L4 — part 2 of {len(files)} -->"))

    def test_an_unreachable_repo_is_named_as_such(self):
        del self.gh.answers["repos/o/r"]
        index = bc_run.cmd_enrich(str(self.run), runner=self.gh)
        self.assertIn({"url": "https://github.com/o/r/blob/main/gone.md", "error": "repo_inaccessible"},
                      [{k: f[k] for k in ("url", "error")} for f in index["failed"]])

    def test_a_missing_gh_fails_every_doc_without_crashing(self):
        def no_gh(argv, **kwargs):
            raise FileNotFoundError("gh")

        result = bc_run.cmd_enrich(str(self.run), runner=no_gh)
        self.assertEqual(result["fetched"], 0)
        self.assertEqual({f["error"] for f in result["failed"]}, {"gh_missing", "unsupported_url"})

    def test_disabled_enrichment_fetches_nothing(self):
        self.configure({"enabled": False})
        result = bc_run.cmd_enrich(str(self.run), runner=self.gh)
        self.assertIn("disabled", result["skipped"])
        self.assertEqual(self.gh.calls, [])

    def test_the_mcp_strategy_leaves_the_urls_for_the_main_thread(self):
        self.configure({"enabled": True, "strategy": "mcp"})
        result = bc_run.cmd_enrich(str(self.run), runner=self.gh)
        self.assertEqual(len(result["pending"]), 4)
        self.assertEqual(self.gh.calls, [])
        self.assertEqual(json.loads((self.run / "docs.json").read_text())["strategy"], "mcp")


class Cli(RunFolderTest):
    def test_each_subcommand_prints_one_json_object(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc_run.main(["new", "--project", str(self.project), "--command", "coverage", "--scope", "all"])
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertTrue(result["ok"])
        run = result["run_dir"]
        (Path(run) / "requirements.json").write_text(json.dumps({"requirements": REQUIREMENTS}))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bc_run.main(["link", "--run", run]), 0)
        self.assertEqual(json.loads(out.getvalue())["counts"]["requirements"], 5)

    def test_search_prints_json_and_show_prints_markdown(self):
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bc_run.main(["search", "--run", str(run), "--query", "visual alarm", "--concept", "visual|visible", "--concept", "alarm"]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual((result["ok"], result["top"][0]["id"]), (True, "CWA-REQ-2"))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(bc_run.main(["show", "--run", str(run), "--ids", "CWA-REQ-2"]), 0)
        self.assertTrue(out.getvalue().startswith("# From run "))
        self.assertIn("## CWA-REQ-2 — Show a visual alarm", out.getvalue())

    def test_errors_print_json_and_exit_non_zero(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = bc_run.main(["link", "--run", self.tmp.name])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
