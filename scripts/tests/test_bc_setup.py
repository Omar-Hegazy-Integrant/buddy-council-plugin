"""A project folder's settings: found by walking up, saved only after they check out against the example, and an
older setup's leftovers moved and removed without a key ever passing through the assistant.

    python3 -m unittest discover -s scripts/tests -v

Standard library only; they must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions. Every
test runs in a throwaway home folder, so a developer's real ~/.buddy-council and ~/.copilot are never read or written.
"""

import contextlib
import copy
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))

import bc_keys  # noqa: E402
import bc_run  # noqa: E402
import bc_setup  # noqa: E402

SECRETS = ("tr-old-key", "jama-old-secret", "jira-old-token", "dc-old-pat", "gh-old-token", "users-own-token", "new-key")
EXAMPLE = json.loads((PLUGIN / "config" / "sources.example.json").read_text(encoding="utf-8"))


def settings_027():
    """A project's settings the way 0.27.0 saved them (shaped like a real team's)."""
    return {
        "plugin_root": "/Users/qa/.copilot/installed-plugins/buddy-council/bc",
        "requirements": {
            "provider": "jama", "base_url": "https://acme-prd.jamacloud.com", "project_id": 49, "project_key": "H",
            "project_name": "Mobile App",
            "roots": [{"id": 1234, "path": "Requirements / Mobile App SRS (SRS-1)"}],
            "feature_inference": {"strategy": "hierarchical_folder", "folder_item_type": "Folder", "depth": 3},
            "item_type_filter": ["Software Requirement"], "item_type_exclude": ["Text"],
            "field_mapping": {"status": "Status", "rationale": "Rationale", "github_url": "Linked to GitHub"},
            "enrichment": {"enabled": True, "strategy": "cli", "max_doc_chars": 50000},
        },
        "test_cases": {
            "provider": "testrail", "base_url": "https://acme.testrail.io", "project_id": 7, "suite_id": 12,
            "authoring": {"template_id": 2, "type_id": 6, "priority_id": 2, "requirement_field": "custom_jama_req_id",
                          "section_strategy": "feature", "section_root": None, "create_missing_sections": True},
        },
        "jira": {
            "base_url": "https://acme-rnd.atlassian.net", "deployment": "cloud", "project_key": "PROJ",
            "default_issue_type": "Story",
            "board": {"url": "https://acme-rnd.atlassian.net/jira/software/c/projects/PROJ/boards/42", "id": 42},
            "vnv_board": {"url": "https://acme-rnd.atlassian.net/jira/software/c/projects/PROJ/boards/77", "id": 77,
                          "project_key": "PROJ", "discriminator": {"kind": "component", "value": "V&V",
                                                                  "confidence": "sampled",
                                                                  "sampled_at": "2026-09-01T12:00:00Z"}},
            "platform": {"field_name": "OS", "field_id": "customfield_10042",
                         "values": {"ios": ["iOS"], "android": ["Android"]}, "require_parity": ["ios", "android"],
                         "title_prefix_fallback": True},
            "vnv_workflow": {"reviewer": {"account_id": "5b10a2844c20165700ede21g", "display_name": "Jane Doe"},
                             "approval_phrases": ["approved", "lgtm", "looks good"],
                             "labels": {"pending_validation": "pending-validation",
                                        "pending_questions": "pending-questions",
                                        "pending_scenario_validation": "pending-scenario-validation",
                                        "ready_for_test_cases": "ready-for-test-case-creation"}},
            "pending": False,
        },
        "project": {"enabled": True, "ignore_dirs": ["node_modules", "Pods"], "id_patterns": ["CWA-REQ-\\d+", "TC-\\d+"]},
    }


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name, "home").resolve()
        self.home.mkdir()
        env = {key: value for key, value in os.environ.items()
               if key not in ("BC_HOME", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                              "GH_HOST")}
        env["HOME"] = str(self.home)
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.project = self.home / "work" / "app"
        self.project.mkdir(parents=True)

    def key(self, provider, url, **fields):
        return bc_keys.write_key(provider, url, fields)

    def write_settings(self, settings, project=None):
        folder = (project or self.project) / ".buddy-council"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "sources.json").write_text(json.dumps(settings))

    def read_settings(self, project=None):
        return json.loads(((project or self.project) / ".buddy-council" / "sources.json").read_text())

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bc_setup.main(list(argv))
        text = out.getvalue()
        for secret in SECRETS:
            self.assertNotIn(secret, text, f"{argv[0]} printed a key")
        return code, json.loads(text)


