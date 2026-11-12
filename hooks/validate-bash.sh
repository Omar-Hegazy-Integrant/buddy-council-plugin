#!/bin/bash
# PreToolUse hook for the shell tool (Claude Code "Bash" / Copilot CLI "bash").
# One Python program decides, in this order:
#   1. AUTO-APPROVE the plugin's own scripts, each only as one plain command whose
#      arguments are bare or single-quoted. Checked first because the settings, notes,
#      and progress they save travel as quoted arguments: a destructive-looking string
#      in one is data the shell never runs, not a command to block.
#   2. HARD-BLOCK destructive operations (exit 2) — on every line, and on every command
#      chained with ;, &&, ||, |, or &, not only the first. A forced git push (--force,
#      -f, or a +refspec) is one; a push with --force-with-lease isn't, so it asks.
# Everything else falls through (exit 0, no JSON) to the normal permission prompt. That
# includes jq and gh: jq reads any file or the environment, and gh can print its token
# (`gh auth token`, `gh auth status -t`), so either could show a key unasked. No plugin
# instruction runs them; the scripts call gh themselves.
#
# Runs under BOTH runtimes: Claude Code sends {tool_name, tool_input: {command}};
# Copilot CLI sends {toolName, toolArgs: "<json string with command>"}. Exit 2 =
# block/deny on both. NOTE: no `set -e` here — Copilot treats any non-zero exit
# from a preToolUse hook as DENY (fail-closed), so incidental failures must not
# change the exit code.
#
# The plugin's own scripts, and what each subcommand may do unprompted:
#   - scripts/bc_run.py (new, enrich, link, search, show, summary, recent, doc, progress, note, archive). It reads
#     and writes only inside <project>/.buddy-council/ — run folders and the onboarding and V&V progress logs — and
#     its one network use is read-only GitHub GETs (gh, or HTTPS with a token only for the host it belongs to).
#   - scripts/bc_setup.py (status, links, save, migrate, cleanup, github). It saves a project's settings after
#     checking them against config/sources.example.json, and moves the keys older setups saved into per-site files.
#     `cleanup` is approved because it deletes only bc's old copies — and only after each site answered with its
#     per-site key — so nothing is lost. `forget` deletes the only copy of a key the user named, so it is never
#     approved here: it always asks.
#   - scripts/bc_tools.py (detect, status, ensure, smoke, start, stop, prune). It installs bc's own pinned,
#     checksummed Node and appium-mcp into ~/.buddy-council/tools; writes only there (including the record the
#     plugin's appium connector starts appium-mcp from), in ~/.buddy-council/automation, and the project's
#     .buddy-council/sources.json; and runs nothing from the project. Its `project` subcommand runs the project's
#     own setup script, and `remove` deletes bc's tools, so those two are never approved here: they always prompt.
#   - scripts/bc_walk.py (check-cases, batch, validate, merge, plan, check, render, publish). It reads and writes
#     only a run folder in <project>/.buddy-council/runs/, and talks to nothing; `publish` copies the run's three
#     Walk Plan files into <project>/walk-plan/ for the team to commit. Its arguments are positional: the run
#     folder first, then a batch id or a Walks file.
#   - scripts/bc_keys.py (path, sites, check, migrate). It never prints a key: it names sites and files, tightens
#     a key file's permissions, and copies the keys older setups saved into ~/.buddy-council/keys without ever
#     replacing one. `forget` deletes a key, so it is never approved here: it always prompts.
# A script call may end in one read-only filter that shortens its output — `| head` or `| tail` with -c/-n N
# or -N, or `| cut -c N-M` — which models add by habit. Nothing else may follow it.

INPUT=$(cat)
PLUGIN_DIR="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd -P)"

# The program is read from a quoted heredoc outside any $( ): macOS's /bin/bash 3.2 misparses a heredoc inside a
# command substitution when its text holds a backtick or a parenthesis.
read -r -d '' PROGRAM <<'PY'
import json, os, re, shlex, sys

