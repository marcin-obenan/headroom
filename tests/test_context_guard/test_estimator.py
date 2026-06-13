"""Unit tests: token estimate + contributor extraction (ai-rules#79)."""

from __future__ import annotations

from headroom.proxy.context_guard import analyze_request


def test_anthropic_request_estimate_splits_system_message_tool() -> None:
    body = {
        "model": "claude-opus-4-8",
        "system": "x" * 400,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "read_file",
                        "input": {"path": "src/app.ts"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "y" * 800},
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "z" * 1200},
                ],
            },
        ],
        "max_tokens": 512,
    }
    est = analyze_request(body, provider="anthropic", client="claude")
    assert est.system_tokens > 0
    assert est.message_tokens > 0
    assert est.tool_result_tokens > 0
    assert est.raw_estimated_input_tokens == (
        est.system_tokens + est.message_tokens + est.tool_result_tokens
    )
    assert est.estimated_output_tokens == 512
    # tool_result path recovered from the preceding tool_use
    tr = [c for c in est.largest_contributors if c.kind == "tool_result"]
    assert tr and tr[0].path == "src/app.ts"
    assert tr[0].source == "tool_result.read_file"


def test_openai_request_estimate_handles_system_and_tool_roles() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "s" * 400},
            {"role": "user", "content": "u" * 400},
            {"role": "tool", "content": "t" * 4000},
        ],
    }
    est = analyze_request(body, provider="openai", client="codex")
    assert est.system_tokens > 0
    assert est.tool_result_tokens > 0
    # largest contributor should be the big tool result
    assert est.largest_contributors[0].kind == "tool_result"


def test_top_contributors_sorted_desc_and_capped() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "a" * (50 * (i + 1))} for i in range(15)],
    }
    est = analyze_request(body, provider="openai", client="codex")
    toks = [c.estimated_tokens for c in est.largest_contributors]
    assert toks == sorted(toks, reverse=True)
    assert len(est.largest_contributors) <= 10


def test_binary_content_flagged() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "\x00\x01\x02" * 2000}],
    }
    est = analyze_request(body, provider="openai", client="codex")
    assert any(c.reason == "binary" for c in est.largest_contributors)


def test_malformed_request_degrades_gracefully() -> None:
    est = analyze_request({"model": "x"}, provider="anthropic", client="claude")
    assert est.raw_estimated_input_tokens == 0
    assert est.warnings
