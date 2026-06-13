"""CLI test: context-guard preflight aborts `wrap agent` before launch (ai-rules#79)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from headroom.cli.main import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _large_repo(tmp_path: Path, *, threshold: int | None = None) -> Path:
    for i in range(10):
        (tmp_path / f"mod{i}.py").write_text("x", encoding="utf-8")
    if threshold is not None:
        cfg = tmp_path / ".headroom"
        cfg.mkdir(exist_ok=True)
        (cfg / "config.json").write_text(
            json.dumps(
                {
                    "contextGuard": {
                        "enabled": True,
                        "mode": "block",
                        "largeRepoFileThreshold": threshold,
                    }
                }
            ),
            encoding="utf-8",
        )
    return tmp_path


def test_block_aborts_before_launch(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # low file threshold so the 10-file repo counts as "large"
    monkeypatch.chdir(_large_repo(tmp_path, threshold=3))

    launched = {"yes": False}

    def fake_launch(**kwargs):  # pragma: no cover - must NOT run
        launched["yes"] = True

    with patch("headroom.cli.wrap.shutil.which", return_value="/usr/local/bin/cursor-agent"):
        with patch("headroom.cli.wrap._launch_tool", side_effect=fake_launch):
            # repo-wide prompt + large repo + no scope → REPO_WIDE_WITHOUT_SCOPE block
            result = runner.invoke(
                main,
                [
                    "wrap",
                    "agent",
                    "--no-context-tool",
                    "--context-guard-mode",
                    "block",
                    "--",
                    "read the entire codebase and refactor everything",
                ],
            )

    assert result.exit_code == 2, result.output
    assert "Headroom Context Guard" in result.output or "BLOCKED_CONTEXT_SPIKE" in result.output
    assert launched["yes"] is False


def test_scoped_prompt_is_not_blocked(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_large_repo(tmp_path))
    launched = {"yes": False}

    def fake_launch(**kwargs):
        launched["yes"] = True

    with patch("headroom.cli.wrap.shutil.which", return_value="/usr/local/bin/cursor-agent"):
        with patch("headroom.cli.wrap._launch_tool", side_effect=fake_launch):
            result = runner.invoke(
                main,
                [
                    "wrap",
                    "agent",
                    "--no-context-tool",
                    "--context-guard-mode",
                    "block",
                    "--",
                    "fix the bug in src/app.ts",
                ],
            )

    assert result.exit_code == 0, result.output
    assert launched["yes"] is True


def test_guard_off_by_default_does_not_block(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_large_repo(tmp_path))
    launched = {"yes": False}

    with patch("headroom.cli.wrap.shutil.which", return_value="/usr/local/bin/cursor-agent"):
        with patch(
            "headroom.cli.wrap._launch_tool",
            side_effect=lambda **k: launched.__setitem__("yes", True),
        ):
            # no --context-guard-mode and no env → guard disabled → launches
            result = runner.invoke(
                main,
                [
                    "wrap",
                    "agent",
                    "--no-context-tool",
                    "--",
                    "read the entire codebase and refactor everything",
                ],
            )

    assert result.exit_code == 0, result.output
    assert launched["yes"] is True
