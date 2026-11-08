"""scripts/bc-mcp, the launcher every assistant starts the connectors with, and the manifests that declare them.

    python3 -m unittest discover -s scripts/tests -v

Standard library only; must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions. A fake uv
stands in for the real one and prints how it was run, so nothing is installed or started.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
LAUNCHER = PLUGIN / "scripts" / "bc-mcp"
CONNECTORS = {"testrail": "testrail-server", "jama": "jama-server", "atlassian": "jira-router", "appium": None}
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"  # what an assistant opened from the Dock gets
FAKE_UV = '#!/bin/sh\nprintf "%s\\n" "uv=$0" "cwd=$PWD" "BC_UV=$BC_UV" "$@"\n'


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = self.tmp / "home"
        self.home.mkdir()

    def fake_uv(self, folder):
        path = self.tmp / folder / "uv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FAKE_UV)
        path.chmod(0o755)
        return path

    def launch(self, name, path=LAUNCHD_PATH, uv_dirs=None, cwd=None, script=None, **env):
        environment = {"PATH": path, "HOME": str(self.home), **env}
        if uv_dirs is not None:
            environment["BC_UV_DIRS"] = uv_dirs
        done = subprocess.run(["sh", str(script or LAUNCHER), name], capture_output=True, text=True, timeout=30,
                              env=environment, cwd=str(cwd or self.tmp), stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout.splitlines(), done.stderr

    def test_each_connector_runs_its_own_server_through_uv(self):
        uv = self.fake_uv("bin")
        for name, project in CONNECTORS.items():
            code, out, err = self.launch(name, uv_dirs=str(uv.parent))
            self.assertEqual((code, err), (0, ""), name)
            self.assertEqual(out[0], f"uv={uv}")
            self.assertEqual(out[2], f"BC_UV={uv}", "the Jira router starts mcp-atlassian with the same uv")
            if project:
                self.assertEqual(out[3:], ["run", "--quiet", "--frozen", "--directory",
                                           str(PLUGIN / "mcp-servers" / project), "python", "server.py"], name)
                self.assertTrue((PLUGIN / "mcp-servers" / project / "server.py").is_file())
                self.assertTrue((PLUGIN / "mcp-servers" / project / "uv.lock").is_file(), "--frozen needs the lock")
            else:
                self.assertEqual(out[3:], ["run", "--quiet", "--no-project", "python",
                                           str(PLUGIN / "scripts" / "bc_tools.py"), "serve"])

    def test_uv_on_path_comes_first(self):
        on_path = self.fake_uv("path-bin")
        fallback = self.fake_uv("fallback")
        code, out, _ = self.launch("jama", path=f"{on_path.parent}:{LAUNCHD_PATH}", uv_dirs=str(fallback.parent))
        self.assertEqual((code, out[0]), (0, f"uv={on_path}"))

    def test_uv_is_found_where_its_installers_put_it_when_path_lacks_it(self):
        local = self.home / ".local" / "bin" / "uv"
        local.parent.mkdir(parents=True)
        local.write_text(FAKE_UV)
        local.chmod(0o755)
        code, out, _ = self.launch("testrail")
        self.assertEqual((code, out[0]), (0, f"uv={local}"), "~/.local/bin comes before Homebrew's folder")
        first, second = self.fake_uv("first"), self.fake_uv("second")
        code, out, _ = self.launch("testrail", uv_dirs=f"{self.tmp / 'missing'}:{first.parent}:{second.parent}")
        self.assertEqual(out[0], f"uv={first}")

    def test_no_uv_is_one_line_on_stderr_and_nothing_on_stdout(self):
        if shutil.which("uv", path=LAUNCHD_PATH):
            self.skipTest("uv is installed in a system folder")
        code, out, err = self.launch("atlassian", uv_dirs="")
        self.assertEqual((code, out), (127, []))
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("needs uv", err)
        self.assertIn("docs.astral.sh/uv", err)

    def test_an_unknown_connector_is_refused(self):
        code, out, err = self.launch("github", uv_dirs=str(self.fake_uv("bin").parent))
        self.assertEqual((code, out), (64, []))
        self.assertIn("testrail|jama|atlassian|appium", err)

    def test_the_plugin_root_is_the_assistants_when_it_holds_the_launcher(self):
        uv = str(self.fake_uv("bin").parent)
        other = self.tmp / "installed" / "bc"
        (other / "scripts").mkdir(parents=True)
        (other / "mcp-servers").mkdir()
        shutil.copy(str(LAUNCHER), str(other / "scripts" / "bc-mcp"))
        for variable in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "CURSOR_PLUGIN_ROOT"):
            code, out, _ = self.launch("jama", uv_dirs=uv, **{variable: str(other)})
            self.assertIn(str(other / "mcp-servers" / "jama-server"), out, variable)
            code, out, _ = self.launch("jama", uv_dirs=uv, **{variable: str(self.tmp)})
            self.assertIn(str(PLUGIN / "mcp-servers" / "jama-server"), out, f"{variable} without the launcher is ignored")

    def test_mcp_jsons_relative_path_works_from_the_plugin_root(self):
        code, out, _ = self.launch("atlassian", uv_dirs=str(self.fake_uv("bin").parent), cwd=PLUGIN,
                                   script="./scripts/bc-mcp")
        self.assertEqual(code, 0)
        self.assertIn(str(PLUGIN / "mcp-servers" / "jira-router"), out)

    def test_the_launcher_is_executable_and_plain_sh(self):
        self.assertTrue(os.access(str(LAUNCHER), os.X_OK))
        self.assertEqual(LAUNCHER.read_text().splitlines()[0], "#!/bin/sh")


class Manifests(unittest.TestCase):
    def setUp(self):
        self.agent_plugins = json.loads((PLUGIN / "mcp.json").read_text())
        self.claude = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["mcpServers"]

    def test_mcp_json_follows_agent_plugins_1_0(self):
        root = json.loads((PLUGIN / "plugin.json").read_text())
        self.assertEqual(set(self.agent_plugins), {"$schema", "mcpServers"}, "the envelope is closed")
        self.assertEqual(self.agent_plugins["$schema"], root["$schema"].replace("plugin.schema.json", "mcp.schema.json"))
        for name, server in self.agent_plugins["mcpServers"].items():
            self.assertLessEqual(set(server), {"type", "command", "args", "env", "cwd"}, name)
            self.assertEqual(server["type"], "stdio")
            self.assertEqual(server["command"], "sh", "a bare token: no placeholder is expanded in command")
            self.assertEqual(server["args"], ["./scripts/bc-mcp", name], "cwd defaults to the plugin root")
            self.assertNotIn("env", server, "no key or per-computer value lives in a manifest")

    def test_claude_code_declares_the_same_connectors(self):
        self.assertEqual(list(self.claude), list(self.agent_plugins["mcpServers"]))
        self.assertEqual(set(self.claude), set(CONNECTORS))
        for name, server in self.claude.items():
            self.assertEqual(server, {"type": "stdio", "command": "sh", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/bc-mcp", name]})

    def test_the_server_names_stay_the_ones_people_already_use(self):
        # Copilot gives a plugin's server precedence over a same-named one in ~/.copilot/mcp-config.json, so these
        # names replace the connectors older setups registered there, and everyday jira_* calls keep working.
        self.assertEqual(set(self.agent_plugins["mcpServers"]), {"testrail", "jama", "atlassian", "appium"})

    def test_no_per_assistant_connector_settings_ship(self):
        self.assertFalse((PLUGIN / ".mcp.example.json").exists(), "setup no longer writes connector settings")


if __name__ == "__main__":
    unittest.main()
