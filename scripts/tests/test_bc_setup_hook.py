"""The bash hook approves the settings script as one plain command — never `forget`, which deletes a key.

    python3 -m unittest discover -s scripts/tests -v
"""

import json
import subprocess
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
HOOK = PLUGIN / "hooks" / "validate-bash.sh"
SCRIPT = PLUGIN / "scripts" / "bc_setup.py"


def decide(command, shape="claude"):
    if shape == "claude":
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    else:
        payload = {"toolName": "bash", "toolArgs": json.dumps({"command": command})}
    done = subprocess.run([str(HOOK)],  # through its shebang, as the assistants run it
                           input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout.strip()


class SettingsScriptApproval(unittest.TestCase):
    def test_every_subcommand_but_forget_is_approved_on_both_runtimes(self):
        patch = ('{"jira": {"provider": "jira", "base_url": "https://jira.acme.com", "board": {"url": '
                 '"https://jira.acme.com/secure/RapidBoard.jspa?rapidView=42&projectKey=CWA", "id": 42}}, '
                 '"requirements": {"project_name": "Bob\\u0027s project"}}')
        for args in ("status", "status --need test_cases,board", "status --need test_cases --reads board",
                     "status --reads 'test_cases,board'", "status --from '/Users/qa/my project/src'",
                     "links --link 'https://acme.atlassian.net/jira/software/c/projects/PROJ/boards/42?x=1&y=2' "
                     "--link 'https://acme.testrail.io/index.php?/suites/view/12&group_by=cases:section_id'",
                     f"save --project '/Users/qa/my project' --patch '{patch}'",
                     "save --project /Users/qa/app --unset jira --patch '{\"jira\": {\"provider\": \"none\"}}'",
                     "migrate", "cleanup --verified 'acme.testrail.io,acme-prd.jamacloud.com'",
                     "github --url 'https://github.com/acme/app/blob/main/docs/sds.md#L4'"):
            for shape in ("claude", "copilot"):
                code, out = decide(f"python3 '{SCRIPT}' {args}", shape)
                self.assertEqual(code, 0, args)
                self.assertIn('"permissionDecision":"allow"', out, args)
                self.assertIn("bc: settings script", out, args)

    def test_forgetting_a_key_always_asks(self):
        code, out = decide(f"python3 '{SCRIPT}' forget --url acme.testrail.io")
        self.assertEqual((code, out), (0, ""))

    def test_anything_that_isnt_one_plain_line_asks(self):
        for command in (
            f"python3 '{SCRIPT}' save --project /p --patch '{{\"name\": \"it's\"}}'",
            f"python3 '{SCRIPT}' save --project /p --patch \"$(cat settings.json)\"",
            f"python3 '{SCRIPT}' save --project /p < settings.json",
            f"python3 '{SCRIPT}' save --project /p --patch '{{}}' > /tmp/x",
            f"python3 '{SCRIPT}' status; cat ~/.buddy-council/keys/acme.testrail.io.json",
            f"python3 '{SCRIPT}' status\nrm -rf /tmp/x",
            f"python3 '{SCRIPT}' status --verbose yes",
            f"python3 '{SCRIPT}' delete --project /p",
            "python3 /tmp/scripts/bc_setup.py status",
        ):
            code, out = decide(command)
            self.assertIn(code, (0, 2), command)
            self.assertNotIn("permissionDecision", out, command)


if __name__ == "__main__":
    unittest.main()
