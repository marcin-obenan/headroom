"""Tests for structured context-guard rejection metrics (ai-rules#79)."""

from __future__ import annotations

from headroom.proxy.context_guard.models import (
    ContextContributor,
    ContextGuardConfig,
    RequestContextEstimate,
)
from headroom.proxy.context_guard.rejection_metrics import (
    build_block_metrics,
    build_stdin_byte_cap_metrics,
    format_rejection_stderr,
)


def _estimate(raw: int = 600_000) -> RequestContextEstimate:
    return RequestContextEstimate(
        provider="anthropic",
        client="claude",
        raw_estimated_input_tokens=raw,
        contributors=[
            ContextContributor(
                source="argv.prompt", kind="message", estimated_tokens=100_000, bytes=400_000
            ),
            ContextContributor(
                source="stdin", kind="stdin", estimated_tokens=500_000, bytes=2_000_000
            ),
        ],
        largest_contributors=[
            ContextContributor(
                source="stdin", kind="stdin", estimated_tokens=500_000, bytes=2_000_000
            ),
            ContextContributor(
                source="argv.prompt", kind="message", estimated_tokens=100_000, bytes=400_000
            ),
        ],
    )


def test_build_block_metrics_includes_overshoot_and_breakdown() -> None:
    cfg = ContextGuardConfig(enabled=True, mode="block", block_at_tokens=500_000)
    metrics = build_block_metrics(
        phase="wrapper_preflight",
        client="claude",
        provider="anthropic",
        reason_codes=["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"],
        estimate=_estimate(),
        config=cfg,
        stdin_bytes=2_000_000,
        repo_wide_without_scope=False,
    )

    assert metrics["estimatedInputTokens"] == 600_000
    assert metrics["budgetTokens"] == 500_000
    assert metrics["overshootTokens"] == 100_000
    assert metrics["argvEstimatedTokens"] == 100_000
    assert metrics["stdinEstimatedTokens"] == 500_000
    assert metrics["stdinBytes"] == 2_000_000
    assert metrics["topContributor"]["source"] == "stdin"


def test_format_rejection_stderr_is_content_free() -> None:
    cfg = ContextGuardConfig(enabled=True, mode="block", block_at_tokens=500_000)
    metrics = build_block_metrics(
        phase="wrapper_preflight",
        client="claude",
        provider="anthropic",
        reason_codes=["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"],
        estimate=_estimate(),
        config=cfg,
        stdin_bytes=100,
    )
    lines = format_rejection_stderr(metrics)
    blob = "\n".join(lines)
    assert "rejection metrics" in blob
    assert "overshoot=100,000" in blob
    assert "argv_tokens" in blob
    assert "stdin_tokens" in blob
    assert "word0" not in blob


def test_build_stdin_byte_cap_metrics() -> None:
    metrics = build_stdin_byte_cap_metrics(
        client="codex",
        byte_cap=32,
        bytes_observed=33,
        stdin_prefix_tokens=10,
    )
    assert metrics["phase"] == "wrapper_stdin_byte_cap"
    assert metrics["stdinBytes"] == 33
    assert metrics["stdinPrefixEstimatedTokens"] == 10