class Links(Home):
    def one(self, link):
        return bc_setup.recognize(link)

    def test_a_jama_link_names_the_site_and_the_project(self):
        cases = [
            ("https://acme-prd.jamacloud.com/perspective.req#/projects/42", "https://acme-prd.jamacloud.com", 42),
            ("https://acme-prd.jamacloud.com/perspective.req?docId=7#/items/123?projectId=42",
             "https://acme-prd.jamacloud.com", 42),
            ("https://jama.acme.com/contour/perspective.req#/containers/1234?projectId=12", "https://jama.acme.com/contour", 12),
            ("https://acme-prd.jamacloud.com", "https://acme-prd.jamacloud.com", None),
        ]
        for link, base, project in cases:
            found = self.one(link)
            self.assertEqual((found["provider"], found["base_url"], found.get("project_id")), ("jama", base, project), link)

    def test_a_testrail_link_names_the_suite_or_the_project(self):
        cases = [
            ("https://acme.testrail.io/index.php?/suites/view/9277&group_by=cases:section_id&group_order=asc",
             {"suite_id": 9277}),
            ("https://acme.testrail.io/index.php?/projects/overview/45", {"project_id": 45}),
            ("https://acme.testrail.io/index.php?/cases/view/1234", {"case_id": 1234}),
            ("https://qa.acme.com/testrail/index.php?/suites/view/12", {"suite_id": 12}),
            ("acme.testrail.io", {}),
        ]
        for link, ids in cases:
            found = self.one(link)
            self.assertEqual(found["provider"], "testrail", link)
            self.assertEqual({k: found[k] for k in ("suite_id", "project_id", "case_id") if k in found}, ids, link)
        self.assertEqual(self.one(cases[3][0])["base_url"], "https://qa.acme.com/testrail")

    def test_every_jira_board_layout_gives_the_board_and_project(self):
        cases = [
            ("https://acme.atlassian.net/jira/software/projects/PROJ/boards/42", 42, "PROJ", "cloud"),
            ("https://acme.atlassian.net/jira/software/c/projects/PROJ/boards/42", 42, "PROJ", "cloud"),
            ("https://acme.atlassian.net/jira/software/c/projects/PROJ/boards/42/backlog?selectedIssue=PROJ-7", 42, "PROJ", "cloud"),
            ("https://acme.atlassian.net/jira/software/projects/PROJ/boards/42/timeline", 42, "PROJ", "cloud"),
            ("https://jira.acme.com/secure/RapidBoard.jspa?rapidView=1656&projectKey=CWA", 1656, "CWA", "server"),
            ("https://jira.acme.com/secure/RapidBoard.jspa?rapidView=1568", 1568, None, "server"),
            ("https://tools.acme.com/jira/secure/RapidBoard.jspa?rapidView=7&view=planning", 7, None, "server"),
        ]
        for link, board, key, deployment in cases:
            found = self.one(link)
            self.assertEqual((found["provider"], found.get("board_id"), found.get("project_key"), found["deployment"]),
                             ("jira", board, key, deployment), link)
        self.assertEqual(self.one(cases[6][0])["base_url"], "https://tools.acme.com/jira")

    def test_a_jira_issue_link_names_the_project_but_no_board(self):
        found = self.one("https://jira.acme.com/browse/CWA-1234")
        self.assertEqual((found["provider"], found["project_key"], found["issue_key"]), ("jira", "CWA", "CWA-1234"))
        self.assertNotIn("board_id", found)

    def test_github_and_github_enterprise_links_name_their_host(self):
        self.assertEqual(self.one("https://github.com/acme/app/blob/main/docs/sds.md")["site"], "github.com")
        found = self.one("https://github.acme.com/team/app/tree/main/docs")
        self.assertEqual((found["provider"], found["site"], found["owner"]), ("github", "github.acme.com", "team"))

    def test_an_enterprise_host_with_any_name_is_github_once_the_user_names_it(self):
        link = "https://code.acme.com/team/app/blob/main/docs/sds.md"
        self.assertIsNone(self.one(link)["provider"])
        with mock.patch.dict(os.environ, {"GH_HOST": "code.acme.com"}):
            self.assertEqual(self.one(link)["provider"], "github")
        self.key("github", "https://code.acme.com", token="ghe-key")
        self.assertEqual(self.one(link)["provider"], "github", "a saved key names the host too")

    def test_an_unknown_link_says_so(self):
        for link in ("https://gitlab.com/acme/app/blob/main/x.md", "https://example.com/board", "", "not a link at all"):
            found = self.one(link)
            self.assertIsNone(found["provider"], link)
            self.assertTrue(found["problem"], link)

    def test_a_site_with_no_key_says_what_to_ask_for_worded_for_that_site(self):
        cloud = self.one("https://acme.atlassian.net/jira/software/projects/PROJ/boards/42")["key"]
        server = self.one("https://jira.acme.com/secure/RapidBoard.jspa?rapidView=42")["key"]
        jama = self.one("https://acme.jamacloud.com/perspective.req#/projects/1")["key"]
        testrail = self.one("https://acme.testrail.io/index.php?/suites/view/12")["key"]
        self.assertEqual((cloud["saved"], cloud["fields"]), (False, ["username", "api_token"]))
        self.assertIn("API token", cloud["ask"])
        self.assertEqual(server["fields"], ["personal_token"])
        self.assertIn("personal access token", server["ask"])
        self.assertEqual(jama["fields"], ["client_id", "client_secret"])
        self.assertEqual(testrail["fields"], ["username", "api_key"])

    def test_a_site_with_a_key_needs_no_question(self):
        self.key("testrail", "https://acme.testrail.io", username="qa@acme.com", api_key="new-key")
        code, result = self.cli("links", "--link", "https://acme.testrail.io/index.php?/suites/view/12",
                                "--link", "https://acme.jamacloud.com/perspective.req#/projects/1")
        self.assertEqual(code, 0)
        self.assertEqual([link["key"]["saved"] for link in result["links"]], [True, False])
        self.assertEqual(result["links"][0]["key"]["auth"], "username + API key")
        self.assertIn("API key", result["links"][0]["key"]["ask"], "the words for a new key, should the site refuse it")


