"""TestRail test cases provider."""

from __future__ import annotations

from typing import Any

import httpx

from buddy_council.models import TestCase, TestStep
from buddy_council.normalization import clean_text, normalize_feature_name, parse_linked_ids


class TestRailProvider:
    """Fetch test cases from the TestRail REST API."""

    def __init__(
        self,
        base_url: str,
        username: str,
        api_key: str,
        project_id: int,
        suite_id: int | None = None,
    ) -> None:
        self._api_base = f"{base_url.rstrip('/')}/index.php?/api/v2/"
        self._project_id = project_id
        self._suite_id = suite_id
        self._client = httpx.AsyncClient(
            auth=(username, api_key),
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._api_base}{endpoint}"
        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if query:
                url = f"{url}&{query}"
        response = await self._client.get(url)
        response.raise_for_status()
        return response.json()

    async def _fetch_sections(self) -> dict[int, str]:
        """Build a section_id → section_name lookup."""
        params: dict[str, Any] = {}
        if self._suite_id is not None:
            params["suite_id"] = self._suite_id
        data = await self._get(f"get_sections/{self._project_id}", params)
        sections = data.get("sections", data) if isinstance(data, dict) else data
        return {s["id"]: s.get("name", "") for s in sections}

    async def _fetch_all_cases(self) -> list[dict[str, Any]]:
        """Fetch all test cases with pagination."""
        all_cases: list[dict[str, Any]] = []
        offset = 0
        limit = 250

        while True:
            params: dict[str, Any] = {"limit": limit, "offset": offset}
            if self._suite_id is not None:
                params["suite_id"] = self._suite_id
            data = await self._get(f"get_cases/{self._project_id}", params)
            cases = data.get("cases", [])
            all_cases.extend(cases)
            if data.get("size", 0) < limit:
                break
            offset += limit

        return all_cases

    async def fetch(self, scope: str | None = None) -> list[TestCase]:
        """Fetch test cases, optionally filtered by scope."""
        # If scope is a specific case ID, fetch just that one
        if scope and scope.upper().startswith("TC-"):
            case_id = scope.upper().replace("TC-", "")
            if case_id.isdigit():
                raw = await self._get(f"get_case/{case_id}")
                sections = await self._fetch_sections()
                return [self._to_test_case(raw, sections)]

        sections = await self._fetch_sections()
        raw_cases = await self._fetch_all_cases()

        test_cases = [self._to_test_case(raw, sections) for raw in raw_cases]

        # Filter by scope if it's a feature/section name
        if scope and not scope.lower() == "all":
            normalized_scope = scope.lower()
            test_cases = [
                tc for tc in test_cases if normalized_scope in tc.feature.lower()
            ]

        return test_cases

    def _to_test_case(self, raw: dict[str, Any], sections: dict[int, str]) -> TestCase:
        """Convert a raw TestRail case to the canonical schema."""
        case_id = f"TC-{raw['id']}"
        section_name = sections.get(raw.get("section_id", 0), "")

        # Build description from custom fields
        desc_parts = []
        if raw.get("custom_preconds"):
            desc_parts.append(clean_text(str(raw["custom_preconds"])))
        if raw.get("custom_desc"):
            desc_parts.append(clean_text(str(raw["custom_desc"])))

        # Parse test steps
        steps = []
        raw_steps = raw.get("custom_steps_separated") or []
        for step in raw_steps:
            steps.append(
                TestStep(
                    content=clean_text(str(step.get("content", ""))),
                    expected=clean_text(str(step.get("expected", ""))),
                )
            )

        # Parse linked requirement IDs
        linked = parse_linked_ids(str(raw.get("custom_jama_req_id", "")))

        return TestCase(
            id=case_id,
            title=raw.get("title", ""),
            description="\n".join(desc_parts),
            feature=normalize_feature_name(section_name),
            status="Active",
            linked_ids=linked,
            steps=steps,
            raw_fields={
                "section_id": raw.get("section_id"),
                "suite_id": raw.get("suite_id"),
                "steps": [{"content": s.content, "expected": s.expected} for s in steps],
            },
        )

    async def close(self) -> None:
        await self._client.aclose()
