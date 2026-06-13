"""Stable reason codes for the Headroom Context Guard (ai-rules#79).

Agents react to these programmatically, so the string values are a contract and
MUST NOT change once shipped. New reasons are added, never renamed.
"""

from __future__ import annotations

from enum import Enum


class ContextSpikeReason(str, Enum):
    """Why a request tripped the context guard.

    ``str`` mixin so the value serializes as the bare code string in JSON and
    compares equal to the literal (``reason == "BLOCKED_CONTEXT_SPIKE_..."``).
    """

    INPUT_TOO_LARGE = "BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE"
    SINGLE_MESSAGE_TOO_LARGE = "BLOCKED_CONTEXT_SPIKE_SINGLE_MESSAGE_TOO_LARGE"
    TOOL_OUTPUT_TOO_LARGE = "BLOCKED_CONTEXT_SPIKE_TOOL_OUTPUT_TOO_LARGE"
    SINGLE_FILE_TOO_LARGE = "BLOCKED_CONTEXT_SPIKE_SINGLE_FILE_TOO_LARGE"
    REPO_WIDE_WITHOUT_SCOPE = "BLOCKED_CONTEXT_SPIKE_REPO_WIDE_WITHOUT_SCOPE"
    GENERATED_FILES = "BLOCKED_CONTEXT_SPIKE_GENERATED_FILES"
    LOCKFILES = "BLOCKED_CONTEXT_SPIKE_LOCKFILES"
    BUILD_ARTIFACTS = "BLOCKED_CONTEXT_SPIKE_BUILD_ARTIFACTS"
    DEPENDENCY_ARTIFACTS = "BLOCKED_CONTEXT_SPIKE_DEPENDENCY_ARTIFACTS"
    FIXTURES = "BLOCKED_CONTEXT_SPIKE_FIXTURES"
    CHAT_HISTORY = "BLOCKED_CONTEXT_SPIKE_CHAT_HISTORY"
    BINARY_CONTEXT = "BLOCKED_CONTEXT_SPIKE_BINARY_CONTEXT"
    UNKNOWN_HUGE_PAYLOAD = "BLOCKED_CONTEXT_SPIKE_UNKNOWN_HUGE_PAYLOAD"
    MALFORMED_REQUEST = "BLOCKED_CONTEXT_SPIKE_MALFORMED_REQUEST"
    FORBIDDEN_FILE = "BLOCKED_CONTEXT_SPIKE_FORBIDDEN_FILE"


#: Reason codes that an operator override can NEVER bypass. These map to
#: correctness/safety problems (not merely "too big"), so a `--context-guard-override`
#: must not force them through. The default set is configurable but always
#: includes these.
DEFAULT_NON_OVERRIDABLE: tuple[str, ...] = (
    ContextSpikeReason.FORBIDDEN_FILE.value,
    ContextSpikeReason.BINARY_CONTEXT.value,
    ContextSpikeReason.MALFORMED_REQUEST.value,
)


#: Reason code → which artifact "class" it represents, for stack-profile matches.
ARTIFACT_REASON_BY_CLASS: dict[str, ContextSpikeReason] = {
    "generated": ContextSpikeReason.GENERATED_FILES,
    "lockfile": ContextSpikeReason.LOCKFILES,
    "build": ContextSpikeReason.BUILD_ARTIFACTS,
    "dependency": ContextSpikeReason.DEPENDENCY_ARTIFACTS,
    "fixture": ContextSpikeReason.FIXTURES,
}