class ProjectFolder(Home):
    def test_the_nearest_settings_folder_at_or_above_wins(self):
        (self.project / ".buddy-council").mkdir()
        deep = self.project / "src" / "feature"
        deep.mkdir(parents=True)
        self.assertEqual(bc_setup.find_project(deep), (self.project, "above"))
        self.assertEqual(bc_setup.find_project(self.project), (self.project, "here"))

    def test_the_key_store_in_the_home_folder_never_counts(self):
        bc_keys.write_key("jira", "https://jira.acme.com", {"personal_token": "new-key"})
        fresh = self.home / "elsewhere"
        fresh.mkdir()
        self.assertEqual(bc_setup.find_project(fresh), (fresh, "new"))
        result = bc_setup.status(self.home)
        self.assertIn("home folder", result["stop"])
        with self.assertRaises(bc_setup.SetupError):
            bc_setup.save(self.home, {"requirements": {"provider": "jama", "base_url": "https://a.jamacloud.com"}})

    def test_a_folder_with_no_settings_anywhere_above_is_new(self):
        result = bc_setup.status(self.project)
        self.assertEqual((result["found"], result["exists"], result["code"]), ("new", False, []))
        self.assertIn("Run /bc:setup", result["stop"])

    def test_a_run_inside_the_plugin_folder_says_so_and_never_offers_to_set_it_up(self):
        plugin = self.home / ".copilot" / "installed-plugins" / "bc"
        (plugin / "scripts").mkdir(parents=True)
        with mock.patch.object(bc_setup, "PLUGIN", plugin):
            result = bc_setup.status(plugin / "scripts")
            self.assertEqual(result["found"], "plugin", "a shell left in the plugin folder by an earlier cd")
            self.assertIn("--from", result["stop"])
            self.assertNotIn("Run /bc:setup here", result["stop"])
            self.assertEqual(bc_setup.status(self.project)["found"], "new")
            (plugin / ".buddy-council").mkdir()
            self.assertEqual(bc_setup.status(plugin)["found"], "here", "a plugin checkout set up on purpose still works")

    def test_code_markers_say_whether_code_mapping_applies(self):
        (self.project / "package.json").write_text("{}")
        (self.project / ".git").mkdir()
        (self.project / "App.csproj").write_text("<Project/>")
        self.assertEqual(bc_setup.status(self.project)["code"], ["package.json", ".git", "App.csproj"])


