"""Tests for the TestRail MCP server, run against an in-process fake of TestRail's API.

    uv run --directory mcp-servers/testrail-server python -m unittest discover -s tests -v

Standard library only (unittest + httpx.MockTransport, which ships with httpx), so the
server gains no test dependency.
"""

import json
import logging
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

logging.getLogger("httpx").setLevel(logging.WARNING)

# The server reads its configuration at import time; point it at a fake instance and an
# empty home so a developer's real ~/.buddy-council/secrets.json is never read.
_HOME = tempfile.TemporaryDirectory()
_SAVED_HOME = os.environ.get("HOME")
os.environ.update(
    {
        "TESTRAIL_BASE_URL": "https://tr.test",
        "TESTRAIL_USERNAME": "qa@example.com",
        "TESTRAIL_API_KEY": "test-key",
        "BC_SECRETS_FILE": str(Path(_HOME.name, "absent.json")),
        "HOME": _HOME.name,
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402

if _SAVED_HOME is not None:
    os.environ["HOME"] = _SAVED_HOME

REQ_FIELD = "custom_jama_req_id"
CREATED_ON = 1_690_000_000
READ_TOOLS = {
    "testrail_get_projects",
    "testrail_get_suites",
    "testrail_get_sections",
    "testrail_get_cases",
    "testrail_get_cases_by_refs",
    "testrail_get_case",
    "testrail_get_case_fields",
    "testrail_get_case_types",
    "testrail_get_priorities",
    "testrail_get_templates",
    "testrail_get_requirement_ids",
    "testrail_get_linked_cases",
    "testrail_get_canonical_cases",
}
WRITE_TOOLS = {
    "testrail_add_section",
    "testrail_add_case",
    "testrail_add_cases",
    "testrail_update_case",
    "testrail_update_cases",
}


def _step(content, expected):
    # TestRail returns extra keys on every step; the server must compare only what was sent.
    return {"content": content, "expected": expected, "additional_info": None, "refs": None}


def _field(system_name, label, type_id, items=None, project_ids=None):
    """A case field definition as get_case_fields returns it, with one config."""
    context = {"is_global": project_ids is None, "project_ids": project_ids}
    return {"system_name": system_name, "label": label, "type_id": type_id,
            "configs": [{"context": context, "options": {"items": items} if items else {}}]}


def _case(case_id, title, refs=None, req=None, preconds=None, steps=None):
    return {
        "id": case_id,
        "title": title,
        "section_id": 5,
        "template_id": 2,
        "type_id": 7,
        "priority_id": 2,
        "milestone_id": None,
        "refs": refs,
        "created_by": 1,
        "created_on": CREATED_ON,
        "updated_by": 1,
        "updated_on": CREATED_ON,
        "estimate": None,
        "suite_id": 9,
        "custom_preconds": preconds,
        "custom_expected": None,
        "custom_steps_separated": steps,
        REQ_FIELD: req,
    }


class FakeTestRail:
    """Just enough of TestRail's API v2, recording every request."""

    def __init__(self):
        self.now = 1_700_000_000
        self.requests: list[tuple[str, str, dict | None]] = []
        self.unstored_fields: set[str] = set()  # accepted, then silently discarded — like a field not on the template
        self.throttle: dict[str, int] = {}
        self.sections = [{"id": 5, "name": "Sync", "parent_id": None, "depth": 0, "suite_id": 9}]
        self.dropped: dict[str, int] = {}  # endpoint -> connections to drop before answering
        self.fields_status = 200
        self.case_fields = [
            {"system_name": REQ_FIELD, "label": "Jama Req ID", "type_id": 3, "configs": []},
            _field("custom_automation", "Automation Type", 6, "1, Manual\n2, Automated"),
            _field("custom_app_os", "Applicable OS", 12, "1, iOS\n2, Android\n3, Web"),
            _field("custom_ctq", "CtQ", 5),
            _field("custom_notes", "Notes", 3),
            _field("custom_owner", "Owner", 7),
        ]
        self.cases = {
            12: _case(
                12,
                "Sync retries on network loss",
                refs="CWA-REQ-85",
                req="CWA-REQ-85",
                preconds="Device online",
                steps=[_step("Drop the network", "Sync retries 3 times")],
            ),
            13: _case(13, "Orphan with refs only", refs="CWA-REQ-86"),
            14: _case(14, "Linked twice", refs="CWA-REQ-85, CWA-REQ-86", req="CWA-REQ-85, CWA-REQ-86"),
            15: _case(
                15,
                "Three steps",
                refs="CWA-REQ-87",
                req="CWA-REQ-87",
                steps=[_step("One", "A"), _step("Two", "B"), _step("Three", "C")],
            ),
        }

    def transport(self):
        return httpx.MockTransport(self.handle)

    @staticmethod
    def _error(status, message):
        return httpx.Response(status, json={"error": message})

    def handle(self, request: httpx.Request) -> httpx.Response:
        raw = request.url.query.decode()
        assert raw.startswith("/api/v2/"), raw
        endpoint, *pairs = raw[len("/api/v2/") :].split("&")
        params = dict(pair.split("=", 1) for pair in pairs if "=" in pair)
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, endpoint, body))
        name, _, arg = endpoint.partition("/")
        if self.dropped.get(name):
            self.dropped[name] -= 1
            raise httpx.ReadTimeout("timed out", request=request)
        if self.throttle.get(name):
            self.throttle[name] -= 1
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "API rate limit reached"})

        if request.method == "POST":
            return self._post(name, arg, body or {})
        if name == "get_case":
            case = self.cases.get(int(arg))
            return httpx.Response(200, json=case) if case else self._error(400, "Field :case_id is not a valid test case.")
        if name == "get_cases":
            cases = list(self.cases.values())
            if "section_id" in params:
                cases = [c for c in cases if str(c["section_id"]) == params["section_id"]]
            if "refs" in params:
                cases = [c for c in cases if params["refs"] in (c["refs"] or "")]
            return httpx.Response(200, json={"offset": 0, "limit": 250, "size": len(cases), "cases": cases})
        if name == "get_sections":
            sections = self.sections
            return httpx.Response(200, json={"offset": 0, "limit": 250, "size": len(sections), "sections": sections})
        if name == "get_projects":
            return httpx.Response(200, json={"offset": 0, "limit": 250, "size": 1, "projects": [{"id": 1, "name": "CWA"}]})
        if name == "get_suites":
            return httpx.Response(200, json=[{"id": 9, "name": "Master"}])
        if name == "get_case_fields":
            if self.fields_status != 200:
                return self._error(self.fields_status, "You are not allowed to read case fields")
            return httpx.Response(200, json=self.case_fields)
        if name == "get_case_types":
            return httpx.Response(200, json=[{"id": 7, "name": "Functional"}])
        if name == "get_priorities":
            return httpx.Response(200, json=[{"id": 2, "name": "Medium"}])
        if name == "get_templates":
            return httpx.Response(200, json=[{"id": 2, "name": "Test Case (Steps)"}])
        return self._error(400, f"Unknown method {endpoint}")

    def _post(self, name, arg, body):
        self.now += 60
        if name == "update_case":
            case = self.cases.get(int(arg))
            if case is None:
                return self._error(400, "Field :case_id is not a valid test case.")
            for field, value in body.items():
                if field not in self.unstored_fields:
                    case[field] = value
            case["updated_on"] = self.now
            return httpx.Response(200, json=case)
        if name == "add_case":
            case_id = max(self.cases) + 1
            case = _case(case_id, body["title"])
            case.update({k: v for k, v in body.items() if k not in self.unstored_fields})
            case["created_on"] = case["updated_on"] = self.now
            self.cases[case_id] = case
            return httpx.Response(200, json=case)
        if name == "add_section":
            return httpx.Response(200, json={"id": 77, "name": body.get("name"), "parent_id": body.get("parent_id")})
        return self._error(400, f"Unknown method {name}")

    def posts(self):
        return [(endpoint, body) for method, endpoint, body in self.requests if method == "POST"]


class ServerTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        server._INDEX.clear()
        server._META_CACHE.clear()
        self.fake = FakeTestRail()
        self.client = httpx.AsyncClient(transport=self.fake.transport())
        patcher = mock.patch.object(server, "_client", self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def update(self, cases, **kwargs):
        return json.loads(await server.testrail_update_cases(cases=cases, **kwargs))


class UpdateCases(ServerTestCase):
    async def test_a_dry_run_diffs_against_the_live_case_and_writes_nothing(self):
        result = await self.update([{"case_id": 12, "preconditions": "Device online, sync running"}], dry_run=True)
        self.assertTrue(result["dry_run"])
        entry = result["updated"][0]
        self.assertEqual(
            entry["changes"],
            {"custom_preconds": {"before": "Device online", "after": "Device online, sync running"}},
        )
        self.assertEqual(entry["updated_on"], CREATED_ON)
        self.assertEqual(self.fake.posts(), [])

    async def test_only_fields_that_differ_are_sent(self):
        result = await self.update(
            [
                {
                    "case_id": "C12",
                    "title": "Sync retries on network loss",
                    "priority_id": "2",
                    "preconditions": "Device online, sync running",
                }
            ]
        )
        self.assertEqual(self.fake.posts(), [("update_case/12", {"custom_preconds": "Device online, sync running"})])
        entry = result["updated"][0]
        self.assertEqual(entry["dropped_fields"], [])
        self.assertEqual(entry["updated_on"], self.fake.now)
        self.assertEqual(entry["url"], "https://tr.test/index.php?/cases/view/12")

    async def test_a_case_with_nothing_to_change_is_left_alone(self):
        result = await self.update([{"case_id": 12, "refs": "cwa-req-85", "preconditions": "Device online\r\n"}])
        self.assertEqual([u["case_id"] for u in result["unchanged"]], [12])
        self.assertEqual(self.fake.posts(), [])

    async def test_reformatting_reference_delimiters_is_not_a_change(self):
        result = await self.update(
            [{"case_id": 14, "refs": "CWA-REQ-85,CWA-REQ-86", "custom_fields": {"jama_req_id": "CWA-REQ-85;CWA-REQ-86"}}],
            requirement_field=REQ_FIELD,
        )
        self.assertEqual(len(result["unchanged"]), 1)
        self.assertEqual(self.fake.posts(), [])

    async def test_removing_a_requirement_link_is_blocked(self):
        result = await self.update(
            [
                {"case_id": 12, "refs": "CWA-REQ-90"},
                {"case_id": 14, "custom_fields": {"jama_req_id": "CWA-REQ-85"}},
            ],
            requirement_field="jama_req_id",
        )
        self.assertEqual(
            [(b["case_id"], b["removed"]) for b in result["blocked"]],
            [(12, {"refs": ["CWA-REQ-85"]}), (14, {REQ_FIELD: ["CWA-REQ-86"]})],
        )
        self.assertEqual(self.fake.posts(), [])

    async def test_the_requirement_field_is_only_protected_when_named(self):
        result = await self.update([{"case_id": 14, "custom_fields": {"jama_req_id": ""}}])
        self.assertEqual(len(result["updated"]), 1)

    async def test_an_explicit_unlink_goes_through(self):
        result = await self.update([{"case_id": 12, "refs": "CWA-REQ-90", "allow_unlink": True}])
        self.assertEqual(result["updated"][0]["changes"]["refs"], {"before": "CWA-REQ-85", "after": "CWA-REQ-90"})
        self.assertEqual(self.fake.cases[12]["refs"], "CWA-REQ-90")

    async def test_adding_a_link_needs_no_permission(self):
        result = await self.update(
            [{"case_id": 13, "custom_fields": {"jama_req_id": "CWA-REQ-86"}}], requirement_field=REQ_FIELD
        )
        self.assertEqual(result["updated"][0]["dropped_fields"], [])
        self.assertEqual(self.fake.cases[13][REQ_FIELD], "CWA-REQ-86")

    async def test_a_rich_text_requirement_field_compares_by_its_ids(self):
        self.fake.cases[14][REQ_FIELD] = "<p>CWA-REQ-85,&nbsp;CWA-REQ-86</p>"
        same = await self.update([{"case_id": 14, "custom_fields": {"jama_req_id": "CWA-REQ-85, CWA-REQ-86"}}], requirement_field=REQ_FIELD)
        self.assertEqual((len(same["unchanged"]), same["blocked"]), (1, []), "the same IDs without markup are no change")
        dropped = await self.update([{"case_id": 14, "custom_fields": {"jama_req_id": "CWA-REQ-85"}}], requirement_field=REQ_FIELD)
        self.assertEqual(dropped["blocked"][0]["removed"], {REQ_FIELD: ["CWA-REQ-86"]})
        self.assertEqual(self.fake.posts(), [])

    async def test_a_case_edited_since_it_was_read_is_not_overwritten(self):
        stale = await self.update([{"case_id": 12, "title": "Renamed", "expected_updated_on": CREATED_ON - 5}])
        self.assertEqual(stale["blocked"][0]["updated_on"], CREATED_ON)
        self.assertIn("changed in TestRail since it was read", stale["blocked"][0]["reason"])
        self.assertEqual(self.fake.posts(), [])
        fresh = await self.update([{"case_id": 12, "title": "Renamed", "expected_updated_on": CREATED_ON}])
        self.assertEqual(len(fresh["updated"]), 1)

    async def test_a_field_that_does_not_stick_stops_the_batch(self):
        self.fake.unstored_fields.add(REQ_FIELD)
        result = await self.update(
            [
                {"case_id": 13, "custom_fields": {"jama_req_id": "CWA-REQ-86"}},
                {"case_id": 14, "title": "Linked twice, renamed"},
            ],
            requirement_field=REQ_FIELD,
        )
        self.assertEqual(result["updated"][0]["dropped_fields"], [REQ_FIELD])
        self.assertEqual(result["not_attempted"], [{"index": 1, "case_id": 14}])
        self.assertIn(REQ_FIELD, result["stopped"])
        self.assertEqual(len(self.fake.posts()), 1)

    async def test_the_batch_can_continue_past_a_dropped_field(self):
        self.fake.unstored_fields.add(REQ_FIELD)
        result = await self.update(
            [
                {"case_id": 13, "custom_fields": {"jama_req_id": "CWA-REQ-86"}},
                {"case_id": 14, "title": "Linked twice, renamed"},
            ],
            stop_on_dropped_field=False,
        )
        self.assertEqual(len(result["updated"]), 2)
        self.assertIsNone(result["stopped"])

    async def test_template_and_folder_changes_are_refused(self):
        result = await self.update([{"case_id": 12, "template_id": 1}, {"case_id": 13, "section_id": 6}])
        self.assertEqual(len(result["failed"]), 2)
        self.assertTrue(all("Refusing" in f["error"] for f in result["failed"]))
        self.assertEqual(self.fake.requests, [])

    async def test_a_misspelt_key_fails_instead_of_doing_nothing(self):
        result = await self.update([{"case_id": 12, "precondition": "x"}])
        self.assertIn("Unknown update keys ['precondition']", result["failed"][0]["error"])

    async def test_empty_and_blank_entries_fail(self):
        result = await self.update([{"case_id": 12}, {"case_id": 13, "title": "   "}])
        self.assertIn("Nothing to change", result["failed"][0]["error"])
        self.assertIn("cannot be blank", result["failed"][1]["error"])

    async def test_case_ids_in_every_common_form(self):
        result = await self.update(
            [{"case_id": "C12", "title": "a"}, {"case_id": "TC-13", "title": "b"}, {"case_id": 14, "title": "c"}, {"case_id": "abc", "title": "d"}],
            dry_run=True,
        )
        self.assertEqual([u["case_id"] for u in result["updated"]], [12, 13, 14])
        self.assertIn("not a test case id", result["failed"][0]["error"])

    async def test_one_case_twice_in_a_batch_fails(self):
        result = await self.update([{"case_id": 12, "title": "a"}, {"case_id": "C12", "title": "b"}], dry_run=True)
        self.assertIn("appears twice", result["failed"][0]["error"])

    async def test_steps_compare_on_what_was_sent_and_shrinking_warns(self):
        same = await self.update([{"case_id": 12, "steps_separated": [{"content": "Drop the network", "expected": "Sync retries 3 times"}]}])
        self.assertEqual(len(same["unchanged"]), 1)
        shrink = await self.update([{"case_id": 15, "steps_separated": [{"content": "One", "expected": "A"}]}], dry_run=True)
        self.assertIn("3 steps to 1", shrink["updated"][0]["warnings"][0])

    async def test_an_unknown_case_fails_without_stopping_the_others(self):
        result = await self.update([{"case_id": 999, "title": "x"}, {"case_id": 12, "title": "Renamed"}])
        self.assertEqual(result["failed"][0]["case_id"], 999)
        self.assertEqual([u["case_id"] for u in result["updated"]], [12])

    async def test_rate_limits_are_retried(self):
        self.fake.throttle["update_case"] = 1
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as sleep:
            result = await self.update([{"case_id": 12, "title": "Renamed"}])
        sleep.assert_awaited_once_with(1.0)
        self.assertEqual(len(result["updated"]), 1)

    async def test_a_dropped_read_is_retried_but_a_write_never_is(self):
        self.fake.dropped["get_case"] = 2
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as sleep:
            case = json.loads(await server.testrail_get_case(case_id=12))
        self.assertEqual(case["id"], 12)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [1.0, 2.0])
        self.fake.dropped["update_case"] = 1
        with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            result = await self.update([{"case_id": 12, "title": "Renamed"}])
        self.assertIn("could not reach TestRail", result["failed"][0]["error"])
        self.assertEqual(len(self.fake.posts()), 1, "a timed-out POST is reported, never sent twice")

    async def test_the_single_case_wrapper(self):
        result = json.loads(await server.testrail_update_case(case_id="TC-12", preconditions="x", dry_run=True))
        self.assertEqual(list(result["updated"][0]["changes"]), ["custom_preconds"])
        blocked = json.loads(await server.testrail_update_case(case_id=12, refs="", dry_run=True))
        self.assertEqual(blocked["blocked"][0]["removed"], {"refs": ["CWA-REQ-85"]})


