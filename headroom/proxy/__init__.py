"""Headroom Proxy Server.

A transparent proxy that sits between LLM clients (Claude Code, Cursor, etc.)
and LLM APIs (Anthropic, OpenAI), applying Headroom optimizations.

Usage:
    # Start the proxy
    python -m headroom.proxy.server

    # Use with Claude Code
    ANTHROPIC_BASE_URL=http://localhost:8787 claude

    # Use with Cursor (if using Anthropic)
    Set base URL in Cursor settings to http://localhost:8787
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .server import create_app, run_server

__all__ = ["create_app", "run_server"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .server import create_app

        return create_app
    if name == "run_server":
        from .server import run_server

        return run_server
    raise AttributeError(name)
