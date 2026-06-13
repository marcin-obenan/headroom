"""Unit tests: JSONL context ledger (ai-rules#79)."""

from __future__ import annotations

import json

from headroom.proxy.context_guard import (
    ContextContributor,
    ContextGuardConfig,
    RequestContextEstimate,
)
from headroom.proxy.context_guard.ledger import (
    LedgerEvent,
    event_from_decision,
    safe_contributors,
    write_event,
)


def _est() -> RequestContextEstimate:
    return RequestContextEstimate(
        provider="anthropic",
        client="claude",
        raw_estimated_input_tokens=1_500_000,
        compressed_estimated_input_tokens=400_000,
        estimated_output_tokens=2_000,
        largest_contributors=[
            ContextContributor("messages[2].content", "message", 900_000, 3_600_000),
            ContextContributor(
                "tool_result.read_file", "tool_result", 310_000, 1_240_000, path="pnpm-lock.yaml"
            ),
        ],
    )


def test_write_event_appends_jsonl(tmp_path) -> None:
    path = tmp_path / "nested" / "context-ledger.jsonl"
    cfg = ContextGuardConfig(
        enabled=True, mode="block", warn_at_tokens=200_000, block_at_tokens=1_000_000
    )
    ev = event_from_decision(
        run_id="r1",
        timestamp="2026-06-12T00:00:00Z",
        phase="proxy_request",
        decision="block",
        reason_codes=["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"],
        estimate=_est(),
        config=cfg,
    )
    assert write_event(ev, path) is True
    # second event appends a new line
    assert write_event(ev, path) is True

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["decision"] == "blocked"
    assert row["client"] == "claude"
    assert row["phase"] == "proxy_request"
    assert row["policy"]["mode"] == "block"
    assert row["estimate"]["rawEstimatedInputTokens"] == 1_500_000
    assert row["estimate"]["compressedEstimatedInputTokens"] == 400_000
    assert row["reasonCodes"] == ["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"]


def test_ledger_never_stores_prompt_content(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    cfg = ContextGuardConfig(enabled=True)
    ev = event_from_decision(
        run_id="r",
        timestamp="t",
        phase="proxy_request",
        decision="allow",
        reason_codes=[],
        estimate=_est(),
        config=cfg,
    )
    write_event(ev, path)
    raw = path.read_text(encoding="utf-8")
    # contributor metadata is present, but no message/file *content* fields exist
    assert "pnpm-lock.yaml" in raw  # path is safe metadata
    assert "content" not in json.loads(raw.strip())["topContributors"][0]
    # only safe keys
    keys = set(json.loads(raw.strip())["topContributors"][0].keys())
    assert keys <= {"source", "kind", "estimated_tokens", "bytes", "path", "reason"}


def test_safe_contributors_caps_and_excludes_content() -> None:
    contribs = safe_contributors(_est())
    assert len(contribs) <= 5
    assert all("content" not in c for c in contribs)


def test_write_event_fails_open_on_bad_path(tmp_path) -> None:
    # a path whose parent is a file (cannot mkdir) → returns False, no raise
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    bad = f / "sub" / "ledger.jsonl"
    ev = LedgerEvent(
        run_id="r",
        timestamp="t",
        client="claude",
        provider="anthropic",
        phase="proxy_request",
        decision="allowed",
    )
    assert write_event(ev, bad) is False


def test_override_recorded_only_when_used(tmp_path) -> None:
    path = tmp_path / "l.jsonl"
    cfg = ContextGuardConfig(enabled=True)
    ev = event_from_decision(
        run_id="r",
        timestamp="t",
        phase="proxy_request",
        decision="warn",
        reason_codes=["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"],
        estimate=_est(),
        config=cfg,
        override_used=True,
        override_reason="intentional",
    )
    write_event(ev, path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["override"] == {"used": True, "reason": "intentional"}
