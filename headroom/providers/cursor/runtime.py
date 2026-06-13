"""Runtime helpers for Cursor integrations."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from headroom.providers.claude import proxy_base_url as claude_proxy_base_url
from headroom.providers.codex import proxy_base_url as codex_proxy_base_url
from headroom.proxy.project_context import with_project_prefix


@dataclass(frozen=True)
class CursorProxyTargets:
    """Resolved local proxy targets shown in Cursor setup instructions."""

    openai_base_url: str
    anthropic_base_url: str


def build_proxy_targets(port: int, project: str | None = None) -> CursorProxyTargets:
    """Build the local proxy URLs shown to Cursor users.

    ``project`` (the wrap launch directory) is encoded as a ``/p/<name>``
    base-URL prefix because Cursor cannot send custom headers; the proxy
    strips it and attributes savings per project.
    """
    return CursorProxyTargets(
        openai_base_url=with_project_prefix(codex_proxy_base_url(port), project),
        anthropic_base_url=with_project_prefix(claude_proxy_base_url(port), project),
    )


def build_launch_env(
    port: int,
    environ: Mapping[str, str] | None = None,
    project: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Build environment variables for the Cursor Agent CLI through the proxy.

    Mirrors the other Pattern-A launchers (claude/codex/aider): the returned
    ``env`` is a copy of ``environ`` with ``OPENAI_BASE_URL`` and
    ``ANTHROPIC_BASE_URL`` pointed at the local Headroom proxy, so the headless
    ``cursor-agent`` CLI routes every provider call through Headroom.

    ``project`` (the wrap launch directory) is encoded as a ``/p/<name>``
    base-URL prefix because Cursor cannot send custom headers; the proxy strips
    it and attributes savings per project. The second tuple element is a
    display list for the wrap banner.
    """
    env = dict(environ or os.environ)
    openai_base_url = with_project_prefix(codex_proxy_base_url(port), project)
    anthropic_base_url = with_project_prefix(claude_proxy_base_url(port), project)
    env["OPENAI_BASE_URL"] = openai_base_url
    env["ANTHROPIC_BASE_URL"] = anthropic_base_url
    return env, [
        f"OPENAI_BASE_URL={openai_base_url}",
        f"ANTHROPIC_BASE_URL={anthropic_base_url}",
    ]


def render_setup_lines(port: int, project: str | None = None) -> list[str]:
    """Render the Cursor setup instructions for the local proxy."""
    targets = build_proxy_targets(port, project)
    lines = [
        "  Headroom proxy is running. Configure Cursor:",
        "",
        "  For OpenAI models:",
        f"    Base URL:  {targets.openai_base_url}",
        "    API Key:   your-openai-api-key",
        "",
        "  For Anthropic models:",
        f"    Base URL:  {targets.anthropic_base_url}",
        "    API Key:   your-anthropic-api-key",
        "",
        "  In Cursor:",
        "    Settings > Models > OpenAI API Key > Override OpenAI Base URL",
        f"    Set to: {targets.openai_base_url}",
    ]
    if project:
        lines += [
            "",
            f"  Dashboard savings will be attributed to project '{project}'",
            "  (the directory this command was run from). Re-run from another",
            "  project directory to get that project's URL.",
        ]
    return lines
