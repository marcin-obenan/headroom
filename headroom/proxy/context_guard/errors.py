"""Agent-readable block error rendering (ai-rules#79).

Produces both the human-readable text and the machine-readable JSON shape from
the spec, so another coding agent can self-correct programmatically.
"""

from __future__ import annotations

from .models import ContextGuardConfig, RequestContextEstimate

_PRIMARY_FIX = [
    "rerun with explicit scope",
    "exclude generated files, lockfiles, fixtures, build outputs, and dependencies",
    "run discovery/repo-map mode first",
    "request only specific files",
    "split the task into smaller subtasks",
]


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def build_machine_error(
    estimate: RequestContextEstimate,
    reason_codes: list[str],
    config: ContextGuardConfig,
) -> dict[str, object]:
    """The JSON ``{"error": {...}}`` payload returned to the client on block."""
    primary = reason_codes[0] if reason_codes else "BLOCKED_CONTEXT_SPIKE_UNKNOWN_HUGE_PAYLOAD"
    return {
        "error": {
            "type": "context_budget_exceeded",
            "code": primary,
            "reason_codes": list(reason_codes),
            "client": estimate.client,
            "provider": estimate.provider,
            "estimated_input_tokens": estimate.raw_estimated_input_tokens,
            "budget_tokens": config.block_at_tokens,
            "top_contributors": [c.to_dict() for c in estimate.largest_contributors[:5]],
            "fix": list(_PRIMARY_FIX),
            "override": {
                "allowed": config.is_overridable(reason_codes),
                "requires_reason": config.override_requires_reason,
            },
        }
    }


def build_human_message(
    estimate: RequestContextEstimate,
    reason_codes: list[str],
    config: ContextGuardConfig,
) -> str:
    """The human-readable block message (also readable by another agent)."""
    primary = reason_codes[0] if reason_codes else "BLOCKED_CONTEXT_SPIKE_UNKNOWN_HUGE_PAYLOAD"
    lines = [
        "LLM request blocked by Headroom Context Guard.",
        "",
        "Provider/client:",
        f"{estimate.client} → {estimate.provider}",
        "",
        "Reason:",
        primary,
    ]
    if len(reason_codes) > 1:
        lines += ["", "Additional reasons:", *[f"- {r}" for r in reason_codes[1:]]]
    lines += [
        "",
        "Estimated raw input:",
        f"{_fmt_int(estimate.raw_estimated_input_tokens)} tokens",
        "",
        "Configured budget:",
        f"{_fmt_int(config.block_at_tokens)} tokens",
    ]
    if estimate.largest_contributors:
        lines += ["", "Top contributors:"]
        for i, c in enumerate(estimate.largest_contributors[:5], 1):
            loc = f"{c.source} {c.path}" if c.path else c.source
            lines.append(f"{i}. {loc}: ~{_fmt_int(c.estimated_tokens)} tokens")
    lines += [
        "",
        "How to fix:",
        "1. Rerun with an explicit scope.",
        "2. Do not include generated files, lockfiles, fixtures, build outputs, or dependency folders.",
        "3. Run discovery/repo-map mode first.",
        "4. Request only the specific files needed.",
        "5. Split the task into smaller subtasks.",
    ]
    overridable = config.is_overridable(reason_codes)
    lines += ["", "Override:"]
    if overridable:
        lines.append('Allowed only with --context-guard-override "reason"')
    else:
        lines.append("Not allowed for this reason code (non-overridable).")
    return "\n".join(lines)


def render_block(
    estimate: RequestContextEstimate,
    reason_codes: list[str],
    config: ContextGuardConfig,
) -> tuple[str, dict[str, object]]:
    """Return (human_message, machine_error)."""
    return (
        build_human_message(estimate, reason_codes, config),
        build_machine_error(estimate, reason_codes, config),
    )
