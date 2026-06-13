"""Raw-request token estimation + contributor extraction (ai-rules#79).

Analyses the *actual outbound request* (Anthropic or OpenAI JSON), not CLI args.
Returns a RequestContextEstimate with per-category token counts and the largest
context contributors (with file paths recovered from tool_use → tool_result
correlation where possible).

Token counts are estimates: we prefer Headroom's model-aware tokenizer and fall
back to a chars/4 heuristic so the guard never hard-fails on an unknown model.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .models import ContextContributor, RequestContextEstimate

_FALLBACK_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=32)
def _cached_tokenizer(model: str) -> Any:
    """Cache one tokenizer per model — building it per-contributor is expensive."""
    from headroom.tokenizers import get_tokenizer

    return get_tokenizer(model or "gpt-4o")


def estimate_tokens(text: str, model: str = "") -> int:
    """Best-effort token estimate for ``text``. Never raises."""
    if not text:
        return 0
    try:
        n = _cached_tokenizer(model or "gpt-4o").count_text(text)
        if isinstance(n, int) and n >= 0:
            return n
    except Exception:
        pass
    return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)


def _text_of(content: Any) -> str:
    """Flatten a string-or-blocks content value into plain text for sizing."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), (str, list)):
                    parts.append(_text_of(block["content"]))
        return "\n".join(parts)
    if isinstance(content, dict):
        return _text_of(content.get("content") or content.get("text"))
    return str(content)


def _looks_binary(text: str) -> bool:
    """Heuristic: NUL bytes or a high ratio of non-printable chars => binary."""
    if not text:
        return False
    if "\x00" in text[:4096]:
        return True
    sample = text[:4096]
    nonprint = sum(1 for c in sample if ord(c) < 9 or (13 < ord(c) < 32))
    return nonprint / max(1, len(sample)) > 0.30


def _tool_use_paths(messages: list[Any]) -> dict[str, tuple[str, str | None]]:
    """Map tool_use_id -> (tool_name, file_path) from Anthropic assistant tool_use blocks."""
    out: dict[str, tuple[str, str | None]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tid = block.get("id")
            name = block.get("name") or "tool"
            inp = block.get("input") or {}
            path = None
            if isinstance(inp, dict):
                for key in ("path", "file_path", "filepath", "filename", "file"):
                    if isinstance(inp.get(key), str):
                        path = inp[key]
                        break
            if isinstance(tid, str):
                out[tid] = (name, path)
    return out


def analyze_request(
    body: dict[str, Any],
    *,
    provider: str,
    client: str,
) -> RequestContextEstimate:
    """Analyse a parsed request body into a RequestContextEstimate.

    ``provider`` is "anthropic" | "openai" | "gemini" | ... ; ``client`` is the
    detected calling CLI. Unknown shapes degrade gracefully (UNKNOWN payload).
    """
    model = str(body.get("model") or "")
    est = RequestContextEstimate(provider=provider, client=client, raw_estimated_input_tokens=0)
    contributors: list[ContextContributor] = []

    # --- system prompt ---
    system_text = _text_of(body.get("system"))
    if provider == "openai":
        # OpenAI carries system as a message with role=system.
        pass
    if system_text:
        est.system_tokens = estimate_tokens(system_text, model)
        contributors.append(
            ContextContributor(
                source="system",
                kind="system",
                estimated_tokens=est.system_tokens,
                bytes=len(system_text),
            )
        )

    messages = body.get("messages")
    if not isinstance(messages, list):
        messages = []
    tool_paths = _tool_use_paths(messages)

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        # OpenAI system message
        if provider == "openai" and role == "system":
            t = _text_of(content)
            n = estimate_tokens(t, model)
            est.system_tokens += n
            contributors.append(
                ContextContributor(
                    source=f"messages[{i}].system", kind="system", estimated_tokens=n, bytes=len(t)
                )
            )
            continue

        # OpenAI tool result message
        if provider == "openai" and role == "tool":
            t = _text_of(content)
            n = estimate_tokens(t, model)
            est.tool_result_tokens += n
            contributors.append(
                ContextContributor(
                    source=f"messages[{i}].tool_result",
                    kind="tool_result",
                    estimated_tokens=n,
                    bytes=len(t),
                    reason="binary" if _looks_binary(t) else None,
                )
            )
            continue

        # Anthropic content blocks (tool_result lives inside content)
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    n = estimate_tokens(_text_of(block), model)
                    est.message_tokens += n
                    continue
                btype = block.get("type")
                if btype == "tool_result":
                    t = _text_of(block.get("content"))
                    n = estimate_tokens(t, model)
                    est.tool_result_tokens += n
                    tid = block.get("tool_use_id")
                    name, path = (
                        tool_paths.get(tid, ("tool", None))
                        if isinstance(tid, str)
                        else ("tool", None)
                    )
                    contributors.append(
                        ContextContributor(
                            source=f"tool_result.{name}",
                            kind="tool_result",
                            estimated_tokens=n,
                            bytes=len(t),
                            path=path,
                            reason="binary" if _looks_binary(t) else None,
                        )
                    )
                elif btype in (None, "text"):
                    t = _text_of(block.get("text") or block)
                    n = estimate_tokens(t, model)
                    est.message_tokens += n
                    contributors.append(
                        ContextContributor(
                            source=f"messages[{i}].content",
                            kind="message",
                            estimated_tokens=n,
                            bytes=len(t),
                        )
                    )
                # tool_use blocks are tiny; skip sizing
            continue

        # plain string content
        t = _text_of(content)
        n = estimate_tokens(t, model)
        est.message_tokens += n
        if t:
            contributors.append(
                ContextContributor(
                    source=f"messages[{i}].content",
                    kind="message",
                    estimated_tokens=n,
                    bytes=len(t),
                    reason="binary" if _looks_binary(t) else None,
                )
            )

    est.prompt_tokens = est.message_tokens  # alias used by some callers/spec
    est.raw_estimated_input_tokens = est.system_tokens + est.message_tokens + est.tool_result_tokens

    mt = body.get("max_tokens") or body.get("max_output_tokens")
    if isinstance(mt, int):
        est.estimated_output_tokens = mt

    if not messages and not system_text:
        est.warnings.append("no parseable messages in request body")

    contributors.sort(key=lambda c: c.estimated_tokens, reverse=True)
    est.contributors = contributors  # full list — policy inspects all of them
    est.largest_contributors = contributors[:10]  # display/error subset
    return est
