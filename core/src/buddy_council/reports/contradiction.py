"""Contradiction analysis markdown report builder."""

from __future__ import annotations

from buddy_council.models import AnalysisReport, ContradictionFinding, Severity


def render_contradiction_report(report: AnalysisReport) -> str:
    """Render an AnalysisReport as a human-readable markdown string."""
    lines: list[str] = []

    # Summary
    lines.append("# Contradiction Analysis Report\n")
    lines.append("## Summary")
    lines.append(f"- **Scope**: {report.scope}")
    lines.append(f"- **Requirements analyzed**: {report.requirements_count}")
    lines.append(f"- **Test cases analyzed**: {report.test_cases_count}")

    by_severity = report.findings_by_severity
    severity_counts = {s.value: len(fs) for s, fs in by_severity.items() if fs}
    if severity_counts:
        lines.append(f"- **Issues found**: {', '.join(f'{v} {k}' for k, v in severity_counts.items())}")
    else:
        lines.append("- **Issues found**: None")
    lines.append("")

    if not report.contradictions:
        lines.append("No contradictions detected. All analyzed artifacts appear to be aligned.")
        return "\n".join(lines)

    # Findings by severity
    for severity in Severity:
        findings = by_severity[severity]
        if not findings:
            continue

        lines.append(f"## {severity.value} Issues\n")
        for finding in findings:
            lines.append(f"### [{severity.value}] {finding.contradiction_type.value.replace('_', ' ').title()}")
            if finding.requirement_id:
                lines.append(f"**Requirement ({finding.requirement_id})**: \"{finding.requirement_text}\"")
            if finding.test_case_id:
                lines.append(f"**Test Case ({finding.test_case_id})**: \"{finding.test_case_text}\"")
            lines.append(f"**Explanation**: {finding.explanation}")
            if finding.recommendation:
                lines.append(f"**Recommendation**: {finding.recommendation}")
            lines.append("")

    return "\n".join(lines)
