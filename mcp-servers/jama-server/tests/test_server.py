"""Tests for the read-only Jama MCP server, run against an in-process fake Jama REST API.

    uv run --directory mcp-servers/jama-server python -m unittest discover -s tests -v

Standard library only (unittest + httpx.MockTransport, which ships with httpx),
so the server gains no test dependency.
"""

import asyncio
import base64
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

BASE_URL = "https://jama.test"
SITE = "jama.test"
PROJECT = 42


def setUpModule():
    # Keys are read on every call, from a throwaway home: a developer's real ~/.buddy-council is never read.
    global _HOME, _ENV
    _HOME = tempfile.TemporaryDirectory()
    _ENV = mock.patch.dict(os.environ, {"HOME": _HOME.name, "BC_HOME": str(Path(_HOME.name, ".buddy-council"))})
    _ENV.start()
    os.environ.pop("JAMA_BASE_URL", None)


def tearDownModule():
    _ENV.stop()
    _HOME.cleanup()


def _forget_keys():
    for path in server.bc_keys.keys_dir().glob("*"):
        path.unlink()


def _save_key(fake, url=BASE_URL):
    fields = ({"client_id": fake.client_id, "client_secret": fake.client_secret} if fake.client_id
              else {"username": fake.username, "password": fake.password})
    return server.bc_keys.write_key("jama", url, fields)


def _item(iid, type_id, name, parent, seq, key, fields=None):
    location_parent = {"item": parent} if parent else {"project": PROJECT}
    return {
        "id": iid,
        "documentKey": key,
        "globalId": f"GID-{iid}",
        "itemType": type_id,
        "project": PROJECT,
        "location": {"sequence": seq, "parent": location_parent, "sortOrder": 0, "globalSortOrder": 0},
        "fields": {"name": name, "documentKey": key, **(fields or {})},
        "modifiedDate": "2026-09-01T10:00:00.000+0000",
        "type": "items",
    }


TYPES = [
    {"id": 31, "typeKey": "FLD", "display": "Folder", "fields": [{"name": "name", "label": "Name", "fieldType": "STRING"}]},
    {"id": 32, "typeKey": "TXT", "display": "Text", "fields": [{"name": "description", "label": "Description", "fieldType": "TEXT"}]},
    {"id": 33, "typeKey": "SET", "display": "Set", "fields": [{"name": "name", "label": "Name", "fieldType": "STRING"}]},
    {"id": 34, "typeKey": "CMP", "display": "Component", "fields": [{"name": "name", "label": "Name", "fieldType": "STRING"}]},
    {
        "id": 89,
        "typeKey": "REQ",
        "display": "Requirement",
        "fields": [
            {"name": "documentKey", "label": "ID", "fieldType": "STRING"},
            {"name": "name", "label": "Name", "fieldType": "STRING"},
            {"name": "description", "label": "Description", "fieldType": "TEXT", "textType": "RICHTEXT"},
            {"name": "status$89", "label": "Status", "fieldType": "LOOKUP", "pickList": 500},
            {"name": "priority$89", "label": "Priority", "fieldType": "LOOKUP", "pickList": 501},
            {"name": "verification$89", "label": "Verification Method", "fieldType": "MULTI_LOOKUP", "pickList": 502},
            {"name": "rationale$89", "label": "Rationale", "fieldType": "TEXT", "textType": "RICHTEXT"},
            {"name": "github$89", "label": "Linked to Github", "fieldType": "URL_STRING"},
            {"name": "owner$89", "label": "Owner", "fieldType": "USER"},
            {"name": "release", "label": "Release", "fieldType": "RELEASE"},
        ],
    },
    {"id": 90, "typeKey": "TC", "display": "Test Case", "fields": [{"name": "name", "label": "Name", "fieldType": "STRING"}]},
    {
        "id": 91,
        "typeKey": "SYS",
        "display": "System Requirement",
        "fields": [
            {"name": "name", "label": "Name", "fieldType": "STRING"},
            {"name": "description", "label": "Description", "fieldType": "TEXT", "textType": "RICHTEXT"},
        ],
    },
    {
        "id": 92,
        "typeKey": "MSRS",
        "display": "Mobile SRS Requirement",
        "fields": [
            {"name": "name", "label": "Name", "fieldType": "STRING"},
            {"name": "description", "label": "Description", "fieldType": "TEXT", "textType": "RICHTEXT"},
        ],
    },
]

REQ1_FIELDS = {
    "description": "<p>The system shall display vitals&nbsp;in real time.</p><ul><li>HR</li><li>SpO2</li></ul>",
    "status$89": 5001,
    "priority$89": 5101,
    "verification$89": [5201, 5202],
    "rationale$89": "<p>Clinicians need <b>continuous</b> visibility.</p>",
    "github$89": "https://github.com/org/repo/blob/main/docs/sds.md",
    "owner$89": 77,
    "release": 12,
}

# Product (component)
#   Software Requirements (set)
#     Patient Monitoring (folder)
#       CWA-REQ-1, CWA-REQ-2
#       Vitals (folder)
#         CWA-REQ-3
#           CWA-REQ-4          <- a requirement nested under a requirement
#       CWA-TXT-1 (text), CWA-TC-1 (test case)
#     Alarms (folder)
#       CWA-REQ-5
#     CWA-REQ-6               <- in a set but in no folder
#   Alarms (folder)           <- same name, different branch
#     CWA-REQ-7
# CWA-REQ-8                   <- at the project root
ITEMS = [
    _item(1000, 34, "Product", None, "1", "CWA-CMP-1"),
    _item(1001, 33, "Software Requirements", 1000, "1.1", "CWA-SET-1"),
    _item(1100, 31, "Patient Monitoring", 1001, "1.1.1", "CWA-FLD-1"),
    _item(2001, 89, "Display vitals", 1100, "1.1.1.1", "CWA-REQ-1", REQ1_FIELDS),
    _item(2002, 89, "Show trends", 1100, "1.1.1.2", "CWA-REQ-2", {"status$89": 5002}),
    _item(1110, 31, "Vitals", 1100, "1.1.1.3", "CWA-FLD-2"),
    _item(2003, 89, "Heart rate", 1110, "1.1.1.3.1", "CWA-REQ-3"),
    _item(2004, 89, "HR alarm limit", 2003, "1.1.1.3.1.1", "CWA-REQ-4"),
    _item(3001, 32, "Intro", 1100, "1.1.1.4", "CWA-TXT-1"),
    _item(4001, 90, "Vitals render", 1100, "1.1.1.5", "CWA-TC-1"),
    _item(1200, 31, "Alarms", 1001, "1.1.2", "CWA-FLD-3"),
    _item(2005, 89, "Audible alarm", 1200, "1.1.2.1", "CWA-REQ-5"),
    _item(2006, 89, "Loose requirement", 1001, "1.1.3", "CWA-REQ-6"),
    _item(1300, 31, "Alarms", 1000, "1.2", "CWA-FLD-4"),
    _item(2007, 89, "Alarm log", 1300, "1.2.1", "CWA-REQ-7"),
    _item(2008, 89, "Root requirement", None, "2", "CWA-REQ-8"),
]

PICKLISTS = {
    500: [{"id": 5001, "name": "Approved"}, {"id": 5002, "name": "Draft"}],
    501: [{"id": 5101, "name": "High"}],
    502: [{"id": 5201, "name": "Test"}, {"id": 5202, "name": "Inspection"}],
}
RELATIONSHIP_TYPES = [{"id": 7, "name": "Derived From"}, {"id": 8, "name": "Verified By"}]
RELATIONSHIPS = {
    ("upstream", 2001): [{"id": 9001, "fromItem": 2008, "toItem": 2001, "relationshipType": 7, "suspect": False}],
    ("downstream", 2001): [{"id": 9002, "fromItem": 2001, "toItem": 4001, "relationshipType": 8, "suspect": True}],
}
PROJECTS = [
    {"id": PROJECT, "projectKey": "CWA", "isFolder": False, "fields": {"name": "Clinical Window App", "projectKey": "CWA"}},
    {"id": 7, "projectKey": "", "isFolder": True, "fields": {"name": "Archive folder"}},
]
USER = {"id": 5, "username": "jdoe", "firstName": "Jane", "lastName": "Doe", "licenseType": "NAMED", "active": True, "email": "jane@example.com"}


def _abstract(item, keep_location=False):
    """Jama's docs show /abstractitems without a location — mirror that unless a test asks otherwise."""
    dropped = ("lock", "childItemType") if keep_location else ("location", "lock", "childItemType")
    return {k: v for k, v in item.items() if k not in dropped}