class LinkIndex(ServerTestCase):
    async def ids(self, **kwargs):
        return json.loads(await server.testrail_get_requirement_ids(project_id=1, requirement_field="jama_req_id", **kwargs))

    async def linked(self, ids, **kwargs):
        return json.loads(
            await server.testrail_get_linked_cases(project_id=1, requirement_field=REQ_FIELD, requirement_ids=ids, **kwargs)
        )

    def reads_of(self, endpoint):
        return sum(1 for method, name, _ in self.fake.requests if method == "GET" and name.startswith(endpoint))

    async def test_requirement_ids_are_summarised(self):
        result = await self.ids()
        self.assertEqual(result["requirement_field"], REQ_FIELD)
        self.assertTrue(result["field_found"])
        self.assertEqual((result["cases_scanned"], result["cases_with_ids"], result["distinct_ids"]), (4, 3, 3))
        self.assertEqual(result["prefixes"], {"CWA-REQ": 3})
        self.assertEqual(result["sample"], ["CWA-REQ-85", "CWA-REQ-86", "CWA-REQ-87"])

    async def test_the_sample_is_spread_and_naturally_sorted(self):
        for n in range(1, 101):
            self.fake.cases[1000 + n] = _case(1000 + n, f"Case {n}", req=f"SYS-REQ-{n}")
        sample = (await self.ids(sample_size=5))["sample"]
        self.assertEqual(sample[0], "CWA-REQ-85")
        self.assertEqual(sample[-1], "SYS-REQ-100")
        self.assertEqual(len(sample), 5)

    async def test_a_wrong_field_name_warns(self):
        result = json.loads(await server.testrail_get_requirement_ids(project_id=1, requirement_field="custom_nope"))
        self.assertFalse(result["field_found"])
        self.assertIn("custom_nope", result["warnings"][0])

    async def test_cases_are_found_by_their_requirement_links(self):
        result = await self.linked(["cwa-req-86", "CWA-REQ-99"])
        self.assertEqual([c["id"] for c in result["cases"]], [14])
        self.assertEqual(result["by_requirement"], {"cwa-req-86": [14]})
        self.assertEqual(result["unlinked_ids"], ["CWA-REQ-99"])
        self.assertEqual(result["sections"], [{"id": 5, "path": "Sync", "cases": 1}])

    async def test_a_summary_without_cases(self):
        result = await self.linked(["CWA-REQ-85"], include_cases=False)
        self.assertEqual(result["matched_cases"], 2)
        self.assertEqual(result["cases"], [])
        self.assertIsNone(result["next_offset"])

    async def test_large_matches_page(self):
        with mock.patch.object(server, "_CASES_CHAR_BUDGET", 10):
            first = await self.linked(["CWA-REQ-85"])
            self.assertEqual(([c["id"] for c in first["cases"]], first["next_offset"]), ([12], 1))
            second = await self.linked(["CWA-REQ-85"], offset=1)
        self.assertEqual(([c["id"] for c in second["cases"]], second["next_offset"]), ([14], None))

    async def test_unlinked_lists_the_orphans(self):
        result = await self.linked(["CWA-REQ-85"], unlinked=True)
        self.assertEqual([c["id"] for c in result["cases"]], [13])
        self.assertEqual((result["by_requirement"], result["unlinked_ids"]), ({}, []))

    async def test_the_suite_is_read_once_and_writes_clear_it(self):
        await self.linked(["CWA-REQ-85"])
        await self.ids()
        self.assertEqual(self.reads_of("get_cases"), 1)
        await self.update([{"case_id": 13, "custom_fields": {"jama_req_id": "CWA-REQ-86"}}], requirement_field=REQ_FIELD)
        result = await self.linked(["CWA-REQ-86"])
        self.assertEqual(self.reads_of("get_cases"), 2)
        self.assertEqual(sorted(c["id"] for c in result["cases"]), [13, 14])


