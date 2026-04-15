"""Contradiction analysis orchestrator."""

from __future__ import annotations

from itertools import groupby
from operator import attrgetter

from buddy_council.analyzers.base import Analyzer
from buddy_council.models import (
    AnalysisReport,
    ContradictionFinding,
    Requirement,
    TestCase,
)

BATCH_THRESHOLD = 50


async def run_contradiction_analysis(
    requirements: list[Requirement],
    test_cases: list[TestCase],
    analyzer: Analyzer,
    scope: str = "all",
) -> AnalysisReport:
    """Orchestrate contradiction detection, batching by feature if needed."""
    total_artifacts = len(requirements) + len(test_cases)
    all_findings: list[ContradictionFinding] = []

    if total_artifacts <= BATCH_THRESHOLD:
        all_findings = await analyzer.detect_contradictions(requirements, test_cases)
    else:
        # Process feature by feature
        req_by_feature: dict[str, list[Requirement]] = {}
        for r in requirements:
            req_by_feature.setdefault(r.feature or "Unknown", []).append(r)

        tc_by_feature: dict[str, list[TestCase]] = {}
        for tc in test_cases:
            tc_by_feature.setdefault(tc.feature or "Unknown", []).append(tc)

        all_features = set(req_by_feature.keys()) | set(tc_by_feature.keys())

        for feature in sorted(all_features):
            feature_reqs = req_by_feature.get(feature, [])
            feature_tcs = tc_by_feature.get(feature, [])
            if feature_reqs or feature_tcs:
                findings = await analyzer.detect_contradictions(feature_reqs, feature_tcs)
                all_findings.extend(findings)

    return AnalysisReport(
        scope=scope,
        requirements_count=len(requirements),
        test_cases_count=len(test_cases),
        contradictions=all_findings,
    )
