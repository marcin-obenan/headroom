"""Glue between the proxy request path and the context-guard engine (ai-rules#79).

A single ``run_guard`` entry point the handlers call right before compression.
It estimates, decides, writes the ledger (fail-open), and — on block — produces
the agent-readable error. The guard NEVER raises into the request path: any
internal failure degrades to "allow" so a guard bug can't take the proxy down.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import resolve_config
from .errors import render_block
from .estimator import analyze_request
from .ledger import event_from_decision, write_event
from .models import ContextGuardConfig, OverrideContext
from .policy import decide


@dataclass
class GuardOutcome:
    decision: str = "allow"
    blocked: bool = False
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    human_message: str | None = None
    machine_error: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: header a client sets to override an *overridable* block, value = audit reason.
OVERRIDE_HEADER = "x-headroom-context-guard-override"


def override_from_headers(headers: Any) -> OverrideContext:
    """Build an OverrideContext from request headers (proxy-side override).

    The reason comes from ``x-headroom-context-guard-override``; CI is detected
    from the ``CI`` env var so automation cannot override (per spec).
    """
    reason = None
    try:
        reason = headers.get(OVERRIDE_HEADER)
    except (AttributeError, TypeError):
        reason = None
    return OverrideContext(
        requested=bool(reason),
        reason=reason or None,
        ci_disabled=bool(os.environ.get("CI")),
    )


def run_guard(
    *,
    body: dict[str, Any],
    provider: str,
    client: str,
    config: ContextGuardConfig | None,
    run_id: str,
    phase: str = "proxy_request",
    override: OverrideContext | None = None,
    repo_wide_without_scope: bool = False,
    parent_run_id: str | None = None,
) -> GuardOutcome:
    """Run the guard for one request. Returns a GuardOutcome (never raises)."""
    if config is None or not config.enabled or config.mode == "off":
        return GuardOutcome()

    try:
        estimate = analyze_request(body, provider=provider, client=client)
    except Exception:
        # Estimator failure must not block the request — fail open.
        return GuardOutcome(warnings=["context-guard estimator failed; request allowed"])

    decision = decide(
        estimate,
        config,
        repo_wide_without_scope=repo_wide_without_scope,
        override=override,
    )

    blocked = decision.decision == "block"
    human = machine = None
    metrics = None
    if blocked:
        from .rejection_metrics import build_block_metrics, log_rejection

        human, machine = render_block(estimate, decision.reason_codes, config)
        if machine is not None and human is not None:
            machine = {**machine, "message": human}
        metrics = build_block_metrics(
            phase=phase,
            client=estimate.client,
            provider=estimate.provider,
            reason_codes=list(decision.reason_codes),
            estimate=estimate,
            config=config,
        )
        log_rejection(metrics)

    override_used = bool(
        override and override.requested and decision.decision != "block" and decision.reason_codes
    )
    try:
        event = event_from_decision(
            run_id=run_id,
            timestamp=_utc_now_iso(),
            phase=phase,
            decision=decision.decision,
            reason_codes=decision.reason_codes,
            estimate=estimate,
            config=config,
            parent_run_id=parent_run_id,
            override_used=override_used,
            override_reason=(override.reason if override else None),
            rejection_metrics=metrics,
        )
        write_event(event, config.ledger_path)
    except Exception:
        pass  # ledger never blocks the request

    return GuardOutcome(
        decision=decision.decision,
        blocked=blocked,
        reason_codes=list(decision.reason_codes),
        warnings=list(decision.warnings),
        human_message=human,
        machine_error=machine,
        metrics=metrics,
    )


def guard_request(
    *,
    raw_config: dict[str, Any] | None,
    body: dict[str, Any],
    provider: str,
    client: str,
    run_id: str,
    phase: str = "proxy_request",
    override: OverrideContext | None = None,
    repo_wide_without_scope: bool = False,
) -> GuardOutcome:
    """Resolve the guard config for ``client`` (per-client overrides) and run it.

    ``raw_config`` is the merged contextGuard config dict stored on ProxyConfig
    (or None to disable). Resolution failures fail OPEN (allow) — config is
    validated at startup, so a per-request resolve error must not crash traffic.
    """
    if not raw_config:
        return GuardOutcome()
    try:
        config = resolve_config(client=client, layers=[raw_config])
    except Exception:
        return GuardOutcome(warnings=["context-guard config resolve failed; request allowed"])
    return run_guard(
        body=body,
        provider=provider,
        client=client,
        config=config,
        run_id=run_id,
        phase=phase,
        override=override,
        repo_wide_without_scope=repo_wide_without_scope,
    )
