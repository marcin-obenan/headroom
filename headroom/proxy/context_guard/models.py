"""Data models for the Headroom Context Guard (ai-rules#79).

Mirrors the TypeScript types in the spec:
- RequestContextEstimate / ContextContributor
- ContextGuardDecision
- ContextGuardConfig (+ per-client overrides)
- ContextLedgerEntry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .reason_codes import DEFAULT_NON_OVERRIDABLE

Decision = Literal["allow", "warn", "compress", "block"]
ContributorKind = Literal[
    "system", "message", "tool_result", "file", "repo_map", "stdin", "unknown"
]
Phase = Literal["wrapper_preflight", "proxy_request", "proxy_response"]
GuardMode = Literal["off", "warn", "block"]


@dataclass(frozen=True)
class ContextContributor:
    """A single thing that contributed tokens to a request."""

    source: str
    kind: ContributorKind
    estimated_tokens: int
    bytes: int
    path: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "source": self.source,
            "kind": self.kind,
            "estimated_tokens": self.estimated_tokens,
            "bytes": self.bytes,
        }
        if self.path is not None:
            out["path"] = self.path
        if self.reason is not None:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class RepoSignal:
    """A repo-level signal detected during analysis (e.g. large-repo, repo-wide prompt)."""

    kind: str
    detail: str


@dataclass
class RequestContextEstimate:
    """Result of analysing one outbound LLM request."""

    provider: str
    client: str
    raw_estimated_input_tokens: int
    prompt_tokens: int = 0
    message_tokens: int = 0
    tool_result_tokens: int = 0
    system_tokens: int = 0
    compressed_estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    #: ALL contributors (used by the policy for size/artifact checks).
    contributors: list[ContextContributor] = field(default_factory=list)
    #: top-N by tokens (used for display + machine error). Subset of `contributors`.
    largest_contributors: list[ContextContributor] = field(default_factory=list)
    detected_repo_signals: list[RepoSignal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextGuardDecision:
    """The guard's verdict for one request."""

    decision: Decision
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: present only when decision == "block"
    user_message: str | None = None
    machine_readable_error: dict[str, object] | None = None


@dataclass(frozen=True)
class ContextGuardConfig:
    """Resolved (already-layered) context-guard configuration.

    Token thresholds are *raw input* token estimates unless noted.
    """

    enabled: bool = False
    mode: GuardMode = "warn"

    warn_at_tokens: int = 200_000
    block_at_tokens: int = 1_000_000

    max_single_message_tokens: int = 200_000
    max_tool_result_tokens: int = 200_000
    max_single_file_tokens: int = 150_000
    max_output_tokens: int = 300_000

    block_repo_wide_prompts: bool = True
    require_explicit_scope_for_large_repos: bool = True
    large_repo_file_threshold: int = 1000

    allow_compression_instead_of_block: bool = True
    block_even_if_compressible: bool = False

    override_enabled: bool = True
    override_requires_reason: bool = True
    non_overridable_reasons: tuple[str, ...] = DEFAULT_NON_OVERRIDABLE

    #: extra glob patterns (relative) that are always allowed even if a stack
    #: profile would otherwise flag them.
    allowlist: tuple[str, ...] = ()

    ledger_path: str = ".headroom/context-ledger.jsonl"

    def is_overridable(self, reason_codes: list[str]) -> bool:
        """Override is allowed only when NONE of the reasons are non-overridable."""
        if not self.override_enabled:
            return False
        non = set(self.non_overridable_reasons)
        return not any(rc in non for rc in reason_codes)


@dataclass(frozen=True)
class OverrideContext:
    """Operator override state for one invocation."""

    requested: bool = False
    reason: str | None = None
    #: when True (CI), overrides are refused regardless of config.
    ci_disabled: bool = False
