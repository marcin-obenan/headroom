"""Unit tests: config layering + machine-readable error shape (ai-rules#79)."""

from __future__ import annotations

import json

import pytest

from headroom.proxy.context_guard import (
    ContextContributor,
    ContextGuardConfig,
    ContextGuardConfigError,
    ContextSpikeReason,
    RequestContextEstimate,
    build_machine_error,
    load_config_file,
    render_block,
    resolve_config,
)
from headroom.proxy.context_guard.config import _extract_guard_block

# ---- config layering ----


def test_defaults_when_no_layers() -> None:
    cfg = resolve_config(client="claude")
    assert cfg.enabled is False
    assert cfg.mode == "warn"
    assert cfg.block_at_tokens == 1_000_000


def test_layers_override_lowest_to_highest() -> None:
    user = {"enabled": True, "mode": "warn", "blockAtTokens": 500_000}
    repo = {"mode": "block"}
    cfg = resolve_config(client="claude", layers=[user, repo])
    assert cfg.enabled is True
    assert cfg.mode == "block"  # repo wins over user
    assert cfg.block_at_tokens == 500_000  # inherited from user


def test_cli_overrides_win_over_everything() -> None:
    user = {"enabled": True, "mode": "block"}
    cfg = resolve_config(client="claude", layers=[user], cli_overrides={"mode": "warn"})
    assert cfg.mode == "warn"


def test_per_client_override_applies() -> None:
    base = {
        "enabled": True,
        "mode": "block",
        "blockAtTokens": 1_000_000,
        "clients": {"cursor": {"blockAtTokens": 300_000}},
    }
    assert resolve_config(client="cursor", layers=[base]).block_at_tokens == 300_000
    assert resolve_config(client="claude", layers=[base]).block_at_tokens == 1_000_000


def test_cli_flag_beats_per_client_override() -> None:
    base = {"enabled": True, "clients": {"cursor": {"blockAtTokens": 300_000}}}
    cfg = resolve_config(client="cursor", layers=[base], cli_overrides={"blockAtTokens": 50_000})
    assert cfg.block_at_tokens == 50_000


def test_invalid_mode_raises() -> None:
    with pytest.raises(ContextGuardConfigError):
        resolve_config(client="claude", layers=[{"mode": "nuke"}])


def test_non_overridable_reasons_union_with_defaults() -> None:
    base = {"override": {"nonOverridableReasons": ["BLOCKED_CONTEXT_SPIKE_LOCKFILES"]}}
    cfg = resolve_config(client="claude", layers=[base])
    assert ContextSpikeReason.FORBIDDEN_FILE.value in cfg.non_overridable_reasons
    assert "BLOCKED_CONTEXT_SPIKE_LOCKFILES" in cfg.non_overridable_reasons


def test_load_config_file_invalid_json_fails_closed(tmp_path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ContextGuardConfigError):
        load_config_file(p)


def test_load_config_file_missing_returns_empty(tmp_path) -> None:
    assert load_config_file(tmp_path / "nope.json") == {}


def test_load_config_file_extracts_nested_block(tmp_path) -> None:
    p = tmp_path / "h.json"
    p.write_text(json.dumps({"headroom": {"contextGuard": {"enabled": True}}}), encoding="utf-8")
    assert load_config_file(p) == {"enabled": True}


def test_extract_guard_block_handles_all_shapes() -> None:
    assert _extract_guard_block({"headroom": {"contextGuard": {"a": 1}}}) == {"a": 1}
    assert _extract_guard_block({"contextGuard": {"a": 1}}) == {"a": 1}
    assert _extract_guard_block({"a": 1}) == {"a": 1}


# ---- machine-readable error shape ----


def _est() -> RequestContextEstimate:
    return RequestContextEstimate(
        provider="anthropic",
        client="cursor-agent",
        raw_estimated_input_tokens=1_240_000,
        largest_contributors=[
            ContextContributor("messages[14].content", "message", 640_000, 2_560_000),
            ContextContributor(
                "tool_result.read_file",
                "tool_result",
                310_000,
                1_240_000,
                path="packages/api/generated/openapi.ts",
            ),
        ],
    )


def test_machine_error_shape_matches_spec() -> None:
    cfg = ContextGuardConfig(enabled=True, mode="block", block_at_tokens=300_000)
    reasons = [ContextSpikeReason.REPO_WIDE_WITHOUT_SCOPE.value]
    err = build_machine_error(_est(), reasons, cfg)["error"]
    assert err["type"] == "context_budget_exceeded"
    assert err["code"] == "BLOCKED_CONTEXT_SPIKE_REPO_WIDE_WITHOUT_SCOPE"
    assert err["client"] == "cursor-agent"
    assert err["provider"] == "anthropic"
    assert err["estimated_input_tokens"] == 1_240_000
    assert err["budget_tokens"] == 300_000
    assert err["top_contributors"][0]["estimated_tokens"] == 640_000
    assert err["top_contributors"][1]["path"] == "packages/api/generated/openapi.ts"
    assert isinstance(err["fix"], list) and err["fix"]
    assert err["override"]["allowed"] is True  # repo-wide is overridable


def test_human_message_contains_key_sections() -> None:
    cfg = ContextGuardConfig(enabled=True, mode="block", block_at_tokens=300_000)
    human, _ = render_block(_est(), [ContextSpikeReason.INPUT_TOO_LARGE.value], cfg)
    assert "Headroom Context Guard" in human
    assert "BLOCKED_CONTEXT_SPIKE_INPUT_TOO_LARGE" in human
    assert "Top contributors:" in human
    assert "How to fix:" in human
    assert "1,240,000 tokens" in human


def test_non_overridable_reason_marks_error_not_overridable() -> None:
    cfg = ContextGuardConfig(enabled=True, mode="block")
    err = build_machine_error(_est(), [ContextSpikeReason.BINARY_CONTEXT.value], cfg)["error"]
    assert err["override"]["allowed"] is False
