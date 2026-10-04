"""Tests for the read-only Jama MCP server, run against an in-process fake Jama REST API.

    uv run --directory mcp-servers/jama-server python -m unittest discover -s tests -v

Standard library only (unittest + httpx.MockTransport, which ships with httpx),
so the server gains no test dependency.
"""

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
PROJECT = 42


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


def _abstract(item):
    """/abstractitems carries no location — mirror that, so the server can't lean on it."""
    return {k: v for k, v in item.items() if k not in ("location", "lock", "childItemType")}


class FakeJama:
    """Just enough of Jama's REST API, recording every request it sees."""

    def __init__(self, client_id="cid", client_secret="s3cret", username="", password=""):
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

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    def transport(self):
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _error(status, message):
        return httpx.Response(status, json={"meta": {"status": "Error", "message": message}})

    @staticmethod
    def _page(data, params, linked=None):
        start = int(params.get("startAt", 0))
        size = min(int(params.get("maxResults", 20)), 50)
        chunk = data[start : start + size]
        body = {
            "meta": {"status": "OK", "pageInfo": {"startIndex": start, "resultCount": len(chunk), "totalResults": len(data)}},
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
        return header.startswith("Bearer tok-") and token not in self.rejected_tokens

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((request.method, path, params))

        if path == "/rest/oauth/token":
            expected = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            if request.method != "POST" or request.headers.get("Authorization") != f"Basic {expected}":
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "Bad credentials"})
            self.tokens_issued += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.tokens_issued}", "token_type": "bearer", "expires_in": 3600})

        if not path.startswith("/rest/v1/"):
            return self._error(404, "no such path")
        if not self._authorized(request):
            return self._error(401, "Unauthorized")
        resource = path[len("/rest/v1/") :]
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
            key = params.get("documentKey")
            data = [_abstract(i) for i in self.items.values() if i["documentKey"] == key]
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
}


class ServerTestCase(unittest.IsolatedAsyncioTestCase):
    def make_fake(self) -> FakeJama:
        return FakeJama()

    async def asyncSetUp(self):
        self.fake = self.make_fake()
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
        self.assertIn({"label": "Status", "name": "status$89", "type": "LOOKUP"}, requirement["fields"])


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
        self.assertIn("/bc:setup", str(ctx.exception))
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

    async def test_an_unknown_document_key_is_a_404(self):
        with self.assertRaises(server.JamaError) as ctx:
            await self.call("jama_get_item", item="CWA-REQ-999", project_id=PROJECT)
        self.assertEqual(ctx.exception.status, 404)


class SetupCheck(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        secrets = Path(self.tmp.name, "secrets.json")
        secrets.write_text(json.dumps({"jama": {"client_id": "cid", "client_secret": "s3cret"}}))
        os.chmod(secrets, 0o600)
        self.patches = [
            mock.patch.dict(os.environ, {"JAMA_BASE_URL": BASE_URL}),
            mock.patch.object(server, "_secrets_candidates", return_value=[str(secrets)]),
        ]
        for patch in self.patches:
            patch.start()
        self.fake = FakeJama()

    async def asyncTearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()
        server.reset_state()

    async def test_without_a_project_it_lists_projects(self):
        report = await server._check(None, transport=self.fake.transport())
        self.assertTrue(report["ok"])
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

    def _secrets(self, tmp, payload):
        path = Path(tmp, "secrets.json")
        path.write_text(json.dumps(payload))
        os.chmod(path, 0o600)
        return mock.patch.object(server, "_secrets_candidates", return_value=[str(path)])

    def test_credentials_come_from_the_secrets_file(self):
        with tempfile.TemporaryDirectory() as tmp, self._secrets(tmp, {"jama": {"client_id": "cid", "client_secret": "s3cret"}}):
            with mock.patch.dict(os.environ, {"JAMA_BASE_URL": "https://jama.example.com/"}):
                config = server.load_config()
        self.assertEqual(config.base_url, "https://jama.example.com")
        self.assertEqual(config.auth_mode, "oauth")
        self.assertNotIn("s3cret", repr(config))

    def test_missing_credentials_say_what_to_do(self):
        with tempfile.TemporaryDirectory() as tmp, self._secrets(tmp, {"testrail": {"username": "u", "api_key": "k"}}):
            with mock.patch.dict(os.environ, {"JAMA_BASE_URL": "https://jama.example.com"}):
                with self.assertRaises(server.JamaError) as ctx:
                    server.load_config()
        self.assertIn("Set API Credentials", str(ctx.exception))
        self.assertIn("/bc:setup", str(ctx.exception))

    def test_cli_check_failure_prints_json_and_exits_non_zero(self):
        with tempfile.TemporaryDirectory() as tmp, self._secrets(tmp, {}):
            with mock.patch.dict(os.environ, {"JAMA_BASE_URL": ""}), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = server.main(["--check"])
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


if __name__ == "__main__":
    unittest.main()
