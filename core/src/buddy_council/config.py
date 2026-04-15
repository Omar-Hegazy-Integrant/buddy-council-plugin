"""Configuration and secrets loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


class RequirementsSourceConfig(BaseModel, frozen=True):
    provider: str
    excel_path: str = ""


class TestCasesSourceConfig(BaseModel, frozen=True):
    provider: str
    base_url: str = ""
    project_id: int | None = None
    suite_id: int | None = None


class SourcesConfig(BaseModel, frozen=True):
    requirements: RequirementsSourceConfig
    test_cases: TestCasesSourceConfig


class TestRailSecrets(BaseModel, frozen=True):
    username: str = ""
    api_key: str = ""


class JamaSecrets(BaseModel, frozen=True):
    username: str = ""
    api_key: str = ""


class SecretsConfig(BaseModel, frozen=True):
    testrail: TestRailSecrets = TestRailSecrets()
    jama: JamaSecrets = JamaSecrets()
    anthropic_api_key: str = ""


def load_config(config_dir: Path | None = None) -> SourcesConfig:
    """Load sources.json from the given directory.

    Falls back to BUDDY_COUNCIL_CONFIG_DIR env var, then ./config/.
    """
    if config_dir is None:
        env_dir = os.environ.get("BUDDY_COUNCIL_CONFIG_DIR")
        config_dir = Path(env_dir) if env_dir else Path("config")

    sources_path = config_dir / "sources.json"
    if not sources_path.exists():
        raise ConfigError(
            f"Configuration not found at {sources_path}. "
            "Run '/bc:setup' or create config/sources.json manually."
        )

    with open(sources_path) as f:
        data = json.load(f)

    return SourcesConfig.model_validate(data)


def load_secrets(path: Path | None = None) -> SecretsConfig:
    """Load secrets from the given path.

    Falls back to BUDDY_COUNCIL_SECRETS_PATH env var, then ~/.buddy-council-secrets.json.
    """
    if path is None:
        env_path = os.environ.get("BUDDY_COUNCIL_SECRETS_PATH")
        path = Path(env_path) if env_path else Path.home() / ".buddy-council-secrets.json"

    if not path.exists():
        return SecretsConfig()

    with open(path) as f:
        data = json.load(f)

    # Also check for ANTHROPIC_API_KEY env var
    anthropic_key = data.get("anthropic_api_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")

    return SecretsConfig(
        testrail=TestRailSecrets.model_validate(data.get("testrail", {})),
        jama=JamaSecrets.model_validate(data.get("jama", {})),
        anthropic_api_key=anthropic_key,
    )
