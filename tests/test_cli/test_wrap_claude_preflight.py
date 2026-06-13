"""CLI test: context-guard preflight aborts `wrap claude` before launch (ai-rules#79)."""

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


def test_block_aborts_before_prepare(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_large_repo(tmp_path, threshold=3))

    with patch("headroom.cli.wrap._prepare_wrap_rtk") as prepare:
        result = runner.invoke(
            main,
            [
                "wrap",
                "claude",
                "--prepare-only",
                "--no-rtk",
                "--",
                "read the entire codebase and refactor everything",
            ],
        )

    assert result.exit_code == 2, result.output
    assert "Headroom Context Guard" in result.output or "BLOCKED_CONTEXT_SPIKE" in result.output
    prepare.assert_not_called()


def test_scoped_prompt_is_not_blocked_by_preflight(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(_large_repo(tmp_path, threshold=3))

    result = runner.invoke(
        main,
        [
            "wrap",
            "claude",
            "--prepare-only",
            "--no-rtk",
            "--",
            "fix the bug in src/app.ts",
        ],
    )

    assert result.exit_code == 0, result.output
