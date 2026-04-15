"""Provider factory — maps config to concrete providers."""

from __future__ import annotations

from buddy_council.config import SecretsConfig, SourcesConfig
from buddy_council.providers.base import RequirementsProvider, TestCasesProvider
from buddy_council.providers.excel import ExcelProvider
from buddy_council.providers.testrail import TestRailProvider


def get_requirements_provider(
    config: SourcesConfig, secrets: SecretsConfig
) -> RequirementsProvider:
    """Create the requirements provider based on configuration."""
    provider_name = config.requirements.provider

    if provider_name == "excel":
        return ExcelProvider(config.requirements.excel_path)

    if provider_name == "jama":
        raise NotImplementedError(
            "Jama provider is not yet implemented. "
            "Use 'excel' provider with a Jama export file as a temporary fallback."
        )

    raise ValueError(f"Unknown requirements provider: {provider_name}")


def get_test_cases_provider(
    config: SourcesConfig, secrets: SecretsConfig
) -> TestCasesProvider:
    """Create the test cases provider based on configuration."""
    provider_name = config.test_cases.provider

    if provider_name == "testrail":
        tr = secrets.testrail
        if not tr.username or not tr.api_key:
            raise ValueError(
                "TestRail credentials are missing. "
                "Set them in ~/.buddy-council-secrets.json or run '/bc:setup'."
            )
        return TestRailProvider(
            base_url=config.test_cases.base_url,
            username=tr.username,
            api_key=tr.api_key,
            project_id=config.test_cases.project_id or 0,
            suite_id=config.test_cases.suite_id,
        )

    raise ValueError(f"Unknown test cases provider: {provider_name}")