class FakeJama:
    """Just enough of Jama's REST API, recording every request it sees."""

    def __init__(self, client_id="cid", client_secret="s3cret", username="", password="", token_prefix="tok"):
        self.token_prefix = token_prefix
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.items = {item["id"]: item for item in ITEMS}
        self.requests: list[tuple[str, str, dict]] = []
        self.tokens_issued = 0
        self.rejected_tokens: set[str] = set()
        self.throttle: dict[str, int] = {}
        self.reject_include = False
        self.sleeps: list[float] = []
        self.search_location = False  # Jama Cloud's /abstractitems does return location
        self.omit_totals = False
        self.latency = 0.0
        self.in_flight = 0
        self.max_in_flight = 0
        self.dropped: dict[str, int] = {}  # resource -> connections to drop before answering
        self.broken: set[str] = set()  # resources that answer 500
        self.page_cap = 50  # most results Jama returns per page, whatever maxResults asks for
        self.hold: asyncio.Event | None = None  # while set and not yet open, project pages wait for it
        self.hold_from = 1  # startAt from which project pages wait: 1 lets the first page through

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    def transport(self):
        return httpx.MockTransport(self.handle_async)

    async def handle_async(self, request: httpx.Request) -> httpx.Response:
        """Every request takes `latency` seconds, so concurrent ones overlap and can be counted."""
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency)
            start = int(request.url.params.get("startAt", 0) or 0)
            if self.hold is not None and request.url.path == "/rest/v1/items" and start >= self.hold_from:
                await self.hold.wait()
            return self.handle(request)
        finally:
            self.in_flight -= 1

    @staticmethod
    def _error(status, message):
        return httpx.Response(status, json={"meta": {"status": "Error", "message": message}})

    def _page(self, data, params, linked=None):
        start = int(params.get("startAt", 0))
        size = min(int(params.get("maxResults", 20)), self.page_cap)
        chunk = data[start : start + size]
        page_info = {"startIndex": start, "resultCount": len(chunk), "totalResults": len(data)}
        if self.omit_totals:
            del page_info["totalResults"]
        body = {
            "meta": {"status": "OK", "pageInfo": page_info},
            "links": {},
            "data": chunk,
        }
        if linked:
            body["linked"] = linked
        return httpx.Response(200, json=body)

    def _authorized(self, request):
        header = request.headers.get("Authorization", "")
        if self.username:
            expected = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return header == f"Basic {expected}"
        token = header.removeprefix("Bearer ")
        return header.startswith(f"Bearer {self.token_prefix}-") and token not in self.rejected_tokens

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((request.method, path, params))

        if path == "/rest/oauth/token":
            expected = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            if request.method != "POST" or request.headers.get("Authorization") != f"Basic {expected}":
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "Bad credentials"})
            self.tokens_issued += 1
            return httpx.Response(200, json={"access_token": f"{self.token_prefix}-{self.tokens_issued}", "token_type": "bearer", "expires_in": 3600})

        if not path.startswith("/rest/v1/"):
            return self._error(404, "no such path")
        if not self._authorized(request):
            return self._error(401, "Unauthorized")
        resource = path[len("/rest/v1/") :]
        if self.dropped.get(resource):
            self.dropped[resource] -= 1
            raise httpx.ConnectTimeout("connection timed out", request=request)
        if resource in self.broken:
            return self._error(500, "internal error")
        if self.throttle.get(resource):
            self.throttle[resource] -= 1
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"meta": {"message": "slow down"}})
        parts = resource.split("/")

        if resource == "users/current":
            return httpx.Response(200, json={"data": USER})
        if resource == "projects":
            return self._page(PROJECTS, params)
        if parts[0] == "projects" and len(parts) == 2:
            match = next((p for p in PROJECTS if p["id"] == int(parts[1])), None)
            return httpx.Response(200, json={"data": match}) if match else self._error(404, "project not found")
        if resource == "itemtypes":
            return self._page(TYPES, params)
        if resource == "relationshiptypes":
            return self._page(RELATIONSHIP_TYPES, params)
        if resource == "items":
            data = [i for i in self.items.values() if str(i["project"]) == params.get("project")]
            return self._page(data, params)
        if parts[0] == "items" and len(parts) == 2:
            match = self.items.get(int(parts[1]))
            return httpx.Response(200, json={"data": match}) if match else self._error(404, "item not found")
        if parts[0] == "items" and len(parts) == 3 and parts[2] in ("upstreamrelationships", "downstreamrelationships"):
            includes = request.url.params.get_list("include")
            if includes and self.reject_include:
                return self._error(400, "unsupported include")
            direction = parts[2].removesuffix("relationships")
            data = RELATIONSHIPS.get((direction, int(parts[1])), [])
            linked = None
            if includes:
                ids = {r["fromItem"] for r in data} | {r["toItem"] for r in data}
                linked = {"items": {str(i): _abstract(self.items[i]) for i in ids}}
            return self._page(data, params, linked)
        if resource == "abstractitems":
            keys = request.url.params.get_list("documentKey")
            type_ids = {int(t) for t in request.url.params.get_list("itemType")}
            data = [
                _abstract(i, self.search_location)
                for i in self.items.values()
                if (not keys or i["documentKey"] in keys)
                and (not type_ids or i["itemType"] in type_ids)
                and str(i["project"]) == params.get("project", str(i["project"]))
            ]
            return self._page(data, params)
        if parts[0] == "abstractitems" and len(parts) == 2:
            match = self.items.get(int(parts[1]))
            return httpx.Response(200, json={"data": _abstract(match)}) if match else self._error(404, "not found")
        if parts[0] == "picklists" and len(parts) == 3 and parts[2] == "options":
            return self._page(PICKLISTS.get(int(parts[1]), []), params)
        if parts[0] == "picklistoptions" and len(parts) == 2:
            for options in PICKLISTS.values():
                for option in options:
                    if option["id"] == int(parts[1]):
                        return httpx.Response(200, json={"data": option})
            return self._error(404, "option not found")
        return self._error(404, f"unknown resource {resource}")

    def count(self, method, path):
        return sum(1 for m, p, _ in self.requests if m == method and p == path)


NON_REQUIREMENT_TYPES = ["Text", "Test Case"]
TOOL_NAMES = {
    "jama_get_current_user",
    "jama_get_projects",
    "jama_get_item_types",
    "jama_get_features",
    "jama_get_requirements",
    "jama_get_item",
    "jama_get_items",
    "jama_get_scope_proposal",
}
ROOT = 1001  # the "Software Requirements" set


class ServerTestCase(unittest.IsolatedAsyncioTestCase):
    def make_fake(self) -> FakeJama:
        return FakeJama()

    async def asyncSetUp(self):
        self.fake = self.make_fake()
        _forget_keys()
        _save_key(self.fake)
        options = mock.patch.object(server, "_CLIENT_OPTIONS", {"transport": self.fake.transport(), "sleep": self.fake.sleep})
        options.start()
        self.addCleanup(options.stop)
        config = server.JamaConfig(
            BASE_URL, self.fake.client_id, self.fake.client_secret, self.fake.username, self.fake.password
        )
        self.client = server.JamaClient(config, transport=self.fake.transport(), sleep=self.fake.sleep)
        server.reset_state(self.client)

    async def asyncTearDown(self):
        await self.client.aclose()
        server.reset_state()

    async def call(self, tool, **kwargs):
        return json.loads(await getattr(server, tool)(**kwargs))

    async def requirements(self, **kwargs):
        kwargs.setdefault("project_id", PROJECT)
        kwargs.setdefault("item_type_exclude", NON_REQUIREMENT_TYPES)
        return await self.call("jama_get_requirements", **kwargs)

    @staticmethod
    def ids(payload):
        return [r["id"] for r in payload["requirements"]]


class ReadOnlyContract(ServerTestCase):
    async def test_only_get_requests_reach_the_api(self):
        await self.call("jama_get_current_user")
        await self.call("jama_get_projects")
        await self.call("jama_get_item_types", project_id=PROJECT)
        await self.call("jama_get_features", project_id=PROJECT)
        await self.requirements(scope="Patient Monitoring", field_mapping={"github_url": "Linked to Github"})
        await self.call("jama_get_item", item="CWA-REQ-1")
        await self.call("jama_get_items", items=["CWA-REQ-2", "2005", "cwa-req-4"], root_ids=[ROOT])
        await self.call("jama_get_scope_proposal", project_id=PROJECT, document_keys=["CWA-REQ-1", "CWA-REQ-5"])
        await self.requirements(root_ids=[ROOT], feature_depth=2, exclude_ids=[1110])
        self.assertGreater(len(self.fake.requests), 10)
        for method, path, _ in self.fake.requests:
            if path.startswith("/rest/v1/"):
                self.assertEqual(method, "GET", path)
            else:
                self.assertEqual((method, path), ("POST", "/rest/oauth/token"))

    async def test_every_tool_is_a_read_only_jama_get(self):
        tools = await server.mcp.list_tools()
        self.assertEqual({tool.name for tool in tools}, TOOL_NAMES)
        for tool in tools:
            self.assertTrue(tool.annotations.readOnlyHint, tool.name)
            self.assertFalse(tool.annotations.destructiveHint, tool.name)

    async def test_results_are_sent_once_as_text(self):
        tools = await server.mcp.list_tools()
        self.assertEqual([t.name for t in tools if t.outputSchema is not None], [], "set structured_output=False")
        result = await server.mcp.call_tool("jama_get_current_user", {})
        self.assertNotIsInstance(result, tuple, "a (text, structured copy) pair doubles every response")
        [block] = result
        self.assertEqual(json.loads(block.text)["user"]["name"], "Jane Doe")

    def test_source_has_no_write_capable_http_call(self):
        source = Path(server.__file__).read_text()
        self.assertEqual(source.count("._http.post("), 1, "only the OAuth token exchange may POST")
        for verb in ("put", "patch", "delete", "request", "send", "stream"):
            self.assertNotIn(f"._http.{verb}(", source)

    def test_malformed_paths_are_refused(self):
        for bad in ("../users", "items/../../admin", "items?project=1", "https://evil.example/x", ""):
            with self.assertRaises(server.JamaError, msg=bad):
                self.client._api_url(bad)


class Requirements(ServerTestCase):
    async def test_all_scope_returns_every_requirement_in_document_order(self):
        payload = await self.requirements()
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 9)])
        self.assertEqual(payload["scope"]["kind"], "all")
        self.assertIn("8 requirements across 5 features", payload["summary"])
        self.assertIn("2 items skipped by item_type_exclude", payload["summary"])
        self.assertEqual(payload["warnings"], [])

    async def test_containers_are_never_requirements(self):
        ids = self.ids(await self.requirements(item_type_exclude=[]))
        for container in ("CWA-CMP-1", "CWA-SET-1", "CWA-FLD-1", "CWA-FLD-2"):
            self.assertNotIn(container, ids)
        self.assertIn("CWA-TXT-1", ids)
        self.assertIn("CWA-TC-1", ids)

    async def test_feature_is_the_nearest_folder(self):
        by_id = {r["id"]: r for r in (await self.requirements())["requirements"]}
        self.assertEqual(by_id["CWA-REQ-1"]["feature"], "Patient Monitoring")
        self.assertEqual(by_id["CWA-REQ-3"]["feature"], "Vitals")
        self.assertEqual(by_id["CWA-REQ-4"]["feature"], "Vitals")
        self.assertEqual(by_id["CWA-REQ-6"]["feature"], "Software Requirements")
        self.assertEqual(by_id["CWA-REQ-8"]["feature"], "Unknown")
        self.assertEqual(
            by_id["CWA-REQ-4"]["raw_fields"]["feature_path"],
            "Product / Software Requirements / Patient Monitoring / Vitals",
        )

    async def test_canonical_fields_are_rendered(self):
        payload = await self.requirements(scope="CWA-REQ-1", field_mapping={"github_url": "Linked to Github"})
        req = payload["requirements"][0]
        self.assertEqual(req["type"], "requirement")
        self.assertEqual(req["title"], "Display vitals")
        self.assertEqual(req["description"], "The system shall display vitals in real time.\n\n- HR\n- SpO2")
        self.assertEqual(req["rationale"], "Clinicians need continuous visibility.")
        self.assertEqual(req["status"], "Approved")
        self.assertEqual(req["linked_ids"], [])
        self.assertEqual(
            req["_enrichment_urls"],
            [{"source": "github", "url": "https://github.com/org/repo/blob/main/docs/sds.md"}],
        )
        raw = req["raw_fields"]
        self.assertEqual(raw["Priority"], "High")
        self.assertEqual(raw["Verification Method"], ["Test", "Inspection"])
        self.assertEqual(raw["item_type"], "Requirement")
        self.assertEqual(raw["jama_id"], 2001)
        self.assertEqual(raw["url"], f"{BASE_URL}/perspective.req#/items/2001?projectId={PROJECT}")
        for absent in ("Owner", "Release", "Status", "Rationale", "Linked to Github", "ID"):
            self.assertNotIn(absent, raw)

    async def test_github_links_need_a_mapping(self):
        req = (await self.requirements(scope="CWA-REQ-1"))["requirements"][0]
        self.assertNotIn("_enrichment_urls", req)
        self.assertEqual(req["raw_fields"]["Linked to Github"], "https://github.com/org/repo/blob/main/docs/sds.md")

    async def test_custom_fields_can_be_left_out(self):
        req = (await self.requirements(scope="CWA-REQ-1", include_custom_fields=False))["requirements"][0]
        self.assertNotIn("Priority", req["raw_fields"])
        self.assertIn("feature_path", req["raw_fields"])
        self.assertEqual(req["status"], "Approved")

    async def test_item_type_filter_keeps_only_listed_types(self):
        payload = await self.call("jama_get_requirements", project_id=PROJECT, item_type_filter=["req"])
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 9)])
        self.assertIn("2 items dropped by item_type_filter", payload["summary"])

    async def test_unmappable_keys_are_reported(self):
        payload = await self.requirements(scope="CWA-REQ-2", field_mapping={"title": "Summary"})
        self.assertTrue(any("title" in warning for warning in payload["warnings"]))

    async def test_an_empty_project_is_a_warning_not_a_crash(self):
        payload = await self.call("jama_get_requirements", project_id=999)
        self.assertEqual(payload["requirements"], [])
        self.assertTrue(any("returned no items" in warning for warning in payload["warnings"]))


