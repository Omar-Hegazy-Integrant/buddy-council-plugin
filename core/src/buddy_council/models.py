"""Canonical artifact schema and analysis result models."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, field_validator, model_validator


class ArtifactType(str, Enum):
    REQUIREMENT = "requirement"
    TEST_CASE = "test_case"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ContradictionType(str, Enum):
    DIRECT_CONFLICT = "direct_conflict"
    BEHAVIORAL_CONFLICT = "behavioral_conflict"
    SCOPE_OVERLAP = "scope_overlap"
    TEST_VS_REQUIREMENT = "test_vs_requirement"
    CROSS_FEATURE_TENSION = "cross_feature_tension"
    MISSING_ALIGNMENT = "missing_alignment"
    TEMPORAL_STATE_CONFLICT = "temporal_state_conflict"


class TestStep(BaseModel, frozen=True):
    """A single step in a test case."""

    content: str = ""
    expected: str = ""


class Artifact(BaseModel, frozen=True):
    """Base artifact in the canonical schema."""

    type: ArtifactType
    id: str
    title: str = ""
    description: str = ""
    feature: str = ""
    status: str = ""
    linked_ids: frozenset[str] = frozenset()
    raw_fields: dict[str, Any] = {}

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("linked_ids", mode="before")
    @classmethod
    def coerce_linked_ids(cls, v: Any) -> frozenset[str]:
        if isinstance(v, (list, set, frozenset)):
            return frozenset(s.strip().upper() for s in v if s and str(s).strip())
        return frozenset()


class Requirement(Artifact):
    """A requirement artifact."""

    type: ArtifactType = ArtifactType.REQUIREMENT

    @model_validator(mode="after")
    def validate_type(self) -> Requirement:
        if self.type != ArtifactType.REQUIREMENT:
            object.__setattr__(self, "type", ArtifactType.REQUIREMENT)
        return self


class TestCase(Artifact):
    """A test case artifact."""

    type: ArtifactType = ArtifactType.TEST_CASE
    steps: list[TestStep] = []

    @model_validator(mode="after")
    def validate_type(self) -> TestCase:
        if self.type != ArtifactType.TEST_CASE:
            object.__setattr__(self, "type", ArtifactType.TEST_CASE)
        return self


class ContradictionFinding(BaseModel, frozen=True):
    """A single contradiction detected during analysis."""

    contradiction_type: ContradictionType
    severity: Severity
    requirement_id: str = ""
    test_case_id: str = ""
    explanation: str
    recommendation: str = ""
    requirement_text: str = ""
    test_case_text: str = ""


class CoverageGap(BaseModel, frozen=True):
    """A single coverage gap detected during analysis."""

    gap_type: str  # "untested", "orphan", "weak"
    severity: Severity
    artifact_id: str
    explanation: str
    recommendation: str = ""


class AnalysisReport(BaseModel, frozen=True):
    """Complete analysis report containing findings."""

    scope: str = "all"
    requirements_count: int = 0
    test_cases_count: int = 0
    contradictions: list[ContradictionFinding] = []
    coverage_gaps: list[CoverageGap] = []

    @property
    def findings_by_severity(self) -> dict[Severity, list[ContradictionFinding]]:
        result: dict[Severity, list[ContradictionFinding]] = {s: [] for s in Severity}
        for f in self.contradictions:
            result[f.severity].append(f)
        return result

    @property
    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.contradictions)
