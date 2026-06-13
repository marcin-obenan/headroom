"""Unit tests: policy allow/warn/compress/block matrix + override (ai-rules#79)."""

from __future__ import annotations

from headroom.proxy.context_guard import (
    ContextContributor,
    ContextGuardConfig,
    ContextSpikeReason,
    OverrideContext,
    RequestContextEstimate,
    decide,
)


def _est(raw: int, contributors=None) -> RequestContextEstimate:
    return RequestContextEstimate(
        provider="anthropic",
        client="claude",
        raw_estimated_input_tokens=raw,
        message_tokens=raw,
        largest_contributors=contributors or [],
    )


def _cfg(**kw) -> ContextGuardConfig:
    base = {
        "enabled": True,
        "mode": "block",
        "warn_at_tokens": 200_000,
        "block_at_tokens": 1_000_000,
    }
    base.update(kw)
    return ContextGuardConfig(**base)


def test_disabled_always_allows() -> None:
    d = decide(_est(5_000_000), _cfg(enabled=False))
    assert d.decision == "allow"


def test_mode_off_always_allows() -> None:
    assert decide(_est(5_000_000), _cfg(mode="off")).decision == "allow"


def test_small_request_allowed() -> None:
    assert decide(_est(1_000), _cfg()).decision == "allow"


def test_above_warn_below_block_warns() -> None:
    d = decide(_est(300_000), _cfg())
    assert d.decision == "warn"
    assert d.warnings


def test_above_block_blocks_in_block_mode_when_not_compressible() -> None:
    d = decide(_est(2_000_000), _cfg(allow_compression_instead_of_block=False))
    assert d.decision == "block"
    assert ContextSpikeReason.INPUT_TOO_LARGE.value in d.reason_codes


def test_above_block_compresses_when_size_only_and_allowed() -> None:
    d = decide(_est(2_000_000), _cfg(allow_compression_instead_of_block=True))
    assert d.decision == "compress"


def test_block_even_if_compressible_forces_block() -> None:
    d = decide(
        _est(2_000_000),
        _cfg(allow_compression_instead_of_block=True, block_even_if_compressible=True),
    )
    assert d.decision == "block"


def test_warn_mode_never_blocks() -> None:
    d = decide(_est(5_000_000), _cfg(mode="warn", allow_compression_instead_of_block=False))
    assert d.decision == "warn"
    assert ContextSpikeReason.INPUT_TOO_LARGE.value in d.reason_codes


def test_generated_file_artifact_blocks_not_compresses() -> None:
    contribs = [
        ContextContributor(
            source="tool_result.read_file",
            kind="tool_result",
            estimated_tokens=10_000,
            bytes=40_000,
            path="src/api.generated.ts",
        )
    ]
    d = decide(_est(10_000, contribs), _cfg())  # below block threshold, but artifact present
    assert d.decision == "block"
    assert ContextSpikeReason.GENERATED_FILES.value in d.reason_codes


def test_repo_wide_without_scope_blocks() -> None:
    d = decide(_est(10_000), _cfg(), repo_wide_without_scope=True)
    assert d.decision == "block"
    assert ContextSpikeReason.REPO_WIDE_WITHOUT_SCOPE.value in d.reason_codes


def test_binary_context_is_non_overridable_block() -> None:
    contribs = [
        ContextContributor(
            source="messages[0].content",
            kind="message",
            estimated_tokens=10_000,
            bytes=40_000,
            reason="binary",
        )
    ]
    d = decide(
        _est(10_000, contribs), _cfg(), override=OverrideContext(requested=True, reason="I need it")
    )
    # BINARY_CONTEXT is non-overridable → stays blocked despite override
    assert d.decision == "block"
    assert ContextSpikeReason.BINARY_CONTEXT.value in d.reason_codes


def test_valid_override_downgrades_overridable_block_to_warn() -> None:
    d = decide(
        _est(2_000_000),
        _cfg(allow_compression_instead_of_block=False),
        override=OverrideContext(requested=True, reason="intentional big context"),
    )
    assert d.decision == "warn"
    assert any("override applied" in w for w in d.warnings)


def test_override_without_reason_when_required_stays_blocked() -> None:
    d = decide(
        _est(2_000_000),
        _cfg(allow_compression_instead_of_block=False),
        override=OverrideContext(requested=True, reason=""),
    )
    assert d.decision == "block"


def test_ci_disabled_override_stays_blocked() -> None:
    d = decide(
        _est(2_000_000),
        _cfg(allow_compression_instead_of_block=False),
        override=OverrideContext(requested=True, reason="x", ci_disabled=True),
    )
    assert d.decision == "block"


def test_single_message_too_large() -> None:
    contribs = [
        ContextContributor(
            source="messages[0].content", kind="message", estimated_tokens=250_000, bytes=1_000_000
        )
    ]
    d = decide(_est(250_000, contribs), _cfg(max_single_message_tokens=200_000))
    assert ContextSpikeReason.SINGLE_MESSAGE_TOO_LARGE.value in d.reason_codes


def test_policy_inspects_all_contributors_not_just_top_n() -> None:
    # 11 small contributors + one oversized generated file ranked LAST.
    small = [
        ContextContributor(source=f"m{i}", kind="message", estimated_tokens=10, bytes=40)
        for i in range(11)
    ]
    huge_artifact = ContextContributor(
        source="tool_result.read_file",
        kind="tool_result",
        estimated_tokens=5,
        bytes=20,
        path="dist/main.js",  # build artifact, outside the top-10 by tokens
    )
    est = RequestContextEstimate(
        provider="anthropic",
        client="claude",
        raw_estimated_input_tokens=115,
        contributors=[*small, huge_artifact],
        largest_contributors=small[:10],  # artifact NOT in the display top-10
    )
    d = decide(est, _cfg())
    # must still catch the artifact even though it's not in largest_contributors
    assert ContextSpikeReason.BUILD_ARTIFACTS.value in d.reason_codes
