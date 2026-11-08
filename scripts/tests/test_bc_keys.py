"""The key store: one file per site, looked up by the site a call names, migrated once from the old files.

    python3 -m unittest discover -s scripts/tests -v

Standard library only; must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions. Every test
runs in a throwaway home folder, so a developer's real ~/.buddy-council is never read or written.
"""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bc_keys  # noqa: E402

SECRETS = ("tr-key-A", "tr-key-B", "jama-secret", "jira-token", "dc-pat", "old-tr-key")


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        env = {key: value for key, value in os.environ.items() if key != "BC_HOME"}
        env["HOME"] = str(self.home)
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def save(self, provider, url, **fields):
        return bc_keys.write_key(provider, url, fields)

    def cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bc_keys.main(list(argv))
        text = out.getvalue()
        for secret in SECRETS:
            self.assertNotIn(secret, text, f"{argv[0]} printed a key")
        return code, json.loads(text)


class Sites(Home):
    def test_a_site_is_host_port_and_path_prefix(self):
        cases = {
            "https://Acme.TestRail.io/": "acme.testrail.io",
            "acme.testrail.io": "acme.testrail.io",
            "https://acme.testrail.io:443": "acme.testrail.io",
            "http://jira.acme.com:80/jira/": "jira.acme.com/jira",
            "https://jira.acme.com:8443/jira": "jira.acme.com:8443/jira",
            "https://acme.testrail.io/index.php?/suites/view/9": "acme.testrail.io/index.php",
            "https://[::1]:8080/x": "[::1]:8080/x",
        }
        for url, site in cases.items():
            self.assertEqual(bc_keys.site_id(url), site, url)

    def test_a_pasted_link_is_cut_back_to_the_sites_address(self):
        cases = [
            ("testrail", "https://acme.testrail.io/index.php?/suites/view/9", "https://acme.testrail.io"),
            ("jama", "https://jama.acme.com/contour/perspective.req#/items/12?projectId=3", "https://jama.acme.com/contour"),
            ("jama", "https://acme.jamacloud.com/rest/v1/", "https://acme.jamacloud.com"),
            ("jira", "https://acme.atlassian.net/jira/software/projects/CWA/boards/42", "https://acme.atlassian.net"),
            ("jira", "https://tools.acme.com/jira/secure/RapidBoard.jspa?rapidView=42", "https://tools.acme.com/jira"),
            ("jira", "https://jira.acme.com/browse/CWA-1", "https://jira.acme.com"),
        ]
        for provider, link, base in cases:
            self.assertEqual(bc_keys.base_url(provider, link), base, link)

    def test_unsafe_addresses_are_refused(self):
        for provider, url in (("testrail", "https://me:pw@acme.testrail.io"), ("jama", "http://jama.acme.com"),
                              ("jira", "ftp://jira.acme.com"), ("jira", ""), ("jira", "https://")):
            with self.assertRaises(bc_keys.KeyStoreError, msg=url):
                bc_keys.base_url(provider, url)
        self.assertEqual(bc_keys.base_url("jama", "http://localhost:8080"), "http://localhost:8080")

    def test_a_broken_address_is_a_key_store_error_not_a_crash(self):
        for url in ("https://[jira.acme.com", "[::1", "https://acme.testrail.io:99999", "https://acme.testrail.io:x"):
            with self.assertRaises(bc_keys.KeyStoreError, msg=url):
                bc_keys.site_id(url)
            with self.assertRaises(bc_keys.KeyStoreError, msg=url):
                bc_keys.read_key(url, "testrail")
        code, result = self.cli("path", "--url", "https://[jira.acme.com", "--provider", "jira")
        self.assertEqual((code, result["ok"]), (1, False))

    def test_every_spelling_of_a_site_shares_one_file(self):
        path = bc_keys.key_path("https://Jira.Acme.com:8443/jira/")
        self.assertEqual(path, bc_keys.key_path("jira.acme.com:8443/jira"))
        self.assertEqual(path.parent, self.home / ".buddy-council" / "keys")
        self.assertEqual(path.name, "jira.acme.com%3A8443%2Fjira.json")


