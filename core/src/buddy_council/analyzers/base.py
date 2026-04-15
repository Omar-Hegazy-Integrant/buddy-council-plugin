"""Analyzer protocol — pluggable LLM backend."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from buddy_council.models import ContradictionFinding, CoverageGap, Requirement, TestCase


class AnalyzerConfig(BaseModel, frozen=True):
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096
    temperature: float = 0.0


@runtime_checkable
class Analyzer(Protocol):
    """Protocol for LLM-based artifact analysis."""

    async def detect_contradictions(
        self,
        requirements: list[Requirement],
        test_cases: list[TestCase],
    ) -> list[ContradictionFinding]: ...

    async def analyze_coverage(
        self,
        requirements: list[Requirement],
        test_cases: list[TestCase],
    ) -> list[CoverageGap]: ...
