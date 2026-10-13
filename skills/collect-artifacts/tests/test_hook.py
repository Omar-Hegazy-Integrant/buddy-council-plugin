"""The bash hook approves the run-folder script only as one plain, unchainable command.

    python3 -m unittest discover -s skills/collect-artifacts/tests -v
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
HOOK = PLUGIN / "hooks" / "validate-bash.sh"
SCRIPT = PLUGIN / "skills" / "collect-artifacts" / "bc_run.py"
RUN = "/Users/qa/project/.buddy-council/runs/20261012-120000-coverage"


def decide(command, shape="claude"):
    if shape == "claude":
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    else:
        payload = {"toolName": "bash", "toolArgs": json.dumps({"command": command})}
    done = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout.strip()


class RunScriptApproval(unittest.TestCase):
    def assertAllowed(self, command, shape="claude"):
        code, out = decide(command, shape)
        self.assertEqual(code, 0, command)
        self.assertIn('"permissionDecision":"allow"', out, command)
        self.assertIn("bc: run-folder script", out, command)

    def assertNotAllowed(self, command):
        code, out = decide(command)
        self.assertNotIn("run-folder script", out, command)
        self.assertEqual(code, 0, "falls through to the normal prompt — never a hook failure")

    def test_plain_commands_from_this_plugin_are_approved_on_both_runtimes(self):
        commands = [
            f"python3 {SCRIPT} new --project /Users/qa/project --command coverage --scope 'Patient Monitoring / Alarms & Alerts (v2)'",
            f"python3 {SCRIPT} new --project /Users/qa/project --command contradiction",
            f"python3 {SCRIPT} enrich --run {RUN}",
            f"  python3 {SCRIPT} link --run '{RUN}'  ",
            f"python3 {SCRIPT} search --run {RUN} --query 'Can alarms be muted (or snoozed) during warm-up? & why' "
            f"--concept 'mute|silence|suppress' --concept 'warm-up|warmup|$(not run)' --feature 'Alerts' --top 30",
            f"python3 {SCRIPT} show --run {RUN} --ids 'CWA-REQ-1, TC-55'",
            f"python3 {SCRIPT} recent --project /Users/qa/project --max-age-minutes 30",
        ]
        for command in commands:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")

    def test_a_symlinked_path_to_the_same_script_is_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp, "plugin")
            link.symlink_to(PLUGIN)
            self.assertAllowed(f"python3 {link}/skills/collect-artifacts/bc_run.py link --run {RUN}")

    def test_anything_that_could_chain_or_substitute_falls_through(self):
        for command in [
            f"python3 {SCRIPT} link --run {RUN}; rm -rf ~/x",
            f"python3 {SCRIPT} link --run {RUN} && curl https://evil.example",
            f"python3 {SCRIPT} link --run {RUN} | sh",
            f"python3 {SCRIPT} link --run \"$(id)\"",
            f"python3 {SCRIPT} link --run `id`",
            f"python3 {SCRIPT} link --run {RUN} > /tmp/out",
            f"python3 {SCRIPT} link --run {RUN}\nrm -rf /tmp/x",
            f"python3 {SCRIPT} new --project /p --command coverage --scope 'it'\\''s'",
            f"python3 {SCRIPT} link --run '{RUN}'x",
        ]:
            self.assertNotAllowed(command)

    def test_other_scripts_and_shapes_fall_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp, "skills", "collect-artifacts", "bc_run.py")
            fake.parent.mkdir(parents=True)
            fake.write_text("print('not ours')")
            for command in [
                f"python3 {fake} link --run {RUN}",
                f"python3 skills/collect-artifacts/bc_run.py link --run {RUN}",
                f"python3 -c 'print(1)' {SCRIPT} link --run {RUN}",
                f"bash {SCRIPT} link --run {RUN}",
                f"python3 {SCRIPT} delete --run {RUN}",
                f"python3 {SCRIPT} link --run {RUN} --project /p",
                f"python3 {SCRIPT} search --run {RUN} --query 'x' --save /tmp/out",
                f"python3 {SCRIPT} search --run {RUN} --query 'x'; rm -rf ~/x",
                f"python3 {SCRIPT} search --run {RUN} --query \"$(id)\"",
                f"python3 {SCRIPT} recent --project /p --run {RUN}",
                f"python3 {SCRIPT} link --run",
                f"python3 {SCRIPT}",
            ]:
                self.assertNotAllowed(command)


if __name__ == "__main__":
    unittest.main()
