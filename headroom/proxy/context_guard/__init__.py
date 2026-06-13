"""Headroom Context Guard (ai-rules#79).

Answers "should this request have been created at all?" — independent of
compression ("can we make this smaller?"). Runs before compression and before
forwarding to the provider, and is also usable as a local wrapper preflight.

Public API:
    analyze_request(body, provider=, client=) -> RequestContextEstimate
    decide(estimate, config, ...)             -> ContextGuardDecision
    render_block(estimate, reasons, config)   -> (human_message, machine_error)
    resolve_config(client=, layers=, cli_overrides=) -> ContextGuardConfig
"""

from __future__ import annotations

from .config import (
    ContextGuardConfigError,
    load_config_file,
    load_raw_config,
    resolve_config,
)
from .errors import build_human_message, build_machine_error, render_block
from .estimator import analyze_request, estimate_tokens
from .integration import (
    OVERRIDE_HEADER,
    GuardOutcome,
    guard_request,
    override_from_headers,
    run_guard,
)
from .models import (
    ContextContributor,
    ContextGuardConfig,
    ContextGuardDecision,
    OverrideContext,
    RepoSignal,
    RequestContextEstimate,
)
from .policy import decide
from .preflight import (
    PreflightInput,
    PreflightOutcome,
    count_repo_files,
    detect_repo_wide_prompt,
    has_explicit_scope,
    preflight_request,
    run_preflight,
)
from .reason_codes import DEFAULT_NON_OVERRIDABLE, ContextSpikeReason
from .repo_profiles import DEFAULT_PROFILES, StackProfile, classify_path

__all__ = [
    "ContextContributor",
    "ContextGuardConfig",
    "ContextGuardConfigError",
    "ContextGuardDecision",
    "ContextSpikeReason",
    "DEFAULT_NON_OVERRIDABLE",
    "DEFAULT_PROFILES",
    "GuardOutcome",
    "OVERRIDE_HEADER",
    "OverrideContext",
    "PreflightInput",
    "PreflightOutcome",
    "RepoSignal",
    "RequestContextEstimate",
    "StackProfile",
    "analyze_request",
    "build_human_message",
    "build_machine_error",
    "classify_path",
    "count_repo_files",
    "decide",
    "detect_repo_wide_prompt",
    "estimate_tokens",
    "guard_request",
    "has_explicit_scope",
    "load_config_file",
    "load_raw_config",
    "override_from_headers",
    "preflight_request",
    "render_block",
    "resolve_config",
    "run_guard",
    "run_preflight",
]
