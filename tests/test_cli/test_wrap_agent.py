"""Tests for `headroom wrap agent` (headless Cursor Agent CLI)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

import pytest
from click.testing import CliRunner

from headroom.cli.main import main


def _expected_project_prefix() -> str:
    """The /p/<name> prefix the wrap embeds (launch-directory basename)."""
    return f"/p/{quote(Path.cwd().name, safe='')}"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_wrap_agent_sets_provider_envs_and_launches_cursor_agent(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_launch_tool(**kwargs):  # noqa: ANN003
        captured.update(kwargs)

    # cursor-agent is preferred over the `agent` alias.
    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/cursor-agent" if name == "cursor-agent" else None

    with patch("headroom.cli.wrap.shutil.which", side_effect=fake_which):
        with patch("headroom.cli.wrap._launch_tool", side_effect=fake_launch_tool):
            result = runner.invoke(
                main,
                ["wrap", "agent", "--no-rtk", "--", "-m", "composer-2.5-fast", "fix the bug"],
            )

    assert result.exit_code == 0, result.output
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["OPENAI_BASE_URL"] == f"http://127.0.0.1:8787{_expected_project_prefix()}/v1"
    assert env["ANTHROPIC_BASE_URL"] == f"http://127.0.0.1:8787{_expected_project_prefix()}"
    assert captured["binary"] == "/usr/local/bin/cursor-agent"
    assert captured["tool_label"] == "CURSOR AGENT"
    assert captured["agent_type"] == "cursor"
    assert captured["args"] == ("-m", "composer-2.5-fast", "fix the bug")


def test_wrap_agent_falls_back_to_agent_binary(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_which(name: str) -> str | None:
        # cursor-agent absent; the `agent` alias is present.
        return "/usr/local/bin/agent" if name == "agent" else None

    with patch("headroom.cli.wrap.shutil.which", side_effect=fake_which):
        with patch("headroom.cli.wrap._launch_tool", side_effect=lambda **k: captured.update(k)):
            result = runner.invoke(main, ["wrap", "agent", "--no-rtk"])

    assert result.exit_code == 0, result.output
    assert captured["binary"] == "/usr/local/bin/agent"


def test_wrap_agent_errors_when_no_binary_on_path(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with patch("headroom.cli.wrap.shutil.which", return_value=None):
        with patch("headroom.cli.wrap._launch_tool") as launch:
            result = runner.invoke(main, ["wrap", "agent", "--no-rtk"])

    assert result.exit_code == 1
    assert "cursor-agent" in result.output
    launch.assert_not_called()
