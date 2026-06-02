# 023. Local Proxy Hardening

**Status:** draft

## Purpose

Headroom can run as a local single-user proxy between Claude Code and
Anthropic. In that mode, local prompt, response, telemetry, cache, and
retrieval data must not be reachable by arbitrary websites, LAN clients, or
unconfigured upstream targets.

## Required Behavior

1. **Local control surface**
   - Dashboard, stats, metrics, debug, cache-control, CCR retrieval, feedback,
     telemetry, and TOIN endpoints are local-control endpoints.
   - Local-control endpoints accept loopback clients by default.
   - Local-control endpoints reject non-loopback clients by default.
   - Local-control endpoints reject browser requests with an untrusted
     `Origin` header by default.
   - State-changing local-control endpoints, including stats reset, follow the
     same browser-origin policy as read-only local-control endpoints.

2. **CORS**
   - The proxy must not use wildcard CORS by default.
   - Cross-origin browser access must require an explicit configured origin.
   - Same-origin proxy dashboard access must continue to work.
   - Browser requests with an untrusted `Origin` header must fail before any
     model, passthrough, dashboard, telemetry, or cache handler executes.
   - Non-health proxy endpoints, including model and passthrough endpoints,
     reject non-loopback clients by default.

3. **WebSockets**
   - WebSocket handshakes reject non-loopback clients by default.
   - WebSocket handshakes reject browser requests with an untrusted `Origin`
     header by default before the connection is accepted.
   - Environment-backed WebSocket credential fallback is available only to
     trusted local callers while local hardening is enabled.

4. **Custom upstream routing**
   - Inbound `x-headroom-base-url` must not select an arbitrary upstream by
     default.
   - Operators may explicitly enable this compatibility behavior only with an
     allowed-host list.
   - Unconfigured or disallowed custom upstreams must fail closed.

5. **Telemetry and logs**
   - External telemetry must be opt-in for the local hardened profile.
   - Proxy access logs must not include raw query strings.
   - Full message logging remains disabled by default.

6. **Request body safety**
   - Request bodies are read with a hard byte cap instead of being fully
     buffered before size checks.
   - Compressed request bodies must be bounded after decompression.
   - Requests exceeding the decompressed limit must fail before JSON parsing or
     upstream forwarding.

7. **Docker defaults**
   - Compose examples must publish proxy ports on host loopback only.
   - Database sidecars must not publish unauthenticated ports by default.
   - Example database credentials must not be static defaults.

8. **Developer setup**
   - The local proxy setup script must be idempotent.
   - The local proxy setup script must install missing Claude Code and
     OpenAI Codex CLIs by default.
   - The installed Claude and Codex shell wrappers must run Headroom in token mode.
   - The installed Claude and Codex shell wrappers must keep telemetry disabled by default.
   - The installed Claude and Codex shell wrappers must keep the local security guard enabled.
   - The installed tmux helpers must execute through an interactive shell so the
     installed `claude` and `codex` wrappers are available.

## Non-Goals

- This does not make Headroom a multi-user hosted service.
- This does not add a full authentication product.
- This does not remove operator-controlled provider API URL overrides.