class Saving(Home):
    def base(self):
        return {"requirements": {"provider": "jama", "base_url": "https://acme.jamacloud.com", "project_id": 1}}

    def test_a_save_merges_and_is_private_and_stamped(self):
        bc_setup.save(self.project, self.base())
        result = bc_setup.save(self.project, {"test_cases": {"provider": "testrail", "base_url": "https://acme.testrail.io",
                                                             "project_id": 4, "suite_id": None}})
        saved = self.read_settings()
        self.assertEqual(saved["requirements"]["project_id"], 1)
        self.assertEqual(saved["test_cases"]["project_id"], 4)
        self.assertEqual(saved["version"], bc_setup.FORMAT)
        self.assertIn("test_cases.project_id", result["changed"])
        mode = stat.S_IMODE((self.project / ".buddy-council" / "sources.json").stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_setting_the_example_lacks_is_refused_and_nothing_is_written(self):
        bc_setup.save(self.project, self.base())
        before = (self.project / ".buddy-council" / "sources.json").read_text()
        bad = [
            {"jira": {"provider": "jira", "base_url": "https://jira.acme.com", "board_url": "x"}},
            {"plugin_path": "/x"},
            {"requirements": {"project_id": "49"}},
            {"test_cases": {"provider": "qase"}},
            {"jira": {"provider": "jira", "base_url": "https://jira.acme.com", "deployment": "datacenter"}},
            {"requirements": {"roots": [{"id": 1, "path": "A", "depth": 2}]}},
        ]
        for patch in bad:
            with self.assertRaises(bc_setup.SetupError, msg=patch):
                bc_setup.save(self.project, patch)
        self.assertEqual((self.project / ".buddy-council" / "sources.json").read_text(), before)

    def test_none_is_saved_on_purpose_and_drops_the_old_tool(self):
        bc_setup.save(self.project, settings_027())
        bc_setup.save(self.project, {"jira": {"provider": "none"}})
        self.assertEqual(self.read_settings()["jira"], {"provider": "none"})
        bc_setup.save(self.project, {"jira": {"provider": "jira", "base_url": "https://jira.acme.com",
                                              "board": {"url": "https://jira.acme.com/secure/RapidBoard.jspa?rapidView=7",
                                                        "id": 7}}})
        jira = self.read_settings()["jira"]
        self.assertEqual((jira["deployment"], jira["board"]["id"]), ("server", 7))

    def test_unset_replaces_a_whole_block(self):
        bc_setup.save(self.project, settings_027())
        bc_setup.save(self.project, {"test_cases": {"provider": "testrail", "base_url": "https://other.testrail.io",
                                                    "project_id": 1}}, unset=["test_cases"])
        self.assertEqual(self.read_settings()["test_cases"],
                         {"provider": "testrail", "base_url": "https://other.testrail.io", "project_id": 1})

    def test_the_v_and_v_board_can_never_be_the_dev_board(self):
        bc_setup.save(self.project, settings_027())
        with self.assertRaises(bc_setup.SetupError) as raised:
            bc_setup.save(self.project, {"jira": {"vnv_board": {"id": 42}}})
        self.assertIn("dev board", str(raised.exception))

    def test_excel_requirements_must_become_jama(self):
        old = settings_027()
        old["requirements"] = {"provider": "excel", "excel_path": "/x.xls", "column_mapping": {"id": "ID"}}
        self.write_settings(old)
        with self.assertRaises(bc_setup.SetupError) as raised:
            bc_setup.save(self.project, {})
        self.assertIn("Jama", str(raised.exception))
        bc_setup.save(self.project, self.base(), unset=["requirements"])
        self.assertEqual(self.read_settings()["requirements"]["provider"], "jama")

    def test_an_update_from_0_27_0_needs_nothing_but_a_save(self):
        old = settings_027()
        old["jira"]["cloud_id"] = "abc"
        del old["jira"]["deployment"]
        self.write_settings(old)
        result = bc_setup.save(self.project, {})
        saved = self.read_settings()
        self.assertNotIn("plugin_root", saved)
        self.assertNotIn("cloud_id", saved["jira"])
        self.assertEqual((saved["jira"]["deployment"], saved["version"]), ("cloud", bc_setup.FORMAT))
        self.assertEqual(len(result["updated"]), 4)
        self.assertEqual(saved["jira"]["vnv_workflow"], old["jira"]["vnv_workflow"], "everything else is kept")

    def test_a_scripts_own_block_is_checked_and_nothing_else(self):
        self.write_settings(settings_027())
        bc_setup.save(self.project, {"automation": {"enabled": False}}, stamp=False)
        saved = self.read_settings()
        self.assertEqual(saved["automation"], {"enabled": False})
        self.assertIn("plugin_root", saved, "only setup brings the rest up to date")
        with self.assertRaises(bc_setup.SetupError):
            bc_setup.save(self.project, {"automation": {"surprise": 1}}, stamp=False)

    def test_a_git_project_keeps_its_settings_out_of_git(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            self.skipTest("git isn't installed")
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        bc_setup.save(self.project, self.base())
        bc_setup.save(self.project, self.base())
        lines = (self.project / ".git" / "info" / "exclude").read_text().splitlines()
        self.assertEqual(lines.count(".buddy-council/"), 1)
        status = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")

    def test_the_command_line_takes_json_with_apostrophes_written_as_u0027(self):
        code, result = self.cli("save", "--project", str(self.project), "--patch",
                                '{"requirements": {"provider": "jama", "base_url": "https://acme.jamacloud.com", '
                                '"project_name": "Bob\\u0027s project"}}')
        self.assertEqual(code, 0, result)
        self.assertEqual(self.read_settings()["requirements"]["project_name"], "Bob's project")
        code, result = self.cli("save", "--project", str(self.project), "--patch", "{'single': 'quotes'}")
        self.assertEqual(code, 1)
        self.assertIn("u0027", result["error"])


class Example(Home):
    """config/sources.example.json lists every setting the plugin reads, so setup can't save one it lacks."""

    def test_the_example_itself_checks_out(self):
        self.assertEqual(bc_setup.validate(copy.deepcopy(EXAMPLE)), [])
        self.assertEqual(EXAMPLE["version"], bc_setup.FORMAT)

    def test_every_settings_block_the_guides_show_checks_out(self):
        shown = 0
        files = [PLUGIN / "skills" / "setup" / "SKILL.md", *sorted(PLUGIN.glob("providers/*/setup.md")),
                 PLUGIN / "howtos" / "automation-tools.md", PLUGIN / "howtos" / "check-platform-parity.md",
                 PLUGIN / "com.github.copilot" / "agents" / "vnv-sprint-prep-agent.agent.md"]
        for path in files:
            text = path.read_text(encoding="utf-8")
            for block in re.findall(r"```json settings\n(.*?)\n```", text, re.S):
                patch = json.loads(block)
                settings = bc_run.merge(copy.deepcopy(settings_027()), patch)
                bc_setup.normalize(settings)
                self.assertEqual(bc_setup.validate(settings), [], f"{path.relative_to(PLUGIN)} shows {block[:80]}")
                shown += 1
        self.assertGreaterEqual(shown, 5, "the guides show the settings they save as ```json settings blocks")

    def test_the_automation_block_bc_tools_writes_checks_out(self):
        import bc_tools

        found = {"stack": {"framework": "Appium", "language": "Java", "build": "gradle", "runner": "TestNG",
                           "evidence": "build.gradle"},
                 "platforms": ["ios", "android"],
                 "driver": {"mode": "project", "appium": "/opt/homebrew/bin/appium", "version": "2.19.0",
                            "drivers": {"xcuitest": "9.10.5", "uiautomator2": "4.2.9"},
                            "expected": {"appium": "2.19.0", "xcuitest": "9.10.5"}},
                 "phone": {"platform": "ios", "udid": "0000-1", "name": "QA iPhone", "source": "MYAPP_UDID"},
                 "apps": {"ios": {"path": "/x/myapp.ipa", "exists": True, "id": "com.acme.myapp", "from": "c.json"}},
                 "language": {"value": None, "from": None},
                 "setup_script": {"path": "setup", "check": "--check", "udid_setting": "MYAPP_UDID"}}
        pins = {"node": {"version": "26.13.0"}, "appium_mcp": {"version": "1.99.2"}}
        proof = {"phone": "QA iPhone", "udid": "0000-1", "platform": "ios", "mode": "project", "page_source": "/x.xml",
                 "bytes": 10, "checked_at": "2026-11-05T10:00:00Z"}
        ready = bc_tools.automation_block(found, pins, "ready", [], {"answered_in_s": 3.5, "tools": 48, "version": "1.99.2"},
                                          proof, {"jdk": "21.0.5"}, [])
        self.assertEqual(bc_setup.validate({"automation": ready}), [])
        found["phone"] = {"why": "no phone is connected over USB", "udid_setting": None}
        pending = bc_tools.automation_block(found, pins, "pending", ["no phone"], None, None, {}, ["plug it in"])
        self.assertEqual(bc_setup.validate({"automation": pending}), [])


class Status(Home):
    def ready(self):
        settings = settings_027()
        bc_setup.normalize(settings)
        return settings

    def keys(self):
        self.key("jama", "https://acme-prd.jamacloud.com", client_id="c", client_secret="new-key")
        self.key("testrail", "https://acme.testrail.io", username="qa", api_key="new-key")
        self.key("jira", "https://acme-rnd.atlassian.net", username="qa", api_token="new-key")

    def test_a_project_set_up_with_an_older_version_is_told_to_finish_updating(self):
        self.keys()
        self.write_settings(settings_027())
        result = bc_setup.status(self.project)
        self.assertTrue(result["update"])
        self.assertIn("Run /bc:setup once to finish updating", result["stop"])

    def test_a_ready_project_reads_everything_and_says_nothing(self):
        self.keys()
        self.write_settings(self.ready())
        result = bc_setup.status(self.project / "deeper" if False else self.project, ["test_cases"])
        self.assertEqual((result["stop"], result["ask"], result["lines"]), (None, [], []))
        self.assertEqual({tool: info["state"] for tool, info in result["tools"].items()},
                         {"requirements": "ready", "test_cases": "ready", "board": "ready", "vnv_board": "ready"})

    def test_the_account_each_key_signs_in_as_is_known_without_asking(self):
        self.keys()
        self.write_settings(self.ready())
        tools = bc_setup.status(self.project)["tools"]
        self.assertEqual(tools["board"]["account"], "qa", "Jira Cloud's user_identifier for re-checks and V&V")
        self.assertNotIn("account", tools["requirements"], "a Jama client ID is no account name")
        self.assertNotIn("new-key", json.dumps(tools))
        bc_keys.write_key("jira", "https://acme-rnd.atlassian.net", {"personal_token": "new-key"})
        self.assertNotIn("account", bc_setup.status(self.project)["tools"]["board"], "a token alone names nobody")

    def test_a_re_check_checks_every_tool_the_project_uses_and_its_github_link(self):
        self.keys()
        settings = self.ready()
        settings["requirements"]["enrichment"]["check_url"] = "https://github.com/acme/app/blob/main/docs/sds.md"
        settings["automation"] = {"enabled": False}
        self.write_settings(settings)
        found = bc_setup.status(self.project)["checks"]
        self.assertEqual([c["check"] for c in found], ["requirements", "test_cases", "board", "github"])
        self.assertEqual(found[3]["url"], "https://github.com/acme/app/blob/main/docs/sds.md")
        settings["jira"] = {"provider": "none"}
        settings["requirements"]["enrichment"] = {"enabled": False}
        settings["automation"] = {"enabled": True, "status": "ready"}
        self.write_settings(settings)
        self.assertEqual([c["check"] for c in bc_setup.status(self.project)["checks"]],
                         ["requirements", "test_cases", "automation"])

    def test_a_tool_not_used_is_named_in_every_report_that_reads_it(self):
        self.keys()
        settings = self.ready()
        settings["jira"] = {"provider": "none"}
        self.write_settings(settings)
        result = bc_setup.status(self.project, ["test_cases"], ["board"])
        self.assertEqual(result["lines"], ["Board: not used in this project. Add it with /bc:setup <board link>."])
        self.assertEqual(bc_setup.status(self.project, reads=["test_cases"])["lines"], [], "onboarding reads no board")

    def test_a_command_that_needs_a_missing_tool_asks_for_its_link(self):
        self.keys()
        settings = self.ready()
        settings["jira"] = {"provider": "none"}
        self.write_settings(settings)
        result = bc_setup.status(self.project, ["board"])
        self.assertIsNone(result["stop"])
        self.assertEqual([(a["tool"], a["need"], a["guide"]) for a in result["ask"]],
                         [("board", "link", "providers/jira/setup.md")])

    def test_unverified_requirements_or_test_cases_stop_analysis_but_an_unverified_board_is_skipped(self):
        self.keys()
        settings = self.ready()
        settings["jira"]["pending"] = True
        self.write_settings(settings)
        result = bc_setup.status(self.project, reads=["test_cases", "board"])
        self.assertIsNone(result["stop"])
        self.assertEqual(result["lines"], ["Board: not verified yet, so it is skipped. Run /bc:setup to check it again."])
        self.assertIn("isn't verified", bc_setup.status(self.project, ["board"])["stop"])
        settings["test_cases"]["pending"] = True
        self.write_settings(settings)
        self.assertIn("Test cases (TestRail) aren't verified", bc_setup.status(self.project, reads=["test_cases"])["stop"])
        self.assertIn("Test cases (TestRail) aren't verified", bc_setup.status(self.project, ["test_cases"])["stop"])

    def test_a_forgotten_key_is_asked_for_on_the_spot(self):
        self.keys()
        self.write_settings(self.ready())
        bc_keys.forget("acme.testrail.io")
        ask = bc_setup.status(self.project, reads=["test_cases"])["ask"]
        self.assertEqual([(a["tool"], a["need"], a["site"], a["fields"]) for a in ask],
                         [("test_cases", "key", "acme.testrail.io", ["username", "api_key"])])

    def test_only_a_command_that_reads_a_tool_asks_for_its_forgotten_key(self):
        self.keys()
        self.write_settings(self.ready())
        bc_keys.forget("acme-rnd.atlassian.net")
        self.assertEqual(bc_setup.status(self.project, reads=["test_cases"])["ask"], [], "onboarding reads no board")
        coverage = bc_setup.status(self.project, ["test_cases"], ["board"])["ask"]
        self.assertEqual([(a["tool"], a["need"], a["site"]) for a in coverage], [("board", "key", "acme-rnd.atlassian.net")])
        both = bc_setup.status(self.project, ["board", "vnv_board"], ["test_cases"])["ask"]
        self.assertEqual([a["tool"] for a in both], ["board"], "one Jira site, one question")

    def test_excel_requirements_send_the_user_to_setup_for_a_jama_link(self):
        settings = self.ready()
        settings["requirements"] = {"provider": "excel", "excel_path": "/x.xls"}
        self.write_settings(settings)
        self.assertIn("Jama only", bc_setup.status(self.project)["stop"])


class Migration(Home):
    """An update from 0.27.0, on a home folder shaped like a real one."""

    def setUp(self):
        super().setUp()
        bc = self.home / ".buddy-council"
        bc.mkdir()
        (bc / "secrets.json").write_text(json.dumps({
            "testrail": {"username": "qa@acme.com", "api_key": "tr-old-key"},
            "jama": {"client_id": "cid", "client_secret": "jama-old-secret"},
            "github": {"token": "gh-old-token"}}))
        (bc / "atlassian.env").write_text("JIRA_URL=https://acme-rnd.atlassian.net\nJIRA_USERNAME=qa@acme.com\n"
                                          "JIRA_API_TOKEN=jira-old-token\n")
        (bc / "atlassian.env.server-dc.bak").write_text("JIRA_URL=https://jira.acme.com\nJIRA_PERSONAL_TOKEN=dc-old-pat\n")
        self.plugin = self.home / ".copilot" / "installed-plugins" / "buddy-council" / "bc"
        (self.plugin / ".claude-plugin").mkdir(parents=True)
        (self.plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "bc"}))
        self.copilot = self.home / ".copilot" / "mcp-config.json"
        self.servers = {
            "docker": {"type": "local", "command": "docker"},
            "testrail": self.entry("testrail", "https://acme.testrail.io"),
            "jama": self.entry("jama", "https://acme-prd.jamacloud.com"),
            "atlassian": {"type": "local", "command": "/opt/homebrew/bin/uvx",
                          "args": ["mcp-atlassian@0.23.1", "--env-file", f"{bc}/atlassian.env", "--transport", "stdio"],
                          "tools": ["*"]},
            "appium": {"type": "local", "command": f"{bc}/tools/node/v26/bin/node",
                       "args": [f"{bc}/tools/appium-mcp/1.99.2/index.js"]},
            "github": {"type": "local", "command": "github-mcp-server", "env": {"GITHUB_TOKEN": "users-own-token"}},
            "playwright": {"type": "local", "command": "npx", "args": ["-y", "@playwright/mcp"]},
        }
        self.copilot.write_text(json.dumps({"mcpServers": self.servers}, indent=2))
        os.chmod(self.copilot, 0o644)
        (self.plugin / ".mcp.json").write_text(json.dumps({"mcpServers": {
            "testrail": self.entry("testrail", "https://acme.testrail.io"),
            "github": {"command": "github-mcp-server", "args": ["stdio"], "env": {"GITHUB_TOKEN": "gh-old-token"}}}}))

    def entry(self, provider, url):
        return {"type": "local", "command": "/opt/homebrew/bin/uv",
                "args": ["run", "--directory", f"{self.plugin}/mcp-servers/{provider}-server", "mcp", "run", "server.py"],
                "env": {f"{provider.upper()}_BASE_URL": url, "BC_SECRETS_FILE": str(self.home / ".buddy-council" / "secrets.json")}}

    def test_migrate_moves_every_key_and_names_the_sites_to_check(self):
        self.assertEqual(bc_setup.status(self.project)["keys"], 0, "counted before the move")
        code, result = self.cli("migrate")
        self.assertEqual(code, 0)
        self.assertEqual(sorted((m["provider"], m["site"]) for m in result["moved"]),
                         [("jama", "acme-prd.jamacloud.com"), ("jira", "acme-rnd.atlassian.net"), ("testrail", "acme.testrail.io")])
        self.assertEqual(result["keys"], 3, "setup asks for keys by this count, so the moved keys aren't asked for again")
        self.assertEqual(result["verify"], ["acme-prd.jamacloud.com", "acme-rnd.atlassian.net", "acme.testrail.io"])
        self.assertTrue(bc_setup.status(self.project)["legacy"])

    def test_cleanup_removes_only_what_answered_and_leaves_everything_else(self):
        bc_setup.migrate()
        result = bc_setup.cleanup(["acme.testrail.io", "acme-prd.jamacloud.com"])
        servers = json.loads(self.copilot.read_text())["mcpServers"]
        self.assertEqual(set(servers), {"docker", "atlassian", "appium", "github", "playwright"})
        self.assertEqual(stat.S_IMODE(self.copilot.stat().st_mode), 0o644, "its permissions are kept")
        self.assertEqual(set(json.loads((self.home / ".buddy-council" / "secrets.json").read_text())), {"github"},
                         "the old GitHub token stays until nothing else can read the documents")
        self.assertTrue((self.home / ".buddy-council" / "atlassian.env").exists(), "Jira wasn't checked")
        self.assertIn("acme-rnd.atlassian.net wasn't checked this run", " ".join(result["lines"]))
        self.assertIn("becomes the github.com key only if nothing else can read your design documents",
                      " ".join(result["lines"]))
        self.assertFalse((self.plugin / ".mcp.json").exists(),
                         "bc's old entries went, GitHub's included: no token stays in connector settings")

    def test_bcs_old_github_entry_goes_at_once_and_its_token_stays_in_the_old_key_file(self):
        servers = json.loads(self.copilot.read_text())
        servers["mcpServers"]["github"] = {"type": "local", "command": "github-mcp-server", "args": ["stdio"],
                                           "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "gh-old-token"}}
        self.copilot.write_text(json.dumps(servers))
        bc_setup.migrate()
        result = bc_setup.cleanup([])
        self.assertNotIn("github", json.loads(self.copilot.read_text())["mcpServers"],
                         "gone though no site was checked: no token stays in connector settings")
        self.assertEqual(set(json.loads((self.plugin / ".mcp.json").read_text())["mcpServers"]), {"testrail"})
        secrets = json.loads((self.home / ".buddy-council" / "secrets.json").read_text())
        self.assertEqual(secrets["github"], {"token": "gh-old-token"}, "the token stays until it's moved or deleted")
        self.assertEqual(bc_keys.sites("github"), [], "removing the entry doesn't make the token a key")
        told = [line for line in result["lines"] if "GitHub token" in line]
        self.assertEqual(len(told), 1, result["lines"])
        self.assertIn("~/.buddy-council/secrets.json", told[0])
        self.assertIn("delete it there", told[0])
        self.assertTrue(any("github" in line and line.startswith("Removed") for line in result["lines"]))
        again = bc_setup.cleanup([])
        self.assertEqual([line for line in again["lines"] if "GitHub" in line or "github" in line], [],
                         "said once, not on every run")

    def test_a_full_update_removes_every_leftover_and_names_the_backup_once(self):
        bc_setup.migrate()
        verify = bc_setup.migrate()["verify"]
        (self.home / ".buddy-council" / "tools").mkdir()
        (self.home / ".buddy-council" / "tools" / "appium-mcp.json").write_text("{}")
        code, result = self.cli("cleanup", "--verified", ",".join(verify))
        self.assertEqual(code, 0)
        self.assertEqual(set(json.loads(self.copilot.read_text())["mcpServers"]), {"docker", "github", "playwright"},
                         "the user's own GitHub server stays")
        self.assertFalse((self.home / ".buddy-council" / "atlassian.env").exists())
        self.assertTrue((self.home / ".buddy-council" / "atlassian.env.server-dc.bak").exists())
        self.assertEqual(result["left_alone"], ["~/.buddy-council/atlassian.env.server-dc.bak"])
        again = bc_setup.cleanup(verify)
        self.assertEqual((again["removed"], again["left_alone"], again["lines"]), ([], [], []), "named once, not every run")
        for site, provider in (("acme.testrail.io", "testrail"), ("acme-prd.jamacloud.com", "jama"),
                               ("acme-rnd.atlassian.net", "jira")):
            self.assertTrue(bc_keys.check(f"https://{site}", provider)["ok"], site)
        doc = "https://github.com/acme/app/blob/main/docs/sds.md"
        read = ({"content": "# SDS"}, "saved key", [{"reader": "saved key", "ok": True}])
        with mock.patch.object(bc_run, "read_doc", return_value=read):
            moved = bc_setup.github(doc, move_old_token=True)
        self.assertTrue(moved["ok"])
        self.assertIn("now the github.com key", " ".join(moved["lines"]))
        bc_setup.cleanup(verify)
        self.assertFalse((self.home / ".buddy-council" / "secrets.json").exists(), "the token is a key now, so it went")
        self.assertFalse((self.plugin / ".mcp.json").exists(), "bc's plugin folder held only bc's entries")
        self.assertFalse(bc_setup.status(self.project)["legacy"])

    def test_a_site_that_never_answered_keeps_its_old_key(self):
        bc_setup.migrate()
        bc_setup.cleanup([])
        secrets = json.loads((self.home / ".buddy-council" / "secrets.json").read_text())
        self.assertEqual(set(secrets), {"testrail", "jama", "github"})
        self.assertIn("testrail", json.loads(self.copilot.read_text())["mcpServers"])

    def test_files_outside_this_home_folder_are_never_touched(self):
        other = Path(self.tmp.name, "real-plugin")
        (other / ".claude-plugin").mkdir(parents=True)
        (other / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "bc"}))
        outside = {"mcpServers": {"testrail": self.entry("testrail", "https://acme.testrail.io")}}
        (other / ".mcp.json").write_text(json.dumps(outside))
        servers = json.loads(self.copilot.read_text())
        servers["mcpServers"]["testrail"]["args"][2] = f"{other}/mcp-servers/testrail-server"
        self.copilot.write_text(json.dumps(servers))
        bc_setup.migrate()
        bc_setup.cleanup(["acme.testrail.io"])
        self.assertEqual(json.loads((other / ".mcp.json").read_text()), outside)

    def test_the_old_github_token_moves_only_when_nothing_else_reads_the_documents(self):
        bc_setup.migrate()
        doc = "https://github.com/acme/app/blob/main/docs/sds.md"
        refused = bc_run.RunError("repo_inaccessible: acme/app is private to you or does not exist")
        with mock.patch.object(bc_run, "read_doc", return_value=({"content": "# SDS"}, "gh", [])):
            read = bc_setup.github(doc)
        self.assertTrue(read["ok"])
        self.assertNotIn("old_token", read)
        self.assertEqual(bc_keys.sites("github"), [], "gh reads them, so the old token stays where it is")
        with mock.patch.object(bc_run, "read_doc", side_effect=refused):
            failed = bc_setup.github(doc)
        self.assertEqual((failed["ok"], failed["old_token"]), (False, True))
        self.assertEqual(bc_keys.sites("github"), [], "asking to move it is the guide's last step")
        with mock.patch.object(bc_run, "read_doc", side_effect=refused):
            enterprise = bc_setup.github("https://github.acme.com/team/app/blob/main/sds.md")
        self.assertFalse(enterprise["old_token"], "the old token was github.com's")

    def test_an_entry_from_a_plugin_that_isnt_bc_is_never_bcs(self):
        other = self.home / ".copilot" / "installed-plugins" / "tools" / "other"
        (other / ".claude-plugin").mkdir(parents=True)
        (other / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "other"}))
        servers = json.loads(self.copilot.read_text())
        servers["mcpServers"]["testrail"]["args"][2] = f"{other}/mcp-servers/testrail-server"
        self.copilot.write_text(json.dumps(servers))
        bc_setup.migrate()
        bc_setup.cleanup(["acme.testrail.io", "acme-prd.jamacloud.com"])
        self.assertIn("testrail", json.loads(self.copilot.read_text())["mcpServers"], "a folder's name alone isn't bc's")

    def test_the_key_file_from_before_secrets_json_goes_too(self):
        flat = self.home / ".buddy-council-secrets.json"
        flat.write_text(json.dumps({"testrail": {"username": "qa@acme.com", "api_key": "tr-older-key"}}))
        bc_setup.migrate()
        self.assertIn(str(flat), {item["path"] for item in bc_setup.legacy_items()})
        bc_setup.cleanup(["acme.testrail.io"])
        self.assertFalse(flat.exists(), "removed once its site answered with the per-site key")

    def test_setup_never_reads_a_key_file_itself(self):
        source = Path(bc_setup.__file__).read_text(encoding="utf-8")
        for reader in (r"\b_read_env\(", r'\.get\("token"\)', r"\b_legacy_section\(", r"\b_legacy_github_token\("):
            self.assertIsNone(re.search(reader, source), "bc_keys.py is the only code that reads or writes keys")

    def test_nothing_older_means_nothing_to_do(self):
        for path in (self.home / ".buddy-council").iterdir():
            path.unlink()
        self.copilot.write_text(json.dumps({"mcpServers": {"docker": {"command": "docker"}}}))
        (self.plugin / ".mcp.json").unlink()
        result = bc_setup.migrate()
        self.assertEqual((result["moved"], result["verify"], result["leftovers"]), ([], [], 0))
        self.assertFalse(bc_setup.status(self.project)["legacy"])


