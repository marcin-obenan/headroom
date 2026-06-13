"""Integration tests for the guard shim used by the proxy handlers (ai-rules#79)."""

from __future__ import annotations

import json

from headroom.proxy.context_guard import (
    OVERRIDE_HEADER,
    guard_request,
    load_raw_config,
    override_from_headers,
    run_guard,
)
from headroom.proxy.context_guard.models import ContextGuardConfig, OverrideContext


def _big_anthropic_body(approx_tokens: int) -> dict:
    # ~4 chars/token fallback → size the content to exceed the threshold.
    return {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "x" * (approx_tokens * 4)}],
        "max_tokens": 1024,
    }


def test_guard_request_blocks_oversized_and_writes_ledger(tmp_path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    raw = {
        "enabled": True,
        "mode": "block",
        "blockAtTokens": 100_000,
        "allowCompressionInsteadOfBlock": False,
        "ledgerPath": str(ledger),
    }
    out = guard_request(
        raw_config=raw,
        body=_big_anthropic_body(200_000),
        provider="anthropic",
        client="claude",
        run_id="rid-1",
    )
    assert out.blocked is True
    assert out.machine_error is not None
    assert out.machine_error["error"]["type"] == "context_budget_exceeded"
    assert "message" in out.machine_error  # human text attached for clients
    # ledger recorded the block, content-free
    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["decision"] == "blocked"
    assert row["phase"] == "proxy_request"


def test_guard_request_allows_small_request(tmp_path) -> None:
    raw = {
        "enabled": True,
        "mode": "block",
        "blockAtTokens": 1_000_000,
        "ledgerPath": str(tmp_path / "l.jsonl"),
    }
    out = guard_request(
        raw_config=raw,
        body=_big_anthropic_body(10),
        provider="anthropic",
        client="claude",
        run_id="r",
    )
    assert out.blocked is False
    assert out.decision == "allow"


def test_guard_request_per_client_threshold(tmp_path) -> None:
    raw = {
        "enabled": True,
        "mode": "block",
        "blockAtTokens": 1_000_000,
        "allowCompressionInsteadOfBlock": False,
        # isolate the total-input threshold: don't let the single-message rule fire
        "maxSingleMessageTokens": 10_000_000,
        "ledgerPath": str(tmp_path / "l.jsonl"),
        "clients": {"cursor": {"blockAtTokens": 100_000}},
    }
    body = _big_anthropic_body(200_000)
    # cursor's lower threshold blocks; claude's high threshold allows
    assert (
        guard_request(
            raw_config=raw, body=body, provider="anthropic", client="cursor", run_id="r"
        ).blocked
        is True
    )
    assert (
        guard_request(
            raw_config=raw, body=body, provider="anthropic", client="claude", run_id="r"
        ).blocked
        is False
    )


def test_override_from_headers_builds_context() -> None:
    ov = override_from_headers({OVERRIDE_HEADER: "intentional big context"})
    assert ov.requested is True
    assert ov.reason == "intentional big context"


def test_override_from_headers_absent() -> None:
    ov = override_from_headers({})
    assert ov.requested is False
    assert ov.reason is None


def test_override_from_headers_ci_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CI", "true")
    ov = override_from_headers({OVERRIDE_HEADER: "reason"})
    assert ov.ci_disabled is True


def test_guard_request_header_override_downgrades_block(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    raw = {
        "enabled": True,
        "mode": "block",
        "blockAtTokens": 100_000,
        "maxSingleMessageTokens": 10_000_000,
        "allowCompressionInsteadOfBlock": False,
        "ledgerPath": str(tmp_path / "l.jsonl"),
    }
    ov = override_from_headers({OVERRIDE_HEADER: "I really need this"})
    out = guard_request(
        raw_config=raw,
        body=_big_anthropic_body(200_000),
        provider="anthropic",
        client="claude",
        run_id="r",
        override=ov,
    )
    assert out.blocked is False
    assert out.decision == "warn"
    # ledger records the override
    row = json.loads((tmp_path / "l.jsonl").read_text(encoding="utf-8").strip())
    assert row["override"] == {"used": True, "reason": "I really need this"}


def test_guard_request_none_config_is_noop() -> None:
    out = guard_request(
        raw_config=None,
        body=_big_anthropic_body(10_000_000),
        provider="anthropic",
        client="claude",
        run_id="r",
    )
    assert out.decision == "allow"
    assert out.blocked is False


def test_guard_request_invalid_config_fails_open() -> None:
    out = guard_request(
        raw_config={"enabled": True, "mode": "explode"},
        body=_big_anthropic_body(10_000_000),
        provider="anthropic",
        client="claude",
        run_id="r",
    )
    assert out.blocked is False  # fail open, never crash the request path


def test_run_guard_override_downgrades_block(tmp_path) -> None:
    cfg = ContextGuardConfig(
        enabled=True,
        mode="block",
        block_at_tokens=100_000,
        allow_compression_instead_of_block=False,
        ledger_path=str(tmp_path / "l.jsonl"),
    )
    out = run_guard(
        body=_big_anthropic_body(200_000),
        provider="anthropic",
        client="claude",
        config=cfg,
        run_id="r",
        override=OverrideContext(requested=True, reason="intentional"),
    )
    assert out.blocked is False
    assert out.decision == "warn"


def test_load_raw_config_off_by_default(tmp_path) -> None:
    # no files, no env → None (proxy path untouched)
    assert load_raw_config(cwd=tmp_path, home=tmp_path, env={}) is None


def test_load_raw_config_env_enables(tmp_path) -> None:
    raw = load_raw_config(cwd=tmp_path, home=tmp_path, env={"HEADROOM_CONTEXT_GUARD_MODE": "block"})
    assert raw is not None and raw["mode"] == "block" and raw["enabled"] is True


def test_load_raw_config_repo_file_overrides_user(tmp_path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    (home / ".headroom").mkdir(parents=True)
    (repo / ".headroom").mkdir(parents=True)
    (home / ".headroom" / "config.json").write_text(
        json.dumps(
            {"headroom": {"contextGuard": {"enabled": True, "mode": "warn", "blockAtTokens": 999}}}
        ),
        encoding="utf-8",
    )
    (repo / ".headroom" / "config.json").write_text(
        json.dumps({"contextGuard": {"mode": "block"}}), encoding="utf-8"
    )
    raw = load_raw_config(cwd=repo, home=home, env={})
    assert raw["mode"] == "block"  # repo wins
    assert raw["blockAtTokens"] == 999  # inherited from user
