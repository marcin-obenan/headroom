#!/usr/bin/env bash
# Restart the local Headroom proxy on 127.0.0.1:8787.
#
# This proxy routes the interactive Claude Code session; killing it without an
# immediate restart drops the session's connection. This script kills any
# listener on the port and relaunches the proxy detached so it survives.
set -euo pipefail

PORT="${HEADROOM_PROXY_PORT:-8787}"
HOST="${HEADROOM_PROXY_HOST:-127.0.0.1}"
LOG="${HEADROOM_PROXY_LOG:-/tmp/headroom-proxy-${PORT}.log}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer the project venv (has all proxy deps: opentelemetry, etc.). A bare
# homebrew python typically lacks them, so only fall back to it explicitly.
if [[ -n "${HEADROOM_PROXY_PYTHON:-}" ]]; then
  PY="${HEADROOM_PROXY_PYTHON}"
elif [[ -x "${REPO_DIR}/.venv-security/bin/python" ]]; then
  PY="${REPO_DIR}/.venv-security/bin/python"
elif [[ -x "${REPO_DIR}/.venv/bin/python" ]]; then
  PY="${REPO_DIR}/.venv/bin/python"
else
  PY="$(command -v python3)"
fi

echo "[restart-proxy] killing any listener on ${HOST}:${PORT}..."
pids="$(lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t 2>/dev/null || true)"
if [[ -n "${pids}" ]]; then
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
  # shellcheck disable=SC2086
  kill -9 ${pids} 2>/dev/null || true
fi

# Wait for the port to free up (max ~5s).
for _ in 1 2 3 4 5; do
  if ! lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "[restart-proxy] starting proxy on ${HOST}:${PORT} (log: ${LOG})..."
cd "${REPO_DIR}"
nohup "${PY}" -m headroom.cli proxy \
  --host "${HOST}" \
  --port "${PORT}" \
  --mode token \
  --backend anthropic \
  --no-telemetry \
  >>"${LOG}" 2>&1 &
disown || true

# Wait until the proxy is accepting connections (max ~10s).
for _ in $(seq 1 10); do
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "[restart-proxy] proxy is up on ${HOST}:${PORT}"
    lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | tail -1
    exit 0
  fi
  sleep 1
done

echo "[restart-proxy] ERROR: proxy did not come up on ${HOST}:${PORT}; tail of ${LOG}:" >&2
tail -20 "${LOG}" >&2 || true
exit 1
