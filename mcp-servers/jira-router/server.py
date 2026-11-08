"""Jira router — the plugin's `atlassian` connector (ADR 0008).

mcp-atlassian, pinned below, talks to one Jira site per process: the one its JIRA_URL names. This router serves
mcp-atlassian's own jira_* tools, each with an optional `site`, and hands every call to a copy of mcp-atlassian
for that site — started from the site's key on its first call, then kept for the calls after it. The tools'
schemas come from tools.json, a snapshot of mcp-atlassian, so they are listed before any key exists.

The router never talks to Jira itself: it has no HTTP client, only the copies do. Each copy gets its site's key
in an env file that exists only while the copy starts (mode 600, in a private folder), and an environment with
every other Jira, Confluence and mcp-atlassian setting taken out, so a key only ever reaches the site it was saved
for. Keys come from the plugin's key store (scripts/bc_keys.py) and are looked up on every call: a key saved
while the assistant runs works on the next call, and a replaced one restarts its site's copy. A call without
`site` uses this computer's only Jira key.

    uv run --directory mcp-servers/jira-router python server.py              serve MCP over stdio
    uv run --directory mcp-servers/jira-router python server.py --snapshot   rewrite tools.json from mcp-atlassian
"""

import argparse
import asyncio
import atexit
import contextlib
import importlib.util
import json
import os
import re
import shutil
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

try:
    _GROUP = BaseExceptionGroup
except NameError:  # Python 3.10, where anyio brings the backport
    from exceptiongroup import BaseExceptionGroup as _GROUP

PACKAGE = "mcp-atlassian@0.23.1"
TOOLSETS = "default,jira_agile,jira_links,jira_projects,jira_users"
ENABLED_TOOLS = (
    "jira_get_issue", "jira_search", "jira_create_issue", "jira_update_issue", "jira_add_comment",
    "jira_get_transitions", "jira_transition_issue", "jira_search_fields", "jira_get_agile_boards",
    "jira_get_board_issues", "jira_get_sprints_from_board", "jira_get_sprint_issues", "jira_add_issues_to_sprint",
    "jira_get_link_types", "jira_create_issue_link", "jira_get_all_projects", "jira_get_project_issue_types",
    "jira_get_create_fields", "jira_get_project_fields", "jira_get_user_profile", "jira_search_assignable_users",
)
SNAPSHOT = Path(__file__).with_name("tools.json")
SITE_HELP = ("The Jira site to use: its address or host, e.g. https://acme.atlassian.net — the project's "
             "jira.base_url. Leave it out only when this computer has one Jira site.")
START_WAIT_SECONDS = 45.0  # a call waits this long for its site's copy; a slower first start carries on behind it
# What a copy never inherits from the assistant: any Jira, Confluence or Atlassian setting (another site's, maybe),
# and anything that changes how mcp-atlassian serves — MCP_LOGGING_STDOUT would corrupt the stdio stream.
PRIVATE_ENV = re.compile(r"^(JIRA_|CONFLUENCE_|ATLASSIAN_|MCP_|FASTMCP_|READ_ONLY_MODE$|ENABLED_TOOLS$|TOOLSETS$"
                         r"|TRANSPORT$|PORT$|HOST$|STATELESS$|STREAMABLE_HTTP_PATH$|VIRTUAL_ENV$)")
QUIET_ENV = {"FASTMCP_SHOW_SERVER_BANNER": "false", "FASTMCP_CHECK_FOR_UPDATES": "off"}
COMMAND: list[str] | None = None  # tests start a stand-in here instead of mcp-atlassian


class RouterError(RuntimeError):
    """A call the router could not hand on, worded for the person who has to fix it. Never carries a key."""


def _key_store():
    """scripts/bc_keys.py, imported by path from the plugin root this server ships in."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "bc_keys.py"
    spec = importlib.util.spec_from_file_location("bc_keys", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bc_keys = _key_store()


# ---------------------------------------------------------------------------
# One mcp-atlassian copy per site
# ---------------------------------------------------------------------------

_PRIVATE_DIR: Path | None = None
PRIVATE_PREFIX = "bc-atlassian-"


def _private_dir() -> Path:
    """A folder only this user can open (mkdtemp makes it 700), for the env files and as the copies' cwd. It is
    named for this process, so a later router can tell when its owner is gone."""
    global _PRIVATE_DIR
    if _PRIVATE_DIR is None or not _PRIVATE_DIR.is_dir():
        _sweep()
        _PRIVATE_DIR = Path(tempfile.mkdtemp(prefix=f"{PRIVATE_PREFIX}{os.getpid()}-"))
        atexit.register(shutil.rmtree, _PRIVATE_DIR, True)
    return _PRIVATE_DIR


def _sweep() -> None:
    """Remove what routers that were killed left behind — a key file, if a copy was still starting."""
    for folder in Path(tempfile.gettempdir()).glob(f"{PRIVATE_PREFIX}*"):
        owner = re.fullmatch(rf"{PRIVATE_PREFIX}(\d+)-[a-z0-9_]+", folder.name)
        if not owner or int(owner.group(1)) == os.getpid():
            continue
        try:
            os.kill(int(owner.group(1)), 0)
        except ProcessLookupError:
            shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass  # alive, as another user's process


def _end_cleanly_on(signum: int) -> None:
    """On SIGTERM or SIGHUP — how an assistant usually stops its servers — remove the private folder before going,
    since atexit doesn't run when a signal ends the process."""
    def handler(number: int, frame: Any) -> None:
        if _PRIVATE_DIR is not None:
            shutil.rmtree(_PRIVATE_DIR, ignore_errors=True)
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)

    signal.signal(signum, handler)


