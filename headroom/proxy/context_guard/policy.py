"""Context-guard policy decision engine (ai-rules#79).

Pure function: (estimate, config, situational flags, override) -> ContextGuardDecision.
No I/O, no logging — callers handle the ledger and the error rendering.
"""

from __future__ import annotations

from .models import (
    ContextGuardConfig,
    ContextGuardDecision,
    OverrideContext,
    RequestContextEstimate,
)
from .reason_codes import ARTIFACT_REASON_BY_CLASS, ContextSpikeReason
from .repo_profiles import DEFAULT_PROFILES, StackProfile, classify_path

# Reasons that mean "too big" (compression can plausibly help) vs "shouldn't exist".
_SIZE_REASONS = {
    ContextSpikeReason.INPUT_TOO_LARGE.value,
    ContextSpikeReason.SINGLE_MESSAGE_TOO_LARGE.value,
    ContextSpikeReason.TOOL_OUTPUT_TOO_LARGE.value,
    ContextSpikeReason.SINGLE_FILE_TOO_LARGE.value,
    ContextSpikeReason.CHAT_HISTORY.value,
    ContextSpikeReason.UNKNOWN_HUGE_PAYLOAD.value,
}


def _collect_reasons(
    estimate: RequestContextEstimate,
    config: ContextGuardConfig,
    *,
    repo_wide_without_scope: bool,
    profiles: tuple[StackProfile, ...],
) -> tuple[list[str], list[str]]:
    """Return (reason_codes, warnings) for the request. Order is stable/deterministic."""
    reasons: list[str] = []
    warnings: list[str] = []

    def add(code: ContextSpikeReason) -> None:
        if code.value not in reasons:
            reasons.append(code.value)

    if estimate.raw_estimated_input_tokens > config.block_at_tokens:
        add(ContextSpikeReason.INPUT_TOO_LARGE)
    elif estimate.raw_estimated_input_tokens > config.warn_at_tokens:
        warnings.append(
            f"raw input ~{estimate.raw_estimated_input_tokens} tokens exceeds "
            f"warn threshold {config.warn_at_tokens}"
        )

    if (
        estimate.estimated_output_tokens is not None
        and estimate.estimated_output_tokens > config.max_output_tokens
    ):
        warnings.append(
            f"requested max output {estimate.estimated_output_tokens} exceeds "
            f"{config.max_output_tokens}"
        )

    # Inspect ALL contributors (not just the top-N shown in the error) so an
    # oversized single file or a generated artifact ranked outside the top-N is
    # still caught.
    for c in estimate.contributors or estimate.largest_contributors:
        if c.kind == "message" and c.estimated_tokens > config.max_single_message_tokens:
            add(ContextSpikeReason.SINGLE_MESSAGE_TOO_LARGE)
        if c.kind == "tool_result" and c.estimated_tokens > config.max_tool_result_tokens:
            add(ContextSpikeReason.TOOL_OUTPUT_TOO_LARGE)
        if c.path and c.estimated_tokens > config.max_single_file_tokens:
            add(ContextSpikeReason.SINGLE_FILE_TOO_LARGE)
        if c.reason == "binary":
            add(ContextSpikeReason.BINARY_CONTEXT)
        if c.path:
            match = classify_path(c.path, profiles=profiles, allowlist=config.allowlist)
            if match is not None:
                code = ARTIFACT_REASON_BY_CLASS.get(match.artifact_class)
                if code is not None:
                    add(code)

    if repo_wide_without_scope and config.block_repo_wide_prompts:
        add(ContextSpikeReason.REPO_WIDE_WITHOUT_SCOPE)

    return reasons, warnings


def decide(
    estimate: RequestContextEstimate,
    config: ContextGuardConfig,
    *,
    repo_wide_without_scope: bool = False,
    override: OverrideContext | None = None,
    profiles: tuple[StackProfile, ...] = DEFAULT_PROFILES,
) -> ContextGuardDecision:
    """Decide allow / warn / compress / block for a request.

    The block ``user_message``/``machine_readable_error`` are filled by
    :mod:`headroom.proxy.context_guard.errors` from the returned reasons; this
    function only decides and lists reasons + warnings.
    """
    override = override or OverrideContext()

    if not config.enabled or config.mode == "off":
        return ContextGuardDecision(decision="allow")

    reasons, warnings = _collect_reasons(
        estimate,
        config,
        repo_wide_without_scope=repo_wide_without_scope,
        profiles=profiles,
    )

    if not reasons:
        # Only warnings (e.g. above warn threshold) → warn; else allow.
        return ContextGuardDecision(
            decision="warn" if warnings else "allow",
            reason_codes=[],
            warnings=warnings,
        )

    # We have block-worthy reasons.
    if config.mode == "warn":
        return ContextGuardDecision(decision="warn", reason_codes=reasons, warnings=warnings)

    # mode == "block":
    only_size = all(r in _SIZE_REASONS for r in reasons)
    compressible = (
        only_size
        and config.allow_compression_instead_of_block
        and not config.block_even_if_compressible
    )
    if compressible:
        warnings.append(
            "context exceeds budget but is compressible; compressing instead of blocking"
        )
        return ContextGuardDecision(decision="compress", reason_codes=reasons, warnings=warnings)

    # Honour a valid operator override (downgrade to warn) unless CI-disabled,
    # a non-overridable reason is present, or a required reason is missing.
    if override.requested and not override.ci_disabled and config.is_overridable(reasons):
        if not (config.override_requires_reason and not (override.reason or "").strip()):
            return ContextGuardDecision(
                decision="warn",
                reason_codes=reasons,
                warnings=[*warnings, f"context-guard override applied: {override.reason!r}"],
            )

    return ContextGuardDecision(decision="block", reason_codes=reasons, warnings=warnings)
