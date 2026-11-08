"""Tests for bc_tools.py — installing, recording, and proving the Plugin Tools. Standard library only.

    python3 -m unittest discover -s scripts/tests -v

They must pass on Python 3.9 (macOS's /usr/bin/python3) as well as current versions. Nothing here downloads from
the internet or touches a phone: downloads come from file:// URLs, and the MCP server is a small fake.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import bc_tools  # noqa: E402

FAKE_SERVER = textwrap.dedent(
    """
    import json, os, sys
    mode = os.environ.get("FAKE_MODE", "ok")
    names = os.environ.get("FAKE_TOOLS", "appium_session_management,appium_get_page_source,generate_locators").split(",")
    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()
    for line in sys.stdin:
        message = json.loads(line)
        method, ident = message.get("method"), message.get("id")
        if method is None:
            continue  # the client's answer to our ping
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": "server-1", "method": "ping"})
            if mode == "die":
                sys.exit(3)
            send({"jsonrpc": "2.0", "id": ident, "result": {"serverInfo": {"name": "fake", "version": "9.9.9"}}})
        elif method == "tools/list":
            cursor = (message.get("params") or {}).get("cursor")
            half = len(names) // 2
            if cursor:
                send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [{"name": n} for n in names[half:]]}})
            else:
                send({"jsonrpc": "2.0", "id": ident, "result": {"tools": [{"name": n} for n in names[:half]], "nextCursor": "2"}})
        elif method == "tools/call":
            name = message["params"]["name"]
            arguments = message["params"]["arguments"]
            with open(os.environ["FAKE_CALLS"], "a") as calls:
                calls.write(json.dumps({"name": name, "arguments": arguments}) + "\\n")
            if name == "appium_session_management" and arguments.get("action") == "create" and mode == "fail-create":
                send({"jsonrpc": "2.0", "id": ident, "result": {"isError": True, "content": [
                    {"type": "text", "text": "Failed to create session. xcodebuild failed with code 65"}]}})
            elif name == "appium_get_page_source":
                send({"jsonrpc": "2.0", "id": ident, "result": {"content": [
                    {"type": "text", "text": json.dumps({"filepath": "/tmp/page-source-1.xml", "bytes": 4321})}]}})
            else:
                send({"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": "done"}]}})
    """
)


class Sandbox(unittest.TestCase):
    """Every test gets its own home folder, ~/.buddy-council, and pins file."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.home = self.tmp / "bc"
        self.record = self.home / "tools" / "appium-mcp.json"
        env = {"BC_HOME": str(self.home), "HOME": str(self.tmp), "NPM_CONFIG_USERCONFIG": str(self.tmp / "npmrc")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, str(self.tmp), True)

    def write(self, relative, text, mode=None):
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode is not None:
            path.chmod(mode)
        return path


class Downloads(Sandbox):
    def test_a_matching_file_is_kept_and_a_wrong_one_never_lands(self):
        source = self.write("mirror/file.bin", "payload")
        good = hashlib.sha256(b"payload").hexdigest()
        target = self.tmp / "downloads" / "file.bin"
        bc_tools.download(source.as_uri(), target, good, "test")
        self.assertEqual(target.read_text(), "payload")
        with self.assertRaises(bc_tools.ToolError) as caught:
            bc_tools.download(source.as_uri(), self.tmp / "downloads" / "other.bin", "0" * 64, "test")
        self.assertIn("isn't the pinned file", str(caught.exception))
        self.assertFalse((self.tmp / "downloads" / "other.bin").exists())
        self.assertFalse((self.tmp / "downloads" / "other.bin.part").exists())

    def test_a_missing_file_is_reported_without_retrying_forever(self):
        with self.assertRaises(bc_tools.ToolError):
            bc_tools.download((self.tmp / "nowhere.bin").as_uri(), self.tmp / "x.bin", "0" * 64, "test")

    def test_npm_settings_read_npmrc_and_npm_config_variables(self):
        self.write("npmrc", "; comment\nhttps-proxy=http://proxy.corp:8080\ncafile=${CORP_CA}\nstrict-ssl=true\n")
        with mock.patch.dict(os.environ, {"CORP_CA": "/etc/corp.pem", "npm_config_strict_ssl": "false"}):
            settings = bc_tools.npm_settings()
        self.assertEqual(settings["https-proxy"], "http://proxy.corp:8080")
        self.assertEqual(settings["cafile"], "/etc/corp.pem")
        self.assertEqual(settings["strict-ssl"], "false")

    def test_npms_proxy_is_used_when_nothing_else_names_one(self):
        self.write("npmrc", "proxy=http://proxy.corp:3128\n")
        with mock.patch.object(bc_tools.urllib.request, "getproxies", return_value={}):
            self.assertEqual(bc_tools.proxies()["https"], "http://proxy.corp:3128")
        with mock.patch.object(bc_tools.urllib.request, "getproxies", return_value={"https": "http://env:1"}):
            self.assertEqual(bc_tools.proxies()["https"], "http://env:1")


def tar_with(path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, data in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif kind == "link":
                info.type = tarfile.SYMTYPE
                info.linkname = data
                tar.addfile(info)
            else:
                payload = data.encode()
                info.size = len(payload)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(payload))


