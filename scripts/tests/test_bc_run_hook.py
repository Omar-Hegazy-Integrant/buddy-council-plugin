"""The bash hook approves the run-folder script only as one plain, unchainable command.

    python3 -m unittest discover -s scripts/tests -v
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
HOOK = PLUGIN / "hooks" / "validate-bash.sh"
SCRIPT = PLUGIN / "scripts" / "bc_run.py"
RUN = "/Users/qa/project/.buddy-council/runs/20261012-120000-coverage"


def decide(command, shape="claude"):
    if shape == "claude":
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    else:
        payload = {"toolName": "bash", "toolArgs": json.dumps({"command": command})}
    done = subprocess.run([str(HOOK)],  # through its shebang, as the assistants run it
                           input=json.dumps(payload), capture_output=True, text=True, timeout=20)
    return done.returncode, done.stdout.strip()


class RunScriptApproval(unittest.TestCase):
    def assertAllowed(self, command, shape="claude"):
        code, out = decide(command, shape)
        self.assertEqual(code, 0, command)
        self.assertIn('"permissionDecision":"allow"', out, command)
        self.assertIn("bc: run-folder script", out, command)

    def assertNotAllowed(self, command):
        code, out = decide(command)
        self.assertNotIn("permissionDecision", out, command)
        self.assertIn(code, (0, 2), "the normal prompt, or a hard block when a chained command is destructive — "
                                    "never a hook failure")

    def test_plain_commands_from_this_plugin_are_approved_on_both_runtimes(self):
        commands = [
            f"python3 {SCRIPT} new --project /Users/qa/project --command coverage --scope 'Patient Monitoring / Alarms & Alerts (v2)'",
            f"python3 {SCRIPT} new --project /Users/qa/project --command contradiction",
            f"python3 {SCRIPT} enrich --run {RUN}",
            f"  python3 {SCRIPT} link --run '{RUN}'  ",
            f"python3 {SCRIPT} search --run {RUN} --query 'Can alarms be muted (or snoozed) during warm-up? & why' "
            f"--concept 'mute|silence|suppress' --concept 'warm-up|warmup|$(not run)' --feature 'Alerts' --top 30",
            f"python3 {SCRIPT} show --run {RUN} --ids 'CWA-REQ-1, TC-55'",
            f"python3 {SCRIPT} summary --run {RUN}",
            f"python3 '{SCRIPT}' summary --run '{RUN}' --feature 'Alarms & Alerts (v2)'",
            f"python3 {SCRIPT} recent --project /Users/qa/project --max-age-minutes 30",
            f"python3 {SCRIPT} doc --url 'https://github.com/o/r/blob/main/docs/sds.md#L4' --max-chars 50000",
        ]
        for command in commands:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")

    def test_progress_saves_are_approved_as_one_line_of_single_quoted_json(self):
        project = "/Users/qa/project"
        commands = [
            f"python3 {SCRIPT} progress --project {project} --log onboarding --patch "
            "'{\"current_step_index\": 3, \"features\": [{\"name\": \"Login & Lockout\", \"status\": \"completed\"}]}'",
            f"python3 {SCRIPT} progress --project '{project}' --log vnv --patch "
            "'{\"stories\": [{\"dev_key\": \"PROJ-1\", \"note\": \"the dev\\u0027s answer; $(not run) | `nor this`\"}]}'",
            f"python3 {SCRIPT} note --project {project} --title 'Login — note' --text 'Revisit lockout (3 tries).'",
            f"python3 {SCRIPT} archive --project {project} --log onboarding",
        ]
        for command in commands:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")

    def test_a_progress_save_that_isnt_one_plain_line_falls_through(self):
        project = "/Users/qa/project"
        for command in [
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch '{{\"note\": \"it's\"}}'",
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch \"$(cat patch.json)\"",
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch '{{}}\n' && echo",
            f"python3 {SCRIPT} progress --project {project} --log vnv < patch.json",
            f"cat patch.json | python3 {SCRIPT} progress --project {project} --log vnv",
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch '{{\n}}'".replace("\\n", "\n"),
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch '{{}}' --force yes",
        ]:
            self.assertNotAllowed(command)

    def test_findings_and_the_report_are_approved_as_one_line_of_single_quoted_json(self):
        finding = ('{"severity": "high", "fix": "TC-55", "why": "The test doesn\\u0027t wait; $(not run) | `nor this`", '
                   '"items": [{"id": "CWA-REQ-1", "quote": "within 30 seconds… the App shall"}, '
                   '{"id": "TC-55", "quote": "Expected: \\"Done\\" & back"}]}')
        commands = [
            f"python3 {SCRIPT} findings --run {RUN} --add '{finding}'",
            f"python3 '{SCRIPT}' findings --run '{RUN}' --numbers '1, 5'",
            f"python3 {SCRIPT} findings --run {RUN}",
            f"python3 {SCRIPT} report --run {RUN} --lines '[\"Fetch: board issues → 4 fetched from board 9\"]' "
            "--partial 'board issues: 401'",
        ]
        for command in commands:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")
        for command in [
            f"python3 {SCRIPT} findings --run {RUN} --add '{{\"why\": \"it's\"}}'",
            f"python3 {SCRIPT} findings --run {RUN} --add \"$(cat findings.json)\"",
            f"python3 {SCRIPT} report --run {RUN} && echo done",
            f"python3 {SCRIPT} report --run {RUN} --force yes",
        ]:
            self.assertNotAllowed(command)

    def test_slices_and_a_slice_s_save_are_approved_as_one_plain_command(self):
        for command in [
            f"python3 {SCRIPT} slices --run {RUN}",
            f"python3 '{SCRIPT}' slices --run '{RUN}' --slice 3",
            f"python3 {SCRIPT} findings --run {RUN} --slice '2' --add '[]'",
        ]:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")
        for command in [
            f"python3 {SCRIPT} slices --run {RUN} --slice 3; rm -rf ~/x",
            f"python3 {SCRIPT} slices --run {RUN} --slice $(id -u)",
            f"python3 {SCRIPT} slices --run {RUN} --feature Alarms",
        ]:
            self.assertNotAllowed(command)

    def test_a_symlinked_path_to_the_same_script_is_approved(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp, "plugin")
            link.symlink_to(PLUGIN)
            self.assertAllowed(f"python3 {link}/scripts/bc_run.py link --run {RUN}")

    def test_one_read_only_filter_that_shortens_the_output_is_approved(self):
        for command in [
            f"python3 '{SCRIPT}' summary --run '{RUN}' | head -c 3000",
            f"python3 '{SCRIPT}' show --run '{RUN}' --ids 'CWA-REQ-813, CWA-REQ-814' | cut -c1-1200",
            f"python3 {SCRIPT} search --run {RUN} --query 'signal loss' --concept 'loss|lost' | tail -n 40",
            f"python3 {SCRIPT} recent --project /Users/qa/project | head -50",
            f"python3 {SCRIPT} link --run {RUN} | head",
        ]:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")

    def test_any_other_pipe_still_asks(self):
        for command in [
            f"python3 {SCRIPT} summary --run {RUN} | sh",
            f"python3 {SCRIPT} summary --run {RUN} | head -c 3000 | sh",
            f"python3 {SCRIPT} summary --run {RUN} | head -c 3000 > /tmp/out",
            f"python3 {SCRIPT} summary --run {RUN} | head -c 3000; rm -rf ~/x",
            f"python3 {SCRIPT} summary --run {RUN} | tee /tmp/out",
            f"python3 {SCRIPT} summary --run {RUN} | python3 -c 'print(1)'",
            f"python3 {SCRIPT} summary --run {RUN} | cut -d, -f1",
            f"python3 {SCRIPT} summary --run {RUN} | head -c $(id -u)",
            f"python3 {SCRIPT} summary --run '{RUN} | head -c 5",
            f"cat /etc/hosts | head -c 50",
        ]:
            self.assertNotAllowed(command)

    def test_destructive_looking_text_inside_a_quoted_argument_is_saved_not_blocked(self):
        project = "/Users/qa/project"
        for command in [
            f"python3 {SCRIPT} note --project {project} --title 'Rollback plan' --text "
            "'If the migration breaks: git reset --hard, DROP TABLE staging, and restore > /etc/hosts'",
            f"python3 {SCRIPT} progress --project {project} --log vnv --patch "
            "'{\"stories\": [{\"dev_key\": \"CWA-1\", \"concerns\": [\"Input \\u0027; DROP TABLE users;-- is rejected\"]}]}'",
        ]:
            self.assertAllowed(command, "claude")
            self.assertAllowed(command, "copilot")

    def test_the_same_text_outside_quotes_is_still_blocked(self):
        code, out = decide(f"python3 {SCRIPT} note --project /Users/qa/project --title x --text y; git reset --hard")
        self.assertEqual(code, 2, out)
        code, out = decide(f"python3 {SCRIPT} link --run {RUN} && psql -c 'DROP TABLE users'")
        self.assertEqual(code, 2, out)

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
            fake = Path(tmp, "scripts", "bc_run.py")
            fake.parent.mkdir(parents=True)
            fake.write_text("print('not ours')")
            for command in [
                f"python3 {fake} link --run {RUN}",
                f"python3 scripts/bc_run.py link --run {RUN}",
                f"python3 {PLUGIN}/skills/collect-artifacts/bc_run.py link --run {RUN}",
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
