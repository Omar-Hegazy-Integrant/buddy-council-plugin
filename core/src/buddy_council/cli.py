"""Buddy-Council CLI — portable entry point for analysis commands."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from buddy_council.analyzers.anthropic import AnthropicAnalyzer
from buddy_council.analyzers.base import AnalyzerConfig
from buddy_council.analyzers.contradiction import run_contradiction_analysis
from buddy_council.config import ConfigError, load_config, load_secrets
from buddy_council.normalization import cross_link
from buddy_council.providers.registry import get_requirements_provider, get_test_cases_provider
from buddy_council.reports.contradiction import render_contradiction_report


@click.group()
@click.option("--config-dir", type=click.Path(exists=True, path_type=Path), default=None,
              help="Path to config directory containing sources.json")
@click.option("--secrets-path", type=click.Path(path_type=Path), default=None,
              help="Path to secrets file")
@click.option("--model", default=None, help="LLM model to use (default: claude-sonnet-4-20250514)")
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.pass_context
def main(ctx: click.Context, config_dir: Path | None, secrets_path: Path | None,
         model: str | None, verbose: bool) -> None:
    """Buddy-Council: requirements and test case analysis."""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config_dir
    ctx.obj["secrets_path"] = secrets_path
    ctx.obj["model"] = model
    ctx.obj["verbose"] = verbose


@main.command()
@click.option("--scope", default="all", help="Requirement ID, feature name, or 'all'")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown",
              help="Output format")
@click.pass_context
def contradiction(ctx: click.Context, scope: str, fmt: str) -> None:
    """Detect contradictions between requirements and test cases."""
    asyncio.run(_run_contradiction(ctx.obj, scope, fmt))


async def _run_contradiction(opts: dict, scope: str, fmt: str) -> None:
    verbose = opts.get("verbose", False)

    try:
        config = load_config(opts.get("config_dir"))
        secrets = load_secrets(opts.get("secrets_path"))
    except ConfigError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    if verbose:
        click.echo(f"Config loaded: {config.requirements.provider} + {config.test_cases.provider}", err=True)

    # Get providers
    try:
        req_provider = get_requirements_provider(config, secrets)
        tc_provider = get_test_cases_provider(config, secrets)
    except (ValueError, NotImplementedError, FileNotFoundError) as e:
        click.echo(f"Provider error: {e}", err=True)
        sys.exit(3)

    # Fetch data
    if verbose:
        click.echo("Fetching requirements...", err=True)
    requirements = await req_provider.fetch(scope if scope != "all" else None)

    if verbose:
        click.echo(f"Fetched {len(requirements)} requirements", err=True)
        click.echo("Fetching test cases...", err=True)
    test_cases = await tc_provider.fetch(scope if scope != "all" else None)

    if verbose:
        click.echo(f"Fetched {len(test_cases)} test cases", err=True)

    # Normalize and cross-link
    requirements, test_cases = cross_link(requirements, test_cases)

    if verbose:
        click.echo("Artifacts normalized and cross-linked", err=True)

    # Analyze
    api_key = secrets.anthropic_api_key
    if not api_key:
        click.echo(
            "Error: Anthropic API key not found. "
            "Set ANTHROPIC_API_KEY env var or add 'anthropic_api_key' to secrets file.",
            err=True,
        )
        sys.exit(2)

    analyzer_config = AnalyzerConfig(model=opts.get("model") or "claude-sonnet-4-20250514")
    analyzer = AnthropicAnalyzer(api_key=api_key, config=analyzer_config)

    if verbose:
        click.echo(f"Analyzing with model {analyzer_config.model}...", err=True)

    report = await run_contradiction_analysis(requirements, test_cases, analyzer, scope=scope)

    # Output
    if fmt == "json":
        click.echo(report.model_dump_json(indent=2))
    else:
        click.echo(render_contradiction_report(report))

    # Exit code: 1 if critical findings
    if report.has_critical:
        sys.exit(1)
