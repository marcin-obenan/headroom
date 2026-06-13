"""Context-guard configuration loading + layering (ai-rules#79).

Sources, lowest precedence first: defaults < user config < repo config < CLI flags.
Per-client overrides (``clients.<client>``) are applied on top of the base for the
resolved client. Config JSON uses camelCase keys (matching the spec); we map them
to the snake_case dataclass fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import ContextGuardConfig, GuardMode
from .reason_codes import DEFAULT_NON_OVERRIDABLE

# camelCase JSON key -> dataclass field name
_KEY_MAP: dict[str, str] = {
    "enabled": "enabled",
    "mode": "mode",
    "warnAtTokens": "warn_at_tokens",
    "blockAtTokens": "block_at_tokens",
    "maxSingleMessageTokens": "max_single_message_tokens",
    "maxToolResultTokens": "max_tool_result_tokens",
    "maxSingleFileTokens": "max_single_file_tokens",
    "maxOutputTokens": "max_output_tokens",
    "blockRepoWidePrompts": "block_repo_wide_prompts",
    "requireExplicitScopeForLargeRepos": "require_explicit_scope_for_large_repos",
    "largeRepoFileThreshold": "large_repo_file_threshold",
    "allowCompressionInsteadOfBlock": "allow_compression_instead_of_block",
    "blockEvenIfCompressible": "block_even_if_compressible",
    "ledgerPath": "ledger_path",
}

_VALID_MODES: tuple[GuardMode, ...] = ("off", "warn", "block")


class ContextGuardConfigError(ValueError):
    """Raised on invalid context-guard config (fail-closed at load time)."""


def _flatten(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate a camelCase contextGuard dict into dataclass kwargs (no clients/override)."""
    out: dict[str, Any] = {}
    for jkey, fkey in _KEY_MAP.items():
        if jkey in raw:
            out[fkey] = raw[jkey]
    if "mode" in out and out["mode"] not in _VALID_MODES:
        raise ContextGuardConfigError(
            f"contextGuard.mode must be one of {_VALID_MODES}, got {out['mode']!r}"
        )
    if "allowlist" in raw:
        out["allowlist"] = tuple(raw["allowlist"])
    ov = raw.get("override")
    if isinstance(ov, dict):
        if "enabled" in ov:
            out["override_enabled"] = bool(ov["enabled"])
        if "requiresReason" in ov:
            out["override_requires_reason"] = bool(ov["requiresReason"])
        if "nonOverridableReasons" in ov:
            # always keep the hard non-overridables, union with configured.
            out["non_overridable_reasons"] = tuple(
                dict.fromkeys((*DEFAULT_NON_OVERRIDABLE, *ov["nonOverridableReasons"]))
            )
    return out


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _extract_guard_block(doc: dict[str, Any]) -> dict[str, Any]:
    """Accept either ``{headroom:{contextGuard:{...}}}`` or ``{contextGuard:{...}}`` or bare."""
    if "headroom" in doc and isinstance(doc["headroom"], dict):
        doc = doc["headroom"]
    if "contextGuard" in doc and isinstance(doc["contextGuard"], dict):
        return doc["contextGuard"]
    return doc


def load_config_file(path: str | Path) -> dict[str, Any]:
    """Load a JSON config file's contextGuard block. Invalid JSON fails closed."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ContextGuardConfigError(f"invalid context-guard config at {p}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ContextGuardConfigError(f"context-guard config at {p} must be a JSON object")
    return _extract_guard_block(doc)


def load_raw_config(
    *,
    cwd: str | Path | None = None,
    home: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Merge user + repo config files + env into a single raw contextGuard dict.

    Precedence (low→high): user (``~/.headroom/config.json``) < repo
    (``<cwd>/.headroom/config.json``) < env (``HEADROOM_CONTEXT_GUARD_MODE`` /
    ``HEADROOM_CONTEXT_GUARD_ENABLED``). Returns ``None`` when nothing enables
    the guard, so ProxyConfig.context_guard stays falsy and the proxy path is
    untouched by default. Invalid config files raise ContextGuardConfigError
    (fail closed at startup).
    """
    import os

    env = env if env is not None else dict(os.environ)
    home_dir = Path(home) if home else Path.home()
    work_dir = Path(cwd) if cwd else Path.cwd()

    merged: dict[str, Any] = {}
    for path in (home_dir / ".headroom" / "config.json", work_dir / ".headroom" / "config.json"):
        merged = _deep_merge(merged, load_config_file(path))

    mode = env.get("HEADROOM_CONTEXT_GUARD_MODE", "").strip().lower()
    if mode:
        if mode not in _VALID_MODES:
            raise ContextGuardConfigError(
                f"HEADROOM_CONTEXT_GUARD_MODE must be one of {_VALID_MODES}, got {mode!r}"
            )
        merged["mode"] = mode
        merged.setdefault("enabled", mode != "off")
    enabled_env = env.get("HEADROOM_CONTEXT_GUARD_ENABLED", "").strip().lower()
    if enabled_env in ("1", "true", "on", "yes"):
        merged["enabled"] = True
    elif enabled_env in ("0", "false", "off", "no"):
        merged["enabled"] = False

    if not merged or not merged.get("enabled"):
        return None
    return merged


def resolve_config(
    *,
    client: str,
    layers: list[dict[str, Any]] | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ContextGuardConfig:
    """Layer config dicts (lowest precedence first) into a resolved ContextGuardConfig.

    ``layers`` are already-extracted contextGuard blocks (e.g. user then repo).
    ``cli_overrides`` are highest precedence. Per-client overrides under
    ``clients.<client>`` are applied last (after the base layers, before nothing —
    they win over base but a CLI flag still wins over them by being merged on top).
    """
    merged: dict[str, Any] = {}
    for layer in layers or []:
        merged = _deep_merge(merged, layer or {})
    if cli_overrides:
        merged = _deep_merge(merged, cli_overrides)

    base_kwargs = _flatten(merged)

    client_block = (merged.get("clients") or {}).get(client)
    if isinstance(client_block, dict):
        base_kwargs.update(_flatten(client_block))
    # CLI flags must still win over per-client config.
    if cli_overrides:
        base_kwargs.update(_flatten(cli_overrides))

    return ContextGuardConfig(**base_kwargs)
