"""Local-only security guardrails for the Python proxy.

The proxy is primarily a single-user local process. These helpers keep
observability, cache, telemetry, and retrieval endpoints from becoming a
browser-readable or LAN-readable control plane when a user runs it for Claude.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response

from headroom.proxy.loopback_guard import is_loopback_host

LOCAL_CONTROL_EXACT_PATHS = frozenset(
    {
        "/dashboard",
        "/stats",
        "/stats/reset",
        "/stats-history",
        "/transformations/feed",
        "/subscription-window",
        "/quota",
        "/metrics",
        "/debug/memory",
        "/cache/clear",
        "/v1/feedback",
        "/v1/telemetry",
        "/v1/telemetry/export",
        "/v1/telemetry/import",
        "/v1/telemetry/tools",
        "/v1/toin/stats",
        "/v1/toin/patterns",
        "/v1/retrieve",
        "/v1/retrieve/stats",
        "/v1/retrieve/tool_call",
    }
)

LOCAL_CONTROL_PREFIXES = (
    "/v1/feedback/",
    "/v1/telemetry/tools/",
    "/v1/toin/pattern/",
    "/v1/retrieve/",
)

LOCAL_PUBLIC_PATHS = frozenset({"/livez", "/readyz", "/health"})

_POLICY_VIOLATION = 1008


def default_loopback_origins(port: int) -> list[str]:
    """Return browser origins allowed to use local CORS by default."""

    return [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"http://[::1]:{port}",
    ]


def is_local_control_path(path: str) -> bool:
    """Return whether ``path`` belongs to the local control plane."""

    return path in LOCAL_CONTROL_EXACT_PATHS or any(
        path.startswith(prefix) for prefix in LOCAL_CONTROL_PREFIXES
    )


def _connection_client_host(connection: Request | WebSocket) -> str | None:
    client = getattr(connection, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return str(host) if host is not None else None


def connection_client_is_loopback(connection: Request | WebSocket) -> bool:
    host = _connection_client_host(connection)
    # Starlette TestClient uses this sentinel host; real uvicorn clients are IP
    # literals because proxy_headers=False is configured at server startup.
    if host == "testclient":
        return True
    return is_loopback_host(host)


def _origin_is_allowed(
    connection: Request | WebSocket,
    *,
    allowed_origins: Sequence[str],
) -> bool:
    origin = connection.headers.get("origin")
    if not origin:
        return True
    if origin in allowed_origins:
        return True

    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if not is_loopback_host(parsed.hostname):
        return False

    host_header = connection.headers.get("host", "").lower()
    return bool(host_header) and parsed.netloc.lower() == host_header


def local_browser_origin_guard_response(
    request: Request,
    *,
    enabled: bool,
    allowed_origins: Sequence[str],
) -> Response | None:
    """Reject cross-site browser access to the local proxy surface."""

    if not enabled or _origin_is_allowed(request, allowed_origins=allowed_origins):
        return None
    return JSONResponse(
        status_code=403,
        content={"error": "local proxy rejects untrusted browser origin"},
    )


def local_proxy_guard_response(
    request: Request,
    *,
    enabled: bool,
    allowed_origins: Sequence[str],
) -> Response | None:
    """Reject non-local access to proxy endpoints in local hardened mode."""

    if not enabled or request.url.path in LOCAL_PUBLIC_PATHS:
        return None
    if not connection_client_is_loopback(request):
        return Response(status_code=404)
    return local_browser_origin_guard_response(
        request,
        enabled=True,
        allowed_origins=allowed_origins,
    )


def local_control_guard_response(
    request: Request,
    *,
    enabled: bool,
    allowed_origins: Sequence[str],
) -> Response | None:
    """Return a rejection response for disallowed local-control requests."""

    if not enabled or not is_local_control_path(request.url.path):
        return None
    if not connection_client_is_loopback(request):
        return Response(status_code=404)
    if not _origin_is_allowed(request, allowed_origins=allowed_origins):
        return JSONResponse(
            status_code=403,
            content={"error": "local control endpoint rejects untrusted browser origin"},
        )
    return None


async def close_websocket_if_local_guard_rejects(
    websocket: WebSocket,
    *,
    enabled: bool,
    allowed_origins: Sequence[str],
) -> bool:
    """Close a WebSocket handshake when local hardening rejects it."""

    if not enabled:
        return False
    if not connection_client_is_loopback(websocket):
        await websocket.close(
            code=_POLICY_VIOLATION,
            reason="local proxy rejects non-loopback websocket client",
        )
        return True
    if not _origin_is_allowed(websocket, allowed_origins=allowed_origins):
        await websocket.close(
            code=_POLICY_VIOLATION,
            reason="local proxy rejects untrusted websocket origin",
        )
        return True
    return False