class FeatureScope(ServerTestCase):
    async def test_a_feature_includes_its_sub_folders(self):
        payload = await self.requirements(scope="patient monitoring")
        self.assertEqual(self.ids(payload), ["CWA-REQ-1", "CWA-REQ-2", "CWA-REQ-3", "CWA-REQ-4"])
        self.assertEqual(
            payload["scope"],
            {
                "input": "patient monitoring",
                "kind": "feature",
                "matched": ["Product / Software Requirements / Patient Monitoring"],
            },
        )

    async def test_a_sub_feature_alone(self):
        self.assertEqual(self.ids(await self.requirements(scope="Vitals")), ["CWA-REQ-3", "CWA-REQ-4"])

    async def test_duplicate_names_return_both_with_a_warning(self):
        payload = await self.requirements(scope="Alarms")
        self.assertEqual(self.ids(payload), ["CWA-REQ-5", "CWA-REQ-7"])
        self.assertEqual(len(payload["scope"]["matched"]), 2)
        self.assertTrue(any("matches 2 features" in warning for warning in payload["warnings"]))

    async def test_a_path_picks_one_of_the_duplicates(self):
        self.assertEqual(self.ids(await self.requirements(scope="Software Requirements/Alarms")), ["CWA-REQ-5"])
        self.assertEqual(self.ids(await self.requirements(scope="Product / Alarms")), ["CWA-REQ-7"])

    async def test_a_set_can_be_scoped_too(self):
        payload = await self.requirements(scope="Software Requirements")
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 7)])

    async def test_a_requirement_id_returns_its_whole_feature(self):
        payload = await self.requirements(scope="cwa-req-1")
        self.assertEqual(payload["scope"]["kind"], "requirement")
        self.assertEqual(self.ids(payload), ["CWA-REQ-1", "CWA-REQ-2"])
        self.assertEqual(self.ids(await self.requirements(scope="CWA-REQ-4")), ["CWA-REQ-3", "CWA-REQ-4"])

    async def test_no_match_lists_the_features(self):
        payload = await self.requirements(scope="Billing")
        self.assertEqual(payload["requirements"], [])
        self.assertEqual(payload["scope"]["kind"], "none")
        self.assertTrue(any("Available features: Alarms, Patient Monitoring, Vitals" in w for w in payload["warnings"]))

    async def test_another_item_type_can_mark_features(self):
        payload = await self.requirements(scope="CWA-REQ-6", feature_item_type="Set")
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 7)])

    async def test_a_missing_feature_type_falls_back_with_a_warning(self):
        payload = await self.requirements(feature_item_type="Epic")
        self.assertTrue(any("No item type is named 'Epic'" in w for w in payload["warnings"]))
        self.assertEqual(payload["requirements"][0]["feature"], "Patient Monitoring")


class Features(ServerTestCase):
    async def test_folders_are_listed_with_counts(self):
        payload = await self.call("jama_get_features", project_id=PROJECT, item_type_exclude=NON_REQUIREMENT_TYPES)
        rows = {f["path"]: (f["requirements"], f["direct_requirements"], f["depth"]) for f in payload["features"]}
        self.assertEqual(
            rows,
            {
                "Product / Software Requirements / Patient Monitoring": (4, 2, 0),
                "Product / Software Requirements / Patient Monitoring / Vitals": (2, 2, 1),
                "Product / Software Requirements / Alarms": (1, 1, 0),
                "Product / Alarms": (1, 1, 0),
            },
        )
        self.assertEqual({o["name"]: o["requirements"] for o in payload["outside_features"]}, {"Software Requirements": 1, "Unknown": 1})
        self.assertIn("4 features (Folder) holding 6 of 8 requirements; 2 outside any feature", payload["summary"])

    async def test_item_types_are_counted_per_project(self):
        payload = await self.call("jama_get_item_types", project_id=PROJECT)
        counts = {t["display"]: (t["kind"], t["count"]) for t in payload["item_types"]}
        self.assertEqual(counts["Requirement"], ("item", 8))
        self.assertEqual(counts["Folder"], ("folder", 4))
        self.assertEqual(counts["Set"], ("set", 1))
        requirement = next(t for t in payload["item_types"] if t["display"] == "Requirement")
        self.assertNotIn("fields", requirement, "a project's answer stays small")
        payload = await self.call("jama_get_item_types", project_id=PROJECT, with_fields=True)
        requirement = next(t for t in payload["item_types"] if t["display"] == "Requirement")
        self.assertIn({"label": "Status", "name": "status$89", "type": "LOOKUP"}, requirement["fields"])

    async def test_a_projects_item_types_carry_what_setup_maps_fields_and_id_patterns_from(self):
        payload = await self.call("jama_get_item_types", project_id=PROJECT)
        self.assertEqual(payload["document_key_samples"]["Requirement"], [f"CWA-REQ-{n}" for n in range(1, 6)])
        candidates = payload["field_candidates"]
        self.assertEqual((candidates["status"], candidates["rationale"]), (["Status"], ["Rationale"]))
        self.assertEqual(candidates["github_url"],
                         [{"field": "Linked to Github", "first_url": "https://github.com/org/repo/blob/main/docs/sds.md"}])


class RequirementRoots(ServerTestCase):
    async def test_a_root_keeps_only_what_is_inside_it(self):
        payload = await self.requirements(root_ids=[ROOT])
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 7)])
        self.assertIn("2 items outside the requirements root ignored", payload["summary"])

    async def test_features_are_top_level_folders_and_deeper_ones_roll_up(self):
        by_id = {r["id"]: r for r in (await self.requirements(root_ids=[ROOT]))["requirements"]}
        self.assertEqual(by_id["CWA-REQ-1"]["feature"], "Patient Monitoring")
        self.assertEqual(by_id["CWA-REQ-4"]["feature"], "Patient Monitoring")
        self.assertEqual(
            by_id["CWA-REQ-4"]["raw_fields"]["folder_path"],
            "Product / Software Requirements / Patient Monitoring / Vitals",
        )
        self.assertEqual(by_id["CWA-REQ-5"]["feature"], "Alarms")
        self.assertEqual(by_id["CWA-REQ-6"]["feature"], "Software Requirements")

    async def test_depth_two_splits_sub_folders_out(self):
        payload = await self.requirements(root_ids=[ROOT], feature_depth=2)
        by_id = {r["id"]: r["feature"] for r in payload["requirements"]}
        self.assertEqual((by_id["CWA-REQ-1"], by_id["CWA-REQ-3"], by_id["CWA-REQ-4"]), ("Patient Monitoring", "Vitals", "Vitals"))

    async def test_a_requirement_scope_returns_its_whole_top_level_feature(self):
        payload = await self.requirements(root_ids=[ROOT], scope="CWA-REQ-4")
        self.assertEqual(self.ids(payload), [f"CWA-REQ-{n}" for n in range(1, 5)])

    async def test_a_sub_folder_can_still_be_targeted(self):
        self.assertEqual(self.ids(await self.requirements(root_ids=[ROOT], scope="Vitals")), ["CWA-REQ-3", "CWA-REQ-4"])

    async def test_a_same_named_folder_outside_the_root_never_matches(self):
        payload = await self.requirements(root_ids=[ROOT], scope="Alarms")
        self.assertEqual(self.ids(payload), ["CWA-REQ-5"])
        self.assertEqual(payload["warnings"], [])

    async def test_an_excluded_folder_is_left_out(self):
        ids = self.ids(await self.requirements(root_ids=[ROOT], exclude_ids=[1110]))
        self.assertEqual(ids, ["CWA-REQ-1", "CWA-REQ-2", "CWA-REQ-5", "CWA-REQ-6"])

    async def test_features_sharing_a_name_get_their_paths(self):
        features = {r["id"]: r["feature"] for r in (await self.requirements(root_ids=[ROOT, 1300]))["requirements"]}
        self.assertEqual(features["CWA-REQ-5"], "Product / Software Requirements / Alarms")
        self.assertEqual(features["CWA-REQ-7"], "Product / Alarms")
        self.assertEqual(features["CWA-REQ-1"], "Patient Monitoring")

    async def test_a_missing_root_is_loud(self):
        payload = await self.requirements(root_ids=[9999])
        self.assertEqual(payload["requirements"], [])
        self.assertTrue(any("Anchor the scope on the test suite again" in w for w in payload["warnings"]))

    async def test_the_feature_list_shows_only_what_holds_requirements(self):
        payload = await self.call(
            "jama_get_features", project_id=PROJECT, root_ids=[ROOT], item_type_exclude=NON_REQUIREMENT_TYPES
        )
        self.assertEqual(payload["mode"], "roots")
        self.assertEqual(payload["roots"], [{"id": ROOT, "path": "Product / Software Requirements"}])
        self.assertEqual(
            [(f["name"], f["requirements"], f["folders"]) for f in payload["features"]],
            [("Software Requirements", 1, 1), ("Patient Monitoring", 4, 2), ("Alarms", 1, 1)],
        )

    async def test_a_single_item_follows_the_same_features(self):
        inside = await self.call("jama_get_item", item="CWA-REQ-4", root_ids=[ROOT])
        self.assertEqual(inside["requirement"]["feature"], "Patient Monitoring")
        outside = await self.call("jama_get_item", item="CWA-REQ-8", root_ids=[ROOT])
        self.assertTrue(any("outside the configured requirements root" in w for w in outside["warnings"]))