class Forget(Home):
    def test_forget_deletes_the_key_and_says_this_project_will_ask_again(self):
        self.key("testrail", "https://acme.testrail.io", username="qa", api_key="new-key")
        settings = settings_027()
        bc_setup.normalize(settings)
        self.write_settings(settings)
        code, result = self.cli("forget", "--url", "acme.testrail.io", "--from", str(self.project))
        self.assertEqual((code, result["used_here"]), (0, ["test_cases"]))
        self.assertFalse(bc_keys.check("https://acme.testrail.io", "testrail")["ok"])
        self.assertIn("asks for a new key", result["lines"][-1])
        code, result = self.cli("forget", "--url", "acme.testrail.io")
        self.assertEqual(result["forgot"], [])


class GitHub(Home):
    def test_the_check_names_the_sign_in_that_read_the_doc(self):
        import bc_run

        def readers(host, runner=None):
            def unavailable(endpoint):
                raise bc_run.RunError("gh_missing: the gh CLI is not installed")

            def public(endpoint):
                import base64
                return {"content": base64.b64encode(b"# SDS\nThe pump shall stop.").decode(), "sha": "1", "size": 25}

            return [("gh", unavailable), ("no sign-in", public)]

        with mock.patch.object(bc_run, "readers", readers):
            code, result = self.cli("github", "--url", "https://github.com/acme/app/blob/main/docs/sds.md")
        self.assertEqual((code, result["read_with"], result["chars"]), (0, "no sign-in", 26))
        self.assertTrue(result["preview"].startswith("# SDS The pump"))

    def test_a_doc_nothing_can_read_says_what_was_tried(self):
        import bc_run

        def readers(host, runner=None):
            def denied(endpoint):
                raise bc_run.RunError("not_found: HTTP 404")

            return [("gh", denied), ("no sign-in", denied)]

        with mock.patch.object(bc_run, "readers", readers):
            code, result = self.cli("github", "--url", "https://github.com/acme/private/blob/main/a.md")
        self.assertEqual((code, result["error"]), (1, "repo_inaccessible"))
        self.assertEqual([t["reader"] for t in result["tried"]], ["gh", "no sign-in"])


if __name__ == "__main__":
    unittest.main()
