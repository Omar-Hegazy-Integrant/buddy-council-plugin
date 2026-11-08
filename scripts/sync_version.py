#!/usr/bin/env python3
"""Copy the plugin's version from the root plugin.json into every other manifest that carries one.

The root plugin.json (the Agent Plugins manifest) is the one place the version is set. Claude Code reads only
.claude-plugin/plugin.json, so this script copies the version there, rewriting just the "version" value to keep
each file's key order and formatting.

    python3 scripts/sync_version.py           # copy the version
    python3 scripts/sync_version.py --check   # change nothing; exit 1 if any manifest differs

Standard library only; Python 3.9+.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "plugin.json"
TARGETS = [ROOT / ".claude-plugin" / "plugin.json"]
VERSION_FIELD = re.compile(r'("version"\s*:\s*")[^"]*(")')


def source_version(source=SOURCE):
    version = json.loads(source.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not version:
        raise SystemExit(f"{source} has no version.")
    return version


def sync(source=SOURCE, targets=None, check=False):
    """Return the manifests whose version differed from the source's, fixing them unless check is set."""
    version = source_version(source)
    stale = []
    for target in TARGETS if targets is None else targets:
        text = target.read_text(encoding="utf-8")
        current = json.loads(text).get("version")
        if current == version:
            continue
        stale.append((target, current))
        if check:
            continue
        updated, count = VERSION_FIELD.subn(lambda m: m.group(1) + version + m.group(2), text, count=1)
        if not count or json.loads(updated).get("version") != version:
            raise SystemExit(f"{target} has no version field to replace.")
        target.write_text(updated, encoding="utf-8")
    return version, stale


def main(argv=None):
    parser = argparse.ArgumentParser(description="Copy the root plugin.json version into the other manifests.")
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if any manifest differs")
    args = parser.parse_args(argv)
    version, stale = sync(check=args.check)
    for target, current in stale:
        where = target.relative_to(ROOT)
        if args.check:
            print(f"{where} is {current}, plugin.json is {version}. Run python3 scripts/sync_version.py.",
                  file=sys.stderr)
        else:
            print(f"{where}: {current} -> {version}")
    if not stale:
        print(f"Every manifest is at {version}.")
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