class Background(ServerTestCase):
    """background=true returns the rest of the project — never a requirement in scope."""

    async def background(self, **kwargs):
        kwargs.setdefault("root_ids", [ROOT])
        kwargs.setdefault("item_type_filter", ["Requirement"])
        return await self.requirements(background=True, **kwargs)

    async def test_it_is_everything_outside_the_roots(self):
        payload = await self.background()
        self.assertEqual(self.ids(payload), ["CWA-REQ-7", "CWA-REQ-8"])
        by_id = {r["id"]: r for r in payload["requirements"]}
        self.assertEqual({r["type"] for r in payload["requirements"]}, {"background"})
        self.assertEqual(by_id["CWA-REQ-7"]["feature"], "Alarms")
        self.assertEqual(by_id["CWA-REQ-7"]["raw_fields"]["folder_path"], "Product / Alarms")
        self.assertEqual(by_id["CWA-REQ-8"]["raw_fields"]["folder_path"], "")
        self.assertIn("2 background items — the rest of the project, beside the 6 requirements in scope (Requirement 2)", payload["summary"])

    async def test_other_item_types_inside_the_roots_are_background_too(self):
        payload = await self.background(item_type_exclude=["Test Case"])
        self.assertEqual(self.ids(payload), ["CWA-TXT-1", "CWA-REQ-7", "CWA-REQ-8"])
        self.assertEqual(payload["requirements"][0]["raw_fields"]["folder_path"], "Product / Software Requirements / Patient Monitoring")

    async def test_excluded_types_and_subtrees_stay_out_of_it(self):
        self.assertEqual(self.ids(await self.background(exclude_ids=[1300])), ["CWA-REQ-8"])
        inside_root = await self.background(item_type_exclude=["Test Case"], exclude_ids=[1100])
        self.assertEqual(self.ids(inside_root), ["CWA-REQ-7", "CWA-REQ-8"])

    async def test_without_roots_it_is_the_other_item_types(self):
        payload = await self.background(root_ids=None, item_type_exclude=["Test Case"])
        self.assertEqual(self.ids(payload), ["CWA-TXT-1"])

    async def test_a_scope_is_ignored_and_says_so(self):
        payload = await self.background(scope="Alarms")
        self.assertEqual(self.ids(payload), ["CWA-REQ-7", "CWA-REQ-8"])
        self.assertTrue(any("ignored: background is always read whole" in w for w in payload["warnings"]))


class ScopeProposal(ServerTestCase):
    async def propose(self, keys, **kwargs):
        return await self.call("jama_get_scope_proposal", project_id=PROJECT, document_keys=keys, **kwargs)

    def add_documents(self):
        """A document as deep as a real SRS, an archived copy of its type, and another document beside it.

        Design Inputs (component)
          Mobile SRS (set)
            Scope (folder) — SRS-TXT-1 (text)
            REQUIREMENTS (folder)
              Functional (folder)
                Alerts (folder) — SRS-1, SRS-2
                Setup (folder) — SRS-3
              Non-Functional (folder) — SRS-4      <- no test links to it
          System Requirements (set) — SYS-1, SYS-2
        Archive (component)
          Archived SRS (set) — SRS-90
        """
        for args in (
            (5000, 34, "Design Inputs", None, "3", "CWA-CMP-5"),
            (5100, 33, "Mobile SRS", 5000, "3.1", "CWA-SET-5"),
            (5110, 31, "Scope", 5100, "3.1.1", "CWA-FLD-51"),
            (5111, 32, "Purpose", 5110, "3.1.1.1", "SRS-TXT-1"),
            (5120, 31, "REQUIREMENTS", 5100, "3.1.2", "CWA-FLD-52"),
            (5130, 31, "Functional", 5120, "3.1.2.1", "CWA-FLD-53"),
            (5140, 31, "Alerts", 5130, "3.1.2.1.1", "CWA-FLD-54"),
            (5141, 92, "Sound an alert", 5140, "3.1.2.1.1.1", "SRS-1"),
            (5142, 92, "Snooze an alert", 5140, "3.1.2.1.1.2", "SRS-2"),
            (5150, 31, "Setup", 5130, "3.1.2.1.2", "CWA-FLD-55"),
            (5151, 92, "Pair a sensor", 5150, "3.1.2.1.2.1", "SRS-3"),
            (5160, 31, "Non-Functional", 5120, "3.1.2.2", "CWA-FLD-56"),
            (5161, 92, "Start within 2 seconds", 5160, "3.1.2.2.1", "SRS-4"),
            (5200, 33, "System Requirements", 5000, "3.2", "CWA-SET-6"),
            (5201, 91, "Alarm volume", 5200, "3.2.1", "SYS-1"),
            (5202, 91, "Battery life", 5200, "3.2.2", "SYS-2"),
            (6000, 34, "Archive", None, "4", "CWA-CMP-6"),
            (6100, 33, "Archived SRS", 6000, "4.1", "CWA-SET-7"),
            (6101, 92, "Old alert tone", 6100, "4.1.1", "SRS-90"),
        ):
            self.fake.items[args[0]] = _item(*args)

    async def test_links_inside_one_set_propose_that_set(self):
        payload = await self.propose([f"CWA-REQ-{n}" for n in range(1, 7)])
        self.assertEqual([r["path"] for r in payload["roots"]], ["Product / Software Requirements"])
        self.assertEqual(payload["coverage"], 1.0)
        self.assertEqual(payload["proposed_item_types"], ["Requirement"])
        self.assertEqual(payload["feature_depth"], 1)
        self.assertEqual(
            [f["name"] for f in payload["features"]["list"]], ["Software Requirements", "Patient Monitoring", "Alarms"]
        )
        self.assertEqual(
            payload["requirements"],
            {"in_roots": 6, "of_type_outside_roots": 2,
             "of_type_outside_where": [{"path": "Product / Alarms", "count": 1}, {"path": "", "count": 1}]},
        )

    async def test_the_linked_ids_can_come_from_a_run_file_so_none_pass_through_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp, "proj", ".buddy-council", "runs", "20261108-120000-setup")
            run_dir.mkdir(parents=True)
            ids = run_dir / "requirement_ids.json"
            ids.write_text(json.dumps({"kind": "requirement_ids", "ids": [f"CWA-REQ-{n}" for n in range(1, 7)]}))
            payload = await self.call("jama_get_scope_proposal", project_id=PROJECT, document_keys_file=str(ids))
            self.assertEqual([r["path"] for r in payload["roots"]], ["Product / Software Requirements"])
            self.assertEqual(payload["anchors"]["given"], 6)
            outside = Path(tmp, "elsewhere.json")
            outside.write_text(json.dumps({"ids": ["CWA-REQ-1"]}))
            with self.assertRaises(Exception):
                await server.jama_get_scope_proposal(project_id=PROJECT, document_keys_file=str(outside))
        with self.assertRaises(Exception):
            await server.jama_get_scope_proposal(project_id=PROJECT)

    async def test_links_inside_one_folder_widen_to_their_whole_set_and_keep_its_features(self):
        payload = await self.propose(["CWA-REQ-1", "CWA-REQ-2", "CWA-REQ-3", "CWA-REQ-4"])
        self.assertEqual([r["path"] for r in payload["roots"]], ["Product / Software Requirements"])
        self.assertEqual(
            payload["linked_folders"],
            [{"id": 1100, "path": "Product / Software Requirements / Patient Monitoring", "linked": 4}],
        )
        self.assertEqual(payload["feature_depth"], 2)
        self.assertEqual(
            [(f["name"], f["requirements"], f["linked"]) for f in payload["features"]["list"]],
            [("Software Requirements", 1, 0), ("Patient Monitoring", 2, 2), ("Vitals", 2, 2), ("Alarms", 1, 0)],
        )
        self.assertEqual(payload["features"]["without_links"], ["Software Requirements", "Alarms"])

    async def test_links_split_across_a_component_become_two_documents(self):
        payload = await self.propose(["CWA-REQ-5", "CWA-REQ-7"])
        self.assertEqual(
            [r["path"] for r in payload["roots"]], ["Product / Software Requirements", "Product / Alarms"]
        )
        self.assertEqual(
            [f["name"] for f in payload["features"]["list"]],
            ["Software Requirements", "Patient Monitoring", "Vitals", "Product / Software Requirements / Alarms",
             "Product / Alarms"],
        )

    async def test_a_deep_document_is_taken_whole_with_the_features_where_the_links_are(self):
        self.add_documents()
        payload = await self.propose(["SRS-1", "SRS-2", "SRS-3"], item_type_exclude=NON_REQUIREMENT_TYPES)
        self.assertEqual(payload["roots"], [{"id": 5100, "path": "Design Inputs / Mobile SRS", "kind": "set", "linked": 3}])
        self.assertEqual(
            payload["linked_folders"],
            [{"id": 5130, "path": "Design Inputs / Mobile SRS / REQUIREMENTS / Functional", "linked": 3}],
        )
        self.assertEqual(payload["feature_depth"], 3)
        self.assertEqual(
            [(f["name"], f["requirements"], f["linked"]) for f in payload["features"]["list"]],
            [("Alerts", 2, 2), ("Setup", 1, 1), ("Non-Functional", 1, 0)],
        )
        self.assertEqual(payload["features"]["without_links"], ["Non-Functional"])
        self.assertEqual(
            payload["requirements"],
            {"in_roots": 4, "of_type_outside_roots": 1,
             "of_type_outside_where": [{"path": "Archive / Archived SRS", "count": 1}]},
        )
        self.assertEqual(payload["background"]["items"], 11)
        self.assertEqual(
            payload["background"]["item_types"],
            [{"display": "Requirement", "count": 8}, {"display": "System Requirement", "count": 2},
             {"display": "Mobile SRS Requirement", "count": 1}],
        )
        self.assertIn("features 3 levels below it with 4 requirements", payload["summary"])
        self.assertIn("11 background items elsewhere in the project", payload["summary"])

        read = await self.requirements(root_ids=[5100], feature_depth=3, item_type_filter=["Mobile SRS Requirement"])
        self.assertEqual(
            [(r["id"], r["feature"]) for r in read["requirements"]],
            [("SRS-1", "Alerts"), ("SRS-2", "Alerts"), ("SRS-3", "Setup"), ("SRS-4", "Non-Functional")],
        )

    async def test_stray_links_are_outliers_not_a_wider_root(self):
        for n in range(20):
            self.fake.items[2100 + n] = _item(2100 + n, 89, f"Extra {n}", 1100, f"1.1.1.9.{n}", f"CWA-REQ-{100 + n}")
        keys = [f"CWA-REQ-{n}" for n in range(1, 7)] + [f"CWA-REQ-{100 + n}" for n in range(20)] + ["CWA-REQ-8"]
        payload = await self.propose(keys)
        self.assertEqual([r["path"] for r in payload["roots"]], ["Product / Software Requirements"])
        self.assertEqual(payload["outliers"], {"count": 1, "sample": [{"key": "CWA-REQ-8", "path": ""}]})
        self.assertEqual(payload["coverage"], round(26 / 27, 3))

    async def test_features_without_links_are_called_out(self):
        payload = await self.propose(["CWA-REQ-1", "CWA-REQ-2", "CWA-REQ-3", "CWA-REQ-4", "CWA-REQ-6"])
        self.assertEqual(payload["features"]["without_links"], ["Alarms"])
        self.assertIn("1 of the 3 features have no linked case", payload["summary"])

    async def test_unknown_keys_are_counted(self):
        payload = await self.propose("CWA-REQ-1, NOPE-1\nOLD-SRS-12")
        self.assertEqual(payload["anchors"], {"given": 3, "resolved": 1, "not_found": 2, "not_found_sample": ["NOPE-1", "OLD-SRS-12"]})

    async def test_keys_from_another_project_warn(self):
        payload = await self.propose(["OLD-SRS-1", "OLD-SRS-2"])
        self.assertEqual(payload["roots"], [])
        self.assertTrue(any("None of the 2 linked IDs" in w for w in payload["warnings"]))

    async def test_an_adjusted_root_can_be_previewed(self):
        payload = await self.propose([f"CWA-REQ-{n}" for n in range(1, 7)], root_ids=[1100])
        self.assertEqual([r["id"] for r in payload["roots"]], [1100])
        self.assertEqual(payload["outliers"]["count"], 2)


