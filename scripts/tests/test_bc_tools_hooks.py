"""The hooks approve the automation tools narrowly: bc_tools.py as one plain command without its two riskier
subcommands, and appium-mcp's read-only tools — never a tap, a session, or an app launch.

    python3 -m unittest discover -s scripts/tests -v
"""

import json
import subprocess
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
BASH_HOOK = PLUGIN / "hooks" / "validate-bash.sh"
MCP_HOOK = PLUGIN / "hooks" / "auto-approve-mcp.sh"
SCRIPT = PLUGIN / "scripts" / "bc_tools.py"
RUN_SCRIPT = PLUGIN / "scripts" / "bc_run.py"
PROJECT = "/Users/qa/ApolloAutomation"


def run_hook(hook, payload):
    done = subprocess.run(["bash", str(hook)], input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout.strip()


def bash(command, shape="claude"):
    if shape == "claude":
        return run_hook(BASH_HOOK, {"tool_name": "Bash", "tool_input": {"command": command}})
    return run_hook(BASH_HOOK, {"toolName": "bash", "toolArgs": json.dumps({"command": command})})


def mcp(tool, shape="claude"):
    if shape == "claude":
        return run_hook(MCP_HOOK, {"tool_name": tool, "tool_input": {}})
    return run_hook(MCP_HOOK, {"toolName": tool, "toolArgs": "{}"})


class ToolsScript(unittest.TestCase):
    def assertAllowed(self, command, shape="claude"):
        code, out = bash(command, shape)
        self.assertEqual(code, 0, command)
        self.assertIn('"permissionDecision":"allow"', out, command)
        self.assertIn("bc: automation tools", out, command)

    def assertPrompts(self, command):
        code, out = bash(command)
        self.assertEqual(code, 0, "falls through to the normal prompt — never a hook failure")
        self.assertNotIn("permissionDecision", out, command)

    def test_the_curated_subcommands_are_approved_on_both_runtimes(self):
        for command in (f"python3 {SCRIPT} detect --project {PROJECT}",
                        f"python3 '{SCRIPT}' status --project '/Users/qa/My Tests'",
                        f"python3 '{SCRIPT}' ensure --project '{PROJECT}'",
                        f"python3 {SCRIPT} status --project '{PROJECT}'",
                        f"python3 {SCRIPT} ensure --project '/Users/qa/My Tests' --shared jdk,android-sdk",
                        f"python3 {SCRIPT} smoke --project {PROJECT}",
                        f"python3 {SCRIPT} start --project {PROJECT}",
                        f"python3 {SCRIPT} stop",
                        f"python3 {SCRIPT} prune"):
            self.assertAllowed(command)
            self.assertAllowed(command, shape="copilot")

    def test_the_form_setup_md_prescribes_is_the_one_approved(self):
        setup = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
        howto = (PLUGIN / "howtos" / "automation-tools.md").read_text(encoding="utf-8")
        for text in (setup, howto):
            self.assertNotIn('python3 "<plugin_root>/scripts/bc_tools.py"', text, "double quotes always prompt")
            self.assertIn("python3 '<plugin_root>/scripts/bc_tools.py'", text)
        self.assertPrompts(f'python3 "{SCRIPT}" status --project "{PROJECT}"')

    def test_running_the_projects_code_or_deleting_tools_always_prompts(self):
        self.assertPrompts(f"python3 {SCRIPT} project --project {PROJECT} --mode check")
        self.assertPrompts(f"python3 {SCRIPT} project --project {PROJECT} --mode fix")
        self.assertPrompts(f"python3 {SCRIPT} remove --project {PROJECT}")

    def test_nothing_can_be_chained_or_smuggled(self):
        for command in (f"python3 {SCRIPT} detect --project {PROJECT} && curl evil.example",
                        f"python3 {SCRIPT} detect --project {PROJECT}; rm x",
                        f"python3 {SCRIPT} detect --project $(whoami)",
                        f"python3 {SCRIPT} detect --project {PROJECT} --evil 1",
                        f"python3 {SCRIPT} detect --project",
                        f"python3 /tmp/elsewhere/bc_tools.py detect --project {PROJECT}",
                        f"python3 scripts/bc_tools.py detect --project {PROJECT}",
                        f"python3 {PLUGIN}/skills/automation-tools/bc_tools.py detect --project {PROJECT}",
                        f"BC_HOME=/tmp python3 {SCRIPT} ensure --project {PROJECT}",
                        f"python3 {SCRIPT} ensure --project {PROJECT}\ncurl evil.example",
                        f"python3 {SCRIPT} uninstall"):
            self.assertPrompts(command)

    def test_the_run_folder_script_keeps_its_own_approval(self):
        code, out = bash(f"python3 {RUN_SCRIPT} new --project {PROJECT} --command coverage")
        self.assertIn("bc: run-folder script", out)
        self.assertPrompts(f"python3 {RUN_SCRIPT} detect --project {PROJECT}")
        self.assertPrompts(f"python3 {SCRIPT} new --project {PROJECT} --command coverage")


class AppiumTools(unittest.TestCase):
    READ = ("appium_mobile_device_info", "appium_find_element", "appium_find_elements", "appium_get_text",
            "appium_get_element_attribute", "appium_get_active_element", "appium_get_window_size", "generate_locators",
            "appium_get_page_source", "appium_screenshot")
    PROMPTS = ("appium_session_management", "appium_gesture", "appium_set_value", "appium_app_lifecycle",
               "appium_mobile_permissions", "appium_perform_actions", "appium_drag_and_drop", "appium_alert",
               "appium_mobile_keyboard", "appium_mobile_clipboard", "appium_mobile_device_control", "select_device",
               "prepare_ios_simulator", "appium_prepare_ios_real_device", "appium_mobile_file", "appium_generate_tests")

    def test_reads_are_approved_in_every_spelling(self):
        for tool in self.READ:
            for name in (f"mcp__appium__{tool}", f"appium-{tool}"):
                code, out = mcp(name, "claude" if name.startswith("mcp__") else "copilot")
                self.assertEqual(code, 0, name)
                self.assertIn('"permissionDecision":"allow"', out, name)

    def test_taps_typing_sessions_and_app_launches_prompt(self):
        for tool in self.PROMPTS:
            for name in (f"mcp__appium__{tool}", f"appium-{tool}", tool):
                code, out = mcp(name, "claude" if name.startswith("mcp__") else "copilot")
                self.assertEqual(code, 0, name)
                self.assertEqual(out, "", name)

    def test_a_same_named_tool_on_another_server_is_not_approved(self):
        code, out = mcp("other-generate_locators", "copilot")
        self.assertEqual(out, "")
        code, out = mcp("mcp__other__appium_find_element")
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
