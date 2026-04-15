"""Normalize and cross-link requirements and test cases."""

from __future__ import annotations

import re
from typing import Sequence

from buddy_council.models import Requirement, TestCase


def clean_text(raw: str) -> str:
    """Strip HTML, collapse whitespace, remove nan/None literals."""
    if not raw or raw in ("nan", "None", "null", "NaN"):
        return ""

    # Replace block-level HTML with newlines
    text = re.sub(r"<br\s*/?>|</?p>|</?li>|</?div>|</?tr>|</?td>", "\n", raw)
    # Remove remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_id(raw: str) -> str:
    """Enforce consistent ID format: uppercase, stripped."""
    if not raw or raw in ("nan", "None", "null"):
        return ""
    return raw.strip().upper()


def parse_linked_ids(raw: str | None) -> frozenset[str]:
    """Parse a linked IDs field into a normalized set.

    Handles comma-separated, semicolon-separated, and newline-separated IDs.
    """
    if not raw or raw in ("nan", "None", "null"):
        return frozenset()

    parts = re.split(r"[,;\n]+", raw)
    ids = set()
    for part in parts:
        normalized = normalize_id(part)
        if normalized:
            ids.add(normalized)
    return frozenset(ids)


def normalize_feature_name(name: str) -> str:
    """Normalize a feature/section name for consistent comparison."""
    if not name or name in ("nan", "None", "null"):
        return ""
    return name.strip().title()


def cross_link(
    requirements: Sequence[Requirement],
    test_cases: Sequence[TestCase],
) -> tuple[list[Requirement], list[TestCase]]:
    """Cross-link requirements and test cases bidirectionally.

    For each test case that references requirement IDs in its linked_ids,
    add the test case ID to that requirement's linked_ids. Returns new
    immutable copies — inputs are not modified.
    """
    # Build a map of requirement ID → set of linked test case IDs
    req_links: dict[str, set[str]] = {r.id: set(r.linked_ids) for r in requirements}

    for tc in test_cases:
        for req_id in tc.linked_ids:
            if req_id in req_links:
                req_links[req_id].add(tc.id)

    # Rebuild requirements with updated linked_ids
    updated_reqs = [
        r.model_copy(update={"linked_ids": frozenset(req_links.get(r.id, r.linked_ids))})
        for r in requirements
    ]

    return updated_reqs, list(test_cases)