class Unpacking(Sandbox):
    def test_relative_links_inside_the_archive_are_fine(self):
        archive = self.tmp / "ok.tar.gz"
        tar_with(archive, [("node-v1/", "dir", None), ("node-v1/bin/", "dir", None),
                           ("node-v1/lib/npm-cli.js", "file", "x"), ("node-v1/bin/npm", "link", "../lib/npm-cli.js")])
        bc_tools.unpack_tar(archive, self.tmp / "out")
        self.assertTrue((self.tmp / "out" / "node-v1" / "bin" / "npm").is_symlink())

    def test_paths_and_links_leaving_the_folder_are_refused(self):
        for entries in ([("../evil", "file", "x")], [("/abs/evil", "file", "x")], [("a/link", "link", "../../outside")],
                        [("a/link", "link", "/etc/passwd")]):
            archive = self.tmp / "bad.tar.gz"
            tar_with(archive, entries)
            with self.assertRaises(bc_tools.ToolError):
                bc_tools.unpack_tar(archive, self.tmp / "out-bad")
        self.assertFalse((self.tmp / "evil").exists())

    def test_nothing_outside_bc_home_is_ever_deleted(self):
        outside = self.write("keep/me.txt", "x")
        with self.assertRaises(bc_tools.ToolError):
            bc_tools.remove_tree(outside.parent)
        self.assertTrue(outside.exists())


class NodeInstall(Sandbox):
    def make_node(self, version="26.13.0"):
        archive = self.tmp / "mirror" / f"v{version}" / f"node-v{version}-darwin-arm64.tar.gz"
        archive.parent.mkdir(parents=True)
        tar_with(archive, [(f"node-v{version}-darwin-arm64/", "dir", None), (f"node-v{version}-darwin-arm64/bin/", "dir", None),
                           (f"node-v{version}-darwin-arm64/bin/node", "file", f"#!/bin/sh\necho v{version}\n")])
        return archive

    def pins(self, archive, version="26.13.0"):
        lock = self.tmp / "lock"
        lock.mkdir(exist_ok=True)
        (lock / "package.json").write_text("{}")
        (lock / "package-lock.json").write_text("{}")
        pins = {"node": {"version": version, "base_url": "https://nodejs.invalid/dist", "mirror_env": "NODEJS_ORG_MIRROR",
                         "files": {"darwin-arm64": [{"name": archive.name, "sha256": bc_tools.file_sha256(archive), "mb": 1}]}},
                "appium_mcp": {"version": "1.0.0", "lockfile_dir": "lock", "entry": "node_modules/appium-mcp/dist/index.js"}}
        path = self.tmp / "pins.json"
        path.write_text(json.dumps(pins))
        return path

    def test_node_comes_from_the_mirror_and_is_checked_and_reused(self):
        archive = self.make_node()
        env = {"BC_PINS_FILE": str(self.pins(archive)), "NODEJS_ORG_MIRROR": (self.tmp / "mirror").as_uri()}
        with mock.patch.dict(os.environ, env):
            pins = bc_tools.load_pins()
            home, fresh = bc_tools.install_node(pins, "darwin-arm64")
            self.assertTrue(fresh)
            self.assertEqual(home, self.home / "tools" / "node" / "v26.13.0-darwin-arm64")
            self.assertTrue(bc_tools.node_works(home, "26.13.0"))
            self.assertFalse(any((self.home / "tools" / "node").glob(".staging-*")))
            self.assertEqual(bc_tools.install_node(pins, "darwin-arm64"), (home, False))

    def test_a_tampered_mirror_installs_nothing(self):
        archive = self.make_node()
        pins_path = self.pins(archive)
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with mock.patch.dict(os.environ, {"BC_PINS_FILE": str(pins_path), "NODEJS_ORG_MIRROR": (self.tmp / "mirror").as_uri()}):
            with self.assertRaises(bc_tools.ToolError) as caught:
                bc_tools.install_node(bc_tools.load_pins(), "darwin-arm64")
        self.assertIn("isn't the pinned file", str(caught.exception))
        self.assertFalse((self.home / "tools" / "node" / "v26.13.0-darwin-arm64").exists())


class Record(Sandbox):
    ENTRY = {"command": "/bc/node", "args": ["--use-system-ca", "/bc/appium/dist/index.js"], "env": {"NO_UI": "true"}}

    def test_the_record_changes_only_when_the_entry_does(self):
        self.assertEqual(bc_tools.save_server_record(self.ENTRY), [bc_tools.tilde(self.record)])
        self.assertEqual(json.loads(self.record.read_text()), self.ENTRY)
        self.assertEqual(bc_tools.read_server_record(), self.ENTRY)
        self.assertEqual(bc_tools.save_server_record(self.ENTRY), [], "a second run changes nothing")
        self.assertEqual(bc_tools.drop_server_record(), [bc_tools.tilde(self.record)])
        self.assertIsNone(bc_tools.read_server_record())
        self.assertEqual(bc_tools.drop_server_record(), [])

    def test_a_broken_record_reads_as_none(self):
        self.write("bc/tools/appium-mcp.json", "{ not json")
        self.assertIsNone(bc_tools.read_server_record())

    def test_no_assistant_config_is_ever_written(self):
        for name in ("claude_config", "copilot_config", "restore", "foreign_entries"):
            self.assertFalse(hasattr(bc_tools, name), name)
        source = (SCRIPTS / "bc_tools.py").read_text()
        self.assertNotIn("mcp-config.json", source)
        self.assertNotIn('".mcp.json"', source)