def _command() -> list[str]:
    if COMMAND:
        return list(COMMAND)
    uv = os.environ.get("BC_UV") or os.environ.get("UV") or shutil.which("uv")
    if not uv:
        raise RouterError("uv was not found, so mcp-atlassian cannot start. Install uv (https://docs.astral.sh/uv/), "
                          "then restart the assistant.")
    return [uv, "tool", "run", "--quiet", PACKAGE]


def _quote(value: str, what: str) -> str:
    if "\n" in value or "\r" in value:
        raise RouterError(f"The {what} saved for this Jira site holds a line break; save it again with /bc:setup.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _env_file(key: dict[str, Any], extra: dict[str, str] | None = None) -> Path:
    """The site's settings for mcp-atlassian, in the dotenv form its --env-file reads, private to this user."""
    values = {"JIRA_URL": key["url"]}
    if key.get("personal_token"):
        values["JIRA_PERSONAL_TOKEN"] = key["personal_token"]
    else:
        values.update(JIRA_USERNAME=key["username"], JIRA_API_TOKEN=key["api_token"])
    if key.get("ssl_verify") is False:
        values["JIRA_SSL_VERIFY"] = "false"
    values.update(TOOLSETS=TOOLSETS, ENABLED_TOOLS=",".join(ENABLED_TOOLS), **(extra or {}))
    fd, name = tempfile.mkstemp(prefix="site-", suffix=".env", dir=str(_private_dir()))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(f"{field}={_quote(value, field)}\n" for field, value in values.items())
    return Path(name)


def _child_env() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not PRIVATE_ENV.match(name)}
    env.update(QUIET_ENV)
    return env


class _Copy:
    """One mcp-atlassian process serving one site, kept for the calls after the first."""

    def __init__(self, key: dict[str, Any], extra_env: dict[str, str] | None = None):
        self.key = key
        self.session: ClientSession | None = None
        self.error: BaseException | None = None
        self.ended = False  # its output ended: the process stopped, maybe between calls
        self.ready = asyncio.Event()
        self.stopping = asyncio.Event()
        self.finished = asyncio.Event()
        self.task = asyncio.ensure_future(self._run(extra_env or {}))

    async def _run(self, extra_env: dict[str, str]) -> None:
        env_file: Path | None = None
        try:
            command = _command()
            env_file = _env_file(self.key)
            env = {**_child_env(), **extra_env}
            params = StdioServerParameters(
                command=command[0], args=[*command[1:], "--env-file", str(env_file), "--transport", "stdio"],
                env=env, cwd=str(_private_dir()),
            )
            async with stdio_client(params) as (read, write), anyio.create_task_group() as group:
                answers, session_read = anyio.create_memory_object_stream(0)
                group.start_soon(self._watch, read, answers)
                async with ClientSession(session_read, write) as session:
                    await session.initialize()  # mcp-atlassian has read its env file by the time it answers
                    env_file.unlink(missing_ok=True)
                    self.session = session
                    self.ready.set()
                    await self.stopping.wait()
                group.cancel_scope.cancel()
        except Exception as exc:  # noqa: BLE001 — reported to the call that waits for this copy
            self.error = exc
        finally:
            if env_file is not None:
                env_file.unlink(missing_ok=True)
            self.session = None
            self.ready.set()
            self.finished.set()

    async def _watch(self, read, answers) -> None:
        """Hand the copy's output to its session, and notice when it ends: the copy has stopped, so the next call
        starts a new one instead of writing to a closed pipe. Its session still answers any call that was waiting."""
        with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
            async with answers:
                async for message in read:
                    await answers.send(message)
        self.ended = True

    @property
    def gone(self) -> bool:
        return self.ended or self.task.done() or self.stopping.is_set()

    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """The copy's answer to one call. A copy closed while the call waits ends the call instead of leaving it
        hanging: closing cancels the session before it can answer the calls it was still waiting on."""
        session = self.session
        if session is None:
            raise ConnectionError("it stopped")
        answer = asyncio.ensure_future(session.call_tool(name, arguments))
        finished = asyncio.ensure_future(self.finished.wait())
        try:
            await asyncio.wait({answer, finished}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            finished.cancel()
            if not answer.done():
                answer.cancel()
        if answer.cancelled():
            raise ConnectionError("it stopped before it answered")
        return answer.result()

    async def close(self) -> None:
        self.stopping.set()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(self.task), 10)
        if not self.task.done():
            self.task.cancel()


