"""Tests for bc_run.py — run folders, GitHub enrichment, and linking. Standard library only.

    python3 -m unittest discover -s scripts/tests -v

They must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions.
"""

import base64
import datetime
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
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
    def test_only_setup_starts_a_run_in_a_folder_never_set_up(self):
        fresh = Path(self.tmp.name, "fresh")
        fresh.mkdir()
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_new(str(fresh), "coverage", "")
        run = Path(bc_run.cmd_new(str(fresh), "setup", "")["run_dir"])
        self.assertEqual(run.parent.parent, (fresh / ".buddy-council").resolve())

    def test_a_run_folder_is_created_privately_with_a_manifest(self):
        result = bc_run.cmd_new(str(self.project), "Coverage!", "Alarms")
        run = Path(result["run_dir"])
        self.assertEqual(run.parent, (self.project / ".buddy-council" / "runs").resolve())
        self.assertRegex(run.name, r"^\d{8}-\d{6}-coverage$")
        self.assertEqual(stat.S_IMODE(run.stat().st_mode), 0o700)
        manifest = json.loads((run / "manifest.json").read_text())
        self.assertEqual((manifest["command"], manifest["scope"]), ("Coverage!", "Alarms"))
        self.assertEqual(result["requirements"], str(run / "requirements.json"))
        self.assertNotIn("background", result, "only a lookup reads the rest of the project")
        lookup = bc_run.cmd_new(str(self.project), "lookup", "all")
        self.assertEqual(lookup["background"], str(Path(lookup["run_dir"]) / "background.json"))
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

    def test_a_requirements_array_uses_the_run_scope(self):
        run = self.new_run(scope="Vitals")
        self.write(run, "requirements.json", [r for r in REQUIREMENTS if r["feature"] == "Vitals"])
        self.write(run, "test_cases.json", {"test_cases": CASES})
        result = bc_run.cmd_link(str(run))
        self.assertEqual(result["counts"]["test_cases_in_scope"], 1)
        self.assertEqual(result["readiness"], 'Readiness: 2 requirements, 1 test cases for scope "Vitals"')

    def test_missing_test_cases_are_reported_not_hidden(self):
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps({"test_cases": {"provider": "testrail"}}))
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        result = bc_run.cmd_link(str(run))
        self.assertEqual(result["counts"]["test_cases_in_scope"], 0)
        self.assertTrue(any("test_cases.json is missing" in w for w in result["warnings"]))
        self.assertIn("test cases", json.loads((run / "links.json").read_text())["missing"])

    def test_a_project_that_uses_no_test_cases_is_complete_without_them(self):
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps({"test_cases": {"provider": "none"}}))
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        result = bc_run.cmd_link(str(run))
        links = json.loads((run / "links.json").read_text())
        self.assertEqual(links["missing"], [], "not PARTIAL, and a lookup can reuse the run")
        self.assertIn("Fetch: test cases → skipped, not used in this project", links["lines"])
        self.assertFalse(any("test_cases.json is missing" in w for w in result["warnings"]))

    def test_a_summary_holds_what_a_model_checks_and_prints_without_the_bulk(self):
        run, result = self.run_all()
        summary = bc_run.cmd_summary(str(run))
        links = json.loads((run / "links.json").read_text())
        for key_name in ("lines", "missing", "mismatches", "warnings", "enrichment", "counts", "scope"):
            self.assertEqual(summary[key_name], links[key_name], key_name)
        self.assertEqual(summary["command"], "coverage")
        self.assertNotIn("by_requirement", summary)
        alarms = next(row for row in summary["features"] if row["feature"] == "Alarms")
        self.assertEqual((alarms["tested"], alarms["untested"], alarms["orphans"]), (2, 1, 2))
        self.assertEqual(alarms["parts"], len(next(r for r in links["features"] if r["feature"] == "Alarms")["bundles"]))
        self.assertNotIn("bundles", alarms, "a feature's parts come with --feature")

    def test_a_feature_summary_gives_its_parts_as_paths_and_only_its_own_lists(self):
        run, _ = self.run_all()
        summary = bc_run.cmd_summary(str(run), "alarms")
        self.assertEqual(summary["feature"]["feature"], "Alarms")
        for part in summary["feature"]["bundles"]:
            self.assertTrue(Path(part).is_absolute() and Path(part).is_file(), part)
        self.assertEqual([u["id"] for u in summary["untested"]], ["CWA-REQ-3"])
        self.assertEqual(sorted(o["id"] for o in summary["orphans"]), ["TC-12", "TC-13"])
        self.assertTrue(all(entry["feature"] == "Alarms" for name in ("untested", "orphans", "stranded")
                            for entry in summary[name]))
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_summary(str(run), "Nope")
        self.assertIn("Alarms", str(caught.exception), "names the features there are")

    def test_a_summary_of_a_run_that_was_never_linked_says_so(self):
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_summary(str(self.new_run()))

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

    def test_a_doc_left_to_the_assistant_s_own_connection_says_how_to_read_it(self):
        run = self.new_run()
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS[:1]})
        self.write(run, "docs.json", {"strategy": "mcp", "docs": [], "failed": [], "pending": [
            {"url": "https://github.com/o/r/blob/main/c.md", "requirement_ids": ["CWA-REQ-1"]}]})
        bc_run.cmd_link(str(run))
        self.assertIn("Linked doc not in this run: https://github.com/o/r/blob/main/c.md — read it once through your "
                      "GitHub connection: providers/github/fetch-mcp.md", (run / "bundles" / "01-alarms-1.md").read_text(),
                      "whoever reads the bundle, a helper included, reads it")

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


