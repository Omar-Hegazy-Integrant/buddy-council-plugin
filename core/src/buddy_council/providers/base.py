"""Abstract provider protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from buddy_council.models import Requirement, TestCase


@runtime_checkable
class RequirementsProvider(Protocol):
    """Protocol for fetching requirements from any source."""

    async def fetch(self, scope: str | None = None) -> list[Requirement]: ...


@runtime_checkable
class TestCasesProvider(Protocol):
    """Protocol for fetching test cases from any source."""

    async def fetch(self, scope: str | None = None) -> list[TestCase]: ...
