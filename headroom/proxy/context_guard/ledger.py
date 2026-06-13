"""JSONL context ledger (ai-rules#79).

One line per attempted request. Records the decision, reason codes, token
estimates, and *safe* top-contributor metadata (source/kind/path/tokens) — never
prompts, message bodies, or secrets. Writes fail OPEN: a ledger error degrades to
a logged warning and never blocks the request (that would turn observability into
an outage).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ContextGuardConfig, RequestContextEstimate

logger = logging.getLogger("headroom.proxy.context_guard")


@dataclass
class LedgerEvent:
    """A single ledger row. Mirrors ContextLedgerEntry in the spec."""

    run_id: str
    timestamp: str
    client: str
    provider: str
    phase: str  # wrapper_preflight | proxy_request | proxy_response
    decision: str  # allowed | warned | compressed | blocked
    reason_codes: list[str] = field(default_factory=list)
    parent_run_id: str | None = None
    mode: str = "warn"
    warn_at_tokens: int = 0
    block_at_tokens: int = 0
    raw_estimated_input_tokens: int | None = None
    compressed_estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    top_contributors: list[dict[str, Any]] = field(default_factory=list)
    override_used: bool = False
    override_reason: str | None = None
    rejection_metrics: dict[str, Any] | None = None

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {
            "runId": self.run_id,
            "timestamp": self.timestamp,
            "client": self.client,
            "provider": self.provider,
            "phase": self.phase,
            "decision": self.decision,
            "reasonCodes": self.reason_codes,
            "policy": {
                "mode": self.mode,
                "warnAtTokens": self.warn_at_tokens,
                "blockAtTokens": self.block_at_tokens,
            },
            "estimate": {
                "rawEstimatedInputTokens": self.raw_estimated_input_tokens,
                "compressedEstimatedInputTokens": self.compressed_estimated_input_tokens,
                "estimatedOutputTokens": self.estimated_output_tokens,
            },
            "topContributors": self.top_contributors,
        }
        if self.parent_run_id:
            payload["parentRunId"] = self.parent_run_id
        if self.override_used:
            payload["override"] = {"used": True, "reason": self.override_reason}
        if self.rejection_metrics:
            payload["rejectionMetrics"] = self.rejection_metrics
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


#: decision -> ledger verb
_DECISION_VERB = {
    "allow": "allowed",
    "warn": "warned",
    "compress": "compressed",
    "block": "blocked",
}


def safe_contributors(estimate: RequestContextEstimate, limit: int = 5) -> list[dict[str, Any]]:
    """Top contributors WITHOUT any content — only source/kind/path/tokens/bytes."""
    return [c.to_dict() for c in estimate.largest_contributors[:limit]]


def write_event(event: LedgerEvent, ledger_path: str | os.PathLike[str]) -> bool:
    """Append one JSONL line. Returns True on success, False on (logged) failure.

    Never raises — ledger failures fail open per the spec.
    """
    try:
        p = Path(ledger_path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(event.to_json_line() + "\n")
        return True
    except OSError as exc:
        logger.warning("context-guard ledger write failed (continuing): %s", exc)
        return False


def event_from_decision(
    *,
    run_id: str,
    timestamp: str,
    phase: str,
    decision: str,
    reason_codes: list[str],
    estimate: RequestContextEstimate,
    config: ContextGuardConfig,
    parent_run_id: str | None = None,
    override_used: bool = False,
    override_reason: str | None = None,
    rejection_metrics: dict[str, Any] | None = None,
) -> LedgerEvent:
    """Build a LedgerEvent from a decision + estimate (content-free)."""
    return LedgerEvent(
        run_id=run_id,
        timestamp=timestamp,
        client=estimate.client,
        provider=estimate.provider,
        phase=phase,
        decision=_DECISION_VERB.get(decision, decision),
        reason_codes=list(reason_codes),
        parent_run_id=parent_run_id,
        mode=config.mode,
        warn_at_tokens=config.warn_at_tokens,
        block_at_tokens=config.block_at_tokens,
        raw_estimated_input_tokens=estimate.raw_estimated_input_tokens,
        compressed_estimated_input_tokens=estimate.compressed_estimated_input_tokens,
        estimated_output_tokens=estimate.estimated_output_tokens,
        top_contributors=safe_contributors(estimate),
        override_used=override_used,
        override_reason=override_reason,
        rejection_metrics=rejection_metrics,
    )