class CanonicalCases(ServerTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake.sections.append({"id": 6, "name": "Retry", "parent_id": 5, "depth": 1, "suite_id": 9})
        self.fake.sections.append({"id": 7, "name": "Alarms", "parent_id": None, "depth": 0, "suite_id": 9})
        nested = _case(16, "Backoff <b>doubles</b> &amp; caps", req="CWA-REQ-88", preconds="<p>Offline</p><p>Retry on</p>")
        nested.update(section_id=6, custom_automation=1, custom_app_os=[1, 2], custom_ctq=True, custom_notes="x" * 600,
                      custom_owner=5, custom_unknown_dropdown=3, custom_unchecked=False)
        self.fake.cases[16] = nested
        self.fake.cases[17] = {**_case(17, "Alarm orphan"), "section_id": 7}
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name, ".buddy-council", "runs", "r1")
        self.run_dir.mkdir(parents=True)

    async def asyncTearDown(self):
        self.tmp.cleanup()
        await super().asyncTearDown()

    async def cases(self, **kwargs):
        return json.loads(await server.testrail_get_canonical_cases(project_id=1, requirement_field="jama_req_id", **kwargs))

    @staticmethod
    def ids(result):
        return [case["id"] for case in result["test_cases"]]

    async def test_cases_come_back_in_the_canonical_schema(self):
        result = await self.cases()
        self.assertEqual(self.ids(result), ["TC-12", "TC-13", "TC-14", "TC-15", "TC-16", "TC-17"])
        case = result["test_cases"][0]
        self.assertEqual(
            {k: case[k] for k in ("type", "id", "title", "description", "feature", "status", "linked_ids")},
            {
                "type": "test_case",
                "id": "TC-12",
                "title": "Sync retries on network loss",
                "description": "Preconditions: Device online",
                "feature": "Sync",
                "status": "",
                "linked_ids": ["CWA-REQ-85"],
            },
        )
        raw = case["raw_fields"]
        self.assertEqual(raw["steps"], [{"content": "Drop the network", "expected": "Sync retries 3 times"}])
        self.assertEqual((raw["refs"], raw["section_path"], raw["testrail_id"]), ("CWA-REQ-85", "Sync", 12))
        self.assertEqual(raw["url"], "https://tr.test/index.php?/cases/view/12")
        self.assertEqual(result["test_cases"][3]["linked_ids"], ["CWA-REQ-87"])
        self.assertEqual(result["test_cases"][2]["linked_ids"], ["CWA-REQ-85", "CWA-REQ-86"])

    async def test_custom_fields_are_shown_by_label_with_option_names(self):
        case = (await self.cases(requirement_ids=["CWA-REQ-88"]))["test_cases"][0]
        self.assertEqual(case["title"], "Backoff doubles & caps")
        self.assertEqual(case["description"], "Preconditions: Offline\nRetry on")
        self.assertEqual((case["feature"], case["raw_fields"]["section_path"]), ("Retry", "Sync/Retry"))
        self.assertEqual((case["raw_fields"]["priority"], case["raw_fields"]["type"]), ("Medium", "Functional"))
        custom = case["raw_fields"]["custom"]
        self.assertEqual(
            {k: v for k, v in custom.items() if k != "Notes"},
            {"Automation Type": "Manual", "Applicable OS": ["iOS", "Android"], "CtQ": True},
        )
        self.assertEqual(custom["Notes"], "x" * 500 + "… [100 more characters in TestRail]")

    async def test_options_come_from_the_config_for_this_project(self):
        self.fake.case_fields[1] = {
            "system_name": "custom_automation", "label": "Automation Type", "type_id": 6,
            "configs": [
                {"context": {"is_global": False, "project_ids": [2]}, "options": {"items": "1, Other project"}},
                {"context": {"is_global": False, "project_ids": [1]}, "options": {"items": "1, Ours"}},
            ],
        }
        case = (await self.cases(requirement_ids=["CWA-REQ-88"]))["test_cases"][0]
        self.assertEqual(case["raw_fields"]["custom"]["Automation Type"], "Ours")

    async def test_unreadable_field_definitions_leave_raw_values_and_a_warning(self):
        self.fake.fields_status = 403
        result = await self.cases(requirement_ids=["CWA-REQ-88"])
        custom = result["test_cases"][0]["raw_fields"]["custom"]
        self.assertEqual(custom["custom_app_os"], ["1", "2"])
        self.assertTrue(custom["custom_ctq"])
        self.assertTrue(any("case field definitions" in w for w in result["warnings"]))

    async def test_requirement_ids_select_linked_cases_and_report_the_untested(self):
        result = await self.cases(requirement_ids=["cwa-req-86", "CWA-REQ-87", "CWA-REQ-99"])
        self.assertEqual(self.ids(result), ["TC-14", "TC-15"])
        self.assertEqual(result["unlinked_ids"], ["CWA-REQ-99"])
        self.assertIn("linked to 2 of 3 requested requirement IDs", result["summary"])

    async def test_a_folder_includes_its_subfolders_and_joins_the_linked_cases(self):
        self.assertEqual(self.ids(await self.cases(section="Sync/Retry")), ["TC-16"])
        self.assertEqual(self.ids(await self.cases(section="sync")), ["TC-12", "TC-13", "TC-14", "TC-15", "TC-16"])
        self.assertEqual(self.ids(await self.cases(section="Retry")), ["TC-16"])
        self.fake.sections.append({"id": 8, "name": "Retry - iOS", "parent_id": None, "depth": 0, "suite_id": 9})
        self.fake.cases[19] = {**_case(19, "iOS retry"), "section_id": 8}
        server._INDEX.clear()
        self.assertEqual(self.ids(await self.cases(section="retry")), ["TC-16", "TC-19"], "whole-word partial match")
        self.assertEqual(self.ids(await self.cases(section="Retr")), [], "never part of a word")
        self.assertEqual(self.ids(await self.cases(section="Alarms", requirement_ids=["CWA-REQ-87"])), ["TC-15", "TC-17"])
        missing = await self.cases(section="Nope")
        self.assertEqual(self.ids(missing), [])
        self.assertIn("No folder matches section 'Nope'.", missing["warnings"])

    async def test_unlinked_lists_the_orphans(self):
        self.assertEqual(self.ids(await self.cases(unlinked=True)), ["TC-13", "TC-17"])
        self.assertEqual(self.ids(await self.cases(unlinked=True, section="Alarms")), ["TC-17"])

    async def test_rich_text_requirement_fields_link_by_their_ids(self):
        self.fake.cases[18] = _case(18, "Rich text links", req="<p>CWA-REQ-90,&nbsp;CWA-REQ-91<br>CWA-REQ-92</p>")
        case = (await self.cases(requirement_ids=["CWA-REQ-91"]))["test_cases"][0]
        self.assertEqual((case["id"], case["linked_ids"]), ("TC-18", ["CWA-REQ-90", "CWA-REQ-91", "CWA-REQ-92"]))
        linked = json.loads(await server.testrail_get_linked_cases(project_id=1, requirement_field=REQ_FIELD, requirement_ids=["CWA-REQ-92"]))
        self.assertEqual(linked["by_requirement"], {"CWA-REQ-92": [18]})
        ids = json.loads(await server.testrail_get_requirement_ids(project_id=1, requirement_field=REQ_FIELD))
        self.assertNotIn("<p>CWA-REQ-90", ids["sample"])
        self.assertIn("CWA-REQ-90", ids["sample"])

    async def test_steps_can_be_left_out(self):
        result = await self.cases(include_steps=False)
        self.assertNotIn("steps", result["test_cases"][0]["raw_fields"])
        self.assertIn("steps left out", result["summary"])

    async def test_large_results_page(self):
        with mock.patch.object(server, "_CASES_CHAR_BUDGET", 900):
            page = await self.cases()
            collected = self.ids(page)
            while page["next_offset"] is not None:
                page = await self.cases(offset=page["next_offset"])
                collected += self.ids(page)
        self.assertEqual(collected, ["TC-12", "TC-13", "TC-14", "TC-15", "TC-16", "TC-17"])
        self.assertEqual(self.reads_of("get_cases"), 1, "later pages come from the cached suite")

    def reads_of(self, endpoint):
        return sum(1 for method, name, _ in self.fake.requests if method == "GET" and name.startswith(endpoint))

    async def test_save_to_writes_every_case_to_the_run_folder(self):
        target = self.run_dir / "test_cases.json"
        with mock.patch.object(server, "_CASES_CHAR_BUDGET", 900):
            result = await self.cases(save_to=str(target))
        self.assertNotIn("test_cases", result)
        self.assertEqual((result["matched"], result["saved_to"]), (6, str(target.resolve())))
        written = json.loads(target.read_text())
        self.assertEqual((written["kind"], written["source"], written["count"]), ("test_cases", "testrail", 6))
        self.assertEqual([c["id"] for c in written["test_cases"]], ["TC-12", "TC-13", "TC-14", "TC-15", "TC-16", "TC-17"])
        self.assertEqual(written["requirement_field"], REQ_FIELD)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(result["bytes"], target.stat().st_size)

    async def test_save_to_outside_a_run_folder_is_refused_before_testrail_is_read(self):
        for path in ("test_cases.json", str(Path(self.tmp.name, "test_cases.json")), str(self.run_dir / "x.txt"),
                     str(self.run_dir.parent / "missing" / "test_cases.json")):
            with self.assertRaises(server.TestRailError, msg=path):
                await self.cases(save_to=path)
        self.assertEqual(self.reads_of("get_cases"), 0)
        victim = Path(self.tmp.name, "victim")
        victim.write_text("keep")
        (self.run_dir / "test_cases.json").symlink_to(victim)
        with self.assertRaises(server.TestRailError):
            await self.cases(save_to=str(self.run_dir / "test_cases.json"))
        self.assertEqual(victim.read_text(), "keep")