class Caching(ServerTestCase):
    async def test_the_project_is_read_once_then_cached(self):
        pages = math.ceil(len(ITEMS) / 4)
        with mock.patch.object(server, "_PAGE_SIZE", 4):
            first = await self.requirements(scope="Vitals")
            self.assertEqual(self.fake.count("GET", "/rest/v1/items"), pages)
            self.assertIn("read live", first["summary"])
            second = await self.requirements(scope="Alarms")
            self.assertEqual(self.fake.count("GET", "/rest/v1/items"), pages)
            self.assertIn("cached", second["summary"])
            await self.requirements(scope="Alarms", refresh=True)
            self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 2 * pages)

    async def test_pick_lists_are_read_once_per_list(self):
        await self.requirements()
        await self.requirements(scope="CWA-REQ-1")
        for pick_list in (500, 501, 502):
            self.assertEqual(self.fake.count("GET", f"/rest/v1/picklists/{pick_list}/options"), 1)
        self.assertFalse([p for _, p, _ in self.fake.requests if p.startswith("/rest/v1/picklistoptions/")])

    async def test_concurrent_calls_share_a_pick_list_read(self):
        self.fake.latency = 0.01
        await asyncio.gather(*(self.call("jama_get_item", item="CWA-REQ-1") for _ in range(3)))
        self.assertEqual(self.fake.count("GET", "/rest/v1/picklists/500/options"), 1)
        self.assertFalse([p for _, p, _ in self.fake.requests if p.startswith("/rest/v1/picklistoptions/")])

    async def test_truncation_is_reported(self):
        with mock.patch.object(server, "_MAX_ITEMS", 10), mock.patch.object(server, "_PAGE_SIZE", 5):
            payload = await self.requirements()
        self.assertTrue(payload["truncated"])
        self.assertTrue(any("results are incomplete" in w for w in payload["warnings"]))


class Paging(ServerTestCase):
    async def test_large_results_page_without_extra_jama_requests(self):
        with mock.patch.object(server, "_PAGE_CHAR_BUDGET", 900):
            page = await self.requirements()
            self.assertEqual(page["total"], 8)
            self.assertIsNotNone(page["next_offset"])
            reads = self.fake.count("GET", "/rest/v1/items")
            collected = list(page["requirements"])
            pages = 1
            while page["next_offset"] is not None:
                offset = page["next_offset"]
                page = await self.requirements(offset=offset)
                self.assertEqual(page["offset"], offset)
                collected.extend(page["requirements"])
                pages += 1
        self.assertGreater(pages, 1)
        self.assertEqual([r["id"] for r in collected], [f"CWA-REQ-{n}" for n in range(1, 9)])
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), reads, "later pages must come from the cache")
        self.assertIn("last page", page["summary"])

    async def test_an_oversized_requirement_still_makes_progress(self):
        with mock.patch.object(server, "_PAGE_CHAR_BUDGET", 10):
            payload = await self.requirements()
        self.assertEqual(len(payload["requirements"]), 1)
        self.assertEqual(payload["next_offset"], 1)

    async def test_small_results_fit_one_page(self):
        payload = await self.requirements(scope="Vitals")
        self.assertEqual((payload["total"], payload["offset"], payload["next_offset"]), (2, 0, None))
        self.assertNotIn("page ", payload["summary"])


class Transport(ServerTestCase):
    async def test_rate_limits_are_retried_after_the_advertised_delay(self):
        self.fake.throttle["users/current"] = 2
        payload = await self.call("jama_get_current_user")
        self.assertEqual(payload["user"]["name"], "Jane Doe")
        self.assertEqual(self.fake.sleeps, [2.0, 2.0])

    async def test_a_persistent_rate_limit_is_an_error(self):
        self.fake.throttle["users/current"] = 10
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_current_user")
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(len(self.fake.sleeps), server._MAX_RATE_LIMIT_RETRIES)

    async def test_a_dropped_connection_is_retried(self):
        self.fake.dropped["users/current"] = 2
        payload = await self.call("jama_get_current_user")
        self.assertEqual(payload["user"]["name"], "Jane Doe")
        self.assertEqual(self.fake.sleeps, [1.0, 2.0])

    async def test_a_connection_that_keeps_failing_is_an_error(self):
        self.fake.dropped["users/current"] = 10
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_current_user")
        self.assertIn("Could not reach", str(ctx.exception))
        self.assertEqual(len(self.fake.sleeps), server._MAX_NETWORK_RETRIES)

    async def test_an_expired_token_is_refreshed_once(self):
        await self.call("jama_get_current_user")
        self.fake.rejected_tokens.add("tok-1")
        payload = await self.call("jama_get_projects")
        self.assertEqual(self.fake.tokens_issued, 2)
        self.assertEqual([p["key"] for p in payload["projects"]], ["CWA"])

    async def test_a_revoked_account_fails_after_one_refresh(self):
        self.fake.rejected_tokens.update(f"tok-{n}" for n in range(1, 10))
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_current_user")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("save it, and try again", str(ctx.exception), "the command asks for the key on the spot")
        self.assertEqual(self.fake.tokens_issued, 2)

    async def test_rejected_client_credentials_never_echo_the_secret(self):
        self.fake.client_secret = "rotated"
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_current_user")
        self.assertIn("Set API Credentials", str(ctx.exception))
        self.assertNotIn("s3cret", str(ctx.exception))

    async def test_current_user_omits_personal_contact_details(self):
        payload = await self.call("jama_get_current_user")
        self.assertEqual(payload["auth"], "oauth")
        self.assertNotIn("jane@example.com", json.dumps(payload))

    async def test_a_redirect_explains_itself(self):
        def handler(request):
            if request.url.path == "/rest/oauth/token":
                return httpx.Response(200, json={"access_token": "tok-x", "expires_in": 3600})
            return httpx.Response(302, headers={"location": "https://sso.example/login"})

        client = server.JamaClient(server.JamaConfig(BASE_URL, "cid", "s3cret"), transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(server.JamaError) as ctx:
                await client.get("users/current")
            self.assertIn("single sign-on", str(ctx.exception))
        finally:
            await client.aclose()


class BasicAuth(ServerTestCase):
    def make_fake(self):
        return FakeJama(client_id="", client_secret="", username="jdoe", password="pw")

    async def test_basic_auth_never_asks_for_a_token(self):
        payload = await self.call("jama_get_current_user")
        self.assertEqual(payload["auth"], "basic")
        self.assertEqual(self.fake.count("POST", "/rest/oauth/token"), 0)


class SingleItem(ServerTestCase):
    async def test_an_item_comes_with_its_traceability(self):
        payload = await self.call("jama_get_item", item="cwa-req-1")
        req = payload["requirement"]
        self.assertEqual(req["id"], "CWA-REQ-1")
        self.assertEqual(req["feature"], "Patient Monitoring")
        self.assertEqual(req["raw_fields"]["feature_path"], "Product / Software Requirements / Patient Monitoring")
        self.assertEqual(req["linked_ids"], ["CWA-REQ-8", "CWA-TC-1"])
        self.assertEqual(
            payload["relationships"]["upstream"],
            [{"id": "CWA-REQ-8", "jama_id": 2008, "title": "Root requirement", "relationship": "Derived From", "suspect": False}],
        )
        downstream = payload["relationships"]["downstream"][0]
        self.assertEqual((downstream["id"], downstream["relationship"], downstream["suspect"]), ("CWA-TC-1", "Verified By", True))
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 0, "a single item must not read the whole project")

    async def test_relationships_without_include_support(self):
        self.fake.reject_include = True
        payload = await self.call("jama_get_item", item="2001")
        self.assertEqual(payload["requirement"]["linked_ids"], ["CWA-REQ-8", "CWA-TC-1"])
        self.assertEqual(self.fake.count("GET", "/rest/v1/abstractitems/2008"), 1)

    async def test_a_warm_project_cache_replaces_the_ancestor_walk(self):
        await self.requirements()
        before = len(self.fake.requests)
        await self.call("jama_get_item", item="2003")
        paths = [p for _, p, _ in self.fake.requests[before:]]
        self.assertNotIn("/rest/v1/items/1110", paths)

    async def test_a_repeat_lookup_is_served_from_the_cache(self):
        await self.call("jama_get_item", item="CWA-REQ-3")
        before = len(self.fake.requests)
        await self.call("jama_get_item", item="CWA-REQ-3")
        self.assertEqual(len(self.fake.requests), before)

    async def test_an_unknown_document_key_is_a_404(self):
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_item", item="CWA-REQ-999", project_id=PROJECT)
        self.assertEqual(ctx.exception.status, 404)


