from __future__ import annotations

import gzip
import logging
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from headroom.providers.proxy_routes import _select_passthrough_base_url
from headroom.proxy.helpers import _read_limited_request_body, _read_request_body_bytes
from headroom.proxy.server import HeadroomProxy, ProxyConfig, create_app


def _local_app(**overrides: Any):
    config = ProxyConfig(
        optimize=False,
        cache_enabled=False,
        rate_limit_enabled=False,
        cost_tracking_enabled=False,
        **overrides,
    )
    return create_app(config)


def test_local_control_endpoint_rejects_non_loopback_client() -> None:
    app = _local_app()

    with TestClient(app, client=("10.0.0.1", 54321)) as client:
        response = client.get("/stats")

    assert response.status_code == 404


def test_local_control_endpoint_keeps_non_loopback_invisible_with_origin() -> None:
    app = _local_app()

    with TestClient(app, client=("10.0.0.1", 54321)) as client:
        response = client.get("/stats", headers={"Origin": "https://evil.example"})

    assert response.status_code == 404


def test_local_control_endpoint_rejects_untrusted_browser_origin() -> None:
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get("/stats", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


def test_local_control_mutation_endpoint_rejects_untrusted_browser_origin() -> None:
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post("/stats/reset", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


def test_local_control_endpoint_allows_same_origin_loopback_browser_origin() -> None:
    app = _local_app()

    with TestClient(
        app,
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 12345),
    ) as client:
        response = client.get("/stats", headers={"Origin": "http://127.0.0.1:8787"})

    assert response.status_code == 200


def test_cors_preflight_does_not_allow_arbitrary_origin() -> None:
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.options(
            "/stats",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.headers.get("access-control-allow-origin") != "https://evil.example"


def test_model_endpoint_rejects_untrusted_browser_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_messages(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("browser-origin guard should fail before model handling")

    monkeypatch.setattr(HeadroomProxy, "handle_anthropic_messages", unexpected_messages)
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.post(
            "/v1/messages",
            headers={"Origin": "https://evil.example"},
            json={"model": "claude-sonnet-4-20250514", "messages": []},
        )

    assert response.status_code == 403


def test_model_endpoint_rejects_non_loopback_client(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_messages(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("local proxy guard should fail before model handling")

    monkeypatch.setattr(HeadroomProxy, "handle_anthropic_messages", unexpected_messages)
    app = _local_app()

    with TestClient(app, client=("10.0.0.1", 54321)) as client:
        response = client.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4-20250514", "messages": []},
        )

    assert response.status_code == 404


def test_health_endpoints_remain_available_to_non_loopback_clients() -> None:
    app = _local_app()

    with TestClient(app, client=("10.0.0.1", 54321)) as client:
        for path in ("/livez", "/readyz", "/health"):
            response = client.get(path)
            assert response.status_code in {200, 503}


def test_model_endpoint_allows_same_origin_browser_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_messages(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    monkeypatch.setattr(HeadroomProxy, "handle_anthropic_messages", fake_messages)
    app = _local_app()

    with TestClient(
        app,
        base_url="http://127.0.0.1:8787",
        client=("127.0.0.1", 12345),
    ) as client:
        response = client.post(
            "/v1/messages",
            headers={"Origin": "http://127.0.0.1:8787"},
            json={"model": "claude-sonnet-4-20250514", "messages": []},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_websocket_rejects_untrusted_browser_origin() -> None:
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/v1/responses",
                headers={"Origin": "https://evil.example"},
            ):
                pass

    assert exc_info.value.code == 1008


def test_websocket_rejects_non_loopback_client() -> None:
    app = _local_app()

    with TestClient(app, client=("10.0.0.1", 54321)) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v1/responses"):
                pass

    assert exc_info.value.code == 1008


def test_custom_upstream_header_is_rejected_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unexpected_passthrough(self, request, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("custom upstream should fail before passthrough")

    monkeypatch.setattr(HeadroomProxy, "handle_passthrough", unexpected_passthrough)
    app = _local_app()

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        response = client.get(
            "/unhandled/path",
            headers={"x-headroom-base-url": "https://custom.example/base/"},
        )

    assert response.status_code == 403


def test_custom_upstream_header_requires_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_passthrough(self, request, base_url, *args, **kwargs):  # type: ignore[no-untyped-def]
        return JSONResponse({"base_url": base_url})

    monkeypatch.setattr(HeadroomProxy, "handle_passthrough", fake_passthrough)
    app = _local_app(
        custom_upstream_header_enabled=True,
        custom_upstream_allowed_hosts=["custom.example"],
    )

    with TestClient(app, client=("127.0.0.1", 12345)) as client:
        allowed = client.get(
            "/unhandled/path",
            headers={"x-headroom-base-url": "https://custom.example/base/"},
        )
        blocked = client.get(
            "/unhandled/path",
            headers={"x-headroom-base-url": "https://metadata.google.internal/"},
        )

    assert allowed.status_code == 200
    assert allowed.json()["base_url"] == "https://custom.example/base"
    assert blocked.status_code == 403


def test_select_passthrough_rejects_azure_header_without_allowlist() -> None:
    proxy = type(
        "Proxy",
        (),
        {
            "ANTHROPIC_API_URL": "https://api.anthropic.test",
            "OPENAI_API_URL": "https://api.openai.test",
            "GEMINI_API_URL": "https://api.gemini.test",
            "config": ProxyConfig(),
            "provider_runtime": type(
                "Runtime",
                (),
                {
                    "api_target": staticmethod(lambda provider: f"https://runtime.{provider}.test"),
                    "model_metadata_provider": staticmethod(lambda headers: "anthropic"),
                },
            )(),
        },
    )()

    with pytest.raises(HTTPException):
        _select_passthrough_base_url(
            proxy,
            {"api-key": "azure", "x-headroom-base-url": "https://azure.example/base/"},
        )


def test_proxy_access_log_redacts_raw_query_string() -> None:
    app = _local_app()
    records: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logger = logging.getLogger("headroom.proxy")
    handler = ListHandler()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    try:
        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            response = client.get("/health?access_token=secret-value")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert response.status_code == 200
    messages = "\n".join(records)
    assert "secret-value" not in messages
    assert "query_hash=" in messages


@pytest.mark.asyncio
async def test_compressed_request_body_is_bounded_after_decompression() -> None:
    class FakeRequest:
        headers = {"content-encoding": "gzip"}
        _body = gzip.compress(b"x" * 32)

        async def stream(self):  # type: ignore[no-untyped-def]
            yield self._body

    with pytest.raises(ValueError, match="decompressed"):
        await _read_request_body_bytes(FakeRequest(), max_bytes=16)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_request_body_reader_stops_after_size_limit() -> None:
    class FakeRequest:
        headers: dict[str, str] = {}

        def __init__(self) -> None:
            self.chunks_read = 0

        async def stream(self):  # type: ignore[no-untyped-def]
            for chunk in (b"abc", b"def", b"unread"):
                self.chunks_read += 1
                yield chunk

    request = FakeRequest()

    with pytest.raises(ValueError, match="Request body too large"):
        await _read_limited_request_body(request, max_bytes=5)  # type: ignore[arg-type]

    assert request.chunks_read == 2