class Slices(RunFolderTest):
    def link(self, requirements, cases, docs=None, doc_files=None):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": requirements})
        self.write(run, "test_cases.json", {"test_cases": cases})
        if docs:
            self.write(run, "docs.json", {"docs": docs})
            (run / "docs").mkdir()
            for name, text in (doc_files or {}).items():
                (run / name).write_text(text)
        bc_run.cmd_link(str(run))
        return run

    def test_a_run_that_fits_one_slice_is_one_slice_with_every_part(self):
        run = self.link(REQUIREMENTS, CASES)
        listed = bc_run.cmd_slices(str(run))
        self.assertEqual(listed["total"], 1)
        self.assertEqual({k: listed["slices"][0][k] for k in ("slice", "features", "requirements", "test_cases", "checked")},
                         {"slice": 1, "features": ["Alarms", "Vitals", "No feature"], "requirements": 5, "test_cases": 6,
                          "checked": False})
        one = bc_run.cmd_slices(str(run), 1)
        self.assertEqual(one["parts"], [str(run / "bundles" / name) for name in
                                        ("01-alarms-1.md", "02-vitals-1.md", "03-no-feature-1.md")])
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_slices(str(run), 2)
        self.assertIn("this run has 1", str(caught.exception))

    def slice_texts(self, run):
        return ["".join(Path(p).read_text() for p in bc_run.cmd_slices(str(run), n)["parts"])
                for n in range(1, bc_run.cmd_slices(str(run))["total"] + 1)]

    def test_a_feature_too_big_for_one_slice_is_cut_between_requirements_and_each_slice_stands_alone(self):
        text = "The pump shall stop when occluded, and say so on the screen. " * 25
        reqs = [requirement(f"CWA-REQ-{n}", f"Requirement {n}", "Pump", text) for n in range(1, 13)]
        cases = [case(100 + n, f"Case {n}", f"CWA-REQ-{n}", section="Pump") for n in range(1, 13)]
        cases.append(case(200, "Shared by the first and the last", "CWA-REQ-1, CWA-REQ-12", section="Pump"))
        with mock.patch.object(bc_run, "SLICE_BYTES", 8_000), mock.patch.object(bc_run, "PART_BYTES", 3_000):
            run = self.link(reqs, cases)
        listed = bc_run.cmd_slices(str(run))
        self.assertGreater(listed["total"], 2)
        self.assertEqual({tuple(row["features"]) for row in listed["slices"]}, {("Pump",)})
        sizes = [piece["bytes"] for piece in json.loads((run / "links.json").read_text())["slices"]]
        self.assertLess(max(sizes), 8_000 + 1_000, "a slice holds no more than one reader checks, part headers aside")
        self.assertLess(max(sizes), 2 * min(sizes), "slices come out about the same size")
        texts = self.slice_texts(run)
        for n in range(1, 13):
            self.assertEqual(sum(f"## CWA-REQ-{n} — Requirement {n}\n" in t for t in texts), 1, n)
        for slice_text in texts:
            first = next(line for line in slice_text.splitlines() if line.startswith("## "))
            self.assertFalse(first.endswith("(continued)"), "a slice never opens in the middle of a requirement")
            for shown in re.findall(r"### (TC-\d+) — shown above", slice_text):
                self.assertRegex(slice_text, rf"### {shown} — (?!shown above)", "a pointer stays inside its slice")
        self.assertEqual(sum("### TC-200 — Shared by the first and the last" in t for t in texts), 2,
                         "a case its two requirements' slices both need is written out in each")
        parts = bc_run.cmd_summary(str(run), "Pump")["feature"]["bundles"]
        self.assertEqual(parts, [p for n in range(1, listed["total"] + 1) for p in bc_run.cmd_slices(str(run), n)["parts"]])
        self.assertIn(f"— part {len(parts)} of {len(parts)}", Path(parts[-1]).read_text().splitlines()[0])

    def test_a_requirement_with_more_test_cases_than_one_slice_holds_comes_with_each_share_of_them(self):
        steps = [{"content": f"Snooze the alarm and wait, round {n}", "expected": "The alarm stays quiet for 5 minutes"}
                 for n in range(1, 6)]
        snooze = requirement("CWA-REQ-7", "Snooze alarms", "Alarms", "The system shall snooze a sounding alarm for 5 minutes.")
        cases = [case(300 + n, f"Snooze case {n}", "CWA-REQ-7", steps=steps) for n in range(1, 41)]
        doc = {"url": "https://github.com/o/r/blob/main/snooze.md", "files": ["docs/01-snooze-1.md"],
               "requirement_ids": ["CWA-REQ-7"]}
        with mock.patch.object(bc_run, "SLICE_BYTES", 6_000):
            run = self.link([snooze], cases, [doc], {"docs/01-snooze-1.md": "s" * 2_500})
        texts = self.slice_texts(run)
        self.assertGreater(len(texts), 1)
        for slice_text in texts:
            self.assertIn("The system shall snooze a sounding alarm for 5 minutes.", slice_text,
                          "each share's reader has the requirement to compare its cases with")
        written = [cid for t in texts for cid in re.findall(r"### (TC-\d+) — Snooze case", t)]
        self.assertEqual(sorted(written), sorted(f"TC-{300 + n}" for n in range(1, 41)), "every case once, none left out")
        self.assertIn("## CWA-REQ-7 — Snooze alarms (continued)\n", texts[1])
        slices = json.loads((run / "links.json").read_text())["slices"]
        self.assertEqual({tuple(piece["docs"]) for piece in slices}, {("docs/01-snooze-1.md",)})
        self.assertLess(max(piece["bytes"] for piece in slices), 6_000 + 1_000, "each share leaves room for the doc")

    def test_only_a_contradiction_run_is_cut_into_slices(self):
        text = "The pump shall stop when occluded, and say so on the screen. " * 25
        reqs = [requirement(f"CWA-REQ-{n}", f"Requirement {n}", "Pump", text) for n in range(1, 13)]
        cases = [case(200, "Shared by the first and the last", "CWA-REQ-1, CWA-REQ-12", section="Pump")]
        run = self.new_run(command="coverage")
        self.write(run, "requirements.json", {"requirements": reqs})
        self.write(run, "test_cases.json", {"test_cases": cases})
        with mock.patch.object(bc_run, "SLICE_BYTES", 8_000):
            bc_run.cmd_link(str(run))
        bundle = "".join(p.read_text() for p in sorted((run / "bundles").glob("*.md")))
        self.assertEqual(bundle.count("### TC-200 — Shared by the first and the last"), 1, "a coverage run's bundle is as before")
        self.assertNotIn("(continued)\nStatus", bundle)
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_slices(str(run))
        self.assertIn("only a contradiction run", str(caught.exception))

    def test_the_docs_a_slice_cites_count_toward_its_size_once_and_come_with_it(self):
        run = self.two_slices()
        listed = bc_run.cmd_slices(str(run))
        self.assertEqual([row["features"] for row in listed["slices"]], [["Alarms"], ["Vitals", "No feature"]],
                         "alone the bundles fit one slice; with their docs they need two, and the alarms doc that "
                         "two requirements cite counts once")
        self.assertEqual([[Path(d).name for d in bc_run.cmd_slices(str(run), n)["docs"]] for n in (1, 2)],
                         [["01-alarms-1.md"], ["02-vitals-1.md"]])

    def two_slices(self):
        docs = [{"url": "https://github.com/o/r/blob/main/alarms.md", "files": ["docs/01-alarms-1.md"],
                 "requirement_ids": ["CWA-REQ-1", "CWA-REQ-2"]},
                {"url": "https://github.com/o/r/blob/main/vitals.md", "files": ["docs/02-vitals-1.md"],
                 "requirement_ids": ["CWA-REQ-4"]}]
        files = {"docs/01-alarms-1.md": "a" * 3_000, "docs/02-vitals-1.md": "v" * 3_000}
        with mock.patch.object(bc_run, "SLICE_BYTES", 6_000):
            run = self.link(REQUIREMENTS, CASES, docs, files)
        self.save_lines(run, 1, 2)
        return run

    def save_lines(self, run, *numbers):
        for n in numbers:
            ids = bc_run.cmd_slices(str(run), n)["needs_lines"]
            bc_run.cmd_subjects(str(run), n, json.dumps({i: f"Subject of {i}: what it says." for i in ids}))

    def test_a_slice_is_checked_once_its_reader_saved_a_subject_line_for_each_of_its_items(self):
        self.assertEqual(bc_run.cmd_slices(str(self.link(REQUIREMENTS, CASES)), 1)["needs_lines"], [],
                         "one slice of the whole product has nothing to be matched with")
        with mock.patch.object(bc_run, "SLICE_BYTES", 1_000):
            run = self.link(REQUIREMENTS, CASES)
        ids = bc_run.cmd_slices(str(run), 1)["needs_lines"]
        self.assertGreater(len(ids), 1)
        self.assertFalse(bc_run.cmd_findings(str(run), add_text="[]", slice_number=1)["checked"], "its lines are to come")
        answer = bc_run.cmd_subjects(str(run), 1, json.dumps({ids[0].lower(): "Alarm sound: an alarm sounds.", "TC-14": "x"}))
        self.assertEqual((answer["saved"], [r["id"] for r in answer["rejected"]], answer["missing"], answer["checked"]),
                         (1, ["TC-14"], ids[1:], False), "an ID from another slice is sent back")
        answer = bc_run.cmd_subjects(str(run), 1, json.dumps({i: "Alarm sound: " + "loud " * 60 for i in ids[1:]}))
        self.assertEqual((answer["saved"], answer["checked"]), (0, False), "a line is one line, not the item's text")
        answer = bc_run.cmd_subjects(str(run), 1, json.dumps({i: f"Alarm {i}: it sounds." for i in ids[1:]}))
        self.assertEqual((answer["saved"], answer["rejected"], answer["missing"], answer["checked"]),
                         (len(ids) - 1, [], [], True))
        self.assertEqual(bc_run.cmd_slices(str(run), 1)["needs_lines"], [])
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_subjects(str(run), 1, '["a list"]')

    def test_the_report_names_a_slice_no_one_checked(self):
        run = self.two_slices()
        bc_run.cmd_findings(str(run), add_text="[]", slice_number=1)
        result = bc_run.cmd_report(str(run))
        self.assertEqual(result["unchecked"], [2])
        self.assertIn("**Not checked:** slice 2 of 2, so any conflicts in it are missing from this report.",
                      Path(result["parts"][0]).read_text())
        bc_run.cmd_findings(str(run), add_text="[]", slice_number=2)
        self.assertIn("never compared", Path(bc_run.cmd_report(str(run))["parts"][0]).read_text())
        bc_run.cmd_match(str(run))
        result = bc_run.cmd_report(str(run))
        self.assertEqual(result["unchecked"], [])
        self.assertNotIn("Not checked", Path(result["parts"][0]).read_text())

    def test_a_save_for_a_slice_marks_it_checked_even_when_it_found_nothing(self):
        with mock.patch.object(bc_run, "SLICE_BYTES", 1_000):
            run = self.link(REQUIREMENTS, CASES)
        total = bc_run.cmd_slices(str(run))["total"]
        self.assertGreater(total, 2)
        self.save_lines(run, *range(1, total + 1))
        found = {"severity": "high", "fix": "TC-10", "why": "It expects a sound where the requirement asks for a light.",
                 "items": [{"id": "CWA-REQ-2", "quote": "show a visual alarm"}, {"id": "TC-10", "quote": "It happens"}]}
        self.assertEqual(bc_run.cmd_findings(str(run), add_text=json.dumps([found]), slice_number=1)["saved"], 1)
        self.assertEqual(bc_run.cmd_findings(str(run), add_text="[]", slice_number=2),
                         {"saved": 0, "rejected": [], "total": 1, "checked": True})
        misquoted = found | {"items": [{"id": "CWA-REQ-4", "quote": "display heart rate"}, {"id": "TC-14", "quote": "nowhere"}],
                             "fix": "TC-14"}
        answer = bc_run.cmd_findings(str(run), add_text=json.dumps([misquoted]), slice_number=3)
        self.assertEqual((answer["saved"], len(answer["rejected"]), answer["checked"]), (0, 1, False),
                         "a slice with a finding still to fix or drop isn't checked, so its reader stopping shows")
        listed = bc_run.cmd_slices(str(run))
        self.assertEqual([(row["checked"], row["findings"]) for row in listed["slices"]],
                         [(True, 1), (True, 0)] + [(False, 0)] * (total - 2))
        self.assertEqual(listed["checked"], 2)
        self.assertTrue(bc_run.cmd_findings(str(run), add_text="[]", slice_number=3)["checked"], "dropped on purpose")
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_findings(str(run), add_text="[]")
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_findings(str(run), add_text="[]", slice_number=total + 1)
        self.assertIn(f"this run has {total}", str(caught.exception))

    def test_a_run_with_no_requirements_still_saves_its_slice_as_checked(self):
        run = self.link([], [case(13, "Alarm orphan, no refs")])
        self.assertEqual(bc_run.cmd_slices(str(run))["total"], 1)
        self.assertTrue(bc_run.cmd_findings(str(run), add_text="[]", slice_number=1)["checked"])
        self.assertEqual(bc_run.cmd_report(str(run))["unchecked"], [])