class ManyItems(ServerTestCase):
    async def items(self, keys, **kwargs):
        return await self.call("jama_get_items", items=keys, **kwargs)

    def reads(self, *item_ids):
        return {i: self.fake.count("GET", f"/rest/v1/items/{i}") for i in item_ids}

    @staticmethod
    def keys_of(payload):
        return [entry["requirement"]["id"] for entry in payload["items"]]

    async def test_items_come_back_in_request_order_with_traceability(self):
        payload = await self.items(["CWA-REQ-5", "cwa-req-1", "2003", "CWA-REQ-999", "77777"])
        self.assertEqual(self.keys_of(payload), ["CWA-REQ-5", "CWA-REQ-1", "CWA-REQ-3"])
        requirements = [entry["requirement"] for entry in payload["items"]]
        self.assertEqual([r["feature"] for r in requirements], ["Alarms", "Patient Monitoring", "Vitals"])
        self.assertEqual(requirements[1]["linked_ids"], ["CWA-REQ-8", "CWA-TC-1"])
        self.assertEqual(requirements[1]["status"], "Approved")
        self.assertEqual(payload["not_found"], ["CWA-REQ-999", "77777"])
        self.assertEqual(payload["failed"], [])
        self.assertIsNone(payload["next_offset"])
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 0, "a batch must not read the whole project")

    async def test_each_entry_matches_jama_get_item(self):
        single = await self.call("jama_get_item", item="CWA-REQ-1")
        server.reset_state(self.client)
        [entry] = (await self.items(["CWA-REQ-1"]))["items"]
        self.assertEqual(entry["requirement"], single["requirement"])
        self.assertEqual(entry["relationships"], single["relationships"])

    async def test_keys_are_searched_several_to_a_request(self):
        keys = [f"CWA-REQ-{n}" for n in range(1, 9)] + ["CWA-FLD-1", "CWA-SET-1", "CWA-CMP-1", "CWA-TXT-1"]
        with mock.patch.object(server, "_KEYS_PER_LOOKUP", 5):
            payload = await self.items(keys, include_relationships=False)
        self.assertEqual(self.keys_of(payload), keys)
        searches = [q for _, p, q in self.fake.requests if p == "/rest/v1/abstractitems" and "documentKey" in q]
        self.assertEqual(len(searches), 3)

    async def test_shared_parents_are_read_once(self):
        await self.items(["CWA-REQ-1", "CWA-REQ-2", "CWA-REQ-3", "CWA-REQ-4", "CWA-REQ-5"])
        self.assertEqual(self.reads(1000, 1001, 1100, 1110, 1200, 2003), dict.fromkeys((1000, 1001, 1100, 1110, 1200, 2003), 1))

    async def test_search_results_with_a_location_spare_every_item_read(self):
        self.fake.search_location = True  # as on Jama Cloud: one container search places every item
        payload = await self.items(["CWA-REQ-1", "CWA-REQ-4", "CWA-REQ-5"], include_relationships=False)
        self.assertEqual([e["requirement"]["feature"] for e in payload["items"]], ["Patient Monitoring", "Vitals", "Alarms"])
        self.assertEqual(payload["items"][1]["requirement"]["raw_fields"]["feature_path"], "Product / Software Requirements / Patient Monitoring / Vitals")
        item_reads = [p for _, p, _ in self.fake.requests if p.startswith("/rest/v1/items/")]
        self.assertEqual(item_reads, ["/rest/v1/items/2003"], "only the requirement a requirement is nested under")
        await self.items(["CWA-REQ-2", "CWA-REQ-7"], include_relationships=False, project_id=PROJECT)
        container_searches = [q for _, p, q in self.fake.requests if p == "/rest/v1/abstractitems" and "itemType" in q]
        self.assertEqual(len(container_searches), 1, "the containers are cached per project")

    async def test_without_locations_in_search_results_the_tree_is_walked(self):
        payload = await self.items(["CWA-REQ-4"], include_relationships=False)
        self.assertEqual(payload["items"][0]["requirement"]["raw_fields"]["feature_path"], "Product / Software Requirements / Patient Monitoring / Vitals")
        await self.items(["CWA-REQ-5"], include_relationships=False)
        container_searches = [q for _, p, q in self.fake.requests if p == "/rest/v1/abstractitems" and "itemType" in q]
        self.assertEqual(len(container_searches), 1, "an instance without locations is not searched again")

    async def test_a_repeat_call_is_served_from_the_cache(self):
        keys = ["CWA-REQ-1", "CWA-REQ-3", "CWA-REQ-999"]
        first = await self.items(keys)
        before = len(self.fake.requests)
        second = await self.items(keys)
        self.assertEqual(len(self.fake.requests), before)
        self.assertEqual(second["items"], first["items"])
        self.assertEqual(second["not_found"], ["CWA-REQ-999"])
        await self.items(keys, refresh=True)
        self.assertGreater(len(self.fake.requests), before)

    async def test_a_large_batch_pages_without_reading_anything_twice(self):
        keys = [f"CWA-REQ-{n}" for n in range(1, 9)]
        with mock.patch.object(server, "_PAGE_CHAR_BUDGET", 2500), mock.patch.object(server, "_BATCH_WINDOW", 3):
            page = await self.items(keys)
            collected, pages = self.keys_of(page), 1
            while page["next_offset"] is not None:
                offset = page["next_offset"]
                page = await self.items(keys, offset=offset)
                self.assertEqual(page["offset"], offset)
                collected += self.keys_of(page)
                pages += 1
        self.assertGreater(pages, 1)
        self.assertEqual(collected, keys)
        self.assertIn("last page", page["summary"])
        for item_id in range(2001, 2009):
            self.assertLessEqual(self.fake.count("GET", f"/rest/v1/items/{item_id}"), 1, item_id)
            self.assertLessEqual(self.fake.count("GET", f"/rest/v1/items/{item_id}/upstreamrelationships"), 1, item_id)

    async def test_an_oversized_entry_still_makes_progress(self):
        with mock.patch.object(server, "_PAGE_CHAR_BUDGET", 10):
            payload = await self.items(["CWA-REQ-1", "CWA-REQ-2"])
        self.assertEqual((self.keys_of(payload), payload["next_offset"]), (["CWA-REQ-1"], 1))

    async def test_a_slow_instance_still_answers_in_time(self):
        keys = [f"CWA-REQ-{n}" for n in range(1, 9)]
        with mock.patch.object(server, "_BATCH_WINDOW", 2), mock.patch.object(server, "_BATCH_TIME_BUDGET_SECONDS", 0):
            payload = await self.items(keys)
        self.assertEqual((self.keys_of(payload), payload["next_offset"]), (["CWA-REQ-1", "CWA-REQ-2"], 2))

    async def test_one_unreadable_item_does_not_sink_the_batch(self):
        self.fake.broken.add("items/2005")
        payload = await self.items(["CWA-REQ-1", "2005", "CWA-REQ-2"])
        self.assertEqual(self.keys_of(payload), ["CWA-REQ-1", "CWA-REQ-2"])
        self.assertEqual([f["item"] for f in payload["failed"]], ["2005"])
        self.assertIn("HTTP 500", payload["failed"][0]["error"])

    async def test_traceability_can_be_skipped(self):
        payload = await self.items(["CWA-REQ-1"], include_relationships=False)
        [entry] = payload["items"]
        self.assertNotIn("relationships", entry)
        self.assertEqual(entry["requirement"]["linked_ids"], [])
        self.assertIn("linked_ids is empty", payload["summary"])
        self.assertFalse([p for _, p, _ in self.fake.requests if p.endswith("relationships")])

    async def test_custom_fields_can_be_left_out(self):
        full = await self.items(["CWA-REQ-1"])
        lean = await self.items(["CWA-REQ-1"], include_custom_fields=False)
        self.assertIn("Priority", full["items"][0]["requirement"]["raw_fields"])
        self.assertNotIn("Priority", lean["items"][0]["requirement"]["raw_fields"])

    async def test_features_follow_the_requirements_root(self):
        listed = await self.requirements(root_ids=[ROOT])
        expected = {r["id"]: r["feature"] for r in listed["requirements"]}
        server.reset_state(self.client)
        payload = await self.items(["CWA-REQ-4", "CWA-REQ-5", "CWA-REQ-7"], root_ids=[ROOT])
        features = {e["requirement"]["id"]: e["requirement"]["feature"] for e in payload["items"]}
        self.assertEqual(features["CWA-REQ-4"], expected["CWA-REQ-4"])
        self.assertEqual(features["CWA-REQ-5"], expected["CWA-REQ-5"])
        outside = next(e for e in payload["items"] if e["requirement"]["id"] == "CWA-REQ-7")
        self.assertTrue(any("outside the configured requirements root" in w for w in outside["warnings"]))

    async def test_a_string_of_keys_is_accepted_and_deduplicated(self):
        payload = await self.items("CWA-REQ-1, cwa-req-1 CWA-REQ-2")
        self.assertEqual((payload["requested"], self.keys_of(payload)), (2, ["CWA-REQ-1", "CWA-REQ-2"]))

    async def test_an_oversized_request_is_refused(self):
        with mock.patch.object(server, "_MAX_BATCH_ITEMS", 3), self.assertRaises(server.JamaError):
            await self.items("CWA-REQ-1, CWA-REQ-2 CWA-REQ-3;CWA-REQ-4")

    async def test_an_instance_that_rejects_include_is_not_asked_again(self):
        self.fake.reject_include = True
        await self.call("jama_get_item", item="CWA-REQ-1")
        before = len(self.fake.requests)
        payload = await self.items(["CWA-REQ-2", "CWA-REQ-5"])
        self.assertEqual(len(payload["items"]), 2)
        later = [q for _, p, q in self.fake.requests[before:] if p.endswith("relationships")]
        self.assertEqual(len(later), 4)
        self.assertFalse([q for q in later if "include" in q])


class Concurrency(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake.latency = 0.01

    async def test_pages_are_read_together_within_the_cap_and_kept_in_order(self):
        with mock.patch.object(server, "_PAGE_SIZE", 1):
            listing = await self.client.get_all("items", {"project": PROJECT})
        self.assertEqual([i["id"] for i in listing.data], [i["id"] for i in ITEMS])
        self.assertEqual(listing.pages, len(ITEMS))
        self.assertEqual(self.fake.max_in_flight, server._MAX_CONCURRENT_REQUESTS)

    async def test_progress_is_reported_as_pages_arrive(self):
        seen = []
        with mock.patch.object(server, "_PAGE_SIZE", 4):
            await self.client.get_all("items", {"project": PROJECT}, on_page=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 4), (2, 4), (3, 4), (4, 4)])

    async def test_a_listing_without_totals_is_read_page_by_page(self):
        self.fake.omit_totals = True
        with mock.patch.object(server, "_PAGE_SIZE", 5):
            listing = await self.client.get_all("items", {"project": PROJECT})
        self.assertEqual([i["id"] for i in listing.data], [i["id"] for i in ITEMS])
        self.assertEqual((listing.pages, self.fake.max_in_flight), (5, 1))

    async def test_an_instance_with_smaller_pages_is_still_read_in_full(self):
        self.fake.page_cap = 3
        listing = await self.client.get_all("items", {"project": PROJECT})
        self.assertEqual([i["id"] for i in listing.data], [i["id"] for i in ITEMS])
        self.assertEqual(listing.pages, math.ceil(len(ITEMS) / 3))

    async def test_the_project_summary_counts_its_requests(self):
        with mock.patch.object(server, "_PAGE_SIZE", 2):
            payload = await self.requirements()
        self.assertIn(f"read live ({len(ITEMS)} items, {len(ITEMS) // 2} requests)", payload["summary"])


