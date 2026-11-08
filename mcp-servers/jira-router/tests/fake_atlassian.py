"""A stand-in for mcp-atlassian: it reads --env-file the way mcp-atlassian does, and answers every call with what
it was given — the site, the key, its process, its environment — so the router's tests can see where each key went.

    python fake_atlassian.py --env-file FILE --transport stdio
"""

import argparse
import json
import os
import re
import stat

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

ESCAPE = re.compile(r"\\([\\'\"abfnrtv])")


def read_env(path: str) -> dict[str, str]:
    """The double-quoted dotenv lines the router writes, decoded like python-dotenv does."""
    values = {}
    for line in open(path, encoding="utf-8").read().splitlines():
        name, _, value = line.partition("=")
        if value.startswith('"') and value.endswith('"'):
            value = ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), value[1:-1])
        values[name] = value
    return values


parser = argparse.ArgumentParser()
parser.add_argument("--env-file", required=True)
parser.add_argument("--transport", default="stdio")
args = parser.parse_args()
ENV_FILE_MODE = oct(stat.S_IMODE(os.stat(args.env_file).st_mode))
INHERITED = {name: value for name, value in os.environ.items() if name.startswith(("JIRA_", "CONFLUENCE_", "ATLASSIAN_"))}
os.environ.update(read_env(args.env_file))  # mcp-atlassian: load_dotenv(env_file, override=True)

server = Server("fake-atlassian")
NAMES = [entry["name"] for entry in json.load(open(os.path.join(os.path.dirname(__file__), "..", "tools.json")))]


@server.list_tools()
async def list_tools():
    return [types.Tool(name=name, inputSchema={"type": "object"}) for name in NAMES]


@server.call_tool(validate_input=False)
async def call_tool(name, arguments):
    if arguments.get("crash"):
        os._exit(3)
    if arguments.get("sleep"):
        await anyio.sleep(float(arguments["sleep"]))
    if arguments.get("fail"):
        return types.CallToolResult(content=[types.TextContent(type="text", text="Error: Jira said no")], isError=True)
    report = {
        "tool": name,
        "arguments": arguments,
        "pid": os.getpid(),
        "env_file": args.env_file,
        "env_file_mode": ENV_FILE_MODE,
        "inherited": INHERITED,
        "jira": {k: v for k, v in os.environ.items() if k.startswith(("JIRA_", "CONFLUENCE_", "ATLASSIAN_"))},
        "toolsets": os.environ.get("TOOLSETS"),
        "enabled_tools": os.environ.get("ENABLED_TOOLS"),
        "banner": os.environ.get("FASTMCP_SHOW_SERVER_BANNER"),
        "cwd": os.getcwd(),
    }
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(report))],
                                structuredContent={"result": "a structured copy the router must drop"})


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


anyio.run(main)