SUBJECT_LINES = {
    "CWA-REQ-21": "Session timeout: the session ends after 5 minutes idle.",
    "TC-210": "Session timeout: logout comes after 5 minutes idle.",
    "CWA-REQ-22": "Session timeout during monitoring: the session stays open while monitoring.",
    "TC-220": "Monitoring display: vitals refresh every 3 seconds.",
    "CWA-REQ-23": "Alarm volume: alarms sound at 80 dB.",
    "TC-230": "Alarm volume: an alarm is heard at 80 dB.",
}
ACROSS_REQUIREMENTS = [
    requirement("CWA-REQ-21", "Idle logout", "Login", "The session shall end after 5 minutes idle.", url="https://req.test/21"),
    requirement("CWA-REQ-22", "Session timeout while monitoring", "Monitoring",
                "The session shall stay open while monitoring.", url="https://req.test/22"),
    requirement("CWA-REQ-23", "Alarm volume", "Alarms", "Alarms shall sound at 80 dB."),
]
ACROSS_CASES = [
    case(210, "Logout when idle", "CWA-REQ-21", section="Login",
         steps=[{"content": "Leave the app idle for 5 minutes", "expected": "You are logged out"}]),
    case(220, "Vitals refresh", "CWA-REQ-22", section="Monitoring"),
    case(230, "Alarm loudness", "CWA-REQ-23", section="Alarms"),
]


class Across(RunFolderTest):
    def check_slices(self, run, lines=SUBJECT_LINES):
        for n in range(1, bc_run.cmd_slices(str(run))["total"] + 1):
            ids = bc_run.cmd_slices(str(run), n)["needs_lines"]
            bc_run.cmd_subjects(str(run), n, json.dumps({i: lines[i] for i in ids}))
            self.assertTrue(bc_run.cmd_findings(str(run), add_text="[]", slice_number=n)["checked"])

    def pairs(self, run, number):
        first = len(json.loads((run / "links.json").read_text())["slices"]) + 1  # pair slices come after the run's own
        made = json.loads((run / "pairs.json").read_text())["slices"][number - first]
        return {tuple(sorted(pair)) for pair in made["pairs"]}

    def link_three_features(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": ACROSS_CASES})
        with mock.patch.object(bc_run, "SLICE_BYTES", 400):
            bc_run.cmd_link(str(run))
        self.assertEqual([row["features"] for row in bc_run.cmd_slices(str(run))["slices"]], [["Login"], ["Monitoring"], ["Alarms"]])
        return run

    def test_the_issue_s_own_example_pairs_though_one_line_names_its_subject_by_what_happens_to_it(self):
        run = self.link_three_features()
        self.check_slices(run, {**SUBJECT_LINES, "CWA-REQ-21": "Idle logout: the session ends after 5 minutes idle.",
                                "TC-210": "Idle logout: logs out after 5 minutes idle.",
                                "CWA-REQ-22": "Monitoring session: the session stays open while monitoring."})
        self.assertEqual(bc_run.cmd_match(str(run))["pairs"], 1)
        self.assertEqual(self.pairs(run, 4), {("CWA-REQ-21", "CWA-REQ-22")})

    def test_items_on_the_same_subject_in_different_slices_are_paired_and_items_in_one_slice_are_not(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": ACROSS_CASES})
        self.write(run, "docs.json", {"docs": [{"url": "https://github.com/o/r/blob/main/session.md",
                                                "files": ["docs/01-session-1.md"], "requirement_ids": ["CWA-REQ-22"]}]})
        (run / "docs").mkdir()
        (run / "docs" / "01-session-1.md").write_text("Monitoring keeps the session open.")
        with mock.patch.object(bc_run, "SLICE_BYTES", 400):
            bc_run.cmd_link(str(run))
        self.assertEqual([row["features"] for row in bc_run.cmd_slices(str(run))["slices"]], [["Login"], ["Monitoring"], ["Alarms"]])
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_match(str(run))
        self.assertIn("slices 1, 2 and 3", str(caught.exception))
        self.check_slices(run)
        self.assertEqual(bc_run.cmd_match(str(run)), {"pairs": 2, "slices": [4], "total": 4})
        self.assertEqual(self.pairs(run, 4), {("CWA-REQ-21", "CWA-REQ-22"), ("CWA-REQ-22", "TC-210")},
                         "a requirement and its own test case share a slice, so that slice's check compares them")
        listed = bc_run.cmd_slices(str(run), 4)
        self.assertEqual((listed["pairs"], listed["checked"], listed["needs_lines"]), (2, False, []))
        self.assertEqual([Path(d).name for d in listed["docs"]], ["01-session-1.md"], "a requirement's docs come with it")
        text = "".join(Path(p).read_text() for p in listed["parts"])
        for words in ("The session shall end after 5 minutes idle.", "The session shall stay open while monitoring.",
                      "Leave the app idle for 5 minutes", SUBJECT_LINES["TC-210"]):
            self.assertIn(words, text)
        self.assertEqual(bc_run.cmd_match(str(run))["slices"], [4], "matching again changes nothing")
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_subjects(str(run), 1, json.dumps({"CWA-REQ-21": "Session: ends at once."}))
        found = {"severity": "high", "fix": "decide", "why": "One feature ends an idle session that the other keeps open.",
                 "items": [{"id": "CWA-REQ-21", "quote": "end after 5 minutes idle"},
                           {"id": "CWA-REQ-22", "quote": "stay open while monitoring"}]}
        self.assertTrue(bc_run.cmd_findings(str(run), add_text=json.dumps([found]), slice_number=4)["checked"])
        report = Path(bc_run.cmd_report(str(run))["parts"][0]).read_text()
        self.assertIn("[Idle logout](https://req.test/21)", report)
        self.assertIn("[Session timeout while monitoring](https://req.test/22)", report)
        self.assertIn("**Checked:** 3 requirements and 3 test cases, and 2 pairs of items on the same subject across "
                      "the product.", report)

    def test_a_run_on_one_feature_is_paired_with_the_rest_of_the_product_and_counts_only_its_own_items(self):
        run = self.new_run(command="contradiction", scope="Login")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS[:1], "scope": {"input": "Login", "kind": "feature"}})
        self.write(run, "product.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": ACROSS_CASES})
        bc_run.cmd_link(str(run))
        unmatched = Path(bc_run.cmd_report(str(run))["parts"][0]).read_text()
        self.assertIn("were never compared", unmatched)
        self.check_slices(run)
        self.assertEqual(bc_run.cmd_match(str(run)), {"pairs": 2, "slices": [2], "total": 2})
        self.assertEqual(self.pairs(run, 2), {("CWA-REQ-21", "CWA-REQ-22"), ("CWA-REQ-22", "TC-210")},
                         "only pairs with the feature: the rest of the product is never paired with itself")
        found = {"severity": "high", "fix": "decide", "why": "One feature ends an idle session that the other keeps open.",
                 "items": [{"id": "TC-210", "quote": "You are logged out"},
                           {"id": "CWA-REQ-22", "quote": "stay open while monitoring"}]}
        self.assertTrue(bc_run.cmd_findings(str(run), add_text=json.dumps([found]), slice_number=2)["checked"])
        report = Path(bc_run.cmd_report(str(run))["parts"][0]).read_text()
        self.assertIn("[Session timeout while monitoring](https://req.test/22) _(in Monitoring, outside the scope)_", report)
        self.assertIn("**Checked:** 1 requirement and 1 test case, and 2 pairs", report)
        self.assertNotIn("never compared", report)
        self.assertIn("## CWA-REQ-22 — Session timeout while monitoring — in Monitoring, outside this run's scope",
                      bc_run.cmd_show(str(run), "CWA-REQ-22"))