_COPIES: dict[str, _Copy] = {}


def _describe(exc: BaseException | None) -> str:
    if isinstance(exc, _GROUP) and exc.exceptions:
        return _describe(exc.exceptions[0])
    return f"{type(exc).__name__}: {exc}" if exc is not None else "it stopped"


async def _copy_for(key: dict[str, Any]) -> _Copy:
    site = key["site"]
    copy = _COPIES.get(site)
    if copy is not None and (copy.key != key or copy.gone):
        asyncio.ensure_future(copy.close())  # a replaced key, or a copy that stopped: start afresh
        copy = None
    if copy is None:
        copy = _COPIES[site] = _Copy(key)
    try:
        await asyncio.wait_for(copy.ready.wait(), START_WAIT_SECONDS)
    except asyncio.TimeoutError:
        raise RouterError(f"mcp-atlassian is still starting for {site} — its first start downloads it. "
                          "Call again in a moment.") from None
    if copy.session is None:
        if _COPIES.get(site) is copy:
            del _COPIES[site]
        raise RouterError(f"mcp-atlassian could not start for {site} ({_describe(copy.error)}).")
    return copy


async def close_all() -> None:
    copies = list(_COPIES.values())
    _COPIES.clear()
    await asyncio.gather(*(copy.close() for copy in copies))


# ---------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------


def load_tools() -> list[types.Tool]:
    """mcp-atlassian's tools from the snapshot, each taking an optional `site` as well."""
    tools = []
    for entry in json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else []:
        schema = json.loads(json.dumps(entry["inputSchema"]))
        schema.setdefault("properties", {})["site"] = {"type": "string", "description": SITE_HELP}
        tools.append(types.Tool(name=entry["name"], title=entry.get("title"), description=entry.get("description"),
                                inputSchema=schema, annotations=entry.get("annotations")))
    return tools


TOOLS = load_tools()
TOOL_NAMES = {tool.name for tool in TOOLS}
READ_ONLY = {tool.name for tool in TOOLS if tool.annotations and tool.annotations.readOnlyHint}


async def route(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
    """Hand one call to its site's copy of mcp-atlassian and return its answer, once, as text."""
    if name not in TOOL_NAMES:
        raise RouterError(f"Unknown tool {name}.")
    arguments = dict(arguments or {})
    site = arguments.pop("site", None)
    try:
        key = bc_keys.read_key(site, "jira")
    except bc_keys.KeyStoreError as exc:
        raise RouterError(str(exc)) from None
    copy = await _copy_for(key)
    try:
        result = await copy.call(name, arguments)
    except Exception as exc:  # noqa: BLE001 — the copy broke; the next call starts a fresh one
        asyncio.ensure_future(copy.close())
        if _COPIES.get(key["site"]) is copy:
            del _COPIES[key["site"]]
        caution = "" if name in READ_ONLY else f" It may have made the change first, so check before calling {name} again."
        raise RouterError(f"mcp-atlassian stopped answering for {key['site']} ({_describe(exc)}); "
                          f"call again to restart it.{caution}") from None
    # The text only: mcp-atlassian also sends a structured copy of it, which would double every answer.
    return types.CallToolResult(content=result.content, isError=result.isError)


server = Server("atlassian")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    return await route(name, arguments)


async def serve() -> None:
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await close_all()


async def snapshot() -> int:
    """Rewrite tools.json from mcp-atlassian itself, started for a made-up site. It never connects to Jira."""
    key = {"site": "example.atlassian.net", "url": "https://example.atlassian.net",
           "username": "snapshot@example.com", "api_token": "not-a-token"}
    copy = _Copy(key, {"PYTHONHASHSEED": "0"})  # mcp-atlassian builds some defaults from sets
    try:
        await asyncio.wait_for(copy.ready.wait(), 300)
        if copy.session is None:
            raise RouterError(f"mcp-atlassian could not start ({_describe(copy.error)}).")
        listed = {tool.name: tool for tool in (await copy.session.list_tools()).tools}
    finally:
        await copy.close()
    missing = [name for name in ENABLED_TOOLS if name not in listed]
    if missing:
        raise RouterError(f"{PACKAGE} has no {', '.join(missing)}.")
    entries = [{"name": name, "title": listed[name].title, "description": listed[name].description,
                "inputSchema": listed[name].inputSchema,
                "annotations": listed[name].annotations.model_dump(exclude_none=True) if listed[name].annotations else None}
               for name in sorted(ENABLED_TOOLS)]
    SNAPSHOT.write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} tools from {PACKAGE} to {SNAPSHOT}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The plugin's atlassian connector. Without --snapshot it serves MCP over stdio.")
    parser.add_argument("--snapshot", action="store_true", help=f"rewrite tools.json from {PACKAGE}")
    args = parser.parse_args(argv)
    if args.snapshot:
        return asyncio.run(snapshot())
    for signum in (signal.SIGTERM, signal.SIGHUP):
        _end_cleanly_on(signum)
    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