class Serve(Sandbox):
    """scripts/bc-mcp appium runs `serve`: the recorded appium-mcp, or an MCP server with no tools until then."""

    def serve(self, *messages):
        lines = "".join(json.dumps(message) + "\n" for message in messages)
        done = subprocess.run([sys.executable, str(SCRIPTS / "bc_tools.py"), "serve"], input=lines, capture_output=True,
                              text=True, timeout=30, env=dict(os.environ))
        return done.returncode, done.stdout, done.stderr

    def test_without_the_tools_it_answers_with_no_tools(self):
        code, out, err = self.serve(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "appium_screenshot"}},
        )
        answers = [json.loads(line) for line in out.splitlines()]
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([a["id"] for a in answers], [1, 2, 3, 4], "a notification gets no answer")
        self.assertEqual(answers[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(answers[0]["result"]["serverInfo"]["name"], "appium")
        self.assertIn("/bc:setup", answers[0]["result"]["instructions"])
        self.assertEqual((answers[1]["result"], answers[2]["result"]), ({"tools": []}, {}))
        self.assertEqual(answers[3]["error"]["code"], -32601)

    def test_with_the_tools_it_becomes_the_recorded_server(self):
        node = self.write("bc/tools/node/v1/bin/node", "#!/bin/sh\necho \"node $* NO_UI=$NO_UI\"\n", mode=0o755)
        app = self.write("bc/tools/appium-mcp/1.0.0-aaaaaaaaaaaa/node_modules/appium-mcp/dist/index.js", "")
        bc_tools.save_server_record({"command": str(node), "args": ["--use-system-ca", str(app)], "env": {"NO_UI": "true"}})
        code, out, _ = self.serve()
        self.assertEqual((code, out.strip()), (0, f"node --use-system-ca {app} NO_UI=true"))

    def test_a_record_bc_didnt_write_or_whose_files_are_gone_is_never_run(self):
        marker = self.tmp / "ran"
        script = self.write("elsewhere/node", f"#!/bin/sh\ntouch {marker}\n", mode=0o755)
        bc_tools.save_server_record({"command": str(script), "args": [], "env": {}})
        self.assertEqual(json.loads(self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1])["result"], {"tools": []})
        bc_tools.save_server_record({"command": str(self.home / "tools" / "node" / "gone" / "bin" / "node"), "args": [], "env": {}})
        self.assertEqual(json.loads(self.serve({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1])["result"], {"tools": []})
        self.assertFalse(marker.exists())


class Pruning(Sandbox):
    def test_old_pins_go_and_whatever_a_config_still_runs_stays(self):
        tools = self.home / "tools"
        for folder in ("node/v26.13.0-darwin-arm64/bin", "node/v24.0.0-darwin-arm64/bin", "appium-mcp/1.99.2-aaaaaaaaaaaa",
                       "appium-mcp/1.98.0-bbbbbbbbbbbb", "appium-mcp/1.97.0-cccccccccccc"):
            (tools / folder).mkdir(parents=True)
        bc_tools.save_server_record({"command": str(tools / "node/v26.13.0-darwin-arm64/bin/node"),
                       "args": [str(tools / "appium-mcp/1.98.0-bbbbbbbbbbbb/node_modules/appium-mcp/dist/index.js")]})
        removed = bc_tools.prune([tools / "appium-mcp/1.99.2-aaaaaaaaaaaa"])
        names = sorted(Path(item).name for item in removed)
        self.assertEqual(names, ["1.97.0-cccccccccccc", "v24.0.0-darwin-arm64"])
        self.assertTrue((tools / "appium-mcp/1.98.0-bbbbbbbbbbbb").exists(), "still recorded, so kept until the new one answers")

    def test_week_old_screen_files_and_extra_logs_go(self):
        old = self.home / "automation" / "page-sources" / "old.xml"
        new = self.home / "automation" / "page-sources" / "new.xml"
        old.parent.mkdir(parents=True)
        old.write_text("x")
        new.write_text("x")
        week = time.time() - 8 * 86400
        os.utime(old, (week, week))
        logs = bc_tools.logs_dir()
        for number in range(bc_tools.KEEP_LOGS + 5):
            (logs / f"log-{number:02d}.log").write_text("x")
        bc_tools.prune()
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertEqual(len(list(logs.iterdir())), bc_tools.KEEP_LOGS)


class ProjectReading(Sandbox):
    def test_a_java_appium_project_with_its_own_conventions(self):
        root = self.tmp / "myapp"
        self.write("myapp/build.gradle", "apply plugin: 'java'\ntestImplementation 'org.testng:testng:7'\n")
        self.write("myapp/src/test/java/pages/Login.java",
                   "import io.appium.java_client.pagefactory.iOSXCUITFindBy;\nimport io.appium.java_client.pagefactory.AndroidFindBy;\n"
                   "class Login { @iOSXCUITFindBy(accessibility = \"Next\") Object next; @AndroidFindBy(id = \"x\") Object y; }\n")
        self.write("myapp/hub-environment-configuration/environmentConfiguration.json", json.dumps(
            {"Apps": [{"platform": "iOS", "location": "location to app on your hub machine", "appPackage": "Empty for iOS apps"}]}))
        self.write("myapp/local-environment-configuration/environmentConfiguration.json", json.dumps(
            {"Apps": [{"platform": "Android", "location": "/Users/qa/Desktop/myapp.apk", "appPackage": "com.acme.myapp.debug"},
                      {"platform": "iOS", "location": "~/Desktop/myapp.ipa", "appPackage": "com.acme.MyApp"}]}))
        self.write("myapp/execution.properties", "executionLocale=de_DE\nexecutionPlatform=iOS\n")
        self.write("myapp/toolchain.properties", "appium=2.19.0\nappium.driver.xcuitest=9.10.5\n")
        self.write("myapp/setup", "#!/bin/bash\n# Set MYAPP_UDID to choose the phone\ncase $1 in --check) MODE=check ;; esac\n", mode=0o755)
        found = bc_tools.detect(root, {}, "darwin-arm64", deep=False)
        self.assertTrue(found["test_project"])
        self.assertEqual(found["stack"], {"framework": "Appium", "language": "Java", "build": "Gradle", "runner": "TestNG",
                                          "evidence": "src/test/java/pages/Login.java"})
        self.assertEqual(found["platforms"], ["ios", "android"])
        self.assertEqual(found["apps"]["ios"]["path"], str(Path.home() / "Desktop" / "myapp.ipa"))
        self.assertEqual(found["apps"]["ios"]["id"], "com.acme.MyApp")
        self.assertIn("local-environment-configuration", found["apps"]["ios"]["from"])
        self.assertEqual(found["language"], {"value": "de_DE", "from": "execution.properties"})
        self.assertEqual(found["setup_script"], {"path": "setup", "check": "--check", "udid_setting": "MYAPP_UDID"})

    def test_a_webdriverio_project_and_a_plain_repo(self):
        self.write("wdio/package.json", json.dumps({"devDependencies": {"@wdio/cli": "9", "@wdio/mocha-framework": "9",
                                                                        "@wdio/appium-service": "9", "typescript": "5"}}))
        self.write("wdio/wdio.android.conf.ts", "export const config = { capabilities: [{ 'appium:app': './apps/demo.apk' }] }\n")
        found = bc_tools.detect(self.tmp / "wdio", {}, "darwin-arm64", deep=False)
        self.assertEqual(found["stack"]["framework"], "WebdriverIO")
        self.assertEqual(found["stack"]["language"], "TypeScript")
        self.assertEqual(found["stack"]["runner"], "Mocha")
        self.assertEqual(found["platforms"], ["android"])
        self.assertTrue(found["apps"]["android"]["path"].endswith("apps/demo.apk"))
        self.write("docs/README.md", "# Just docs\n")
        self.assertEqual(bc_tools.detect(self.tmp / "docs", {}, "darwin-arm64"), {"test_project": False, "project": str(self.tmp / "docs")})

    def test_a_web_only_webdriverio_project_installs_nothing(self):
        self.write("web/package.json", json.dumps({"devDependencies": {"@wdio/cli": "9"}}))
        self.write("web/wdio.conf.js", "exports.config = { capabilities: [{ browserName: 'chrome' }] }\n")
        found = bc_tools.detect(self.tmp / "web", {}, "darwin-arm64")
        self.assertTrue(found["test_project"])
        self.assertFalse(found["applies"])

    def test_a_setup_script_needs_a_check_mode_and_to_be_runnable(self):
        self.write("p1/setup", "#!/bin/bash\necho fix everything\n", mode=0o755)
        self.assertIsNone(bc_tools.find_setup_script(self.tmp / "p1"))
        self.write("p2/setup", "#!/bin/bash\n[ \"$1\" = --check ] && exit 0\n", mode=0o644)
        self.assertIsNone(bc_tools.find_setup_script(self.tmp / "p2"))


class Phones(unittest.TestCase):
    IPHONE = {"platform": "ios", "udid": "U1", "name": "QA iPhone", "state": "connected", "developer_mode": "enabled"}
    PIXEL = {"platform": "android", "udid": "P1", "name": "Pixel 8", "state": "connected", "developer_mode": None}

    def test_the_projects_udid_setting_wins(self):
        with mock.patch.dict(os.environ, {"MYAPP_UDID": "U1"}):
            chosen = bc_tools.choose_phone(["android", "ios"], [self.PIXEL, self.IPHONE], "MYAPP_UDID")
        self.assertEqual((chosen["udid"], chosen["source"]), ("U1", "MYAPP_UDID"))
        with mock.patch.dict(os.environ, {"MYAPP_UDID": "GONE"}):
            chosen = bc_tools.choose_phone(["ios"], [self.IPHONE], "MYAPP_UDID")
        self.assertNotIn("udid", chosen)
        self.assertIn("MYAPP_UDID", chosen["why"])

    def test_the_first_connected_phone_of_the_projects_platforms(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MYAPP_UDID", None)
            chosen = bc_tools.choose_phone(["ios", "android"], [self.PIXEL, self.IPHONE], "MYAPP_UDID")
        self.assertEqual(chosen["udid"], "U1")
        self.assertEqual(bc_tools.choose_phone(["android"], [self.IPHONE], None)["why"], "no phone is connected over USB")

    def test_a_phone_that_needs_a_person_says_what_to_do(self):
        wifi = dict(self.IPHONE, state="wifi")
        self.assertIn("plug it in with a USB cable", bc_tools.choose_phone(["ios"], [wifi], None)["why"])
        untrusted = dict(self.PIXEL, state="untrusted")
        self.assertIn("allow USB debugging", bc_tools.choose_phone(["android"], [untrusted], None)["why"])
        self.assertIn("tap Trust", bc_tools.phone_hint({"why": "no phone is connected over USB"}, ["ios"]))


class FakeServer(Sandbox):
    def entry(self, mode="ok", tools=None):
        server = self.write("fake_server.py", FAKE_SERVER)
        env = {"FAKE_MODE": mode, "FAKE_CALLS": str(self.tmp / "calls.jsonl")}
        if tools:
            env["FAKE_TOOLS"] = ",".join(tools)
        # inside bc's tools folder, like the real node, so bc counts the entry as its own
        python = self.write("bc/tools/test-bin/python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', mode=0o755)
        return {"command": str(python), "args": [str(server)], "env": env}

    def calls(self):
        path = self.tmp / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_handshake_follows_pagination_and_answers_the_servers_ping(self):
        with bc_tools.McpClient(self.entry(), self.tmp / "server.log") as client:
            hello = client.handshake()
        self.assertEqual(hello["version"], "9.9.9")
        self.assertEqual(sorted(hello["tools"]), ["appium_get_page_source", "appium_session_management", "generate_locators"])

    def test_a_server_that_dies_is_a_plain_error(self):
        with self.assertRaises(bc_tools.ToolError) as caught:
            with bc_tools.McpClient(self.entry("die"), self.tmp / "server.log") as client:
                client.handshake()
        self.assertIn("stopped", str(caught.exception))

    def test_reading_a_screen_opens_and_closes_one_session_without_an_app(self):
        found = {"phone": {"platform": "ios", "udid": "U1", "name": "QA iPhone"}, "platforms": ["ios"],
                 "driver": {"mode": "built-in"}, "setup_script": None}
        with bc_tools.McpClient(self.entry(), self.tmp / "server.log") as client:
            client.handshake()
            result = bc_tools.read_screen(client, found)
        self.assertEqual(result, {"ok": True, "phone": "QA iPhone", "platform": "ios", "page_source": "/tmp/page-source-1.xml", "bytes": 4321})
        calls = self.calls()
        self.assertEqual([call["arguments"].get("action") for call in calls if call["name"] == "appium_session_management"], ["create", "delete"])
        caps = json.loads(calls[0]["arguments"]["capabilities"])
        self.assertEqual(caps["appium:udid"], "U1")
        self.assertNotIn("appium:app", caps)
        self.assertNotIn("appium:bundleId", caps)
        self.assertNotIn("remoteServerUrl", calls[0]["arguments"])

    def test_a_webdriveragent_failure_points_at_the_projects_setup(self):
        found = {"phone": {"platform": "ios", "udid": "U1", "name": "QA iPhone"}, "platforms": ["ios"],
                 "driver": {"mode": "built-in"}, "setup_script": {"path": "setup"}}
        with bc_tools.McpClient(self.entry("fail-create"), self.tmp / "server.log") as client:
            client.handshake()
            result = bc_tools.read_screen(client, found)
        self.assertFalse(result["ok"])
        self.assertIn("run ./setup in a terminal", result["pending"])

    def test_no_phone_means_pending_without_touching_the_server(self):
        found = {"phone": {"why": "no phone is connected over USB"}, "platforms": ["ios"], "driver": {"mode": "built-in"}}
        with bc_tools.McpClient(self.entry(), self.tmp / "server.log") as client:
            result = bc_tools.read_screen(client, found)
        self.assertFalse(result["ok"])
        self.assertIn("tap Trust", result["pending"])
        self.assertEqual(self.calls(), [])


class ProjectServer(Sandbox):
    FAKE_APPIUM = textwrap.dedent(
        """\
        #!{python}
        import http.server, json, sys
        port = int(sys.argv[sys.argv.index("--port") + 1])
        class Status(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({{"value": {{"ready": True}}}}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        http.server.HTTPServer(("127.0.0.1", port), Status).serve_forever()
        """
    )

    def test_the_projects_appium_starts_on_localhost_and_stops(self):
        appium = self.write("bin/appium", self.FAKE_APPIUM.format(python=sys.executable), mode=0o755)
        info = bc_tools.start_server(str(appium))
        self.assertRegex(info["url"], r"^http://127\.0\.0\.1:\d+$")
        self.assertRegex(info["url"], bc_tools.LOCAL_ONLY_URLS)
        self.assertTrue(bc_tools.server_ready(info["url"]))
        self.assertTrue(bc_tools.server_file().exists())
        self.assertTrue(bc_tools.stop_server())
        self.assertFalse(bc_tools.server_file().exists())
        self.assertFalse(bc_tools.server_ready(info["url"]))

    def test_the_local_only_rule_refuses_other_hosts(self):
        import re
        for url in ("http://10.0.0.5:4723", "https://grid.example.com/wd/hub", "http://127.0.0.1.evil.com:4723",
                    "http://user:pw@127.0.0.1:4723", "http://localhost:4723?x=1"):
            self.assertIsNone(re.search(bc_tools.LOCAL_ONLY_URLS, url), url)
        for url in ("http://127.0.0.1:4723", "http://localhost:4723/wd/hub", "http://[::1]:4723"):
            self.assertIsNotNone(re.search(bc_tools.LOCAL_ONLY_URLS, url), url)


class Ensure(FakeServer):
    """ensure installs, proves, and records the server; smoke reads the phone. Each fits in one shell call."""

    PHONE = {"platform": "ios", "udid": "U1", "name": "QA iPhone", "source": "connected over USB"}

    def found(self, root, phone=None, mode="built-in", platforms=("ios",)):
        return {"test_project": True, "applies": True, "project": str(root), "platforms": list(platforms),
                "stack": {"framework": "Appium", "language": "Java", "build": "Gradle", "runner": "TestNG", "evidence": "x"},
                "apps": {}, "language": {"value": None, "from": None}, "setup_script": None,
                "driver": {"mode": mode, "why": "test"}, "mac": {"java": None, "android_sdk": None, "xcode": {"ok": True}},
                "phones": [], "phone": dict(phone if phone is not None else self.PHONE), "plan": {}, "guided": [], "lines": []}

    def project(self):
        root = self.tmp / "project"
        self.write("project/.buddy-council/sources.json", json.dumps({"requirements": {"provider": "jama"}}))
        return root

    def block(self, root):
        return json.loads((root / ".buddy-council" / "sources.json").read_text())["automation"]

    def run_ensure(self, root, found, entry=None, phone_check=False):
        node, app = self.home / "tools" / "node" / "v1", self.home / "tools" / "appium-mcp" / "1.0.0-aaaaaaaaaaaa"
        tools = list(bc_tools.READ_TOOLS) + ["appium_session_management"]
        with mock.patch.object(bc_tools, "detect", return_value=found), \
             mock.patch.object(bc_tools, "platform_key", return_value="darwin-arm64"), \
             mock.patch.object(bc_tools, "install_node", return_value=(node, False)), \
             mock.patch.object(bc_tools, "install_appium_mcp", return_value=(app, False)), \
             mock.patch.object(bc_tools, "node_works", return_value=True), \
             mock.patch.object(bc_tools, "appium_installed", return_value=True), \
             mock.patch.object(bc_tools, "server_entry", return_value=entry or self.entry(tools=tools)):
            return bc_tools.cmd_ensure(str(root), install=not phone_check, phone_check=phone_check)

    def test_a_failed_download_is_recorded_as_pending_and_records_no_server(self):
        root = self.project()
        with mock.patch.object(bc_tools, "detect", return_value=self.found(root)), \
             mock.patch.object(bc_tools, "platform_key", return_value="darwin-arm64"), \
             mock.patch.object(bc_tools, "install_node", side_effect=bc_tools.ToolError("couldn't download https://nodejs.org/x: timed out")):
            result = bc_tools.cmd_ensure(str(root))
        self.assertEqual(result["status"], "pending")
        self.assertIn("setup tries again on its next run", result["pending"][0])
        self.assertEqual(self.block(root)["status"], "pending")
        self.assertFalse(self.record.exists())

    def test_ensure_records_and_hands_the_phone_to_smoke_which_makes_it_ready(self):
        root = self.project()
        result = self.run_ensure(root, self.found(root))
        self.assertEqual((result["status"], result["phone_check"]), ("pending", "due"))
        self.assertIn("next, one page source from QA iPhone", result["lines"][0])
        self.assertEqual(result["recorded"], [bc_tools.tilde(self.record)])
        self.assertTrue(any("restart your assistant once" in line for line in result["lines"]), result["lines"])
        self.assertEqual(self.calls(), [], "ensure never touches the phone")
        result = self.run_ensure(root, self.found(root), phone_check=True)
        self.assertEqual((result["status"], result["phone_check"]), ("ready", "passed"), result)
        self.assertIn("saved a page source from QA iPhone (4,321 bytes)", result["lines"][0])
        block = self.block(root)
        self.assertEqual((block["status"], block["proof"]["page_source"], block["proof"]["udid"]), ("ready", "/tmp/page-source-1.xml", "U1"))
        self.assertEqual(json.loads((root / ".buddy-council" / "sources.json").read_text())["requirements"], {"provider": "jama"})

    def test_a_passed_check_survives_an_unplugged_phone_but_not_a_failure(self):
        root = self.project()
        self.run_ensure(root, self.found(root), phone_check=True)
        no_phone = self.found(root, phone={"why": "no phone is connected over USB"})
        for phone_check in (False, True):
            result = self.run_ensure(root, no_phone, phone_check=phone_check)
            self.assertEqual((result["status"], result["phone_check"]), ("ready", "passed"), result)
            self.assertIn("passed the phone check on", result["lines"][0])
            self.assertFalse(any("needs you" in line for line in result["lines"]), result["lines"])
        failing = self.entry("fail-create", tools=list(bc_tools.READ_TOOLS) + ["appium_session_management"])
        result = self.run_ensure(root, self.found(root), entry=failing, phone_check=True)
        self.assertEqual((result["status"], result["phone_check"]), ("pending", "failed"))
        self.assertIsNone(self.block(root)["proof"])
        result = self.run_ensure(root, no_phone)
        self.assertEqual(result["status"], "pending", "an older pass no longer counts after a failure")

    def test_a_different_phone_or_driver_needs_a_new_check(self):
        root = self.project()
        self.run_ensure(root, self.found(root), phone_check=True)
        other = dict(self.PHONE, udid="U2", name="Other iPhone")
        self.assertEqual(self.run_ensure(root, self.found(root, phone=other))["phone_check"], "due")
        self.assertEqual(self.run_ensure(root, self.found(root, mode="project"))["phone_check"], "due")

    def test_no_phone_and_no_earlier_check_names_what_to_do(self):
        root = self.project()
        found = self.found(root, phone={"why": "no phone is connected over USB"})
        found["guided"] = [bc_tools.phone_hint(found["phone"], ["ios"])]
        result = self.run_ensure(root, found)
        self.assertEqual((result["status"], result["phone_check"]), ("pending", "not connected"))
        self.assertIn("tap Trust", result["lines"][0])
        self.assertEqual(sum("tap Trust" in line for line in result["lines"]), 1, "said once, not again as needs-you")

    def test_the_assistants_own_configs_are_never_touched(self):
        root = self.project()
        theirs = self.write(".copilot/mcp-config.json", json.dumps({"mcpServers": {"appium": {"command": "/usr/local/bin/npx"}}}))
        before = theirs.read_text()
        result = self.run_ensure(root, self.found(root))
        self.assertEqual(result["recorded"], [bc_tools.tilde(self.record)])
        self.assertEqual(theirs.read_text(), before)

    def test_a_failed_upgrade_keeps_the_old_version_in_use(self):
        root = self.project()
        old_node = self.write("bc/tools/node/v0/bin/node", "#!/bin/sh\n", mode=0o755)
        old_app = self.write("bc/tools/appium-mcp/0.9.0-bbbbbbbbbbbb/node_modules/appium-mcp/dist/index.js", "")
        before = {"command": str(old_node), "args": [str(old_app)], "env": {"NO_UI": "true"}}
        bc_tools.save_server_record(before)
        result = self.run_ensure(root, self.found(root), entry=self.entry("die"))
        self.assertEqual(result["status"], "pending")
        self.assertIn("the version before it stays in use", result["pending"][0])
        self.assertEqual((bc_tools.read_server_record(), result["recorded"]), (before, []))
        self.assertTrue(old_app.exists(), "nothing is pruned when the new version didn't answer")

    def test_smoke_keeps_bcs_own_jdk_and_sdk_wired(self):
        root = self.project()
        pins = bc_tools.load_pins()
        self.write(f"bc/tools/jdk/{pins['jdk']['version']}/Contents/Home/bin/java",
                   "#!/bin/sh\necho 'openjdk version \"21.0.13\" 2026-10-21' >&2\n", mode=0o755)
        sdk = self.home / "tools" / "android-sdk"
        self.write("bc/tools/android-sdk/platform-tools/adb", "#!/bin/sh\n", mode=0o755)
        self.write("bc/tools/android-sdk/build-tools/37.0.0/apksigner", "#!/bin/sh\n", mode=0o755)
        for part in pins["android_sdk"]["parts"]:
            self.write(f"bc/tools/android-sdk/{part['dir']}/.bc-complete", part["sha256"] + "\n")
        found = self.found(root, phone={"why": "no phone is connected over USB"}, platforms=("android",))
        for install in (True, False):
            env, shared = bc_tools.shared_tools(found, pins, "darwin-arm64", set(), {}, [], [], install)
            self.assertEqual(env["JAVA_HOME"], str(self.home / "tools" / "jdk" / pins["jdk"]["version"] / "Contents" / "Home"))
            self.assertEqual(env["ANDROID_HOME"], str(sdk))
            self.assertEqual(set(shared), {"jdk", "android_sdk"})

    def test_ensure_on_android_with_bcs_jdk_keeps_it(self):
        root = self.project()
        pins = bc_tools.load_pins()
        java_home = self.home / "tools" / "jdk" / pins["jdk"]["version"] / "Contents" / "Home"
        self.write(str((java_home / "bin" / "java").relative_to(self.tmp)),
                   "#!/bin/sh\necho 'openjdk version \"21.0.13\"' >&2\n", mode=0o755)
        self.write("bc/tools/android-sdk/platform-tools/adb", "#!/bin/sh\n", mode=0o755)
        self.write("bc/tools/android-sdk/build-tools/37.0.0/apksigner", "#!/bin/sh\n", mode=0o755)
        fake = self.entry(tools=list(bc_tools.READ_TOOLS) + ["appium_session_management"])
        with mock.patch.object(bc_tools, "server_entry", side_effect=lambda p, n, a, env: {**fake, "env": {**fake["env"], **env}}):
            node, app = self.home / "tools" / "node" / "v1", self.home / "tools" / "appium-mcp" / "1.0.0-aaaaaaaaaaaa"
            with mock.patch.object(bc_tools, "detect", return_value=self.found(root, platforms=("android",))), \
                 mock.patch.object(bc_tools, "platform_key", return_value="darwin-arm64"), \
                 mock.patch.object(bc_tools, "install_node", return_value=(node, False)), \
                 mock.patch.object(bc_tools, "install_appium_mcp", return_value=(app, False)):
                result = bc_tools.cmd_ensure(str(root))
        self.assertEqual(result["removed"], [])
        self.assertTrue((java_home / "bin" / "java").exists(), "the JDK the server points at stays")
        self.assertEqual(bc_tools.read_server_record()["env"]["JAVA_HOME"], str(java_home))


class Downloads2(Sandbox):
    def test_only_a_certificate_error_tries_the_keychains_certificates(self):
        refused = bc_tools.urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        with mock.patch.object(bc_tools, "fetch", side_effect=refused) as fetch, mock.patch.object(bc_tools.time, "sleep"):
            with self.assertRaises(bc_tools.DownloadError) as caught:
                bc_tools.download("https://nodejs.invalid/x", self.tmp / "x", "0" * 64, "x")
        self.assertEqual(fetch.call_count, 3, "three tries, one trust store")
        self.assertFalse(caught.exception.missing)
        certificate = bc_tools.urllib.error.URLError(bc_tools.ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
        with mock.patch.object(bc_tools, "fetch", side_effect=certificate) as fetch, \
             mock.patch.object(bc_tools, "mac_certificates", return_value=[]):
            with self.assertRaises(bc_tools.DownloadError):
                bc_tools.download("https://nodejs.invalid/x", self.tmp / "x", "0" * 64, "x")
        self.assertEqual(fetch.call_count, 2, "once per trust store")

    def test_a_missing_file_is_one_try_and_lets_node_try_its_other_archive(self):
        gone = bc_tools.urllib.error.HTTPError("https://mirror/x.xz", 404, "Not Found", {}, None)
        with mock.patch.object(bc_tools, "fetch", side_effect=gone) as fetch:
            with self.assertRaises(bc_tools.DownloadError) as caught:
                bc_tools.download("https://mirror/x.xz", self.tmp / "x", "0" * 64, "x")
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(caught.exception.missing)
        pins = bc_tools.load_pins()
        with mock.patch.object(bc_tools, "download", side_effect=bc_tools.DownloadError("host down")) as download:
            with self.assertRaises(bc_tools.DownloadError):
                bc_tools.install_node(pins, "darwin-arm64")
        self.assertEqual(download.call_count, 1, "the other archive is on the same host")


class Links(Sandbox):
    def test_a_path_through_an_earlier_link_is_refused(self):
        cases = [
            [("top/", "dir", None), ("top/a", "link", "."), ("top/a/b", "link", ".."), ("top/b/c", "link", ".."),
             ("c/pwned.txt", "file", "x")],
            [("top/", "dir", None), ("top/a", "link", "."), ("top/a/x", "file", "x")],
            [("top/", "dir", None), ("top/sub/", "dir", None), ("top/l", "link", "sub"), ("top/m", "link", "l/../..")],
            [("top/", "dir", None), ("top/l", "link", "x"), ("top/l", "file", "replaced")],
        ]
        for entries in cases:
            archive = self.tmp / "bad.tar.gz"
            tar_with(archive, entries)
            with self.assertRaises(bc_tools.ToolError, msg=entries):
                bc_tools.unpack_tar(archive, self.tmp / "out")
        self.assertFalse((self.tmp / "c").exists())
        self.assertFalse((self.tmp / "pwned.txt").exists())

    def test_zip_links_follow_the_same_rules(self):
        import zipfile
        archive = self.tmp / "bad.zip"
        with zipfile.ZipFile(archive, "w") as packed:
            info = zipfile.ZipInfo("tools/escape")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            packed.writestr(info, "../../etc")
        with self.assertRaises(bc_tools.ToolError):
            bc_tools.unpack_zip(archive, self.tmp / "out")
        good = self.tmp / "good.zip"
        with zipfile.ZipFile(good, "w") as packed:
            packed.writestr("platform-tools/adb", "x")
        bc_tools.unpack_zip(good, self.tmp / "ok")
        self.assertTrue((self.tmp / "ok" / "platform-tools" / "adb").exists())


class PruneKeepsWhatsInside(Sandbox):
    def test_a_kept_path_inside_a_pin_keeps_the_pin(self):
        jdk = self.home / "tools" / "jdk" / "21.0.13+9" / "Contents" / "Home"
        jdk.mkdir(parents=True)
        (self.home / "tools" / "jdk" / "17.0.0").mkdir()
        removed = bc_tools.prune([jdk])
        self.assertEqual([Path(item).name for item in removed], ["17.0.0"])
        self.assertTrue(jdk.exists())

    def test_a_wired_env_value_keeps_its_pin(self):
        jdk = self.home / "tools" / "jdk" / "21.0.13+9" / "Contents" / "Home"
        jdk.mkdir(parents=True)
        bc_tools.save_server_record({"command": "/x/node", "args": [], "env": {"JAVA_HOME": str(jdk)}})
        self.assertEqual(bc_tools.prune(), [])
        self.assertTrue(jdk.exists())


class ProjectReport(unittest.TestCase):
    def test_status_reports_a_pending_reason_once(self):
        tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, str(tmp), True)
        (tmp / ".buddy-council").mkdir()
        block = {"enabled": True, "status": "pending", "tools": {"appium_mcp": "1.99.2"},
                 "pending": ["no phone is connected over USB yet: plug one in, unlock it and tap Trust; setup checks again on its next run"]}
        (tmp / ".buddy-council" / "sources.json").write_text(json.dumps({"automation": block}))
        with mock.patch.dict(os.environ, {"BC_HOME": str(tmp / "bc")}):
            status = bc_tools.cmd_status(str(tmp))
        self.assertEqual(status["line"], "Automation:    pending — no phone is connected over USB yet: plug one in, unlock it "
                                         "and tap Trust (re-checking this run)")
        self.assertIn("retry", status["needs"])
        (tmp / ".buddy-council" / "sources.json").write_text(json.dumps({"automation": {"enabled": False}}))
        self.assertIn("left out", bc_tools.cmd_status(str(tmp))["line"])
    def test_a_setup_scripts_report(self):
        text = "\n".join([
            "MyAppAutomation setup: checking only, nothing will be changed",
            "   ✅ Xcode 27.2", "   ⚠️  Kept Appium 2.18.0; the known-good version is 2.19.0", "      hint line",
            "   ❌ The phone in MYAPP_UDID isn't connected over USB", "\x1b[31m   ❌ WebDriverAgent isn't signed\x1b[0m",
            "", "Result: 31 OK, 1 warning, 2 problems", "Full log: /Users/qa/.myapp-setup/logs/x.log",
        ])
        report = bc_tools.parse_report(text)
        self.assertEqual(report["result"], "31 OK, 1 warning, 2 problems")
        self.assertEqual(report["problems"], ["The phone in MYAPP_UDID isn't connected over USB", "WebDriverAgent isn't signed"])
        self.assertEqual(report["warnings"], ["Kept Appium 2.18.0; the known-good version is 2.19.0"])


class Pins(unittest.TestCase):
    def test_the_shipped_pins_are_complete_and_agree_with_the_lockfile(self):
        pins = json.loads((SCRIPTS / "automation" / "pins.json").read_text())
        for key in ("darwin-arm64", "darwin-x64"):
            for choice in pins["node"]["files"][key]:
                self.assertRegex(choice["sha256"], r"^[0-9a-f]{64}$")
                self.assertIn(f"v{pins['node']['version']}", choice["name"])
            self.assertRegex(pins["jdk"]["files"][key][0]["sha256"], r"^[0-9a-f]{64}$")
        for part in pins["android_sdk"]["parts"]:
            self.assertRegex(part["sha256"], r"^[0-9a-f]{64}$")
        major = int(pins["node"]["version"].split(".")[0])
        lock = json.loads((SCRIPTS / "automation" / "appium-mcp" / "package-lock.json").read_text())
        package = json.loads((SCRIPTS / "automation" / "appium-mcp" / "package.json").read_text())
        self.assertEqual(package["dependencies"]["appium-mcp"], pins["appium_mcp"]["version"], "an exact version, never a range")
        self.assertEqual(lock["packages"]["node_modules/appium-mcp"]["version"], pins["appium_mcp"]["version"])
        engines = lock["packages"]["node_modules/appium-mcp"].get("engines", {}).get("node", "")
        self.assertTrue(engines.startswith(">="), engines)
        self.assertGreaterEqual(major, int(engines[2:].split(".")[0]), "the pinned Node must satisfy appium-mcp's engines")
        for name, entry in lock["packages"].items():
            if name and not entry.get("inBundle") and not entry.get("link"):
                resolved = entry.get("resolved", "")
                self.assertTrue(resolved.startswith("https://registry.npmjs.org/") or entry.get("optional"),
                                f"{name} must come from the public registry: {resolved}")

    def test_the_read_tools_match_the_hook(self):
        hook = (SCRIPTS.parent / "hooks" / "auto-approve-mcp.sh").read_text()
        for tool in bc_tools.READ_TOOLS:
            self.assertIn(f"mcp__appium__{tool}", hook)
            self.assertIn(f"appium-{tool}", hook)


if __name__ == "__main__":
    unittest.main()