def command_of(raw):
    try:
        data = json.loads(raw)
        tool_input = data.get("tool_input")
        if tool_input is None:
            args = data.get("toolArgs")
            tool_input = json.loads(args) if isinstance(args, str) else (args or {})
        return str(tool_input.get("command") or "").strip()
    except Exception:
        return ""

TOKEN = r"(?:[A-Za-z0-9_./:=@%+,-]+|'[^']*')"
PLAIN = re.compile(r"\s*" + TOKEN + r"(?:\s+" + TOKEN + r")*\s*")
TRIM = re.compile(r"\s\|\s*(?:(?:head|tail)(?:\s+(?:-[cn]\s*\d+|-\d+))?|cut\s+-c\s*\d+(?:-\d*)?)\s*$")
SCRIPTS = {
    "bc_run.py": ("bc: run-folder script", {
        "new": {"--project", "--command", "--scope"}, "enrich": {"--run"}, "link": {"--run"},
        "search": {"--run", "--query", "--concept", "--feature", "--top"}, "show": {"--run", "--ids"},
        "summary": {"--run", "--feature"},
        "recent": {"--project", "--max-age-minutes"}, "doc": {"--url", "--max-chars"},
        "progress": {"--project", "--log", "--patch"},
        "note": {"--project", "--title", "--text"}, "archive": {"--project", "--log"}}),
    "bc_setup.py": ("bc: settings script (never prints a key)", {
        "status": {"--from", "--need", "--reads"}, "links": {"--link"}, "save": {"--project", "--patch", "--unset"},
        "migrate": set(), "cleanup": {"--verified"}, "github": {"--url", "--move-old-token"}}),
    "bc_tools.py": ("bc: Plugin Tools script", {
        "detect": {"--project"}, "status": {"--project"}, "ensure": {"--project", "--shared"},
        "smoke": {"--project"}, "start": {"--project"}, "stop": set(), "prune": set()}),
    "bc_keys.py": ("bc: key store (never prints a key)", {
        "path": {"--url", "--provider"}, "sites": {"--provider"}, "check": {"--url", "--provider"}, "migrate": set()}),
    # (positional arguments, options that take a value, options that take none)
    "bc_walk.py": ("bc: Walk Plan script", {
        "check-cases": (1, {"--folder"}, set()), "batch": (1, set(), set()), "validate": (2, set(), set()),
        "merge": (1, {"--same"}, set()), "plan": (1, set(), set()), "check": (2, set(), set()),
        "render": (1, set(), {"--batches"}), "publish": (1, set(), set())}),
}

def plain_words(text):
    """The words of one plain command — bare or single-quoted words and nothing else — or None."""
    if not text or "\n" in text or "\r" in text or not PLAIN.fullmatch(text):
        return None
    return [word[1:-1] if word.startswith("'") else word for word in re.findall(TOKEN, text)]

def plugin_script(cmd, root):
    trim = TRIM.search(cmd)
    words = plain_words(cmd[:trim.start()] if trim else cmd)
    if not root or not words or len(words) < 3 or words[0] != "python3" or not os.path.isabs(words[1]):
        return None
    for name, (reason, table) in SCRIPTS.items():
        if os.path.realpath(words[1]) != os.path.realpath(os.path.join(root, "scripts", name)):
            continue
        options, rest = table.get(words[2]), words[3:]
        if isinstance(options, tuple):
            count, valued, flags = options
            given, rest = rest[:count], rest[count:]
            if len(given) < count or any(word.startswith("-") for word in given):
                continue
            while rest and rest[0] in flags:
                rest = rest[1:]
            if any(word in flags for word in rest):
                continue
            options = valued
        if options is not None and not len(rest) % 2 and all(option in options for option in rest[::2]):
            return reason
    return None

