#!/bin/bash
# PostToolUse hook: append a one-line JSONL record of every tool call to
# .buddy-council/logs/tool-log-YYYY-MM-DD.jsonl in the project, so bc runs are
# debuggable after the fact — which tools ran, in what order, against what.
#
# - Active only in projects that use the plugin (a .buddy-council/ dir exists);
#   sessions in other projects are never logged.
# - Logs metadata only: timestamp, tool name, and a redacted target summary.
#   Never logs file contents, tool responses, or credentials (common credential
#   shapes are masked before writing).
# - Claude Code only — Copilot CLI does not run plugin hooks; use
#   `copilot --log-level debug` there (see README "Debugging a bc run").

INPUT=$(cat)

[ -d .buddy-council ] || exit 0
mkdir -p .buddy-council/logs 2>/dev/null || exit 0

echo "$INPUT" | python3 -c "
import json, sys, re, datetime

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool = d.get('tool_name', '')
ti = d.get('tool_input') or {}

if tool == 'Bash':
    target = (ti.get('command') or '')[:200]
elif tool in ('Write', 'Edit', 'Read', 'NotebookEdit'):
    target = ti.get('file_path', '')
elif tool == 'Skill':
    target = ti.get('skill', '')
elif tool in ('Glob', 'Grep'):
    target = ti.get('pattern', '')
elif tool in ('Agent', 'Task'):
    target = (ti.get('description') or ti.get('subagent_type') or '')[:120]
else:
    target = ''

# Mask credential shapes that can appear in setup-time commands.
target = re.sub(r'(-u\s+|--user\s+)\S+', r'\g<1>***', target)
target = re.sub(r'(Authorization:\s*)\S+', r'\g<1>***', target, flags=re.I)
target = re.sub(r'(api[_-]?key\"?\s*[:=]\s*\"?)[^\s\",]+', r'\g<1>***', target, flags=re.I)
target = re.sub(r'(token\"?\s*[:=]\s*\"?)[^\s\",]+', r'\g<1>***', target, flags=re.I)

rec = {
    'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    'session': (d.get('session_id') or '')[:8],
    'tool': tool,
    'target': target,
}
path = '.buddy-council/logs/tool-log-' + datetime.date.today().isoformat() + '.jsonl'
with open(path, 'a') as f:
    f.write(json.dumps(rec, ensure_ascii=False) + '\n')
" 2>/dev/null

exit 0
