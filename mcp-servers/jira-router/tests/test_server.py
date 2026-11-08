"""Tests for the Jira router: two Jira sites side by side, each call handed to its own site's copy of mcp-atlassian.

    uv run --directory mcp-servers/jira-router python -m unittest discover -s tests -v

A stand-in (tests/fake_atlassian.py) takes mcp-atlassian's place and reports the site, key and environment each
copy received. Standard library only, plus the mcp SDK the router already uses.
"""

import ast
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as router  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

FAKE = [sys.executable, str(Path(__file__).with_name("fake_atlassian.py"))]
CLOUD, SERVER = "https://acme.atlassian.net", "https://jira.acme.test/jira"


def setUpModule():
    # Keys are read on every call, from a throwaway home: a developer's real ~/.buddy-council is never read.
    global _HOME, _ENV
    _HOME = tempfile.TemporaryDirectory()
    _ENV = mock.patch.dict(os.environ, {"HOME": _HOME.name, "BC_HOME": str(Path(_HOME.name, ".buddy-council"))})
    _ENV.start()


def tearDownModule():
    _ENV.stop()
    _HOME.cleanup()


def save(url, **fields):
    return router.bc_keys.write_key("jira", url, fields)


class RouterTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = router.bc_keys.keys_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for path in folder.glob("*"):
            path.unlink()
        (folder / router.bc_keys.MARKER).write_text("{}")  # nothing to migrate in a throwaway home
        patch = mock.patch.object(router, "COMMAND", FAKE)
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        await router.close_all()

    async def call(self, name, **arguments):
        result = await router.route(name, arguments)
        self.assertFalse(result.isError, result.content)
        self.assertIsNone(result.structuredContent, "the text is sent once")
        [block] = result.content
        return json.loads(block.text)

    async def refused(self, name, **arguments):
        with self.assertRaises(router.RouterError) as ctx:
            await router.route(name, arguments)
        return str(ctx.exception)