class BackgroundRead(ServerTestCase):
    """A project read the client would time out on carries on in the background."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake.hold = asyncio.Event()
        self.patches = [mock.patch.object(server, "_PAGE_SIZE", 2), mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 0.01)]
        for patch in self.patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        await super().asyncTearDown()

    async def finish_reads(self):
        loads = [load.task for load in server._STATES[SITE].loads.values()]
        self.fake.hold.set()
        if loads:
            await asyncio.wait(loads)

    async def test_a_slow_read_reports_progress_then_answers(self):
        first = await self.requirements()
        self.assertTrue(first["loading"])
        self.assertEqual((first["project_id"], first["pages_read"], first["pages_total"]), (PROJECT, 1, 8))
        self.assertIn("call this tool again with the same arguments", first["summary"])
        await self.finish_reads()
        second = await self.requirements()
        self.assertEqual(self.ids(second), [f"CWA-REQ-{n}" for n in range(1, 9)])
        self.assertIn("read live", second["summary"])
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 8, "the retry must reuse the first read")

    async def test_every_project_tool_reports_progress_and_shares_the_read(self):
        for tool, kwargs in (
            ("jama_get_features", {}),
            ("jama_get_item_types", {}),
            ("jama_get_scope_proposal", {"document_keys": ["CWA-REQ-1"]}),
        ):
            payload = await self.call(tool, project_id=PROJECT, **kwargs)
            self.assertTrue(payload.get("loading"), tool)
        await self.finish_reads()
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 8)

    async def test_a_refresh_retry_gets_the_read_it_started(self):
        self.assertTrue((await self.requirements(refresh=True))["loading"])
        self.assertTrue((await self.requirements(refresh=True))["loading"])
        await self.finish_reads()
        self.assertEqual(len((await self.requirements(refresh=True))["requirements"]), 8)
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 8, "one read for all three calls")
        with mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 5):
            await self.requirements(refresh=True)
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 16, "a deliberate refresh still re-reads")

    async def test_later_pages_ignore_refresh(self):
        await self.finish_reads()
        with mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 5), mock.patch.object(server, "_PAGE_CHAR_BUDGET", 900):
            page = await self.requirements(refresh=True)
            while page["next_offset"] is not None:
                page = await self.requirements(refresh=True, offset=page["next_offset"])
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 8)

    async def test_a_failed_background_read_is_reported_once_then_retried(self):
        self.fake.hold_from = 0
        self.fake.broken.add("items")
        self.assertTrue((await self.requirements())["loading"])
        await self.finish_reads()
        with self.assertRaises(server.JamaError) as ctx:
            await self.requirements()
        self.assertIn(f"Reading project {PROJECT} from Jama failed", str(ctx.exception))
        self.fake.broken.clear()
        with mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 5):
            self.assertEqual(len((await self.requirements())["requirements"]), 8)

    async def test_a_cancelled_call_leaves_the_read_running(self):
        with mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 5):
            call = asyncio.ensure_future(self.requirements())
            await asyncio.sleep(0.01)
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
        await self.finish_reads()
        self.assertEqual(len((await self.requirements())["requirements"]), 8)
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 8)


class SaveToRunFolder(ServerTestCase):
    """With save_to, the whole result goes to a file in the run folder and the response stays small."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name, "proj", ".buddy-council", "runs", "20261012-coverage")
        self.run_dir.mkdir(parents=True)
        self.target = str(self.run_dir / "requirements.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()
        await super().asyncTearDown()

    async def test_every_requirement_is_written_and_none_returned(self):
        with mock.patch.object(server, "_PAGE_CHAR_BUDGET", 900):
            payload = await self.requirements(save_to=self.target, offset=3)
        self.assertNotIn("requirements", payload)
        self.assertNotIn("next_offset", payload)
        self.assertEqual((payload["total"], payload["saved_to"]), (8, str(self.run_dir.resolve() / "requirements.json")))
        self.assertIn("8 written to", payload["summary"])
        written = json.loads(Path(self.target).read_text())
        self.assertEqual((written["kind"], written["source"], written["count"]), ("requirements", "jama", 8))
        self.assertEqual(written["site"], SITE, "bc_run.py link checks the run came from the project's site")
        self.assertEqual([r["id"] for r in written["requirements"]], [f"CWA-REQ-{n}" for n in range(1, 9)])
        self.assertEqual(written["scope"]["kind"], "all")
        self.assertEqual(payload["bytes"], Path(self.target).stat().st_size)
        self.assertEqual(Path(self.target).stat().st_mode & 0o777, 0o600)

    async def test_a_scoped_read_saves_only_the_scope(self):
        await self.requirements(scope="Vitals", save_to=self.target)
        written = json.loads(Path(self.target).read_text())
        self.assertEqual([r["id"] for r in written["requirements"]], ["CWA-REQ-3", "CWA-REQ-4"])
        self.assertEqual(written["scope"]["kind"], "feature")

    async def test_the_file_records_the_arguments_it_was_read_with(self):
        await self.requirements(root_ids=[ROOT], feature_depth=2, field_mapping={"status": "Status"}, save_to=self.target)
        args = json.loads(Path(self.target).read_text())["args"]
        self.assertEqual(
            args,
            {"item_type_filter": [], "item_type_exclude": NON_REQUIREMENT_TYPES, "root_ids": [ROOT], "exclude_ids": [],
             "feature_depth": 2, "feature_item_type": "Folder", "field_mapping": {"status": "Status"}, "background": False},
        )

    async def test_background_is_written_with_its_own_kind(self):
        target = str(self.run_dir / "background.json")
        payload = await self.requirements(root_ids=[ROOT], item_type_filter=["Requirement"], background=True, save_to=target)
        self.assertEqual(payload["total"], 2)
        self.assertIn("2 background items — the rest of the project, beside the 6 requirements in scope", payload["summary"])
        written = json.loads(Path(target).read_text())
        self.assertEqual((written["kind"], written["count"], written["scope"]["kind"]), ("background", 2, "background"))
        self.assertTrue(written["args"]["background"])
        self.assertEqual([r["id"] for r in written["requirements"]], ["CWA-REQ-7", "CWA-REQ-8"])

    async def test_one_call_writes_the_requirements_and_the_background_with_the_same_arguments(self):
        rest = str(self.run_dir / "background.json")
        payload = await self.requirements(root_ids=[ROOT], item_type_filter=["Requirement"], feature_depth=2,
                                          field_mapping={"status": "Status"}, save_to=self.target, background_save_to=rest)
        self.assertEqual((payload["total"], payload["background"]["total"]), (6, 2))
        self.assertEqual(payload["background"]["saved_to"], str(self.run_dir.resolve() / "background.json"))
        requirements, background = (json.loads(Path(p).read_text()) for p in (self.target, rest))
        self.assertEqual((requirements["kind"], background["kind"]), ("requirements", "background"))
        self.assertEqual([r["id"] for r in background["requirements"]], ["CWA-REQ-7", "CWA-REQ-8"])
        same = {k: v for k, v in background["args"].items() if k != "background"}
        self.assertEqual(same, {k: v for k, v in requirements["args"].items() if k != "background"},
                         "the item-type filter decides what is background, so it can't be left off one of them")
        self.assertTrue(background["args"]["background"])
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 1, "one read of the project tree for both files")

    async def test_background_save_to_needs_a_requirements_read_and_a_run_folder(self):
        rest = str(self.run_dir / "background.json")
        for kwargs in ({"background_save_to": rest},
                       {"background_save_to": rest, "save_to": self.target, "background": True},
                       {"background_save_to": str(Path(self.tmp.name, "background.json")), "save_to": self.target}):
            with self.assertRaises(server.JamaError, msg=kwargs):
                await self.requirements(**kwargs)
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 0)
        self.assertFalse(Path(rest).exists())

    async def test_paths_outside_a_run_folder_are_refused_before_jama_is_read(self):
        outside = Path(self.tmp.name, "proj", "requirements.json")
        bad = [
            "requirements.json",
            str(outside),
            str(self.run_dir / "Requirements.json"),
            str(self.run_dir / "requirements.txt"),
            str(self.run_dir / ".." / "x" / "requirements.json"),
            str(self.run_dir.parent / "missing-run" / "requirements.json"),
            str(Path(self.tmp.name, "proj", ".buddy-council", "requirements.json")),
        ]
        for path in bad:
            with self.assertRaises(server.JamaError, msg=path):
                await self.requirements(save_to=path)
        self.assertEqual(self.fake.count("GET", "/rest/v1/items"), 0)
        self.assertFalse(outside.exists())

    async def test_a_symlink_is_never_followed(self):
        victim = Path(self.tmp.name, "victim.txt")
        victim.write_text("keep me")
        Path(self.target).symlink_to(victim)
        with self.assertRaises(server.JamaError):
            await self.requirements(save_to=self.target)
        elsewhere = Path(self.tmp.name, "elsewhere")
        elsewhere.mkdir()
        linked_run = self.run_dir.parent / "linked-run"
        linked_run.symlink_to(elsewhere)
        with self.assertRaises(server.JamaError):
            await self.requirements(save_to=str(linked_run / "requirements.json"))
        self.assertEqual(victim.read_text(), "keep me")
        self.assertEqual(list(elsewhere.iterdir()), [])

    async def test_nothing_is_written_while_the_project_is_still_loading(self):
        self.fake.hold = asyncio.Event()
        with mock.patch.object(server, "_PAGE_SIZE", 2), mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 0.01):
            payload = await self.requirements(save_to=self.target)
            self.assertTrue(payload["loading"])
            self.assertFalse(Path(self.target).exists())
            self.fake.hold.set()
            await asyncio.wait([load.task for load in server._STATES[SITE].loads.values()])
            payload = await self.requirements(save_to=self.target)
        self.assertEqual(payload["total"], 8)
        self.assertTrue(Path(self.target).exists())


