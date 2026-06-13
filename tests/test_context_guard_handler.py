"""App-level integration: context guard blocks before upstream (ai-rules#79).

Proves the guard wired into the Anthropic handler returns the agent-readable
413 error and never calls the provider, and that a small request passes the
guard (does not 413).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from headroom.proxy.context_guard.integration import OVERRIDE_HEADER
from headroom.proxy.context_guard.reason_codes import ContextSpikeReason
from headroom.proxy.server import HeadroomProxy, ProxyConfig, create_app

_BLOCK_GUARD = {
    "enabled": True,
    "mode": "block",
    "blockAtTokens": 50_000,
    "maxSingleMessageTokens": 10_000_000,  # isolate the total-input rule
    "allowCompressionInsteadOfBlock": False,
}

_WARN_GUARD = {
    "enabled": True,
    "mode": "warn",
    "blockAtTokens": 50_000,
    "maxSingleMessageTokens": 10_000_000,
    "allowCompressionInsteadOfBlock": False,
}

_COMPRESS_GUARD = {
    "enabled": True,
    "mode": "block",
    "blockAtTokens": 50_000,
    "maxSingleMessageTokens": 10_000_000,
    "allowCompressionInsteadOfBlock": True,
}


def _app(**overrides: Any):
    config = ProxyConfig(
        optimize=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        cost_tracking_enabled=False,
        **overrides,
    )
    return create_app(config)


def test_invalid_guard_config_does_not_crash_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A malformed guard config must NOT take down the proxy — it starts with the
    # guard disabled.
    monkeypatch.setenv("HEADROOM_CONTEXT_GUARD_MODE", "bogus-mode")
    app = _app()  # context_guard not provided → create_app calls load_raw_config
    assert app is not None  # did not raise


def _messages_body(approx_tokens: int) -> dict:
    return {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "x" * (approx_tokens * 4)}],
        "max_tokens": 256,
    }


def test_oversized_request_is_blocked_with_413_and_no_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"upstream": False}

    async def _explode(*a: Any, **k: Any):  # pragma: no cover - must not run
        called["upstream"] = True
        raise AssertionError("upstream must not be called for a blocked request")

    # Any attempt to forward upstream flips the flag / raises.
    monkeypatch.setattr(HeadroomProxy, "_forward_to_anthropic", _explode, raising=False)

    app = _app(context_guard=_BLOCK_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(80_000)),
        )

    assert resp.status_code == 413, resp.text
    payload = resp.json()
    assert payload["error"]["type"] == "context_budget_exceeded"
    assert payload["error"]["code"].startswith("BLOCKED_CONTEXT_SPIKE_")
    assert called["upstream"] is False


def test_openai_chat_oversized_request_blocked_413() -> None:
    app = _app(context_guard=_BLOCK_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            # varied words so tiktoken doesn't BPE-merge a repeated char into
            # far fewer tokens than the byte length implies.
            content=json.dumps(
                {
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "user", "content": " ".join(f"word{i}" for i in range(80_000))}
                    ],
                }
            ),
        )
    assert resp.status_code == 413, resp.text
    assert resp.json()["error"]["type"] == "context_budget_exceeded"


def test_small_request_is_not_blocked_by_guard() -> None:
    # With the guard enabled but the request tiny, the guard must NOT 413.
    # (The request may fail later trying to reach a real upstream — we only
    # assert the guard itself did not block it.)
    app = _app(context_guard=_BLOCK_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(5)),
        )
    assert resp.status_code != 413


def test_warn_mode_oversized_request_is_not_413() -> None:
    """Warn mode logs but must not return 413 — request continues past the guard."""
    app = _app(context_guard=_WARN_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(80_000)),
        )

    assert resp.status_code != 413


def test_compress_decision_is_not_413() -> None:
    """Size-only spike with compression allowed proceeds instead of 413."""
    app = _app(context_guard=_COMPRESS_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(80_000)),
        )

    assert resp.status_code != 413


def test_block_writes_content_free_ledger_event(tmp_path: Path) -> None:
    ledger = tmp_path / "context-ledger.jsonl"
    guard = {
        **_BLOCK_GUARD,
        "ledgerPath": str(ledger),
    }
    app = _app(context_guard=guard)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(80_000)),
        )

    assert resp.status_code == 413
    assert ledger.exists()
    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["decision"] == "blocked"
    assert row["reasonCodes"]
    assert "estimate" in row
    blob = json.dumps(row)
    assert "xxxx" not in blob  # no raw prompt payload from _messages_body


def test_openai_block_writes_content_free_ledger_event(tmp_path: Path) -> None:
    ledger = tmp_path / "context-ledger-openai.jsonl"
    guard = {
        **_BLOCK_GUARD,
        "ledgerPath": str(ledger),
    }
    app = _app(context_guard=guard)
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": " ".join(f"word{i}" for i in range(80_000))}],
    }
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(body),
        )

    assert resp.status_code == 413
    assert ledger.exists()
    row = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert row["decision"] == "blocked"
    assert row["reasonCodes"]
    blob = json.dumps(row)
    assert "word0" not in blob  # no raw prompt payload from chat body


def test_header_override_downgrades_block_to_non_413(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"upstream": False}

    async def _explode(*a: Any, **k: Any):  # pragma: no cover
        called["upstream"] = True
        raise AssertionError("overridden size block must not 413")

    monkeypatch.setattr(HeadroomProxy, "_forward_to_anthropic", _explode, raising=False)
    monkeypatch.delenv("CI", raising=False)

    app = _app(context_guard=_BLOCK_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={
                "host": "127.0.0.1:8787",
                "content-type": "application/json",
                OVERRIDE_HEADER: "intentional large context for migration",
            },
            content=json.dumps(_messages_body(80_000)),
        )

    assert resp.status_code != 413
    assert called["upstream"] is False


def test_non_overridable_binary_context_stays_413_with_override_header() -> None:
    app = _app(context_guard=_BLOCK_GUARD)
    body = {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "\x00\x01\x02" * 2000}],
        "max_tokens": 256,
    }
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        resp = client.post(
            "/v1/messages",
            headers={
                "host": "127.0.0.1:8787",
                "content-type": "application/json",
                OVERRIDE_HEADER: "should not bypass binary",
            },
            content=json.dumps(body),
        )

    assert resp.status_code == 413, resp.text
    payload = resp.json()
    assert ContextSpikeReason.BINARY_CONTEXT.value in payload["error"]["code"]


def test_guard_analyze_runs_on_each_guarded_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    estimator_mod = __import__(
        "headroom.proxy.context_guard.estimator",
        fromlist=["analyze_request"],
    )
    real_analyze = estimator_mod.analyze_request

    def _tracking_analyze(body: dict[str, Any], **kwargs: Any):
        order.append("analyze")
        return real_analyze(body, **kwargs)

    monkeypatch.setattr(
        "headroom.proxy.context_guard.integration.analyze_request",
        _tracking_analyze,
    )

    app = _app(context_guard=_BLOCK_GUARD)
    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        client.post(
            "/v1/messages",
            headers={"host": "127.0.0.1:8787", "content-type": "application/json"},
            content=json.dumps(_messages_body(5)),
        )

    assert order == ["analyze"]