TICKETS = [
    {"id": "PROJ-7", "title": "Shorter idle logout", "url": "https://jira.test/browse/PROJ-7",
     "linked_ids": ["CWA-REQ-21", "PROJ-1"], "line": "Session timeout: the session ends after 2 minutes idle."},
    {"id": "PROJ-8", "title": "Quieter alarms at night", "url": "https://jira.test/browse/PROJ-8", "linked_ids": [],
     "line": "Alarm volume: alarms sound at 60 dB at night."},
    {"id": "PROJ-9", "title": "Export reports as CSV", "url": "https://jira.test/browse/PROJ-9",
     "line": "Report export: reports download as CSV files."},
]


class Tickets(RunFolderTest):
    def checked_run(self, cases=ACROSS_CASES, lines=SUBJECT_LINES):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": cases})
        with mock.patch.object(bc_run, "SLICE_BYTES", 400):
            bc_run.cmd_link(str(run))
        for n in range(1, bc_run.cmd_slices(str(run))["total"] + 1):
            ids = bc_run.cmd_slices(str(run), n)["needs_lines"]
            bc_run.cmd_subjects(str(run), n, json.dumps({i: lines[i] for i in ids}))
            bc_run.cmd_findings(str(run), add_text="[]", slice_number=n)
        return run

    def matched_run(self, **run):
        run = self.checked_run(**run)
        for n in bc_run.cmd_match(str(run))["slices"]:
            bc_run.cmd_findings(str(run), add_text="[]", slice_number=n)
        return run

    def test_a_ticket_is_compared_with_what_it_links_to_and_the_items_on_its_subject_in_a_slice_of_its_own(self):
        run = self.matched_run()
        answer = bc_run.cmd_tickets(str(run), json.dumps(TICKETS))
        self.assertEqual(answer, {"saved": 3, "rejected": [], "alone": ["PROJ-9"], "not_in_run": {}, "slices": [5], "total": 5})
        listed = bc_run.cmd_slices(str(run), 5)
        self.assertEqual((listed["tickets"], listed["checked"], listed["needs_lines"]), (2, False, []))
        text = "".join(Path(p).read_text() for p in listed["parts"])
        self.assertIn("## PROJ-7 — Shorter idle logout\nLink: https://jira.test/browse/PROJ-7\n"
                      "Subject line: \"Session timeout: the session ends after 2 minutes idle.\"\n"
                      "Compare with: CWA-REQ-21 (it links to it) · CWA-REQ-22, TC-210 (on its subject)\n", text)
        self.assertIn("Compare with: CWA-REQ-23, TC-230 (on its subject)\n", text)
        for words in ("The session shall end after 5 minutes idle.", "The session shall stay open while monitoring.",
                      "Leave the app idle for 5 minutes", "Alarms shall sound at 80 dB."):
            self.assertIn(words, text)
        self.assertNotIn("PROJ-9", text, "a ticket with nothing on its subject has nothing to compare")

    def test_tickets_are_saved_once_after_the_run_is_matched_and_all_or_nothing(self):
        run = self.checked_run()
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_tickets(str(run), json.dumps(TICKETS))
        self.assertIn("run match first", str(caught.exception))
        bc_run.cmd_match(str(run))
        answer = bc_run.cmd_tickets(str(run), json.dumps([
            TICKETS[0], {"id": "PROJ-8", "title": "Quieter alarms at night"}, TICKETS[0],
            {**TICKETS[2], "line": "Report export: " + "CSV " * 60}]))
        self.assertEqual(answer["saved"], 0)
        self.assertEqual([(r["ticket"], r["id"]) for r in answer["rejected"]], [(2, "PROJ-8"), (3, "PROJ-7"), (4, "PROJ-9")])
        self.assertIn("PROJ-7 is listed twice", answer["rejected"][1]["problem"])
        self.assertFalse((run / "tickets.json").exists(), "nothing is saved while a ticket is sent back")
        self.assertEqual(bc_run.cmd_tickets(str(run), json.dumps(TICKETS))["saved"], 3)
        with self.assertRaises(bc_run.RunError) as caught:
            bc_run.cmd_tickets(str(run), json.dumps(TICKETS))
        self.assertIn("saved already", str(caught.exception))

    def test_in_a_run_of_one_slice_a_ticket_is_matched_by_the_items_own_words(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": ACROSS_CASES})
        bc_run.cmd_link(str(run))
        self.assertEqual(bc_run.cmd_match(str(run))["pairs"], 0, "one slice: nobody wrote subject lines")
        answer = bc_run.cmd_tickets(str(run), json.dumps(TICKETS[1:]))
        self.assertEqual((answer["slices"], answer["alone"]), ([2], ["PROJ-9"]))
        text = "".join(Path(p).read_text() for p in bc_run.cmd_slices(str(run), 2)["parts"])
        self.assertIn("Compare with: CWA-REQ-23", text)

    def idle_run(self):
        """A run with two idle test cases, which a ticket that shortens the idle logout breaks."""
        warning = case(211, "Warning before idle logout", "CWA-REQ-21", section="Login", steps=[
            {"content": "Leave the app idle for 4 minutes", "expected": "A warning says you will be logged out in 1 minute"}])
        return self.matched_run(cases=[*ACROSS_CASES, warning],
                                lines={**SUBJECT_LINES, "TC-211": "Session timeout: a warning comes 1 minute before logout."})

    def test_an_item_that_counts_a_ticket_among_its_closest_is_compared_with_it_too(self):
        run = self.idle_run()
        with mock.patch.object(bc_run, "MATCHES_PER_ITEM", 1):
            n = bc_run.cmd_tickets(str(run), json.dumps(TICKETS))["slices"][0]
        text = "".join(Path(p).read_text() for p in bc_run.cmd_slices(str(run), n)["parts"])
        self.assertIn("Compare with: CWA-REQ-21 (it links to it) · CWA-REQ-22, TC-210, TC-211 (on its subject)\n", text,
                      "a ticket's own closest are capped, so a test case on its subject that counts it among its own "
                      "closest is compared too")

    def test_the_report_names_tickets_compared_with_nothing_and_links_outside_the_run(self):
        run = self.matched_run()
        linked = {**TICKETS[0], "linked_ids": ["CWA-REQ-21", "PROJ-1", "CWA-REQ-99", "TC-999"]}
        answer = bc_run.cmd_tickets(str(run), json.dumps([linked, *TICKETS[1:]]))
        self.assertEqual((answer["alone"], answer["not_in_run"]), (["PROJ-9"], {"PROJ-7": ["CWA-REQ-99", "TC-999"]}),
                         "PROJ-1 is another ticket, not a requirement or test case")
        text = "".join(Path(p).read_text() for p in bc_run.cmd_slices(str(run), answer["slices"][0])["parts"])
        self.assertIn("Not in this run: CWA-REQ-99, TC-999\n", text)
        bc_run.cmd_findings(str(run), add_text="[]", slice_number=answer["slices"][0])
        report = Path(bc_run.cmd_report(str(run))["parts"][0]).read_text()
        self.assertIn("**Not compared:** PROJ-9 — nothing in this run is on its subject.", report)
        self.assertIn("**Not in this run:** CWA-REQ-99 and TC-999, linked from PROJ-7.", report)

    def ticket_run(self):
        """A run whose idle logout a ticket shortens: it breaks the requirement and both idle test cases."""
        run = self.idle_run()
        n = bc_run.cmd_tickets(str(run), json.dumps(TICKETS))["slices"][0]
        shorter = {"id": "PROJ-7", "quote": "Log out after 2 minutes idle", "board": {"title": "ignored", "url": "https://x.test"}}
        self.breaks = {"severity": "high", "fix": ["TC-210", "tc-211"], "items": [
            shorter, {"id": "TC-210", "quote": "Leave the app idle for 5 minutes"},
            {"id": "TC-211", "quote": "Leave the app idle for 4 minutes"}],
            "why": "Both test cases wait longer than the 2 minutes after which the ticket logs out."}
        self.contradicts = {"severity": "critical", "fix": "proj-7", "why": "The ticket logs out 3 minutes sooner.",
                            "items": [shorter, {"id": "CWA-REQ-21", "quote": "end after 5 minutes idle"}]}
        self.one_case = {**self.breaks, "fix": "TC-210", "items": self.breaks["items"][:2]}
        return run, n

    def test_a_ticket_s_finding_is_never_offered_and_names_the_test_cases_to_update_when_it_ships(self):
        run, n = self.ticket_run()
        unsaved = {**self.contradicts, "items": [{**self.contradicts["items"][0], "id": "PROJ-99"}, self.contradicts["items"][1]]}
        not_a_ticket = {**self.contradicts, "fix": ["TC-210"], "items": [self.contradicts["items"][1], self.breaks["items"][1]]}
        result = bc_run.cmd_findings(str(run), slice_number=n, add_text=json.dumps(
            [self.breaks, self.contradicts, self.one_case, unsaved, not_a_ticket]))
        self.assertEqual((result["saved"], result["checked"]), (3, False))
        problems = {r["finding"]: " ".join(r["problems"]) for r in result["rejected"]}
        self.assertIn("PROJ-99 is not in this run", problems[4])
        self.assertIn("a list of test cases is for a board ticket's finding", problems[5])
        found = bc_run.cmd_findings(str(run))
        self.assertEqual([(f["kind"], f["fix"]) for f in found["findings"]], [
            ("board ticket ↔ requirement", "change PROJ-7."),
            ("board ticket ↔ test case", "update TC-210 (step 1) and TC-211 (step 1) when PROJ-7 ships."),
            ("board ticket ↔ test case", "update TC-210 (step 1) when PROJ-7 ships.")])
        self.assertEqual(found["fix_test_cases"], [], "the ticket hasn't shipped and may still change")
        ticket = bc_run.cmd_findings(str(run), numbers_text="1")["findings"][0]["items"][0]
        self.assertEqual((ticket["title"], ticket["url"], ticket["checked"]),
                         ("Shorter idle logout", "https://jira.test/browse/PROJ-7", False))
        self.assertTrue(bc_run.cmd_findings(str(run), add_text="[]", slice_number=n)["checked"])

    def test_the_report_gives_tickets_their_own_section_and_names_the_test_cases_to_update_when_each_ships(self):
        run, n = self.ticket_run()
        unchecked = Path(bc_run.cmd_report(str(run))["parts"][0]).read_text()
        self.assertIn(f"**Not checked:** slice {n} of {n}", unchecked)
        bc_run.cmd_findings(str(run), slice_number=n, add_text=json.dumps([self.breaks, self.contradicts, self.one_case]))
        result = bc_run.cmd_report(str(run))
        self.assertEqual(result["fix_test_cases"], [])
        report = Path(result["parts"][0]).read_text()
        self.assertRegex(report, r"\*\*Checked:\*\* 3 requirements and 4 test cases, \d+ pairs? of items on the same "
                                 r"subject across the product, and 3 board tickets\.")
        self.assertIn("## Board tickets — work in progress that conflicts with a requirement or a test case\n\n"
                      "### 1. Critical: board ticket ↔ requirement\n"
                      "- **PROJ-7** [Shorter idle logout](https://jira.test/browse/PROJ-7): \"Log out after 2 minutes idle\" "
                      "_(not checked: the ticket's text isn't in the run)_\n", report)
        self.assertIn("- **Fix:** update TC-210 (step 1) and TC-211 (step 1) when PROJ-7 ships.", report)
        self.assertIn("## Test cases to update when the tickets ship\n\n"
                      "- **PROJ-7** [Shorter idle logout](https://jira.test/browse/PROJ-7): TC-210 and TC-211 — "
                      "findings 2 and 3.\n", report)
        self.assertNotIn("Not checked", report)
        self.assertNotIn("## Test cases to fix", report)

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


def background_item(bid, title, description, folder, item_type="System Requirement", status="Approved"):
    return {"type": "background", "id": bid, "title": title, "description": description, "rationale": "",
            "feature": folder.split(" / ")[-1], "status": status, "linked_ids": [],
            "raw_fields": {"item_type": item_type, "folder_path": folder, "url": f"https://jama.test/{bid}"}}


BACKGROUND = [
    background_item("SYS-1", "Alarm audibility", "The system shall keep alarms audible during sensor warm-up.", "Design Inputs / System RS / Alarms"),
    background_item("RISK-4", "Missed alarm", "A muted alarm can hide a low reading.", "Design Inputs / Risk", item_type="Risk"),
]


def background_file(items, **args):
    data = jama_file(items, **args)
    return {**data, "kind": "background", "scope": {"input": "", "kind": "background"},
            "summary": f"{len(items)} background items — the rest of the project", "args": {**data["args"], "background": True}}


def testrail_file(cases, **overrides):
    return {"kind": "test_cases", "source": "testrail", "project_id": 1, "suite_id": 9,
            "requirement_field": "custom_jama_req_id", "filter": {"requirement_ids": [], "section": "", "unlinked": False},
            "test_cases": cases, **overrides}


class Checks(RunFolderTest):
    def setUp(self):
        super().setUp()
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(JAMA_CONFIG))

    def link(self, requirements_file, cases_file, docs=None, scope="", command="coverage", background=None, product=None):
        run = self.new_run(command=command, scope=scope)
        self.write(run, "requirements.json", requirements_file)
        if cases_file is not None:
            self.write(run, "test_cases.json", cases_file)
        if docs is not None:
            self.write(run, "docs.json", docs)
        if background is not None:
            self.write(run, "background.json", background)
        if product is not None:
            self.write(run, "product.json", product)
        return bc_run.cmd_link(str(run))

    def test_a_contradiction_run_on_part_of_the_product_reads_the_whole_product_too(self):
        self.assertEqual(Path(bc_run.cmd_new(str(self.project), "contradiction", "Vitals")["product"]).name, "product.json")
        for command, scope in (("contradiction", ""), ("contradiction", "all"), ("coverage", "Vitals"), ("lookup", "Vitals")):
            self.assertNotIn("product", bc_run.cmd_new(str(self.project), command, scope), (command, scope))

    def test_the_rest_of_the_product_is_read_to_compare_with_and_never_counted(self):
        vitals = jama_file([r for r in REQUIREMENTS if r["feature"] == "Vitals"], scope="Vitals")
        result = self.link(vitals, testrail_file(CASES), scope="Vitals", command="contradiction", product=jama_file(REQUIREMENTS))
        self.assertEqual((result["mismatches"], result["missing"]), ([], []))
        self.assertEqual(result["lines"][1], "Fetch: the rest of the product → 3 requirements and 6 test cases outside the "
                                             "scope, to compare it with, never counted")
        self.assertEqual((result["counts"]["requirements"], result["counts"]["test_cases_in_scope"]), (2, 1))

        result = self.link(vitals, testrail_file(CASES), scope="Vitals", command="contradiction")
        self.assertEqual(result["missing"], ["the rest of the product"])
        self.assertIn("Fetch: the rest of the product → FAILED — no file in the run", result["lines"])

        wrong = jama_file(REQUIREMENTS, scope="Vitals", root_ids=[])
        result = self.link(vitals, testrail_file(CASES), scope="Vitals", command="contradiction", product=wrong)
        self.assertIn("the product's requirements were read for scope 'Vitals', but the whole product is read with "
                      "scope all.", result["mismatches"])
        self.assertIn("the product's requirements were read with root_ids=none, but the config says [7].", result["mismatches"])

        whole = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="contradiction", product=wrong)
        self.assertEqual((whole["mismatches"], whole["missing"]), ([], []), "a run of the whole product needs no more")
        self.assertFalse([line for line in whole["lines"] if "rest of the product" in line])

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

    def test_files_read_from_another_site_are_named(self):
        config = {**JAMA_CONFIG, "requirements": {**JAMA_CONFIG["requirements"], "base_url": "https://acme.jamacloud.com/"},
                  "test_cases": {**JAMA_CONFIG["test_cases"], "base_url": "https://acme.testrail.io/index.php?/dashboard"}}
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(config))
        same = self.link({**jama_file(REQUIREMENTS), "site": "acme.jamacloud.com"}, testrail_file(CASES, site="acme.testrail.io"))
        self.assertEqual(same["mismatches"], [])
        other = self.link({**jama_file(REQUIREMENTS), "site": "other.jamacloud.com"}, testrail_file(CASES, site="other.testrail.io"),
                          command="lookup", background={**background_file(BACKGROUND), "site": "other.jamacloud.com"})
        self.assertEqual(other["mismatches"], [
            "requirements were read from other.jamacloud.com, but the config names acme.jamacloud.com.",
            "background items were read from other.jamacloud.com, but the config names acme.jamacloud.com.",
            "test cases were read from other.testrail.io, but the config names acme.testrail.io.",
        ])

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

    def test_a_project_still_set_up_for_excel_is_sent_to_setup(self):
        config = {**JAMA_CONFIG, "requirements": {"provider": "excel", "enrichment": {"enabled": False}}}
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(config))
        result = self.link(REQUIREMENTS, testrail_file(CASES))
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertIn("run /bc:setup with a link to the Jama project", result["mismatches"][0])
        self.assertEqual(result["lines"][0], "Fetch: requirements → 5")

    def test_a_lookup_on_jama_reports_its_background_and_never_counts_it(self):
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="lookup", background=background_file(BACKGROUND))
        self.assertEqual((result["mismatches"], result["missing"]), ([], []))
        self.assertEqual(
            result["lines"][1],
            "Fetch: background → 2 items from the rest of Jama project 42, context for answers, never counted "
            "(2 background items — the rest of the project)",
        )
        self.assertEqual((result["counts"]["background"], result["counts"]["requirements"]), (2, 5))

    def test_a_lookup_on_jama_without_its_background_is_missing_a_source(self):
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="lookup")
        self.assertEqual(result["missing"], ["background"])
        self.assertIn("Fetch: background → FAILED — no background file in the run", result["lines"])
        self.assertEqual(self.link(jama_file(REQUIREMENTS), testrail_file(CASES))["missing"], [], "only lookups read it")

    def test_a_background_file_in_a_run_that_isnt_a_lookup_is_left_out(self):
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="coverage",
                           background=background_file(BACKGROUND, root_ids=[]))
        self.assertEqual((result["mismatches"], result["missing"]), ([], []))
        self.assertFalse([line for line in result["lines"] if line.startswith("Fetch: background")])
        self.assertNotIn("background", result["counts"])

    def test_background_read_with_other_arguments_is_named(self):
        wrong = background_file(BACKGROUND, root_ids=[], item_type_exclude=["Text"])
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="lookup", background=wrong)
        self.assertIn("background items were read with root_ids=none, but the config says [7].", result["mismatches"])
        self.assertIn("background items were read with item_type_exclude=['text'], but the config says none.", result["mismatches"])
        unmarked = {**background_file(BACKGROUND), "kind": "requirements"}
        result = self.link(jama_file(REQUIREMENTS), testrail_file(CASES), command="lookup", background=unmarked)
        self.assertIn("background.json was not written by the Jama server with background=true.", result["mismatches"])


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

    def test_background_is_searched_and_shown_apart_from_the_requirements(self):
        before = [t["id"] for t in self.search()["top"]]
        self.write(self.run, "background.json", background_file(BACKGROUND))
        result = self.search()
        self.assertEqual([t["id"] for t in result["top"]], before, "background never enters the requirement ranking")
        self.assertEqual((result["searched"]["background"], result["matched"]["background"]), (2, 2))
        self.assertEqual({b["id"] for b in result["top_background"]}, {"SYS-1", "RISK-4"})
        text = self.text(result)
        self.assertIn("and 2 background items elsewhere in the project", text)
        self.assertIn("## Elsewhere in the project — background, not requirements in scope: 2 match, the top 2 shown", text)
        self.assertIn(
            "- SYS-1 — Alarm audibility · System Requirement · Status: Approved · Folder: Design Inputs / System RS / Alarms · matches:",
            text,
        )
        self.assertIn("  > The system shall keep alarms audible during sensor warm-up.\n  Link: https://jama.test/SYS-1", text)
        shown = bc_run.cmd_show(str(self.run), "SYS-1, R-1")
        self.assertIn("## SYS-1 — Alarm audibility — background, not a requirement in scope\nStatus: Approved · Type: System Requirement", shown)
        self.assertIn("## R-1\n", shown)

    def test_background_answers_when_no_requirement_does(self):
        self.write(self.run, "background.json", background_file(BACKGROUND))
        result = self.search("missed reading risk", ["missed|hide", "reading"])
        self.assertEqual(result["matched"]["requirements"], 0)
        self.assertEqual(result["top_background"][0]["id"], "RISK-4")
        text = self.text(result)
        self.assertIn("## No requirement matches any of the concepts", text)
        self.assertIn("- RISK-4 — Missed alarm · Risk", text)


