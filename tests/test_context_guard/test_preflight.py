"""Unit tests: local wrapper preflight (ai-rules#79)."""

from __future__ import annotations

import json

from headroom.proxy.context_guard import (
    detect_repo_wide_prompt,
    has_explicit_scope,
    preflight_request,
)
from headroom.proxy.context_guard.models import ContextGuardConfig
from headroom.proxy.context_guard.preflight import (
    PreflightInput,
    count_repo_files,
    run_preflight,
)


def test_detect_repo_wide_prompt() -> None:
    assert detect_repo_wide_prompt("please read the entire codebase and summarize")
    assert detect_repo_wide_prompt("scan the repo for bugs")
    assert not detect_repo_wide_prompt("fix the bug in src/app.ts")


def test_has_explicit_scope() -> None:
    assert has_explicit_scope(["fix", "src/app.ts"], "")
    assert has_explicit_scope([], "look at @packages/api/handler.ts")
    assert has_explicit_scope([], "update billing.py please")
    assert not has_explicit_scope(["refactor", "everything"], "the whole thing")


def test_count_repo_files_skips_artifacts(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    for i in range(50):
        (tmp_path / "node_modules" / f"m{i}.js").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x", encoding="utf-8")
    # only the 2 src files count
    assert count_repo_files(tmp_path) == 2


def test_count_repo_files_caps(tmp_path) -> None:
    for i in range(20):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    assert count_repo_files(tmp_path, cap=5) == 5


def _cfg(**kw) -> ContextGuardConfig:
    base = {
        "enabled": True,
        "mode": "block",
        "block_at_tokens": 1_000_000,
        "large_repo_file_threshold": 3,
    }
    base.update(kw)
    return ContextGuardConfig(**base)


def test_preflight_blocks_repo_wide_in_large_repo(tmp_path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    out = run_preflight(
        PreflightInput(
            client="agent", argv=["read the entire codebase and refactor"], cwd=str(tmp_path)
        ),
        _cfg(ledger_path=str(tmp_path / "l.jsonl")),
        run_id="r",
    )
    assert out.blocked is True
    assert "BLOCKED_CONTEXT_SPIKE_REPO_WIDE_WITHOUT_SCOPE" in out.reason_codes
    assert out.machine_error is not None


def test_preflight_allows_repo_wide_when_scoped(tmp_path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    out = run_preflight(
        PreflightInput(
            client="agent",
            argv=["read the entire codebase but only", "src/app.ts"],
            cwd=str(tmp_path),
        ),
        _cfg(ledger_path=str(tmp_path / "l.jsonl")),
        run_id="r",
    )
    assert out.blocked is False  # explicit scope present


def test_preflight_allows_repo_wide_in_small_repo(tmp_path) -> None:
    (tmp_path / "only.py").write_text("x", encoding="utf-8")
    out = run_preflight(
        PreflightInput(client="agent", argv=["scan the whole repo"], cwd=str(tmp_path)),
        _cfg(ledger_path=str(tmp_path / "l.jsonl"), large_repo_file_threshold=100),
        run_id="r",
    )
    assert out.blocked is False  # small repo → not blocked


def test_preflight_blocks_huge_stdin(tmp_path) -> None:
    big = " ".join(f"word{i}" for i in range(60_000))  # >50k tokens
    out = run_preflight(
        PreflightInput(client="claude", argv=["summarize"], stdin_text=big, cwd=str(tmp_path)),
        _cfg(
            block_at_tokens=50_000,
            allow_compression_instead_of_block=False,
            ledger_path=str(tmp_path / "l.jsonl"),
        ),
        run_id="r",
    )
    assert out.blocked is True
    assert "BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE" in out.reason_codes


def test_preflight_writes_ledger_with_wrapper_phase(tmp_path) -> None:
    ledger = tmp_path / "l.jsonl"
    run_preflight(
        PreflightInput(client="claude", argv=["fix src/app.ts"], cwd=str(tmp_path)),
        _cfg(mode="warn", ledger_path=str(ledger)),
        run_id="r",
    )
    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["phase"] == "wrapper_preflight"


def test_preflight_detects_prompt_after_boolean_flag(tmp_path) -> None:
    # cursor-agent style: `-p` is boolean (--print); the prompt follows it and
    # must NOT be dropped from analysis.
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    out = run_preflight(
        PreflightInput(
            client="agent",
            argv=["-p", "--force", "read the entire codebase and refactor"],
            cwd=str(tmp_path),
        ),
        _cfg(ledger_path=str(tmp_path / "l.jsonl")),
        run_id="r",
    )
    assert out.blocked is True
    assert "BLOCKED_CONTEXT_SPIKE_REPO_WIDE_WITHOUT_SCOPE" in out.reason_codes


def test_preflight_request_none_config_noop() -> None:
    out = preflight_request(
        raw_config=None, client="agent", argv=["read the entire codebase"], run_id="r"
    )
    assert out.blocked is False


def test_preflight_request_per_client(tmp_path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
    raw = {
        "enabled": True,
        "mode": "block",
        "largeRepoFileThreshold": 3,
        "ledgerPath": str(tmp_path / "l.jsonl"),
        "clients": {"agent": {"blockRepoWidePrompts": True}},
    }
    out = preflight_request(
        raw_config=raw,
        client="agent",
        argv=["read the entire codebase"],
        cwd=str(tmp_path),
        run_id="r",
    )
    assert out.blocked is True


def test_preflight_fail_open_when_policy_raises(tmp_path) -> None:
    from unittest.mock import patch

    with patch(
        "headroom.proxy.context_guard.preflight.decide",
        side_effect=RuntimeError("policy boom"),
    ):
        out = run_preflight(
            PreflightInput(client="claude", argv=["hello"], cwd=str(tmp_path)),
            _cfg(block_at_tokens=50_000, ledger_path=str(tmp_path / "l.jsonl")),
            run_id="r",
        )
    assert out.blocked is False
    assert any("preflight failed" in w for w in out.warnings)


def test_preflight_block_includes_rejection_metrics_and_ledger(tmp_path) -> None:
    ledger = tmp_path / "l.jsonl"
    big = " ".join(f"word{i}" for i in range(60_000))
    out = run_preflight(
        PreflightInput(client="claude", argv=["summarize"], stdin_text=big, cwd=str(tmp_path)),
        _cfg(
            block_at_tokens=50_000,
            allow_compression_instead_of_block=False,
            ledger_path=str(ledger),
        ),
        run_id="r",
    )
    assert out.blocked is True
    assert out.metrics is not None
    assert out.metrics["overshootTokens"] > 0
    assert out.metrics["stdinEstimatedTokens"] > 0

    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["decision"] == "blocked"
    assert "rejectionMetrics" in row
    assert row["rejectionMetrics"]["stdinEstimatedTokens"] == out.metrics["stdinEstimatedTokens"]