def segments(cmd):
    """Every line, every command chained in it (;, &&, ||, |, &, a subshell), and every command substitution —
    which runs even inside double quotes — with single-quoted text kept whole where the line parses."""
    found = []
    for line in cmd.splitlines() or [cmd]:
        found.append(line)
        found.extend(part.split(")")[0] for part in line.split("$(")[1:])
        found.extend(line.split("`")[1::2])
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            words = list(lexer)
        except ValueError:
            found.extend(re.split(r"\|\||&&|[;|&()]", line))
            continue
        current = []
        for word in words:
            if re.fullmatch(r"[();<>|&]+", word) and re.search(r"[;|&()]", word):
                found.append(" ".join(current))
                current = []
            else:
                current.append(word)
        found.append(" ".join(current))
    return [segment.strip() for segment in found if segment.strip()]

def blocked(cmd):
    for segment in segments(cmd):
        words = segment.split()
        while words and words[0] in ("sudo", "command", "exec", "nohup"):
            words = words[1:]
        program = os.path.basename(words[0]) if words else ""
        if program == "rm" and any(re.fullmatch(r"-[A-Za-z]*[rRf][A-Za-z]*|--force|--recursive", word) for word in words[1:]):
            return "Destructive rm command."
        if program in ("kill", "pkill", "killall"):
            return "Process termination commands."
        if git_undoes(segment.split()):
            return "Destructive git operation."
    if re.search(r">\s*/(?:etc|usr|var)/", cmd):
        return "Writing to system directories."
    if re.search(r"DROP\s+(?:TABLE|DATABASE)|TRUNCATE\s+TABLE", cmd, re.I):
        return "Destructive database operation."
    return None

GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}

def git_undoes(words):
    """A git command among the words that can't be undone: a push that overwrites the remote unchecked — --force,
    -f, or a +refspec, wherever it sits — or a hard reset. A push with --force-with-lease isn't one: it refuses to
    overwrite work it hasn't seen, so it asks like any other command."""
    for start, word in enumerate(words):
        if os.path.basename(word) != "git":
            continue
        rest = words[start + 1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in GIT_OPTIONS_WITH_VALUE else rest[1:]
        args = []
        for arg in rest[1:]:
            if re.search(r"[;&|()<>`]", arg):
                break
            args.append(arg)
        if rest and rest[0] == "reset" and "--hard" in args:
            return True
        if rest and rest[0] == "push" and any(forces(arg) for arg in args):
            return True
    return False

def forces(arg):
    if arg == "--force" or (arg.startswith("+") and len(arg) > 1):
        return True
    # A cluster of short options such as -f or -uf; -o takes the rest of its word as a value.
    return bool(re.fullmatch(r"-[A-Za-z0-9]+", arg)) and "f" in arg[1:].split("o")[0]

try:
    cmd, root = command_of(os.environ.get("BC_INPUT", "")), os.environ.get("BC_PLUGIN_DIR", "")
    reason = plugin_script(cmd, root)
    if reason:
        print("allow:" + reason)
        sys.exit(0)
    why = blocked(cmd)
    if why:
        print("block:" + why)
except SystemExit:
    raise
except Exception:
    pass
PY

DECISION=$(BC_INPUT="$INPUT" BC_PLUGIN_DIR="$PLUGIN_DIR" python3 -c "$PROGRAM" 2>/dev/null)

case "$DECISION" in
  allow:*)
    # Emits BOTH decision shapes: top-level keys for Copilot CLI, the
    # hookSpecificOutput wrapper for Claude Code. Each runtime reads its own.
    REASON="${DECISION#allow:}"
    printf '{"permissionDecision":"allow","permissionDecisionReason":"%s","hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"%s"}}\n' "$REASON" "$REASON"
    exit 0
    ;;
  block:*)
    echo "[bc plugin] BLOCKED: ${DECISION#block:}" >&2
    exit 2
    ;;
esac
exit 0