class Recent(RunFolderTest):
    def setUp(self):
        super().setUp()
        (self.project / ".buddy-council" / "sources.json").write_text(json.dumps(JAMA_CONFIG))

    def complete_run(self, scope="", requirements=None, cases=None, background=True):
        run = self.new_run(command="lookup", scope=scope)
        self.write(run, "requirements.json", requirements or jama_file(REQUIREMENTS, scope=scope))
        self.write(run, "test_cases.json", cases or testrail_file(CASES))
        if background:
            self.write(run, "background.json", background_file(BACKGROUND))
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

    def test_a_jama_run_without_background_is_not_reused_for_a_lookup(self):
        coverage = self.new_run(command="coverage")
        self.write(coverage, "requirements.json", jama_file(REQUIREMENTS))
        self.write(coverage, "test_cases.json", testrail_file(CASES))
        bc_run.cmd_link(str(coverage))
        self.age(coverage, 1)
        self.assertIsNone(bc_run.cmd_recent(str(self.project), 30)["run_dir"])
        lookup = self.complete_run()
        self.age(lookup, 2)
        self.assertEqual(bc_run.cmd_recent(str(self.project), 30)["run_dir"], str(lookup))

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


class FakeHttp:
    """Stands in for GitHub's REST API: answers from a dict of URL -> JSON; anything else is a 404."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.requests = []

    def __call__(self, request, timeout=None):
        assert timeout, "every request is bounded"
        self.requests.append((request.full_url, request.get_header("Authorization")))
        answer = self.answers.get(request.full_url)
        if answer is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "No", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(answer).encode())


class Enrich(RunFolderTest):
    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items()
               if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_HOST")}
        env["BC_HOME"] = str(Path(self.tmp.name, "bc-home"))
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.http = FakeHttp()
        opener = mock.patch.object(bc_run, "URLOPEN", self.http)
        opener.start()
        self.addCleanup(opener.stop)
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

    def test_with_no_sign_in_that_can_read_them_every_doc_fails_without_crashing(self):
        def no_gh(argv, **kwargs):
            raise FileNotFoundError("gh")

        result = bc_run.cmd_enrich(str(self.run), runner=no_gh)
        self.assertEqual(result["fetched"], 0)
        self.assertEqual({f["error"] for f in result["failed"]}, {"repo_inaccessible", "unsupported_url"})
        self.assertTrue(self.http.requests, "public repositories are tried with no sign-in")
        self.assertEqual({auth for _, auth in self.http.requests}, {None})

    def test_without_gh_a_token_in_the_environment_reads_them(self):
        def no_gh(argv, **kwargs):
            raise FileNotFoundError("gh")

        sds = "# SDS from the API"
        self.http.answers["https://api.github.com/repos/o/r/contents/docs/sds.md?ref=main"] = {
            "content": b64(sds), "sha": "1", "size": len(sds)}
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}):
            bc_run.cmd_enrich(str(self.run), runner=no_gh)
        docs = json.loads((self.run / "docs.json").read_text())["docs"]
        self.assertEqual([(d["url"], d["read_with"]) for d in docs],
                         [("https://github.com/o/r/blob/main/docs/sds.md#L4", "GITHUB_TOKEN")])
        self.assertIn(("https://api.github.com/repos/o/r/contents/docs/sds.md?ref=main", "Bearer env-token"),
                      self.http.requests)

    def test_gh_that_isnt_signed_in_falls_back_to_public_reads(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 4, "", "To get started with GitHub CLI, please run:  gh auth login")

        sds = "# Public SDS"
        self.http.answers["https://api.github.com/repos/o/r/contents/docs/sds.md?ref=main"] = {
            "content": b64(sds), "sha": "1", "size": len(sds)}
        bc_run.cmd_enrich(str(self.run), runner=runner)
        docs = json.loads((self.run / "docs.json").read_text())["docs"]
        self.assertEqual([d["read_with"] for d in docs], ["no sign-in"])

    def test_a_file_gh_can_see_is_missing_is_not_retried_with_other_sign_ins(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "env-token"}):
            index = bc_run.cmd_enrich(str(self.run), runner=self.gh)
        self.assertIn({"url": "https://github.com/o/r/blob/main/gone.md", "error": "file_not_found"},
                      [{k: f[k] for k in ("url", "error")} for f in index["failed"]])
        self.assertFalse([url for url, _ in self.http.requests if "gone.md" in url])

    def test_github_enterprise_uses_its_own_host_and_never_a_github_com_token(self):
        import bc_keys

        bc_keys.write_key("github", "https://github.acme.com", {"token": "ghe-key"})
        doc = "# Enterprise SDS"
        self.http.answers["https://github.acme.com/api/v3/repos/team/app/contents/docs/a.md?ref=main"] = {
            "content": b64(doc), "sha": "9", "size": len(doc)}
        calls = []

        def gh(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "gh: Not Found (HTTP 404)")

        with mock.patch.dict(os.environ, {"GH_TOKEN": "github-com-token"}):
            got, reader, tried = bc_run.read_doc("https://github.acme.com/team/app/blob/main/docs/a.md", gh)
        self.assertEqual((got["content"], reader), (doc, "saved key"))
        self.assertEqual(got["locator"]["host"], "github.acme.com")
        self.assertIn("--hostname", calls[0])
        self.assertEqual(calls[0][calls[0].index("--hostname") + 1], "github.acme.com")
        self.assertNotIn("Bearer github-com-token", {auth for _, auth in self.http.requests})
        self.assertEqual([t["reader"] for t in tried], ["gh", "saved key"])

    def test_gh_always_names_the_host_it_reads(self):
        calls = []

        def gh(argv, **kwargs):
            calls.append((argv, kwargs.get("env")))
            return self.gh(argv, **kwargs)

        with mock.patch.dict(os.environ, {"GH_HOST": "github.acme.com", "GH_ENTERPRISE_TOKEN": "ghe-env-token"}):
            bc_run.read_doc("https://github.com/o/r/blob/main/docs/sds.md", gh)
        argv, env = calls[0]
        self.assertEqual(argv[argv.index("--hostname") + 1], "github.com", "never GH_HOST's host for a github.com doc")
        self.assertNotIn("GH_ENTERPRISE_TOKEN", env)

    def test_an_enterprise_token_goes_only_to_the_host_named_in_gh_host(self):
        doc = "# Enterprise SDS"
        for host in ("github.acme.com", "github.evil.io"):
            self.http.answers[f"https://{host}/api/v3/repos/team/app/contents/docs/a.md?ref=main"] = {
                "content": b64(doc), "sha": "9", "size": len(doc)}
        envs = []

        def gh(argv, **kwargs):
            envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(argv, 1, "", "gh: Not Found (HTTP 404)")

        with mock.patch.dict(os.environ, {"GH_ENTERPRISE_TOKEN": "ghe-env-token", "GH_TOKEN": "github-com-token"}):
            _, reader, _ = bc_run.read_doc("https://github.evil.io/team/app/blob/main/docs/a.md", gh)
            self.assertEqual(reader, "no sign-in")
            self.assertEqual({auth for _, auth in self.http.requests}, {None}, "a look-alike host gets no token")
            self.assertFalse({"GH_ENTERPRISE_TOKEN", "GH_TOKEN"} & set(envs[-1]), "nor does gh, when it goes there")
            with mock.patch.dict(os.environ, {"GH_HOST": "https://github.acme.com"}):
                _, reader, _ = bc_run.read_doc("https://github.acme.com/team/app/blob/main/docs/a.md", gh)
        self.assertEqual(reader, "GH_ENTERPRISE_TOKEN")
        self.assertTrue(self.http.requests[-1][1].endswith("ghe-env-token"))
        self.assertIn("GH_ENTERPRISE_TOKEN", envs[-1])

    def test_one_doc_prints_verbatim_with_what_read_it(self):
        text = bc_run.cmd_doc("https://github.com/o/r/blob/main/docs/sds.md#L4", max_chars=10 ** 6, runner=self.gh)
        head, _, body = text.partition("\n")
        meta = json.loads(head[len("<!-- "):-len(" -->")])
        self.assertEqual((meta["read_with"], meta["locator"]["anchor"], meta["truncated"]), ("gh", "L4", False))
        self.assertTrue(body.startswith("# SDS\n![flow](images/flow.png)"))
        self.assertEqual(meta["referenced_images"][0]["url"], "https://raw.githubusercontent.com/o/r/main/docs/images/flow.png")
        with self.assertRaises(bc_run.RunError):
            bc_run.cmd_doc("https://github.com/o/r/blob/main/gone.md", runner=self.gh)

    def test_a_redirect_is_an_error_so_a_token_never_follows_it(self):
        handler = bc_run._NoRedirect()
        request = urllib.request.Request("https://api.github.com/repos/o/r", headers={"Authorization": "Bearer t"})
        with self.assertRaises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 301, "Moved", {}, "https://evil.example/repos/o/r")

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


SNOOZE_STEPS = [
    {"content": "Silence the alarm", "expected": "The alarm stays quiet for 15 minutes"},
    {"content": "Wait until the snooze ends", "expected": "The alarm sounds again"},
]


class Findings(RunFolderTest):
    def setUp(self):
        super().setUp()
        self.run = self.new_run(command="contradiction")
        reqs = [requirement(r["id"], r["title"], r["feature"], url=f"https://req.test/{r['id']}") for r in REQUIREMENTS]
        snooze = case(20, "Snooze a sounding alarm", "CWA-REQ-3", steps=SNOOZE_STEPS)
        snooze["raw_fields"]["url"] = "https://tc.test/20"
        cases = [*CASES, snooze, case(21, "Snooze ends on time", "CWA-REQ-3", steps=[
            {"content": "Silence the alarm", "expected": "The alarm stays quiet for 5 minutes"}])]
        self.write(self.run, "requirements.json", {"requirements": reqs})
        self.write(self.run, "test_cases.json", {"test_cases": cases})
        bc_run.cmd_link(str(self.run))

    def add(self, *findings):
        return bc_run.cmd_findings(str(self.run), add_text=json.dumps(list(findings)))

    def snooze(self, severity="critical", fix="TC-20", why="The test passes an alarm that stays quiet three times as long."):
        return {"severity": severity, "fix": fix, "why": why, "items": [
            {"id": "CWA-REQ-3", "quote": "silence  ALARMS for 5 minutes"},
            {"id": "C20", "quote": "\u201cThe alarm stays quiet for 15 minutes\u201d"}]}

    def test_a_finding_is_saved_only_when_its_ids_and_quotes_are_in_the_run(self):
        result = self.add(
            self.snooze(),
            {"severity": "high", "fix": "TC-20", "why": "x", "items": [
                {"id": "CWA-REQ-3", "quote": "for 5 minutes"}, {"id": "TC-20", "quote": "5 minutes"}]},
            {"severity": "high", "fix": "decide", "why": "x", "items": [
                {"id": "CWA-REQ-99", "quote": "anything"}, {"id": "TC-20", "quote": "The alarm sounds again"}]},
            {"severity": "high", "fix": "TC-10", "why": "x", "items": [
                {"id": "CWA-REQ-3", "quote": "silence alarms"}, {"id": "TC-20", "quote": "The alarm sounds again"}]},
        )
        self.assertEqual((result["saved"], result["total"]), (1, 1))
        problems = {r["finding"]: " ".join(r["problems"]) for r in result["rejected"]}
        self.assertEqual(sorted(problems), [2, 3, 4])
        self.assertIn('"5 minutes" is not in its text', problems[2], "a quote never matches inside a longer number")
        self.assertIn('The closest is step 1, expected result: "The alarm stays quiet for 15 minutes"', problems[2])
        self.assertIn("CWA-REQ-99 is not in this run", problems[3])
        self.assertIn("fix: the ID of the item to change (CWA-REQ-3, TC-20)", problems[4])
        saved = bc_run.cmd_findings(str(self.run), numbers_text="1")["findings"][0]
        self.assertEqual(saved["kind"], "requirement ↔ test case")
        self.assertEqual([(i["id"], i["where"], i["quote"]) for i in saved["items"]], [
            ("CWA-REQ-3", "description", "silence alarms for 5 minutes"),
            ("TC-20", "step 1, expected result", "The alarm stays quiet for 15 minutes")])
        self.assertEqual(saved["fix"], {"id": "TC-20", "type": "test case", "where": "step 1, expected result"})

    def test_a_quote_copied_with_its_bundle_prefix_is_placed_and_a_long_one_is_sent_back(self):
        result = self.add(
            {"severity": "high", "fix": "decide", "why": "x", "items": [
                {"id": "TC-20", "quote": "Expected: The alarm sounds again"}, {"id": "TC-20", "quote": "Preconditions: device paired"}]},
            {"severity": "high", "fix": "TC-20", "why": "x", "items": [
                {"id": "CWA-REQ-3", "quote": "Silence alarms " * 40}, {"id": "TC-20", "quote": "1. Silence the alarm"}]},
        )
        self.assertEqual(result["saved"], 1)
        self.assertIn("at most 500 characters", " ".join(result["rejected"][0]["problems"]))
        saved = bc_run.cmd_findings(str(self.run), numbers_text="1")["findings"][0]
        self.assertEqual([(i["where"], i["quote"]) for i in saved["items"]], [
            ("step 2, expected result", "The alarm sounds again"), ("preconditions", "Preconditions: device paired")])

    def test_kinds_gaps_and_a_finding_saved_again(self):
        self.add(
            {"severity": "medium", "fix": "TC-20", "why": "It silences the alarm, then expects it to sound.", "items": [
                {"id": "TC-20", "quote": "Wait until … ends"}, {"id": "TC-20", "quote": "Silence the alarm"},
                {"id": "CWA-REQ-3", "quote": "silence alarms for 5 minutes"}]},
            {"severity": "medium", "fix": "decide", "why": "Both describe the alarm differently.", "items": [
                {"id": "CWA-REQ-1", "quote": "sound an audible alarm"}, {"id": "CWA-REQ-2", "quote": "show a visual alarm"}]},
        )
        self.add({**self.snooze(), "severity": "high"})
        self.add(self.snooze(why="Saved again with a sharper reason."))
        found = bc_run.cmd_findings(str(self.run))
        self.assertEqual([(f["n"], f["severity"], f["kind"]) for f in found["findings"]], [
            (1, "critical", "requirement ↔ test case"),
            (2, "medium", "requirement ↔ test case"),
            (3, "medium", "requirement ↔ requirement")])
        first = bc_run.cmd_findings(str(self.run), numbers_text="1, 2")["findings"]
        self.assertEqual(first[0]["why"], "Saved again with a sharper reason.")
        self.assertEqual(first[1]["items"][0]["quote"], "Wait until … ends")
        self.assertEqual(first[1]["fix"]["where"], "step 2", "the fix points at the first quote of the item to change")

    def test_a_test_case_is_the_side_to_fix_only_beside_the_requirement_words_it_breaks(self):
        pair = {"severity": "high", "fix": "TC-20", "why": "Two test cases of one snooze expect different lengths.",
                "items": [{"id": "TC-20", "quote": "stays quiet for 15 minutes"}, {"id": "TC-21", "quote": "stays quiet for 5 minutes"}]}
        itself = {"severity": "medium", "fix": "TC-20", "why": "It silences the alarm, then expects it to sound.",
                  "items": [{"id": "TC-20", "quote": "Wait until … ends"}, {"id": "TC-20", "quote": "Silence the alarm"}]}
        result = self.add(pair, itself)
        self.assertEqual(result["saved"], 0)
        self.assertEqual({p for r in result["rejected"] for p in r["problems"]}, {
            'fix: add the requirement words TC-20 breaks to items, or write "decide" when no requirement settles it'})
        settles = {"id": "CWA-REQ-3", "quote": "silence alarms for 5 minutes"}
        result = self.add(pair | {"items": [*pair["items"], settles]}, itself | {"fix": "decide"})
        self.assertEqual((result["saved"], result["rejected"]), (2, []))
        found = bc_run.cmd_findings(str(self.run))
        self.assertEqual([(f["kind"], f["fix"]) for f in found["findings"]], [
            ("requirement ↔ test case", "change TC-20, step 1, expected result."),
            ("test case contradicts itself", "a person decides which side is right.")])
        self.assertEqual(found["fix_test_cases"], [1])

    def test_the_report_links_both_sides_and_offers_only_checked_test_case_fixes(self):
        bc_run.cmd_tickets(str(self.run), json.dumps([{"id": "PROJ-7", "title": "Shorter snooze", "url": "https://jira.test/PROJ-7",
                                                       "linked_ids": ["CWA-REQ-3"], "line": "Snooze: lasts 1 minute."}]))
        self.add(
            self.snooze(),
            self.snooze(severity="low", why="A weak test.") | {"items": [
                {"id": "CWA-REQ-4", "quote": "display heart rate"}, {"id": "TC-14"}], "fix": "TC-14"},
            {"severity": "high", "fix": "PROJ-7", "why": "The ticket shortens the snooze.", "items": [
                {"id": "PROJ-7", "quote": "Snooze for 1 minute"},
                {"id": "CWA-REQ-3", "quote": "silence alarms for 5 minutes"}]},
        )
        result = bc_run.cmd_report(str(self.run), lines_text='["Fetch: board issues \u2192 4 fetched from board 9"]')
        self.assertEqual(result["findings"], {"critical": 1, "high": 1, "medium": 0, "low": 1})
        self.assertEqual(result["fix_test_cases"], [1], "never a low finding, a board finding, or an unchecked quote")
        report = Path(result["parts"][0]).read_text()
        self.assertTrue(report.startswith("# Contradictions — the whole project\n"))
        self.assertIn("Fetch: board issues \u2192 4 fetched from board 9", report)
        self.assertIn("**Links:** 1 requirement has no test case and 3 test cases have no requirement. "
                      "`/bc:coverage` lists and repairs them.", report)
        self.assertIn("- **CWA-REQ-3** [Silence alarms for 5 minutes](https://req.test/CWA-REQ-3) — description: "
                      "\"silence alarms for 5 minutes\"", report)
        self.assertIn("- **TC-20** [Snooze a sounding alarm](https://tc.test/20) — step 1, expected result: "
                      "\"The alarm stays quiet for 15 minutes\"", report)
        self.assertIn("- **Fix:** change TC-20, step 1, expected result.", report)
        self.assertLess(report.index("### 1. Requirement ↔ test case"), report.index("### 2. Requirement ↔ test case"))
        self.assertIn("conflicts with a requirement or a test case\n\n### 3. High: board ticket ↔ requirement", report)
        self.assertIn("- **PROJ-7** [Shorter snooze](https://jira.test/PROJ-7): \"Snooze for 1 minute\"", report)
        self.assertIn("1 finding names a test case to change: 1.", report)

    def test_a_long_report_comes_in_parts_small_enough_for_one_read(self):
        self.add(*[self.snooze(why=f"Reason {n}: " + "the alarm stays quiet too long. " * 12) | {"items": [
            {"id": "CWA-REQ-3", "quote": "silence alarms for 5 minutes"},
            {"id": "TC-20", "quote": "The alarm stays quiet for 15 minutes"}, {"id": "TC-20", "quote": word}]}
            for n, word in enumerate(["Silence the alarm", "Wait until the snooze ends", "The alarm sounds again"])])
        with mock.patch.object(bc_run, "PART_BYTES", 1500):
            result = bc_run.cmd_report(str(self.run), partial="board issues: 401")
            parts = [Path(p).read_text() for p in result["parts"]]
            self.assertGreater(len(parts), 1)
            self.assertTrue(all(len(p.encode("utf-8")) <= bc_run.PART_BYTES for p in parts))
        self.assertIn("**PARTIAL** — not read: board issues: 401.", parts[0])
        self.assertTrue(parts[1].startswith("# Contradictions — the whole project (continued)\n\n## Critical"))


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

    def test_findings_and_report_print_json(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": CASES})
        bc_run.cmd_link(str(run))
        finding = {"severity": "high", "fix": "TC-10", "why": "It expects a sound the requirement doesn\u0027t ask for.",
                   "items": [{"id": "CWA-REQ-2", "quote": "show a visual alarm"}, {"id": "TC-10", "quote": "It happens"}]}
        ticket = {"id": "PROJ-7", "title": "Louder visual alarm", "line": "Visual alarm: flashes brighter."}
        for argv in (["tickets", "--run", str(run), "--add", json.dumps([ticket])],
                     ["findings", "--run", str(run), "--add", json.dumps(finding)], ["report", "--run", str(run)]):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(bc_run.main(argv), 0, argv)
            self.assertTrue(json.loads(out.getvalue())["ok"])
        self.assertEqual(json.loads(out.getvalue())["fix_test_cases"], [1])

    def test_slices_print_json_and_a_save_names_its_slice(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": REQUIREMENTS})
        bc_run.cmd_link(str(run))
        for argv, check in ((["findings", "--run", str(run), "--slice", "1", "--add", "[]"], "saved"),
                            (["slices", "--run", str(run)], "slices"), (["slices", "--run", str(run), "--slice", "1"], "parts")):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(bc_run.main(argv), 0, argv)
            result = json.loads(out.getvalue())
            self.assertTrue(result["ok"] and check in result, argv)
        self.assertEqual(result["checked"], True)

    def test_subject_lines_and_matching_print_json(self):
        run = self.new_run(command="contradiction")
        self.write(run, "requirements.json", {"requirements": ACROSS_REQUIREMENTS})
        self.write(run, "test_cases.json", {"test_cases": ACROSS_CASES})
        with mock.patch.object(bc_run, "SLICE_BYTES", 400):
            bc_run.cmd_link(str(run))
        lines = json.dumps({"CWA-REQ-21": SUBJECT_LINES["CWA-REQ-21"]})
        for argv, check in ((["subjects", "--run", str(run), "--slice", "1", "--add", lines], "missing"),
                            (["match", "--run", str(run)], "error")):
            out = io.StringIO()
            with redirect_stdout(out):
                bc_run.main(argv)
            self.assertIn(check, json.loads(out.getvalue()), argv)


if __name__ == "__main__":
    unittest.main()
