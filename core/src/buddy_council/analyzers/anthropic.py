"""Anthropic SDK implementation of the Analyzer protocol."""

from __future__ import annotations

import json
import re
from typing import Any

import anthropic

from buddy_council.analyzers.base import AnalyzerConfig
from buddy_council.analyzers.prompts import CONTRADICTION_SYSTEM_PROMPT, COVERAGE_SYSTEM_PROMPT
from buddy_council.models import (
    ContradictionFinding,
    ContradictionType,
    CoverageGap,
    Requirement,
    Severity,
    TestCase,
)


def _artifacts_to_json(
    requirements: list[Requirement], test_cases: list[TestCase]
) -> str:
    """Serialize artifacts for the LLM prompt."""
    data = {
        "requirements": [
            {
                "id": r.id,
                "title": r.title,
                "description": r.description[:500],
                "feature": r.feature,
                "status": r.status,
                "linked_test_cases": sorted(r.linked_ids),
            }
            for r in requirements
        ],
        "test_cases": [
            {
                "id": tc.id,
                "title": tc.title,
                "description": tc.description[:300],
                "feature": tc.feature,
                "linked_requirements": sorted(tc.linked_ids),
                "steps": [
                    {"content": s.content[:200], "expected": s.expected[:200]}
                    for s in tc.steps[:10]
                ],
            }
            for tc in test_cases
        ],
    }
    return json.dumps(data, indent=2)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Extract a JSON array from LLM response text."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try to find JSON array in markdown code block
    match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON array
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return []


class AnthropicAnalyzer:
    """Analyzer using the Anthropic API."""

    def __init__(self, api_key: str, config: AnalyzerConfig | None = None) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._config = config or AnalyzerConfig()

    async def detect_contradictions(
        self,
        requirements: list[Requirement],
        test_cases: list[TestCase],
    ) -> list[ContradictionFinding]:
        artifacts_json = _artifacts_to_json(requirements, test_cases)

        response = await self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            system=CONTRADICTION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Analyze these artifacts for contradictions:\n\n{artifacts_json}",
                }
            ],
        )

        text = response.content[0].text if response.content else "[]"
        raw_findings = _extract_json_array(text)

        findings = []
        for f in raw_findings:
            try:
                findings.append(
                    ContradictionFinding(
                        contradiction_type=ContradictionType(f.get("contradiction_type", "missing_alignment")),
                        severity=Severity(f.get("severity", "MEDIUM")),
                        requirement_id=f.get("requirement_id", ""),
                        test_case_id=f.get("test_case_id", ""),
                        explanation=f.get("explanation", ""),
                        recommendation=f.get("recommendation", ""),
                        requirement_text=f.get("requirement_text", ""),
                        test_case_text=f.get("test_case_text", ""),
                    )
                )
            except (ValueError, KeyError):
                continue

        return findings

    async def analyze_coverage(
        self,
        requirements: list[Requirement],
        test_cases: list[TestCase],
    ) -> list[CoverageGap]:
        artifacts_json = _artifacts_to_json(requirements, test_cases)

        response = await self._client.messages.create(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            system=COVERAGE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": f"Analyze these artifacts for coverage gaps:\n\n{artifacts_json}",
                }
            ],
        )

        text = response.content[0].text if response.content else "[]"
        raw_gaps = _extract_json_array(text)

        gaps = []
        for g in raw_gaps:
            try:
                gaps.append(
                    CoverageGap(
                        gap_type=g.get("gap_type", "untested"),
                        severity=Severity(g.get("severity", "MEDIUM")),
                        artifact_id=g.get("artifact_id", ""),
                        explanation=g.get("explanation", ""),
                        recommendation=g.get("recommendation", ""),
                    )
                )
            except (ValueError, KeyError):
                continue

        return gaps