class Store(Home):
    def test_a_saved_key_is_private(self):
        path = self.save("testrail", "https://a.testrail.io/index.php?/dashboard", username="qa@a.com", api_key="tr-key-A")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(json.loads(path.read_text()), {"provider": "testrail", "url": "https://a.testrail.io",
                                                        "username": "qa@a.com", "api_key": "tr-key-A"})

    def test_a_key_holds_exactly_one_way_to_sign_in(self):
        with self.assertRaises(bc_keys.KeyStoreError):
            self.save("testrail", "a.testrail.io", username="qa@a.com")
        with self.assertRaises(bc_keys.KeyStoreError):
            self.save("jira", "acme.atlassian.net", username="qa@a.com", api_token="t", personal_token="p")
        with self.assertRaises(bc_keys.KeyStoreError):
            self.save("testrail", "a.testrail.io", username="qa@a.com", api_key="k", apikey="typo")
        path = self.save("jira", "jira.acme.com", username="ignored-without-a-token", personal_token="dc-pat")
        self.assertEqual(set(json.loads(path.read_text())), {"provider", "url", "personal_token"})
        with self.assertRaises(bc_keys.KeyStoreError) as ctx:
            self.save("jama", "acme.jamacloud.com", client_id="cid")
        self.assertNotIn("cid", str(ctx.exception))

    def test_a_site_keeps_one_key(self):
        self.save("testrail", "tools.acme.com", username="qa", api_key="tr-key-A")
        with self.assertRaises(bc_keys.KeyStoreError):
            self.save("jira", "tools.acme.com", personal_token="dc-pat")
        self.save("testrail", "https://tools.acme.com/", username="qa", api_key="tr-key-B")
        self.assertEqual(len(bc_keys.sites()), 1)
        self.assertEqual(bc_keys.read_key("tools.acme.com", "testrail")["api_key"], "tr-key-B")

    def test_a_call_finds_its_sites_key_by_address_link_or_host(self):
        self.save("testrail", "https://a.testrail.io", username="qa@a.com", api_key="tr-key-A")
        self.save("testrail", "https://b.testrail.io", username="qa@b.com", api_key="tr-key-B")
        self.assertEqual(bc_keys.read_key("https://a.testrail.io", "testrail")["api_key"], "tr-key-A")
        self.assertEqual(bc_keys.read_key("b.testrail.io/index.php?/suites/view/1", "testrail")["api_key"], "tr-key-B")
        key = bc_keys.read_key("A.testrail.io", "testrail")
        self.assertEqual((key["url"], key["site"], key["auth"]), ("https://a.testrail.io", "a.testrail.io", "username + API key"))

    def test_no_site_means_the_only_key_and_never_a_guess(self):
        self.save("jira", "https://acme.atlassian.net", username="qa@acme.com", api_token="jira-token")
        self.assertEqual(bc_keys.read_key(None, "jira")["site"], "acme.atlassian.net")
        self.save("jira", "https://jira.acme.com", personal_token="dc-pat")
        with self.assertRaises(bc_keys.KeyStoreError) as ctx:
            bc_keys.read_key("", "jira")
        message = str(ctx.exception)
        self.assertIn("acme.atlassian.net", message)
        self.assertIn("jira.acme.com", message)
        self.assertIn("`site`", message)
        for secret in SECRETS:
            self.assertNotIn(secret, message)

    def test_an_unknown_site_never_gets_another_sites_key(self):
        self.save("testrail", "https://a.testrail.io", username="qa@a.com", api_key="tr-key-A")
        for other in ("https://c.testrail.io", "a.testrail.io.evil.example", "https://evil.example/a.testrail.io"):
            with self.assertRaises(bc_keys.KeyStoreError, msg=other) as ctx:
                bc_keys.read_key(other, "testrail")
            self.assertIn("/bc:setup", str(ctx.exception))
            self.assertNotIn("tr-key-A", str(ctx.exception))
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key("https://a.testrail.io", "jira")

    def test_context_paths_on_one_host_stay_apart(self):
        self.save("jira", "https://tools.acme.com/jira-a", personal_token="dc-pat")
        self.save("jira", "https://tools.acme.com/jira-b", username="qa", api_token="jira-token")
        self.assertEqual(bc_keys.read_key("https://tools.acme.com/jira-b/browse/X-1", "jira")["site"], "tools.acme.com/jira-b")
        self.assertEqual(bc_keys.read_key("tools.acme.com/jira-a", "jira")["personal_token"], "dc-pat")
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key("tools.acme.com", "jira")
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key("tools.acme.com/jira", "jira")

    def test_a_file_claiming_another_site_is_ignored(self):
        path = self.save("testrail", "https://a.testrail.io", username="qa", api_key="tr-key-A")
        data = json.loads(path.read_text())
        data["url"] = "https://evil.example"
        path.write_text(json.dumps(data))
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key("a.testrail.io", "testrail")
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key("evil.example", "testrail")
        code, listing = self.cli("sites")
        self.assertEqual((code, listing["sites"]), (0, []))
        self.assertIn("evil.example", listing["problems"][0])

    def test_a_key_file_with_a_broken_address_is_ignored_and_the_others_still_work(self):
        self.save("testrail", "https://a.testrail.io", username="qa", api_key="tr-key-A")
        (bc_keys.keys_dir() / "broken.json").write_text(json.dumps(
            {"provider": "jira", "url": "https://[jira.acme.com", "personal_token": "dc-pat"}))
        self.assertEqual(bc_keys.read_key(None, "testrail")["api_key"], "tr-key-A")
        self.assertEqual([entry["site"] for entry in bc_keys.sites()], ["a.testrail.io"])
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key(None, "jira")
        code, listing = self.cli("sites")
        self.assertEqual((code, [s["site"] for s in listing["sites"]]), (0, ["a.testrail.io"]))
        self.assertIn("broken.json", listing["problems"][0])

    def test_loose_permissions_are_tightened_when_read(self):
        path = self.save("jira", "https://acme.atlassian.net", username="qa", api_token="jira-token")
        os.chmod(path, 0o644)
        bc_keys.read_key(None, "jira")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_ssl_verify_rides_along_with_a_jira_key(self):
        self.save("jira", "https://jira.acme.com", personal_token="dc-pat", ssl_verify=False)
        self.assertIs(bc_keys.read_key(None, "jira")["ssl_verify"], False)
        with self.assertRaises(bc_keys.KeyStoreError):
            self.save("jira", "https://jira.acme.com", personal_token="dc-pat", ssl_verify="no")

    def test_forget_deletes_one_sites_key(self):
        self.save("testrail", "https://a.testrail.io", username="qa", api_key="tr-key-A")
        self.save("testrail", "https://b.testrail.io", username="qa", api_key="tr-key-B")
        code, result = self.cli("forget", "--url", "https://a.testrail.io/index.php?/x")
        self.assertEqual((code, result["forgot"]), (0, [{"provider": "testrail", "site": "a.testrail.io"}]))
        self.assertEqual([entry["site"] for entry in bc_keys.sites()], ["b.testrail.io"])


