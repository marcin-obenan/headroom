"""Local wrapper preflight for the Context Guard (ai-rules#79).

Runs in the ``headroom wrap <cli>`` process BEFORE the child CLI launches, so it
can stop an obviously-dangerous invocation (huge stdin, a repo-wide prompt in a
large repo) early — and it is the ONLY enforcement point for the Cursor Agent
CLI, whose outbound traffic cannot be intercepted (cert-pinned).

It does NOT see the actual outbound LLM request; it inspects argv + stdin + the
repo. It reuses the same policy engine as the proxy guard so decisions and reason
codes are consistent across both layers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .errors import render_block
from .estimator import estimate_tokens
from .ledger import event_from_decision, write_event
from .models import (
    ContextContributor,
    ContextGuardConfig,
    OverrideContext,
    RepoSignal,
    RequestContextEstimate,
)
from .policy import decide

# client → provider it ultimately talks to (best effort; for labelling only).
_CLIENT_PROVIDER = {
    "claude": "anthropic",
    "codex": "openai",
    "cursor": "cursor",
    "agent": "cursor",
    "cursor-agent": "cursor",
    "aider": "openai",
    "copilot": "openai",
}

# Phrases that imply "look at everything" with no scope.
_REPO_WIDE_PHRASES = (
    "entire repo",
    "whole repo",
    "entire codebase",
    "whole codebase",
    "all files",
    "every file",
    "all the files",
    "read the repo",
    "scan the repo",
    "scan the codebase",
    "across the codebase",
    "the entire project",
    "the whole project",
    "index the repo",
    "look at everything",
    "all source files",
)

# Directories that never count toward "repo size" (artifacts/deps/vcs).
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "target",
        "__pycache__",
        ".gradle",
        ".next",
        "coverage",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "vendor",
    }
)

_SCOPE_EXTS = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".kt",
    ".swift",
    ".scala",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sql",
)


@dataclass
class PreflightInput:
    client: str
    argv: list[str] = field(default_factory=list)
    stdin_text: str | None = None
    cwd: str | None = None
    repo_file_count: int | None = None  # None → compute lazily


@dataclass
class PreflightOutcome:
    decision: str = "allow"
    blocked: bool = False
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    human_message: str | None = None
    machine_error: dict | None = None
    metrics: dict | None = None


def detect_repo_wide_prompt(text: str) -> bool:
    """True if ``text`` contains a repo-wide phrase."""
    low = (text or "").lower()
    return any(phrase in low for phrase in _REPO_WIDE_PHRASES)


def has_explicit_scope(argv: list[str], prompt_text: str) -> bool:
    """Heuristic: does the invocation name specific files/dirs (a scope)?"""
    blob = " ".join(argv) + " " + (prompt_text or "")
    if "@" in blob:  # @path mentions (cursor/aider) count as scope
        return True
    for tok in blob.split():
        t = tok.strip("'\"`,.()[]{}")
        if "/" in t and not t.startswith("-"):
            return True
        if t.lower().endswith(_SCOPE_EXTS):
            return True
    return False


def count_repo_files(cwd: str | os.PathLike[str], *, cap: int = 5000) -> int:
    """Count source files under ``cwd`` (skipping artifact/dep dirs), capped.

    Returns ``cap`` as soon as the cap is reached (we only need "is it large").
    Never raises — unreadable dirs are skipped.
    """
    root = Path(cwd)
    n = 0
    try:
        for _dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            n += len(filenames)
            if n >= cap:
                return cap
    except OSError:
        return n
    return n


def _prompt_text(argv: list[str]) -> str:
    """Recover the human prompt portion from argv: every non-flag token.

    We deliberately do NOT try to skip flag *values* — different CLIs disagree
    on which flags take a value (e.g. cursor-agent's ``-p`` is the boolean
    ``--print``, not a value flag), and dropping the wrong token would drop the
    real prompt and defeat repo-wide detection. Including a stray flag value
    (a model name, ``text``) is harmless: it won't match a repo-wide phrase or
    look like an explicit file scope.
    """
    return " ".join(tok for tok in argv if not tok.startswith("-"))


def build_preflight_estimate(inp: PreflightInput) -> RequestContextEstimate:
    """Build a synthetic estimate from argv prompt + stdin (no provider request yet)."""
    provider = _CLIENT_PROVIDER.get(inp.client, "unknown")
    est = RequestContextEstimate(provider=provider, client=inp.client, raw_estimated_input_tokens=0)
    contributors: list[ContextContributor] = []

    prompt = _prompt_text(inp.argv)
    if prompt:
        pt = estimate_tokens(prompt)
        est.message_tokens += pt
        contributors.append(
            ContextContributor(
                source="argv.prompt", kind="message", estimated_tokens=pt, bytes=len(prompt)
            )
        )

    if inp.stdin_text:
        st = estimate_tokens(inp.stdin_text)
        est.message_tokens += st
        contributors.append(
            ContextContributor(
                source="stdin", kind="stdin", estimated_tokens=st, bytes=len(inp.stdin_text)
            )
        )

    est.prompt_tokens = est.message_tokens
    est.raw_estimated_input_tokens = est.message_tokens
    contributors.sort(key=lambda c: c.estimated_tokens, reverse=True)
    est.contributors = contributors
    est.largest_contributors = contributors[:10]
    return est


def run_preflight(
    inp: PreflightInput,
    config: ContextGuardConfig | None,
    *,
    run_id: str,
    override: OverrideContext | None = None,
) -> PreflightOutcome:
    """Run the preflight policy. Never raises (fail open)."""
    if config is None or not config.enabled or config.mode == "off":
        return PreflightOutcome()

    try:
        estimate = build_preflight_estimate(inp)

        prompt_blob = _prompt_text(inp.argv) + " " + (inp.stdin_text or "")
        repo_wide = detect_repo_wide_prompt(prompt_blob)
        scoped = has_explicit_scope(inp.argv, prompt_blob)

        large_repo = False
        repo_file_count: int | None = None
        if repo_wide and not scoped and config.require_explicit_scope_for_large_repos:
            count = inp.repo_file_count
            if count is None and inp.cwd:
                count = count_repo_files(inp.cwd, cap=config.large_repo_file_threshold + 1)
            repo_file_count = count
            large_repo = (count or 0) >= config.large_repo_file_threshold

        if repo_wide:
            est_signal = "repo-wide prompt detected"
            estimate.detected_repo_signals.append(RepoSignal("repo_wide_prompt", est_signal))

        repo_wide_without_scope = bool(large_repo and repo_wide and not scoped)

        decision = decide(
            estimate, config, repo_wide_without_scope=repo_wide_without_scope, override=override
        )
    except Exception:
        return PreflightOutcome(warnings=["context-guard preflight failed; allowing launch"])

    blocked = decision.decision == "block"
    human = machine = None
    metrics = None
    if blocked:
        from .rejection_metrics import build_block_metrics, log_rejection

        human, machine = render_block(estimate, decision.reason_codes, config)
        if machine is not None and human is not None:
            machine = {**machine, "message": human}
        metrics = build_block_metrics(
            phase="wrapper_preflight",
            client=estimate.client,
            provider=estimate.provider,
            reason_codes=list(decision.reason_codes),
            estimate=estimate,
            config=config,
            stdin_bytes=len(inp.stdin_text.encode("utf-8")) if inp.stdin_text else None,
            repo_file_count=repo_file_count,
            repo_wide_without_scope=repo_wide_without_scope,
        )
        log_rejection(metrics)

    try:
        event = event_from_decision(
            run_id=run_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase="wrapper_preflight",
            decision=decision.decision,
            reason_codes=decision.reason_codes,
            estimate=estimate,
            config=config,
            parent_run_id=None,
            override_used=bool(
                override
                and override.requested
                and decision.decision != "block"
                and decision.reason_codes
            ),
            override_reason=(override.reason if override else None),
            rejection_metrics=metrics,
        )
        write_event(event, config.ledger_path)
    except Exception:
        pass

    return PreflightOutcome(
        decision=decision.decision,
        blocked=blocked,
        reason_codes=list(decision.reason_codes),
        warnings=list(decision.warnings),
        human_message=human,
        machine_error=machine,
        metrics=metrics,
    )


def preflight_request(
    *,
    raw_config: dict | None,
    client: str,
    argv: list[str],
    stdin_text: str | None = None,
    cwd: str | None = None,
    run_id: str,
    override: OverrideContext | None = None,
) -> PreflightOutcome:
    """Resolve per-client config from the raw dict and run the preflight.

    Resolution failures fail OPEN (allow launch) so a config bug can't stop the
    developer's CLI.
    """
    if not raw_config:
        return PreflightOutcome()
    from .config import resolve_config

    try:
        config = resolve_config(client=client, layers=[raw_config])
    except Exception:
        return PreflightOutcome(warnings=["context-guard config resolve failed; allowing launch"])
    return run_preflight(
        PreflightInput(client=client, argv=list(argv), stdin_text=stdin_text, cwd=cwd),
        config,
        run_id=run_id,
        override=override,
    )
