"""Structured rejection metrics for the Context Guard (ai-rules#79).

Emits grep-friendly log events and human-readable stderr summaries when a
request is blocked — without leaking prompt bodies.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import ContextGuardConfig, RequestContextEstimate

logger = logging.getLogger("headroom.proxy.context_guard")

_EVENT = "context_guard_rejected"


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _contributor_tokens_by_kind(estimate: RequestContextEstimate | None) -> dict[str, int]:
    if estimate is None:
        return {}
    out: dict[str, int] = {}
    for c in estimate.contributors or estimate.largest_contributors:
        out[c.kind] = out.get(c.kind, 0) + c.estimated_tokens
    return out


def _contributor_tokens_by_source(estimate: RequestContextEstimate | None) -> dict[str, int]:
    if estimate is None:
        return {}
    out: dict[str, int] = {}
    for c in estimate.contributors or estimate.largest_contributors:
        out[c.source] = out.get(c.source, 0) + c.estimated_tokens
    return out


def build_block_metrics(
    *,
    phase: str,
    client: str,
    provider: str,
    reason_codes: list[str],
    estimate: RequestContextEstimate | None,
    config: ContextGuardConfig | None,
    stdin_bytes: int | None = None,
    stdin_byte_cap: int | None = None,
    repo_file_count: int | None = None,
    repo_wide_without_scope: bool | None = None,
) -> dict[str, Any]:
    """Build a content-free metrics dict for a guard rejection."""
    raw_tokens = estimate.raw_estimated_input_tokens if estimate else None
    budget = config.block_at_tokens if config else None
    warn_at = config.warn_at_tokens if config else None

    overshoot_tokens: int | None = None
    overshoot_pct: float | None = None
    budget_utilization_pct: float | None = None
    if raw_tokens is not None and budget is not None:
        if raw_tokens > budget:
            overshoot_tokens = raw_tokens - budget
            overshoot_pct = _pct(overshoot_tokens, budget)
        budget_utilization_pct = _pct(raw_tokens, budget)

    top = None
    if estimate and estimate.largest_contributors:
        c0 = estimate.largest_contributors[0]
        top = {
            "source": c0.source,
            "kind": c0.kind,
            "estimatedTokens": c0.estimated_tokens,
            "bytes": c0.bytes,
            **({"path": c0.path} if c0.path else {}),
        }

    by_kind = _contributor_tokens_by_kind(estimate)
    by_source = _contributor_tokens_by_source(estimate)

    metrics: dict[str, Any] = {
        "event": _EVENT,
        "phase": phase,
        "client": client,
        "provider": provider,
        "primaryReason": reason_codes[0] if reason_codes else "UNKNOWN",
        "reasonCodes": list(reason_codes),
        "estimatedInputTokens": raw_tokens,
        "budgetTokens": budget,
        "warnAtTokens": warn_at,
        "overshootTokens": overshoot_tokens,
        "overshootPctOfBudget": overshoot_pct,
        "budgetUtilizationPct": budget_utilization_pct,
        "argvEstimatedTokens": by_source.get("argv.prompt"),
        "stdinEstimatedTokens": by_source.get("stdin"),
        "tokensByKind": by_kind or None,
        "topContributor": top,
        "stdinBytes": stdin_bytes,
        "stdinByteCap": stdin_byte_cap,
        "hadPipedStdin": bool(stdin_bytes),
        "repoFileCount": repo_file_count,
        "repoWideWithoutScope": repo_wide_without_scope,
        "overrideAllowed": (
            config.is_overridable(reason_codes) if config and reason_codes else None
        ),
    }
    return {k: v for k, v in metrics.items() if v is not None}


def build_stdin_byte_cap_metrics(
    *,
    client: str,
    byte_cap: int,
    bytes_observed: int,
    stdin_prefix_tokens: int | None = None,
) -> dict[str, Any]:
    """Metrics when piped stdin exceeds the wrap byte cap (before token policy)."""
    return {
        "event": _EVENT,
        "phase": "wrapper_stdin_byte_cap",
        "client": client,
        "primaryReason": "BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE",
        "reasonCodes": ["BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"],
        "stdinBytes": bytes_observed,
        "stdinByteCap": byte_cap,
        "stdinPrefixEstimatedTokens": stdin_prefix_tokens,
        "hadPipedStdin": True,
        "budgetUtilizationPct": _pct(bytes_observed, byte_cap),
    }


def format_rejection_stderr(metrics: dict[str, Any]) -> list[str]:
    """Human-readable stderr lines (content-free) for operators and agents."""
    lines = ["[context-guard] rejection metrics:"]
    phase = metrics.get("phase", "?")
    client = metrics.get("client", "?")
    lines.append(f"  phase={phase} client={client}")

    if metrics.get("primaryReason"):
        lines.append(f"  reason={metrics['primaryReason']}")
    if metrics.get("reasonCodes") and len(metrics["reasonCodes"]) > 1:
        lines.append(f"  all_reasons={','.join(metrics['reasonCodes'])}")

    if metrics.get("estimatedInputTokens") is not None:
        est = metrics["estimatedInputTokens"]
        budget = metrics.get("budgetTokens")
        if budget is not None:
            lines.append(f"  estimated_input_tokens={est:,} budget={budget:,}")
            if metrics.get("overshootTokens") is not None:
                lines.append(
                    f"  overshoot={metrics['overshootTokens']:,} tokens "
                    f"({metrics.get('overshootPctOfBudget', '?')}% over budget)"
                )
            elif metrics.get("budgetUtilizationPct") is not None:
                lines.append(f"  budget_utilization={metrics['budgetUtilizationPct']}%")
        else:
            lines.append(f"  estimated_input_tokens={est:,}")

    if (
        metrics.get("argvEstimatedTokens") is not None
        or metrics.get("stdinEstimatedTokens") is not None
    ):
        argv_t = metrics.get("argvEstimatedTokens", 0)
        stdin_t = metrics.get("stdinEstimatedTokens", 0)
        lines.append(f"  argv_tokens≈{argv_t:,} stdin_tokens≈{stdin_t:,}")

    if metrics.get("stdinBytes") is not None:
        cap = metrics.get("stdinByteCap")
        if cap is not None:
            lines.append(f"  stdin_bytes={metrics['stdinBytes']:,} cap={cap:,}")
        else:
            lines.append(f"  stdin_bytes={metrics['stdinBytes']:,}")

    top = metrics.get("topContributor")
    if isinstance(top, dict):
        loc = top.get("path") or top.get("source", "?")
        lines.append(
            f"  top_contributor={loc} "
            f"(~{top.get('estimatedTokens', '?')} tokens, kind={top.get('kind', '?')})"
        )

    if metrics.get("repoWideWithoutScope"):
        count = metrics.get("repoFileCount")
        lines.append(
            f"  repo_wide_without_scope=true repo_files≥{count if count is not None else '?'}"
        )

    if metrics.get("overrideAllowed") is False:
        lines.append("  override_allowed=false")

    return lines


def log_rejection(metrics: dict[str, Any]) -> None:
    """Emit a structured log line for grep / log-based alerts."""
    logger.warning(_EVENT, extra=metrics)