class CommandLine(Home):
    def test_path_says_where_a_key_belongs_and_makes_the_folder_private(self):
        code, result = self.cli("path", "--url", "https://jama.acme.com/contour/perspective.req#/items/1", "--provider", "jama")
        self.assertEqual(code, 0)
        self.assertEqual((result["site"], result["url"], result["saved"]), ("jama.acme.com/contour", "https://jama.acme.com/contour", False))
        self.assertEqual(Path(result["path"]).parent, self.home / ".buddy-council" / "keys")
        self.assertEqual(stat.S_IMODE(Path(result["path"]).parent.stat().st_mode), 0o700)

    def test_check_reports_a_key_without_showing_it(self):
        code, result = self.cli("check", "--url", "https://acme.jamacloud.com", "--provider", "jama")
        self.assertEqual((code, result["ok"]), (1, False))
        target = bc_keys.key_path("acme.jamacloud.com")
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"provider": "jama", "url": "https://acme.jamacloud.com",
                                      "client_id": "cid", "client_secret": "jama-secret"}))
        os.chmod(target, 0o644)
        code, result = self.cli("check", "--url", "acme.jamacloud.com", "--provider", "jama")
        self.assertEqual((code, result["ok"], result["auth"]), (0, True, "OAuth API credentials"))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        code, result = self.cli("check", "--url", "acme.jamacloud.com", "--provider", "testrail")
        self.assertFalse(result["ok"])


