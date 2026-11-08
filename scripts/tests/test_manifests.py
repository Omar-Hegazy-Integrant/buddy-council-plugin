"""The plugin's two manifests, its command skills, its how-tos, and the paths its instructions name.

    python3 -m unittest discover -s scripts/tests -v

Standard library only; they must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions.
"""

import ast
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "scripts"))

import sync_version  # noqa: E402

AGENT_PLUGINS_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_PLUGINS_FIELDS = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license",
                        "keywords", "extensions"}
COMMANDS = {"ask", "codemap", "contradiction", "coverage", "onboarding", "setup", "validate", "vnv-sprint-prep"}
AGENTS_DIR = PLUGIN / "com.github.copilot" / "agents"
PATH_REFERENCE = re.compile(
    r"(?<![\w./-])(howtos/[a-z0-9-]+\.md|providers/[a-z0-9-]+/[a-z0-9-]+\.md|skills/[a-z0-9-]+/SKILL\.md"
    r"|com\.github\.copilot/agents/[a-z0-9-]+\.agent\.md|com\.github\.copilot/hooks/hooks\.json"
    r"|scripts/[a-z0-9_]+\.py|hooks/[a-z0-9-]+\.(?:sh|json))")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def frontmatter(path):
    """The flat `key: value` pairs between a file's opening `---` lines, or None when it has none."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith((" ", "-")):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fields[key.strip()] = value
    return fields


def instruction_files():
    return (sorted(PLUGIN.glob("skills/*/SKILL.md")) + sorted(PLUGIN.glob("howtos/*.md"))
            + sorted(AGENTS_DIR.glob("*.agent.md")) + sorted(PLUGIN.glob("providers/*/*.md")))


class Version(unittest.TestCase):
    def test_every_manifest_carries_the_root_version(self):
        version, stale = sync_version.sync(check=True)
        self.assertEqual(stale, [], f"run python3 scripts/sync_version.py to copy {version} into every manifest")
        done = subprocess.run([sys.executable, str(PLUGIN / "scripts" / "sync_version.py"), "--check"],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_marketplace_carries_no_version_to_drift(self):
        marketplace = read_json(PLUGIN / ".claude-plugin" / "marketplace.json")
        for entry in marketplace["plugins"]:
            self.assertNotIn("version", entry, "the version lives only in plugin.json")
        self.assertEqual([entry["name"] for entry in marketplace["plugins"]], [read_json(PLUGIN / "plugin.json")["name"]])

    def test_sync_rewrites_only_the_version_and_check_reports_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp, "plugin.json"), Path(tmp, "claude.json")
            source.write_text('{"name": "bc", "version": "9.1.0"}\n', encoding="utf-8")
            original = '{\n  "name": "bc",\n  "version": "0.1.0",\n  "agents": ["./a.md"]\n}\n'
            target.write_text(original, encoding="utf-8")
            self.assertEqual(sync_version.sync(source, [target], check=True), ("9.1.0", [(target, "0.1.0")]))
            self.assertEqual(target.read_text(encoding="utf-8"), original, "--check changes nothing")
            sync_version.sync(source, [target])
            self.assertEqual(target.read_text(encoding="utf-8"), original.replace("0.1.0", "9.1.0"))
            self.assertEqual(sync_version.sync(source, [target], check=True), ("9.1.0", []))


class Manifests(unittest.TestCase):
    def setUp(self):
        self.root = read_json(PLUGIN / "plugin.json")
        self.claude = read_json(PLUGIN / ".claude-plugin" / "plugin.json")

    def test_the_root_manifest_follows_agent_plugins_1_0(self):
        self.assertEqual(self.root["$schema"], AGENT_PLUGINS_SCHEMA)
        self.assertLessEqual(set(self.root), AGENT_PLUGINS_FIELDS, "unknown fields are ignored, component paths too")
        name = self.root["name"]
        self.assertRegex(name, r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
        self.assertNotIn("--", name)
        self.assertNotIn("..", name)
        self.assertLessEqual(set(self.root.get("author", {})), {"name", "email", "url"})
        self.assertTrue(all(isinstance(word, str) for word in self.root.get("keywords", [])))

    def test_claude_code_gets_the_same_plugin(self):
        for field in ("name", "description", "author", "repository", "license", "keywords"):
            self.assertEqual(self.claude.get(field), self.root.get(field), field)
        self.assertNotIn("commands", self.claude, "commands are skills now")

    def test_claude_code_lists_every_agent_copilot_reads(self):
        listed = self.claude["agents"]
        self.assertTrue(all(path.startswith("./") for path in listed), listed)
        files = sorted("./" + path.relative_to(PLUGIN).as_posix() for path in AGENTS_DIR.glob("*.agent.md"))
        self.assertEqual(sorted(listed), files)
        self.assertEqual(len(files), 7)

    def test_every_agent_keeps_its_name(self):
        for path in AGENTS_DIR.glob("*.agent.md"):
            fields = frontmatter(path)
            self.assertIsNotNone(fields, path.name)
            self.assertEqual(fields.get("name"), path.name[: -len(".agent.md")], "Copilot's id is the file name")
            self.assertTrue(fields.get("description"), path.name)


class Skills(unittest.TestCase):
    def test_the_skills_are_the_eight_commands(self):
        self.assertEqual({path.parent.name for path in PLUGIN.glob("skills/*/SKILL.md")}, COMMANDS)
        self.assertEqual({path.name for path in (PLUGIN / "skills").iterdir() if path.is_dir()}, COMMANDS)
        self.assertFalse((PLUGIN / "SKILL.md").exists())
        self.assertFalse((PLUGIN / "commands").exists(), "commands are skills now")
        self.assertFalse((PLUGIN / "agents").exists(), "agents live in com.github.copilot/agents/")

    def test_only_the_user_can_start_a_command(self):
        for name in COMMANDS:
            fields = frontmatter(PLUGIN / "skills" / name / "SKILL.md")
            self.assertEqual(fields.get("name"), name)
            self.assertEqual(fields.get("disable-model-invocation"), "true", name)
            self.assertTrue(fields.get("description"), name)
            codex = (PLUGIN / "skills" / name / "agents" / "openai.yaml").read_text(encoding="utf-8")
            self.assertRegex(codex, r'(?m)^  display_name: ".+"$', name)
            self.assertRegex(codex, r'(?m)^  short_description: ".+"$', name)
            self.assertRegex(codex, r"(?m)^policy:\n  allow_implicit_invocation: false$", name)

    def test_how_tos_are_plain_files_outside_skills(self):
        howtos = sorted(PLUGIN.glob("howtos/*.md"))
        self.assertEqual(len(howtos), 25)
        for path in howtos:
            self.assertIsNone(frontmatter(path), f"{path.name} is a plain file, not a skill")
            self.assertFalse((PLUGIN / "skills" / path.stem).exists(), path.stem)
            self.assertFalse(list(PLUGIN.glob(f"skills/**/{path.stem}.md")), path.stem)

    def test_every_how_to_is_reached_by_path(self):
        texts = {path: path.read_text(encoding="utf-8") for path in instruction_files()}
        for howto in PLUGIN.glob("howtos/*.md"):
            reference = f"howtos/{howto.name}"
            self.assertTrue(any(reference in text for path, text in texts.items() if path != howto), howto.name)


class Paths(unittest.TestCase):
    def test_every_path_the_docs_and_instructions_name_exists(self):
        for path in instruction_files() + [PLUGIN / "AGENTS.md", PLUGIN / "README.md"]:
            for reference in PATH_REFERENCE.findall(path.read_text(encoding="utf-8")):
                self.assertTrue((PLUGIN / reference).exists(), f"{path.relative_to(PLUGIN)} names {reference}")

    def test_instructions_use_paths_every_assistant_can_follow(self):
        howtos = {path.stem for path in PLUGIN.glob("howtos/*.md")}
        for path in instruction_files():
            text = path.read_text(encoding="utf-8")
            where = path.relative_to(PLUGIN).as_posix()
            self.assertFalse("${CLAUDE_PLUGIN_ROOT}/" in text, f"{where}: only Claude Code expands ${{CLAUDE_PLUGIN_ROOT}}")
            self.assertFalse("$ARGUMENTS" in text, f"{where}: only Claude Code expands $ARGUMENTS")
            self.assertIsNone(re.search(r"(?<![\w./-])commands/", text), f"{where}: commands are skills now")
            self.assertIsNone(re.search(r"(?<![\w./-])agents/[a-z0-9-]+\.agent\.md", text),
                              f"{where}: agents live in com.github.copilot/agents/")
            for name in re.findall(r"(?<![\w./-])skills/([a-z0-9-]+)", text):
                self.assertNotIn(name, howtos, f"{where}: {name} is a how-to now")

    def test_every_command_says_where_the_plugin_root_is(self):
        for name in COMMANDS:
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"**Plugin root:** two folders above this skill's base directory, which is "
                          f"`<plugin root>/skills/{name}/`.", text)


class Setup(unittest.TestCase):
    GUIDES = {PLUGIN / "skills" / "setup" / "SKILL.md", *PLUGIN.glob("providers/*/setup.md")}

    def test_the_setup_skill_is_short_and_each_provider_has_a_guide(self):
        size = (PLUGIN / "skills" / "setup" / "SKILL.md").stat().st_size
        self.assertLessEqual(size, 12_000, "about 10 KB: the order, the questions, the recap, the save rule")
        for provider in ("jama", "testrail", "jira", "github"):
            self.assertTrue((PLUGIN / "providers" / provider / "setup.md").exists(), provider)

    def test_every_connector_call_names_its_site_on_every_kind_of_run(self):
        skill = (PLUGIN / "skills" / "setup" / "SKILL.md").read_text(encoding="utf-8")
        rule = skill.find("Every connector call passes `site`")
        self.assertGreaterEqual(rule, 0)
        self.assertLess(rule, skill.find("## 1."), "a rule for every run, not only for the Connect step")
        for provider in ("jama", "testrail", "jira"):
            text = (PLUGIN / "providers" / provider / "setup.md").read_text(encoding="utf-8")
            recheck = text.split("## Re-check", 1)[1].split("\n## ", 1)[0]
            calls = re.findall(r"`((?:jama|testrail|jira)_[a-z_]+)`([^,.;—]*)", recheck)
            self.assertTrue(calls, provider)
            for tool, rest in calls:
                self.assertIn("with `site`", rest, f"{provider} Re-check: {tool} — a re-check reads only this section")

    def test_nothing_outside_the_guides_names_a_setup_step_number(self):
        for path in instruction_files() + [PLUGIN / "AGENTS.md", PLUGIN / "README.md"]:
            if path in self.GUIDES:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if "setup" in line.lower():
                    self.assertIsNone(re.search(r"\bSteps? \d", line), f"{path.relative_to(PLUGIN)}: {line.strip()[:120]}")

    def test_every_command_finds_its_project_folder_through_the_settings_script(self):
        for name in COMMANDS - {"setup"}:
            text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("howtos/load-settings.md", text, name)
        self.assertIn("scripts/bc_setup.py' status", (PLUGIN / "howtos" / "load-settings.md").read_text(encoding="utf-8"))

    def test_no_instruction_reads_a_plugin_path_from_the_settings(self):
        for path in instruction_files() + [PLUGIN / "AGENTS.md", PLUGIN / "README.md"]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"plugin_root":', text, path.name)
            self.assertNotIn("plugin_root` in `.buddy-council/sources.json", text, path.name)
            for paragraph in re.split(r"\n\s*\n", text):
                if "plugin_root" in paragraph and "sources.json" in paragraph:
                    self.assertRegex(paragraph, r"plugin_root`? is gone|drops `plugin_root`|dropped plugin_root",
                                     f"{path.name} reads plugin_root from the settings:\n{paragraph[:300]}")


class Scripts(unittest.TestCase):
    def test_the_scripts_import_one_way(self):
        ours = {"bc_keys", "bc_run", "bc_setup", "bc_tools"}
        imports = {}
        for name in ours:
            tree = ast.parse((PLUGIN / "scripts" / f"{name}.py").read_text(encoding="utf-8"))
            imports[name] = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                             for alias in node.names} & ours
        self.assertEqual(imports, {"bc_keys": set(), "bc_run": {"bc_keys"}, "bc_setup": {"bc_keys", "bc_run"},
                                   "bc_tools": {"bc_setup"}}, "bc_keys ← bc_run ← bc_setup ← bc_tools, never back")

    def test_every_connector_reads_keys_through_the_one_key_store(self):
        for server in ("testrail-server", "jama-server", "jira-router"):
            text = (PLUGIN / "mcp-servers" / server / "server.py").read_text(encoding="utf-8")
            self.assertIn("import bc_keys", text, server)
            self.assertNotIn("spec_from_file_location", text, f"{server} copies a loader of its own")


class Hooks(unittest.TestCase):
    def test_both_hook_files_run_the_same_scripts(self):
        claude = read_json(PLUGIN / "hooks" / "hooks.json")
        copilot = read_json(PLUGIN / "com.github.copilot" / "hooks" / "hooks.json")
        claude_commands = [hook["command"] for groups in claude["hooks"].values() for group in groups
                           for hook in group["hooks"]]
        copilot_commands = [hook["bash"] for hooks in copilot["hooks"].values() for hook in hooks]
        script = re.compile(r"/hooks/([a-z0-9-]+\.sh)")
        names = {name for command in claude_commands for name in script.findall(command)}
        self.assertEqual(names, {name for command in copilot_commands for name in script.findall(command)})
        self.assertEqual(names, {"validate-bash.sh", "auto-approve-mcp.sh", "log-tool-use.sh"})
        for name in names:
            self.assertTrue((PLUGIN / "hooks" / name).stat().st_mode & 0o111, f"{name} is executable")
        for command in claude_commands:
            self.assertTrue(command.startswith('"${CLAUDE_PLUGIN_ROOT}"/hooks/'), command)
        for command in copilot_commands:
            self.assertTrue(command.startswith('"${PLUGIN_ROOT:-.}"/hooks/'), command)
        self.assertFalse((PLUGIN / "hooks.json").exists(), "Agent Plugins read com.github.copilot/hooks/hooks.json")

    def test_no_hook_approves_a_file_write(self):
        # Settings and progress logs are saved by bc_setup.py and bc_run.py, which validate-bash.sh approves; a file
        # the assistant writes itself — a key above all — always reaches the assistant's own prompt.
        claude = read_json(PLUGIN / "hooks" / "hooks.json")
        copilot = read_json(PLUGIN / "com.github.copilot" / "hooks" / "hooks.json")
        self.assertEqual([g["matcher"] for g in claude["hooks"]["PreToolUse"]], ["Bash", "mcp__.*"])
        self.assertEqual([h.get("matcher") for h in copilot["hooks"]["preToolUse"]], ["bash", None])
        self.assertFalse((PLUGIN / "hooks" / "auto-approve-plugin-writes.sh").exists())


if __name__ == "__main__":
    unittest.main()
