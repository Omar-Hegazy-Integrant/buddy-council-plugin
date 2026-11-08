#!/bin/bash
# PostToolUse hook: append a one-line JSONL record of every tool call to
# .buddy-council/logs/tool-log-YYYY-MM-DD.jsonl in the project, so bc runs are
# debuggable after the fact — which tools ran, in what order, against what.
#
# Runs under BOTH runtimes:
#   - Claude Code: registered in hooks/hooks.json (PostToolUse, matcher .*);
#     payload {tool_name, tool_input, session_id}; hook cwd = project dir.
#   - Copilot CLI and VS Code: registered in com.github.copilot/hooks/hooks.json
#     (postToolUse + postToolUseFailure); payload {toolName, toolArgs: "<json string>",
#     sessionId, cwd, toolResult}; hook cwd = PLUGIN dir, so the project is
#     resolved from COPILOT_PROJECT_DIR / the payload's cwd.
#
# - Active only in projects that use the plugin: the nearest .buddy-council/ at or
#   above the project dir (never the key store's own ~/.buddy-council); sessions
#   in other projects are never logged.
# - Logs metadata only: timestamp, tool name, a redacted target summary, and a
#   result status when the runtime provides one. Never logs file contents, tool
#   responses, or credentials (common credential shapes are masked).

INPUT=$(cat)

echo "$INPUT" | python3 -c "
import json, sys, os, re, datetime

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

# Project dir: env (both runtimes export their own), then payload cwd, then pwd.
start = (os.environ.get('CLAUDE_PROJECT_DIR')
         or os.environ.get('COPILOT_PROJECT_DIR')
         or d.get('cwd')
         or os.getcwd())
store = os.path.realpath(os.environ.get('BC_HOME') or os.path.expanduser('~/.buddy-council'))
proj, here = None, os.path.realpath(start)
while True:
    candidate = os.path.join(here, '.buddy-council')
    if os.path.isdir(candidate) and os.path.realpath(candidate) != store:
        proj = here
        break
    parent = os.path.dirname(here)
    if parent == here:
        break
    here = parent
if proj is None:
    sys.exit(0)

tool = d.get('tool_name') or d.get('toolName') or ''
ti = d.get('tool_input')
if ti is None:
    ta = d.get('toolArgs')
    try:
        ti = json.loads(ta) if isinstance(ta, str) else (ta or {})
    except Exception:
        ti = {}
if not isinstance(ti, dict):
    ti = {}

# Tool-agnostic target summary, by precedence: shell command, file path, skill,
# search pattern, agent description.
target = (
    (ti.get('command') or '')[:200]
    or ti.get('file_path') or ti.get('path')
    or ti.get('skill')
    or ti.get('pattern')
    or (ti.get('description') or ti.get('subagent_type') or '')[:120]
    or ''
)

# Mask credential shapes that can appear in setup-time commands.
target = re.sub(r'(-u\s+|--user\s+)\S+', r'\g<1>***', target)
target = re.sub(r'(Authorization:\s*)\S+', r'\g<1>***', target, flags=re.I)
target = re.sub(r'(api[_-]?key\"?\s*[:=]\s*\"?)[^\s\",]+', r'\g<1>***', target, flags=re.I)
target = re.sub(r'(token\"?\s*[:=]\s*\"?)[^\s\",]+', r'\g<1>***', target, flags=re.I)

rec = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    'session': (d.get('session_id') or d.get('sessionId') or '')[:8],
    'tool': tool,
    'target': target,
}
# Copilot provides toolResult.resultType ('success' / failure kinds); record it.
result = (d.get('toolResult') or {}).get('resultType')
if result:
    rec['result'] = result

log_dir = os.path.join(proj, '.buddy-council', 'logs')
os.makedirs(log_dir, exist_ok=True)
path = os.path.join(log_dir, 'tool-log-' + datetime.date.today().isoformat() + '.jsonl')
with open(path, 'a') as f:
    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
" 2>/dev/null

exit 0