class Migration(Home):
    def setUp(self):
        super().setUp()
        bc = self.home / ".buddy-council"
        bc.mkdir()
        self.secrets = bc / "secrets.json"
        self.secrets.write_text(json.dumps({
            "testrail": {"username": "qa@acme.com", "api_key": "old-tr-key"},
            "jama": {"client_id": "cid", "client_secret": "jama-secret"},
            "github": {"token": "not-a-connector-key"},
        }))
        self.env = bc / "atlassian.env"
        self.env.write_text("JIRA_URL=https://acme.atlassian.net\nJIRA_USERNAME=qa@acme.com\nJIRA_API_TOKEN=jira-token\n"
                            "TOOLSETS=default,jira_agile\n")
        (bc / "atlassian.env.server-dc.bak").write_text("JIRA_URL=https://jira.acme.com\nJIRA_PERSONAL_TOKEN=dc-pat\n")
        self.copilot = self.home / ".copilot" / "mcp-config.json"
        self.copilot.parent.mkdir()
        self.write_copilot("https://acme.testrail.io", "https://acme.jamacloud.com")
        self.before = {path: path.read_bytes() for path in (self.secrets, self.env, self.copilot)}

    def write_copilot(self, testrail, jama, root=None):
        root = root or self.home / "plugins" / "bc"
        servers = {"docker": {"type": "local", "command": "docker"},
                   "testrail": self.entry("testrail", testrail, root, BC_SECRETS_FILE="~/.buddy-council/secrets.json"),
                   "jama": self.entry("jama", jama, root, BC_SECRETS_FILE=str(self.secrets))}
        self.copilot.write_text(json.dumps({"mcpServers": servers}))

    @staticmethod
    def entry(provider, url, root, **env):
        """A connector entry the way older setups wrote it: uv running bc's server folder, the site in env."""
        if url is not None:
            env[provider.upper() + "_BASE_URL"] = url
        return {"type": "local", "command": "/opt/homebrew/bin/uv",
                "args": ["run", "--directory", f"{root}/mcp-servers/{provider}-server", "mcp", "run", "server.py"],
                "env": env}

    def plugin_folder(self, folder, name="bc", manifest=".claude-plugin/plugin.json", **servers):
        """A plugin folder with its manifest, and the .mcp.json an older setup wrote there."""
        (folder / manifest).parent.mkdir(parents=True, exist_ok=True)
        (folder / manifest).write_text(json.dumps({"name": name}))
        entries = {provider: self.entry(provider, url, folder) for provider, url in servers.items()}
        (folder / ".mcp.json").write_text(json.dumps({"mcpServers": entries}))
        return folder

    def migrated(self, provider):
        return [entry["site"] for entry in bc_keys.sites(provider)]

    def test_each_key_moves_to_the_site_it_was_set_up_for(self):
        report = bc_keys.migrate_legacy()
        self.assertEqual(sorted((m["provider"], m["site"]) for m in report["migrated"]),
                         [("jama", "acme.jamacloud.com"), ("jira", "acme.atlassian.net"), ("testrail", "acme.testrail.io")])
        self.assertEqual(bc_keys.read_key(None, "testrail")["api_key"], "old-tr-key")
        self.assertEqual(bc_keys.read_key(None, "jama")["client_secret"], "jama-secret")
        jira = bc_keys.read_key(None, "jira")
        self.assertEqual((jira["username"], jira["api_token"], jira["url"]), ("qa@acme.com", "jira-token", "https://acme.atlassian.net"))
        self.assertEqual([e["site"] for e in bc_keys.sites("jira")], ["acme.atlassian.net"], "a backup file is never a key")
        self.assertEqual({path: path.read_bytes() for path in self.before}, self.before, "the old files stay for setup")

    def test_it_runs_once_so_a_forgotten_key_stays_forgotten(self):
        bc_keys.migrate_legacy()
        self.assertTrue(bc_keys.migrate_legacy()["already"])
        bc_keys.forget("acme.testrail.io")
        with self.assertRaises(bc_keys.KeyStoreError):
            bc_keys.read_key(None, "testrail")
        self.assertEqual(bc_keys.migrate_legacy(force=True)["migrated"][0]["site"], "acme.testrail.io")

    def test_a_saved_key_is_never_replaced(self):
        path = self.save("testrail", "https://acme.testrail.io", username="new@acme.com", api_key="tr-key-A")
        report = bc_keys.migrate_legacy()
        self.assertIn({"provider": "testrail", "site": "acme.testrail.io"}, report["kept"])
        self.assertEqual(json.loads(path.read_text())["api_key"], "tr-key-A")

    def test_a_key_with_no_recorded_site_stays_where_it_is(self):
        self.write_copilot(None, "https://acme.jamacloud.com")
        report = bc_keys.migrate_legacy()
        self.assertEqual([s["provider"] for s in report["skipped"]], ["testrail"])
        self.assertEqual(bc_keys.sites("testrail"), [])

    def test_two_recorded_sites_are_never_guessed_between(self):
        data = json.loads(self.secrets.read_text())
        data["testrail"]["base_url"] = "https://other.testrail.io"
        self.secrets.write_text(json.dumps(data))
        report = bc_keys.migrate_legacy()
        self.assertIn("2 sites", report["skipped"][0]["reason"])
        self.assertEqual(bc_keys.sites("testrail"), [])

    def test_the_secrets_own_base_url_is_enough(self):
        self.copilot.unlink()
        data = json.loads(self.secrets.read_text())
        data["testrail"]["base_url"] = "https://acme.testrail.io/"
        self.secrets.write_text(json.dumps(data))
        report = bc_keys.migrate_legacy()
        self.assertIn(("testrail", "acme.testrail.io", "secrets.json"),
                      [(m["provider"], m["site"], m["from"]) for m in report["migrated"]])

    def test_a_bc_plugin_folder_counts_when_copilot_has_none(self):
        self.copilot.unlink()
        self.plugin_folder(self.home / ".claude/plugins/cache/buddy-council/bc/0.27.0", testrail="https://acme.testrail.io")
        report = bc_keys.migrate_legacy()
        self.assertIn(("testrail", "acme.testrail.io", "~/.claude/plugins/cache/buddy-council/bc/0.27.0/.mcp.json"),
                      [(m["provider"], m["site"], m["from"]) for m in report["migrated"]])

    def test_every_bc_plugin_folder_counts_so_folders_that_disagree_leave_the_site_unknown(self):
        self.copilot.unlink()
        for version, url, age in (("0.26.0", "https://old.testrail.io", 100), ("0.27.0", "https://acme.testrail.io", 0)):
            folder = self.plugin_folder(self.home / ".claude/plugins/cache/buddy-council/bc" / version, testrail=url)
            os.utime(folder / ".mcp.json", (time.time() - age, time.time() - age))
        report = bc_keys.migrate_legacy()
        self.assertIn("2 sites", [s for s in report["skipped"] if s["provider"] == "testrail"][0]["reason"])
        self.assertEqual(self.migrated("testrail"), [], "the newest folder is not a tie-breaker")

    def test_another_plugins_settings_never_decide_a_site(self):
        self.copilot.unlink()
        bc = self.plugin_folder(self.home / ".claude/plugins/cache/buddy-council/bc/0.26.0", testrail="https://acme.testrail.io")
        os.utime(bc / ".mcp.json", (time.time() - 100, time.time() - 100))
        # A newer plugin with its own `testrail` server — even one laid out like bc's — is not bc.
        self.plugin_folder(self.home / ".copilot/installed-plugins/some-marketplace/other-testrail-plugin",
                           name="other-testrail-plugin", testrail="https://vendor-demo.testrail.io")
        self.plugin_folder(self.home / ".claude/plugins/marketplaces/vendor", name="vendor", manifest="plugin.json",
                           testrail="https://vendor-demo.testrail.io")
        bc_keys.migrate_legacy()
        self.assertEqual(self.migrated("testrail"), ["acme.testrail.io"])
        self.assertEqual(bc_keys.read_key(None, "testrail")["url"], "https://acme.testrail.io")

    def test_a_server_that_isnt_bcs_never_decides_a_site(self):
        data = json.loads(self.copilot.read_text())
        data["mcpServers"]["testrail"] = {"type": "local", "command": "npx", "args": ["-y", "testrail-mcp"],
                                          "env": {"TESTRAIL_BASE_URL": "https://vendor-demo.testrail.io"}}
        self.copilot.write_text(json.dumps(data))
        report = bc_keys.migrate_legacy()
        self.assertEqual(self.migrated("testrail"), [])
        self.assertIn("no site was saved", [s for s in report["skipped"] if s["provider"] == "testrail"][0]["reason"])
        self.assertEqual(self.migrated("jama"), ["acme.jamacloud.com"])

    def test_every_kind_of_bc_folder_is_read(self):
        folders = {
            "claude version": (self.home / ".claude/plugins/cache/mkt/bc/0.25.0", ".claude-plugin/plugin.json"),
            "claude marketplace": (self.home / ".claude/plugins/marketplaces/buddy-council", ".claude-plugin/plugin.json"),
            "copilot install": (self.home / ".copilot/installed-plugins/buddy-council/bc", "plugin.json"),
            "copilot direct install": (self.home / ".copilot/installed-plugins/_direct/Omar--buddy-council-plugin",
                                       ".claude-plugin/plugin.json"),
            "the folder copilot ran bc from": (self.home / "plugins" / "bc", "plugin.json"),
        }
        for kind, (folder, manifest) in folders.items():
            with self.subTest(kind):
                self.plugin_folder(folder, manifest=manifest, testrail="https://other.testrail.io")
                bc_keys.migrate_legacy(force=True)
                self.assertEqual(self.migrated("testrail"), [], f"a {kind} that disagrees leaves the site unknown")
                (folder / ".mcp.json").unlink()
                bc_keys.migrate_legacy(force=True)
                self.assertEqual(self.migrated("testrail"), ["acme.testrail.io"])
                bc_keys.forget("acme.testrail.io")

    def test_old_keys_that_differ_between_secrets_files_stay_where_they_are(self):
        other = self.home / "other-secrets.json"
        other.write_text(json.dumps({"testrail": {"username": "qa@acme.com", "api_key": "tr-key-B"}}))
        folder = self.home / ".claude/plugins/cache/buddy-council/bc/0.27.0"
        self.plugin_folder(folder, testrail="https://acme.testrail.io")
        data = json.loads((folder / ".mcp.json").read_text())
        data["mcpServers"]["testrail"]["env"]["BC_SECRETS_FILE"] = "~/other-secrets.json"
        (folder / ".mcp.json").write_text(json.dumps(data))
        report = bc_keys.migrate_legacy()
        self.assertIn("differs between", [s for s in report["skipped"] if s["provider"] == "testrail"][0]["reason"])
        self.assertEqual(self.migrated("testrail"), [])
        self.assertEqual(self.migrated("jama"), ["acme.jamacloud.com"])

    def test_one_broken_address_never_holds_up_the_other_keys(self):
        self.write_copilot("https://[acme.testrail.io", "https://acme.jamacloud.com")
        report = bc_keys.migrate_legacy()
        self.assertIn("not a valid address", [s for s in report["skipped"] if s["provider"] == "testrail"][0]["reason"])
        self.assertEqual((self.migrated("jama"), self.migrated("jira")), (["acme.jamacloud.com"], ["acme.atlassian.net"]))
        self.assertTrue((bc_keys.keys_dir() / bc_keys.MARKER).exists())

    def test_a_server_jira_keeps_its_pat_and_certificate_choice(self):
        self.env.write_text('export JIRA_URL="https://jira.acme.com"\nJIRA_USERNAME=qa\nJIRA_PERSONAL_TOKEN=dc-pat\n'
                            "JIRA_SSL_VERIFY=false\n")
        bc_keys.migrate_legacy()
        key = bc_keys.read_key("jira.acme.com", "jira")
        self.assertEqual((key["personal_token"], key["ssl_verify"], key["auth"]), ("dc-pat", False, "personal access token"))
        self.assertNotIn("username", key)

    def test_the_first_lookup_migrates_by_itself(self):
        key = bc_keys.read_key("https://acme.testrail.io", "testrail")
        self.assertEqual(key["username"], "qa@acme.com")
        self.assertTrue((bc_keys.keys_dir() / bc_keys.MARKER).exists())

    def test_old_files_can_be_read_from_another_home(self):
        target = tempfile.TemporaryDirectory()
        self.addCleanup(target.cleanup)
        with mock.patch.dict(os.environ, {"HOME": target.name}):
            report = bc_keys.migrate_legacy(legacy_home=self.home)
            self.assertEqual(len(report["migrated"]), 3)
            self.assertTrue((Path(target.name) / ".buddy-council" / "keys" / "acme.testrail.io.json").exists())
        self.assertFalse((self.home / ".buddy-council" / "keys").exists())

    def test_the_command_line_reports_sites_and_never_keys(self):
        code, result = self.cli("migrate")
        self.assertEqual((code, len(result["migrated"])), (0, 3))
        code, listing = self.cli("sites", "--provider", "jira")
        self.assertEqual([s["site"] for s in listing["sites"]], ["acme.atlassian.net"])
        marker = (bc_keys.keys_dir() / bc_keys.MARKER).read_text()
        for secret in SECRETS:
            self.assertNotIn(secret, marker)


if __name__ == "__main__":
    unittest.main()