class TwoSites(unittest.IsolatedAsyncioTestCase):
    """Two Jama sites side by side: a call reaches only the site it names, with only that site's credentials."""

    A, B = "a.jama.test", "b.jama.test"

    async def asyncSetUp(self):
        _forget_keys()
        self.fakes = {
            self.A: FakeJama(client_id="cid-a", client_secret="secret-a", token_prefix="tokA"),
            self.B: FakeJama(client_id="", client_secret="", username="jdoe-b", password="pw-b"),
        }
        _save_key(self.fakes[self.A], "https://a.jama.test")
        _save_key(self.fakes[self.B], "https://b.jama.test/jama")  # self-hosted under a context path
        self.seen: list[tuple[str, str, str]] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            host, path = request.url.host, request.url.path
            self.seen.append((host, path, request.headers.get("authorization", "")))
            if host == self.B:
                self.assertTrue(path.startswith("/jama/rest/"), path)
                request = httpx.Request(request.method, str(request.url).replace("/jama/rest/", "/rest/", 1),
                                        headers=request.headers, content=request.content)
            return await self.fakes[host].handle_async(request)

        options = mock.patch.object(server, "_CLIENT_OPTIONS", {"transport": httpx.MockTransport(handle)})
        options.start()
        self.addCleanup(options.stop)
        server.reset_state()

    async def asyncTearDown(self):
        for state in server._STATES.values():
            await state.client.aclose()
        server.reset_state()

    async def call(self, tool, **kwargs):
        return json.loads(await getattr(server, tool)(**kwargs))

    def credentials(self, host):
        basic = lambda user, secret: "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()  # noqa: E731
        if host == self.A:
            return {basic("cid-a", "secret-a")} | {f"Bearer tokA-{n}" for n in range(1, 5)}
        return {basic("jdoe-b", "pw-b")}

    async def test_each_site_is_read_with_only_its_own_credentials(self):
        for site, expected in (("https://a.jama.test", "https://a.jama.test"), ("b.jama.test/jama", "https://b.jama.test/jama"),
                               ("a.jama.test", "https://a.jama.test"), ("https://b.jama.test/jama/perspective.req#/items/1",
                                                                         "https://b.jama.test/jama")):
            self.assertEqual((await self.call("jama_get_current_user", site=site))["base_url"], expected, site)
        self.assertEqual({host for host, _, _ in self.seen}, {self.A, self.B})
        for host, path, authorization in self.seen:
            self.assertIn(authorization, self.credentials(host), f"{path} on {host}")
        self.assertEqual(self.fakes[self.A].tokens_issued, 1, "a's token is reused, never sent to b")

    async def test_both_sites_at_once_keep_their_credentials_apart(self):
        calls = [self.call("jama_get_projects", site=site) for site in [self.A, "b.jama.test/jama"] * 4]
        self.assertEqual(len(await asyncio.gather(*calls)), 8)
        for host, path, authorization in self.seen:
            self.assertIn(authorization, self.credentials(host), f"{path} on {host}")

    async def test_each_site_keeps_its_own_project_tree(self):
        args = {"project_id": PROJECT, "item_type_exclude": NON_REQUIREMENT_TYPES}
        a = await self.call("jama_get_requirements", site=self.A, **args)
        b = await self.call("jama_get_requirements", site="b.jama.test/jama", **args)
        self.assertEqual((a["total"], b["total"]), (8, 8))
        self.assertTrue(all(r["raw_fields"]["url"].startswith("https://a.jama.test/") for r in a["requirements"]))
        self.assertTrue(all(r["raw_fields"]["url"].startswith("https://b.jama.test/jama/") for r in b["requirements"]))
        for host in (self.A, self.B):
            self.assertEqual(self.fakes[host].count("GET", "/rest/v1/items"), 1, f"{host} read its own tree once")

    async def test_no_site_with_two_keys_reads_nothing(self):
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_projects")
        self.assertIn("a.jama.test", str(ctx.exception))
        self.assertIn("b.jama.test/jama", str(ctx.exception))
        with self.assertRaises(server.JamaError):
            await self.call("jama_get_projects", site="https://c.jama.test")
        self.assertEqual(self.seen, [])

    async def test_a_replaced_key_is_used_on_the_next_call(self):
        await self.call("jama_get_current_user", site=self.A)
        self.fakes[self.A].client_secret = "rotated-a"
        server.bc_keys.write_key("jama", "https://a.jama.test", {"client_id": "cid-a", "client_secret": "rotated-a"})
        await self.call("jama_get_current_user", site=self.A)
        self.assertEqual(self.fakes[self.A].tokens_issued, 2, "the new key fetched its own token, with no restart")


class SetupCheck(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakeJama()
        _forget_keys()
        _save_key(self.fake)

    async def asyncTearDown(self):
        server.reset_state()

    async def test_without_a_project_it_lists_projects(self):
        report = await server._check(None, transport=self.fake.transport(), site="https://jama.test/perspective.req")
        self.assertTrue(report["ok"])
        self.assertEqual(report["base_url"], BASE_URL)
        self.assertEqual(report["user"]["name"], "Jane Doe")
        self.assertEqual(report["projects"], [{"id": PROJECT, "key": "CWA", "name": "Clinical Window App"}])

    async def test_with_a_project_it_describes_features_types_and_fields(self):
        report = await server._check(PROJECT, transport=self.fake.transport())
        self.assertEqual(report["project"], {"id": PROJECT, "key": "CWA", "name": "Clinical Window App"})
        self.assertEqual(report["features"]["count"], 4)
        self.assertIn("Product / Software Requirements / Patient Monitoring / Vitals", report["features"]["sample"])
        self.assertEqual(report["requirements_outside_features"], 2)
        kinds = {t["display"]: (t["kind"], t["count"]) for t in report["item_types"]}
        self.assertEqual(kinds["Requirement"], ("item", 8))
        self.assertEqual(kinds["Text"], ("item", 1))
        self.assertEqual(kinds["Test Case"], ("item", 1))
        self.assertEqual(report["document_key_samples"]["Requirement"], [f"CWA-REQ-{n}" for n in range(1, 6)])
        candidates = report["field_candidates"]
        self.assertEqual(candidates["status"], ["Status"])
        self.assertEqual(candidates["rationale"], ["Rationale"])
        self.assertEqual(
            candidates["github_url"],
            [{"field": "Linked to Github", "first_url": "https://github.com/org/repo/blob/main/docs/sds.md"}],
        )

    async def test_anchors_add_a_scope_proposal(self):
        report = await server._check(PROJECT, transport=self.fake.transport(), anchors=["CWA-REQ-1", "CWA-REQ-5"])
        self.assertEqual([r["path"] for r in report["scope_proposal"]["roots"]], ["Product / Software Requirements"])

    async def test_the_check_waits_for_a_slow_project_read(self):
        self.fake.latency = 0.01
        with mock.patch.object(server, "_SNAPSHOT_WAIT_SECONDS", 0), mock.patch.object(server, "_PAGE_SIZE", 2):
            report = await server._check(PROJECT, transport=self.fake.transport())
        self.assertEqual(report["items"], len(ITEMS))

    async def test_bad_credentials_raise(self):
        self.fake.client_secret = "rotated"
        with self.assertRaises(server.JamaError):
            await server._check(None, transport=self.fake.transport())


class Configuration(unittest.TestCase):
    def test_base_url_is_normalised(self):
        cases = {
            "https://jama.example.com/": "https://jama.example.com",
            "https://jama.example.com/rest/v1": "https://jama.example.com",
            "https://jama.example.com/perspective.req#/items/12?projectId=3": "https://jama.example.com",
            "https://example.com/contour/perspective.req": "https://example.com/contour",
            "http://localhost:8080": "http://localhost:8080",
        }
        for raw, expected in cases.items():
            self.assertEqual(server.normalize_base_url(raw), expected, raw)

    def test_unsafe_base_urls_are_refused(self):
        for raw in ("http://jama.example.com", "https://user:pw@jama.example.com", "jama.example.com", ""):
            with self.assertRaises(server.JamaError, msg=raw):
                server.normalize_base_url(raw)

    def setUp(self):
        _forget_keys()
        server.bc_keys.keys_dir().mkdir(parents=True, exist_ok=True)

    def test_credentials_come_from_the_sites_key(self):
        server.bc_keys.write_key("jama", "https://jama.example.com/", {"client_id": "cid", "client_secret": "s3cret"})
        server.bc_keys.write_key("jama", "https://other.example.com", {"username": "jdoe", "password": "pw"})
        config = server.load_config("jama.example.com")
        self.assertEqual(config.base_url, "https://jama.example.com")
        self.assertEqual(config.auth_mode, "oauth")
        self.assertNotIn("s3cret", repr(config))
        self.assertEqual(server.load_config("https://other.example.com/perspective.req").auth_mode, "basic")

    def test_credentials_never_come_from_the_environment(self):
        env = {"JAMA_BASE_URL": "https://jama.example.com", "JAMA_CLIENT_ID": "cid", "JAMA_CLIENT_SECRET": "s3cret",
               "BC_SECRETS_FILE": str(Path(_HOME.name, "secrets.json"))}
        Path(env["BC_SECRETS_FILE"]).write_text(json.dumps({"jama": {"client_id": "cid", "client_secret": "s3cret"}}))
        (server.bc_keys.keys_dir() / server.bc_keys.MARKER).write_text("{}")  # migration already ran
        with mock.patch.dict(os.environ, env), self.assertRaises(server.JamaError) as ctx:
            server.load_config("jama.example.com")
        self.assertIn("No Jama key is saved for jama.example.com", str(ctx.exception))

    def test_missing_credentials_say_what_to_do(self):
        target = server.bc_keys.key_path("jama.example.com")
        target.write_text(json.dumps({"provider": "jama", "url": "https://jama.example.com", "client_id": "cid"}))
        with self.assertRaises(server.JamaError) as ctx:
            server.load_config()
        self.assertIn("Set API Credentials", str(ctx.exception))
        self.assertIn("Ask for it again and save it", str(ctx.exception))
        self.assertNotIn("cid", str(ctx.exception))

    def test_anchors_file_needs_a_project(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", new_callable=io.StringIO):
            server.main(["--check", "--anchors-file", "keys.txt"])

    def test_cli_check_failure_prints_json_and_exits_non_zero(self):
        (server.bc_keys.keys_dir() / server.bc_keys.MARKER).write_text("{}")  # migration already ran
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = server.main(["--check", "--site", "https://jama.example.com"])
        self.assertEqual(code, 1)
        report = json.loads(out.getvalue())
        self.assertFalse(report["ok"])
        self.assertIn("/bc:setup", report["error"])


class RichText(unittest.TestCase):
    def test_html_is_flattened(self):
        self.assertEqual(server.html_to_text("<p>A &amp; B</p><p>C<br/>D</p>"), "A & B\n\nC\nD")

    def test_links_keep_their_targets(self):
        html = '<p>See <a href="https://github.com/org/repo/blob/main/x.md">the SDS</a>.</p>'
        self.assertEqual(server.html_to_text(html), "See the SDS (https://github.com/org/repo/blob/main/x.md).")

    def test_plain_text_passes_through(self):
        self.assertEqual(server.html_to_text("  plain  "), "plain")
        self.assertEqual(server.html_to_text(None), "")

    def test_github_urls_are_found_in_text_and_links(self):
        value = 'Docs: https://github.com/a/b/blob/main/x.md, and <a href="https://github.com/a/b/tree/main/y">y</a>.'
        self.assertEqual(
            server._github_urls(value),
            ["https://github.com/a/b/blob/main/x.md", "https://github.com/a/b/tree/main/y"],
        )

    def test_github_enterprise_file_and_folder_links_are_found_and_other_links_are_not(self):
        value = (
            "SDS: https://github.acme.com/team/app/blob/main/docs/sds.md and "
            "https://code.acme.com/team/app/tree/main/docs; not https://gitlab.com/a/b/-/blob/main/x.md, "
            "https://bitbucket.org/a/b/src/main/x.md, or https://example.com/about/us"
        )
        self.assertEqual(
            server._github_urls(value),
            ["https://github.acme.com/team/app/blob/main/docs/sds.md", "https://code.acme.com/team/app/tree/main/docs"],
        )


if __name__ == "__main__":
    unittest.main()