class TwoSites(RouterTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        save(CLOUD, username="qa@acme.com", api_token="cloud-token")
        save(SERVER, personal_token="server-pat", ssl_verify=False)

    async def test_each_site_gets_its_own_copy_with_only_its_own_key(self):
        cloud = await self.call("jira_get_user_profile", site=CLOUD, user_identifier="qa@acme.com")
        server = await self.call("jira_get_user_profile", site="jira.acme.test/jira", user_identifier="currentUser()")
        self.assertEqual(cloud["jira"], {"JIRA_URL": CLOUD, "JIRA_USERNAME": "qa@acme.com", "JIRA_API_TOKEN": "cloud-token"})
        self.assertEqual(server["jira"], {"JIRA_URL": SERVER, "JIRA_PERSONAL_TOKEN": "server-pat", "JIRA_SSL_VERIFY": "false"})
        self.assertNotEqual(cloud["pid"], server["pid"])
        self.assertEqual(cloud["arguments"], {"user_identifier": "qa@acme.com"}, "site is the router's, never forwarded")

    async def test_a_sites_copy_is_kept_for_the_next_calls(self):
        first = await self.call("jira_search", site=CLOUD, jql="project = CWA")
        links = ("https://acme.atlassian.net/jira/software/projects/CWA/boards/42", "acme.atlassian.net")
        for site in links:
            again = await self.call("jira_get_board_issues", site=site, board_id="42", jql="")
            self.assertEqual(again["pid"], first["pid"], site)

    async def test_both_sites_at_once_keep_their_keys_apart(self):
        calls = [self.call("jira_get_issue", site=site, issue_key="CWA-1") for site in [CLOUD, SERVER] * 4]
        reports = await asyncio.gather(*calls)
        for site, report in zip([CLOUD, SERVER] * 4, reports):
            self.assertEqual(report["jira"]["JIRA_URL"], site)
            secrets = {"cloud-token", "server-pat"} - ({"cloud-token"} if site == CLOUD else {"server-pat"})
            self.assertFalse(secrets & set(report["jira"].values()), f"{site} saw another site's key")
        self.assertEqual(len({r["pid"] for r in reports}), 2, "one copy per site")

    async def test_no_site_with_two_keys_starts_nothing(self):
        message = await self.refused("jira_get_issue", issue_key="CWA-1")
        self.assertIn("acme.atlassian.net", message)
        self.assertIn("jira.acme.test/jira", message)
        self.assertNotIn("cloud-token", message)
        self.assertEqual(router._COPIES, {})

    async def test_an_unknown_site_starts_nothing(self):
        for site in ("https://other.atlassian.net", "jira.acme.test/jira2", "acme.atlassian.net.evil.example"):
            self.assertIn("/bc:setup", await self.refused("jira_get_issue", site=site, issue_key="CWA-1"), site)
        self.assertEqual(router._COPIES, {})

    async def test_a_copy_inherits_no_other_jira_settings(self):
        leak = {"JIRA_URL": "https://evil.example", "JIRA_PERSONAL_TOKEN": "leaked", "CONFLUENCE_API_TOKEN": "leaked",
                "ATLASSIAN_OAUTH_ACCESS_TOKEN": "leaked", "MCP_LOGGING_STDOUT": "true", "READ_ONLY_MODE": "true"}
        with mock.patch.dict(os.environ, leak):
            report = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertEqual(report["inherited"], {}, "the environment carries no Jira setting at all")
        self.assertNotIn("leaked", json.dumps(report))
        self.assertEqual(report["banner"], "false")

    async def test_the_key_file_is_private_and_gone_once_the_copy_answers(self):
        report = await self.call("jira_get_issue", site=SERVER, issue_key="CWA-1")
        self.assertEqual(report["env_file_mode"], "0o600")
        self.assertFalse(Path(report["env_file"]).exists())
        self.assertEqual(oct(os.stat(report["cwd"]).st_mode & 0o777), "0o700")
        self.assertEqual(report["toolsets"], router.TOOLSETS)
        self.assertEqual(report["enabled_tools"].split(","), list(router.ENABLED_TOOLS))

    async def test_a_replaced_key_restarts_only_that_sites_copy(self):
        before = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        other = await self.call("jira_get_issue", site=SERVER, issue_key="CWA-1")
        save(CLOUD, username="qa@acme.com", api_token="rotated-token")
        after = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertNotEqual(after["pid"], before["pid"])
        self.assertEqual(after["jira"]["JIRA_API_TOKEN"], "rotated-token")
        self.assertEqual((await self.call("jira_get_issue", site=SERVER, issue_key="CWA-1"))["pid"], other["pid"])

    async def test_a_key_saved_mid_session_works_on_the_next_call(self):
        site = "https://third.atlassian.net"
        self.assertIn("/bc:setup", await self.refused("jira_get_issue", site=site, issue_key="X-1"))
        save(site, username="qa@third.com", api_token="third-token")
        self.assertEqual((await self.call("jira_get_issue", site=site, issue_key="X-1"))["jira"]["JIRA_URL"], site)

    async def test_a_forgotten_key_stops_being_used(self):
        await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        router.bc_keys.forget(CLOUD)
        self.assertIn("No Jira key is saved", await self.refused("jira_get_issue", site=CLOUD, issue_key="CWA-1"))

    async def test_a_copy_that_stops_is_started_again_on_the_next_call(self):
        first = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertIn("call again", await self.refused("jira_get_issue", site=CLOUD, issue_key="CWA-1", crash=True))
        again = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertNotEqual(again["pid"], first["pid"])

    async def test_a_copy_that_stops_between_calls_is_replaced_by_the_next_call(self):
        first = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        other = await self.call("jira_get_issue", site=SERVER, issue_key="CWA-1")
        copy = router._COPIES["acme.atlassian.net"]
        os.kill(first["pid"], signal.SIGKILL)
        for _ in range(200):  # the router sees the copy's output end
            if copy.gone:
                break
            await asyncio.sleep(0.05)
        again = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertNotEqual(again["pid"], first["pid"])
        self.assertEqual((await self.call("jira_get_issue", site=SERVER, issue_key="CWA-1"))["pid"], other["pid"])

    async def test_a_call_waiting_on_a_copy_that_is_replaced_ends_instead_of_hanging(self):
        await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        waiting = asyncio.ensure_future(router.route("jira_get_issue", {"site": CLOUD, "issue_key": "CWA-1", "sleep": 60}))
        await asyncio.sleep(0.5)  # the slow call is with the first copy
        save(CLOUD, username="qa@acme.com", api_token="rotated-token")
        after = await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1")
        self.assertEqual(after["jira"]["JIRA_API_TOKEN"], "rotated-token")
        with self.assertRaises(router.RouterError) as ctx:
            await asyncio.wait_for(waiting, 20)
        self.assertIn("call again", str(ctx.exception))

    async def test_a_change_cut_off_by_a_stopping_copy_says_to_check_before_retrying(self):
        change = await self.refused("jira_create_issue", site=CLOUD, project_key="CWA", summary="x", issue_type="Task",
                                    crash=True)
        self.assertIn("check before calling jira_create_issue again", change)
        self.assertNotIn("check before", await self.refused("jira_get_issue", site=CLOUD, issue_key="CWA-1", crash=True))

    async def test_jiras_own_errors_come_back_as_errors(self):
        result = await router.route("jira_get_issue", {"site": CLOUD, "issue_key": "CWA-1", "fail": True})
        self.assertTrue(result.isError)
        self.assertEqual(result.content[0].text, "Error: Jira said no")

    async def test_a_copy_that_cannot_start_says_so(self):
        with mock.patch.object(router, "COMMAND", [sys.executable, "-c", "import sys; sys.exit(7)"]):
            self.assertIn("could not start for acme.atlassian.net",
                          await self.refused("jira_get_issue", site=CLOUD, issue_key="CWA-1"))
        self.assertEqual((await self.call("jira_get_issue", site=CLOUD, issue_key="CWA-1"))["jira"]["JIRA_URL"], CLOUD)


class OneSite(RouterTestCase):
    async def test_no_site_uses_this_computers_only_key(self):
        self.assertIn("No Jira key is saved on this computer", await self.refused("jira_get_user_profile", user_identifier="me"))
        save(SERVER, personal_token="server-pat")
        self.assertEqual((await self.call("jira_get_user_profile", user_identifier="me"))["jira"]["JIRA_URL"], SERVER)


class Protocol(RouterTestCase):
    async def test_the_tools_are_mcp_atlassians_each_with_an_optional_site(self):
        snapshot = {entry["name"]: entry for entry in json.loads(router.SNAPSHOT.read_text())}
        self.assertEqual(set(snapshot), set(router.ENABLED_TOOLS))
        async with create_connected_server_and_client_session(router.server) as client:
            tools = (await client.list_tools()).tools
        self.assertEqual({tool.name for tool in tools}, set(router.ENABLED_TOOLS))
        for tool in tools:
            expected = json.loads(json.dumps(snapshot[tool.name]["inputSchema"]))
            expected["properties"]["site"] = {"type": "string", "description": router.SITE_HELP}
            self.assertEqual(tool.inputSchema, expected, tool.name)
            self.assertNotIn("site", tool.inputSchema.get("required", []))
            self.assertIn("jira.base_url", tool.inputSchema["properties"]["site"]["description"])
            self.assertIsNone(tool.outputSchema, "answers are sent once, as text")
        read = {tool.name for tool in tools if tool.annotations and tool.annotations.readOnlyHint}
        self.assertIn("jira_get_issue", read)
        self.assertNotIn("jira_create_issue", read)

    async def test_a_call_through_mcp_reaches_its_site(self):
        save(CLOUD, username="qa@acme.com", api_token="cloud-token")
        async with create_connected_server_and_client_session(router.server) as client:
            result = await client.call_tool("jira_get_user_profile", {"site": CLOUD, "user_identifier": "qa@acme.com"})
            refused = await client.call_tool("jira_get_user_profile", {"user_identifier": "qa", "bogus": 1})
        self.assertFalse(result.isError)
        self.assertEqual(json.loads(result.content[0].text)["jira"]["JIRA_URL"], CLOUD)
        self.assertIsNone(result.structuredContent)
        self.assertTrue(refused.isError, "arguments are checked against mcp-atlassian's schema first")


class Construction(unittest.TestCase):
    def test_the_router_never_talks_to_jira_itself(self):
        tree = ast.parse(Path(router.__file__).read_text())
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertFalse(imported & {"httpx", "requests", "urllib", "http", "aiohttp", "socket", "ssl"}, imported)

    def test_mcp_atlassian_is_pinned_and_started_through_uv(self):
        with mock.patch.dict(os.environ, {"BC_UV": "/opt/uv/bin/uv"}):
            self.assertEqual(router._command(), ["/opt/uv/bin/uv", "tool", "run", "--quiet", "mcp-atlassian@0.23.1"])

    def test_a_key_with_a_line_break_is_refused(self):
        with self.assertRaises(router.RouterError) as ctx:
            router._env_file({"url": CLOUD, "username": "qa", "api_token": "a\nJIRA_URL=https://evil.example"})
        self.assertNotIn("evil", str(ctx.exception))

    def test_a_router_stopped_by_its_assistant_leaves_no_key_file_behind(self):
        script = (f"import signal, sys, time; sys.path.insert(0, {str(Path(router.__file__).parent)!r}); import server; "
                  "folder = server._private_dir(); (folder / 'site-x.env').write_text('JIRA_API_TOKEN=\"t\"'); "
                  "server._end_cleanly_on(signal.SIGTERM); print(folder, flush=True); time.sleep(60)")
        process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
        folder = Path(process.stdout.readline().decode().strip())
        self.assertTrue((folder / "site-x.env").exists())
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(20), -signal.SIGTERM, "it still ends the way SIGTERM means")
        process.stdout.close()
        self.assertFalse(folder.exists())

    def test_what_a_killed_router_left_is_swept_by_the_next_one(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(tempfile, "tempdir", tmp):
            ended = subprocess.Popen([sys.executable, "-c", "pass"])
            ended.wait()
            gone = Path(tmp, f"{router.PRIVATE_PREFIX}{ended.pid}-ab12_cd3")
            gone.mkdir()
            (gone / "site-x.env").write_text('JIRA_API_TOKEN="t"')
            kept = [Path(tmp, f"{router.PRIVATE_PREFIX}{pid}-ab12_cd3") for pid in (os.getpid(), os.getppid())]
            kept.append(Path(tmp, f"{router.PRIVATE_PREFIX}someone-else"))
            for folder in kept:
                folder.mkdir()
            router._sweep()
            self.assertFalse(gone.exists())
            self.assertTrue(all(folder.exists() for folder in kept))


if __name__ == "__main__":
    unittest.main()