class AddCases(ServerTestCase):
    async def test_created_cases_report_updated_on(self):
        result = json.loads(await server.testrail_add_cases(project_id=1, cases=[{"title": "Brand new"}], section_id=5))
        created = result["created"][0]
        self.assertEqual(created["updated_on"], self.fake.cases[created["id"]]["updated_on"])


class Contract(ServerTestCase):
    async def test_read_tools_never_write(self):
        calls = {
            "testrail_get_projects": {},
            "testrail_get_suites": {"project_id": 1},
            "testrail_get_sections": {"project_id": 1},
            "testrail_get_cases": {"project_id": 1},
            "testrail_get_cases_by_refs": {"project_id": 1, "refs": "CWA-REQ-85"},
            "testrail_get_case": {"case_id": 12},
            "testrail_get_case_fields": {},
            "testrail_get_case_types": {},
            "testrail_get_priorities": {},
            "testrail_get_templates": {"project_id": 1},
            "testrail_get_requirement_ids": {"project_id": 1, "requirement_field": REQ_FIELD},
            "testrail_get_linked_cases": {"project_id": 1, "requirement_field": REQ_FIELD, "requirement_ids": ["CWA-REQ-85"]},
            "testrail_get_canonical_cases": {"project_id": 1, "requirement_field": REQ_FIELD},
        }
        names = {tool.name for tool in await server.mcp.list_tools()}
        self.assertEqual(set(calls), {n for n in names if n.startswith("testrail_get_")}, "test every read tool")
        for tool, kwargs in calls.items():
            await getattr(server, tool)(**kwargs)
        self.assertEqual(self.fake.posts(), [])

    async def test_results_are_sent_once_as_text(self):
        tools = await server.mcp.list_tools()
        self.assertEqual([t.name for t in tools if t.outputSchema is not None], [], "set structured_output=False")
        result = await server.mcp.call_tool("testrail_get_projects", {})
        self.assertNotIsInstance(result, tuple, "a (text, structured copy) pair doubles every response")
        [block] = result
        json.loads(block.text)

    async def test_no_write_tool_is_named_like_a_read(self):
        # The auto-approve hook trusts the testrail_get_* glob, so a write tool under it would run unprompted.
        names = {tool.name for tool in await server.mcp.list_tools()}
        self.assertEqual(names, READ_TOOLS | WRITE_TOOLS)
        self.assertFalse(WRITE_TOOLS & {n for n in names if n.startswith("testrail_get_")})


if __name__ == "__main__":
    unittest.main()
