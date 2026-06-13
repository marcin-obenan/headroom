# Plan — ai-rules#79: Route Cursor Agent through Headroom + repo-aware context-budget guard

Tracking issue: https://github.com/obenan-dashboard/ai-rules/issues/79
Plan author: Claude (Opus 4.8). Implementation: **composer-2.5-fast via the wrapped `agent` CLI**, except Phase 0 which we do by hand to bootstrap that capability.

## Repo split (important)

The issue lives in `ai-rules` but the work spans two repos:

| Area | Repo | Files |
|---|---|---|
| Shell wrappers + setup flags | `ai-rules` | `setup.sh`, `setup_headroom_local_proxy.sh` |
| `wrap` launch path for the agent CLI | `headroom` | `headroom/cli/wrap.py`, `headroom/providers/cursor/*` |
| Context-guard mode (Goal 2) | `headroom` | new `headroom/proxy/context_guard/*`, wired into request handlers |

## What already exists (so we extend, not rebuild)

- `headroom wrap cursor` exists but is **Pattern-B** (start proxy + print config + inject `.cursorrules`); it does **not** launch the headless `agent` CLI with proxy env.
- `headroom/providers/cursor/` already builds the correct base URLs (`build_install_env` → `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`) with the `/p/<project>` prefix Cursor needs (it can't send custom headers).
- `claude`/`codex` are **Pattern-A** (`build_launch_env` + exec child with `-- "$@"`) — the template for the agent CLI.
- The Cursor Agent CLI is on PATH as `agent` / `cursor-agent` (Cursor curl installer, not npm).
- `setup_headroom_local_proxy.sh` already pins `HEADROOM_REF=local-claude-proxy-security-pass-2026-06-01` (the hardened branch we just merged) and emits shell-function wrappers via `wrapper_block()`.

---

## Phase 0 — Bootstrap: wrap the `agent` CLI (DONE BY US, then tested)

Goal: `agent "..."` routes through the hardened Headroom proxy exactly like `claude`/`codex`, so we can then drive composer-2.5-fast through it to build the rest.

1. **Headroom — add an `agent` launch target (Pattern-A) in `headroom/cli/wrap.py`.**
   - New subcommand `headroom wrap agent` (alias of/under cursor) that: starts the proxy, builds launch env from `headroom.providers.cursor.install.build_install_env(port, backend)` (`OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` with `/p/<project>` prefix), then **execs `cursor-agent`** with `-- "$@"`.
   - Resolve the binary: prefer `cursor-agent`, fall back to `agent`; clear error if neither is on PATH.
   - Add `agent`/`cursor-agent` to `_AGENT_SAVINGS_WRAP_AGENTS` so savings attribute correctly.
   - Tests: unit test that the launch env contains both base URLs + project prefix; that binary resolution prefers `cursor-agent`; that missing-binary yields a clear non-zero error.
2. **ai-rules — `setup_headroom_local_proxy.sh`.**
   - Add `INSTALL_CURSOR_AGENT` (default true) + `--install-cursor-agent`/`--no-install-cursor-agent`.
   - `install_agent_clis()`: if `cursor-agent` missing and install enabled, run Cursor's official installer (documented command), else warn with the manual install line. (No npm package — Cursor ships a curl installer.)
   - `wrapper_block()`: emit an `agent()` shell function mirroring `claude()`/`codex()`:
     ```sh
     agent() {
       HEADROOM_MODE=token HEADROOM_TELEMETRY=off HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true \
       "${HEADROOM_LOCAL_PROXY_PYTHON}" -m headroom.cli wrap agent \
         --port "${HEADROOM_LOCAL_PROXY_PORT:-8787}" -- "$@"
     }
     ```
     plus `tagent()` tmux variant for symmetry.
3. **ai-rules — `setup.sh`.** Add `--no-cursor-agent` / `--headroom-install-cursor-agent` flags that append to `HEADROOM_PROXY_ARGS`; document in the header block.
4. **Verification (the "tested and done" gate).**
   - Reload shell, run `agent "Reply with exactly: proxy-ok"` and confirm the reply.
   - Confirm traffic appears in Headroom (dashboard/ledger/savings shows a `cursor`/`agent` client entry) — proves it actually went through the proxy, not direct.
   - Document the verify command in the issue's "verification command exists" AC.

**Exit criteria for Phase 0:** `agent` runs through Headroom by default, opt-out is explicit, a verify command proves routing, all new unit tests green, local `verify-like-ci` (or project gate) green. Only then proceed.

---

## Phases 1–5 — Context Guard (DELEGATED to composer-2.5-fast via the `agent` CLI)

Built around Headroom (not a new gateway). Each phase is a spec-first, test-first unit with its own PR.

- **Phase 1 — Wrapper preflight (local, fail-fast before the CLI launches).**
  argv/stdin/cwd/repo-size estimate; risky-phrase + repo-wide-without-scope detection; writes a `wrapper_preflight` ledger event; blocks clearly unsafe invocations early. Fail-closed on dangerous cases, fail-open (warn) on estimator/ledger errors.

- **Phase 2 — Proxy raw-request analyzer + policy engine.**
  New `headroom/proxy/context_guard/`: `RequestContextEstimate` (raw token estimate pre-compression, per-contributor breakdown), `ContextGuardDecision` (allow/warn/compress/block), config model (`contextGuard.mode = off|warn|block`, thresholds, per-client overrides) layered CLI > repo > user > defaults. Wire as a pre-compression hook in the Anthropic/OpenAI/Gemini handlers. Block path returns the human + machine-readable error and **never calls upstream**.

- **Phase 3 — Agent-readable errors + override.**
  Human + JSON error shapes from the issue (reason codes, top contributors, remediation, example corrected command); override flag requiring a reason, non-overridable reason codes, CI-disables-override; override reason recorded in the ledger.

- **Phase 4 — Repo-aware diagnostics.**
  Stack profiles (java/node/js/ts/python/generic) classifying dependency/build/generated/test/cache/lockfile artifacts; matched files surface as top contributors with reason codes; allowlist exceptions.

- **Phase 5 — JSONL ledger + rollout.**
  `.headroom/context-ledger.jsonl`, one event per phase, records allowed/warned/compressed/blocked + output size when available, **no prompts/secrets by default**. Roll out warn-mode → review ledgers → block-mode in CI first, then local.

Testing per the issue: unit (token estimate, contributor extraction, path classification, policy matrix, override, error shape) + integration (`wrap claude|codex|agent`, oversized stdin, generated-file/lockfile context, large-repo-without-scope, allowed/compressed/blocked) + synthetic fixture repos per stack.

---

## How composer-2.5-fast is driven

After Phase 0, each Phase 1–5 PR is implemented by invoking the wrapped `agent` CLI with the composer-2.5-fast model, fed a scoped brief (spec + the specific files + the proof line to return), following the repo's spec→test→implement→verify gates. The orchestrator (this session) reviews each phase's diff + proof before the next.

## Open decisions to confirm before Phase 0

1. **`wrap agent` vs reuse `wrap cursor`** — add a distinct `agent` target (keeps `cursor` = IDE-config printer) vs. teach `cursor` to launch when a child command is present. Recommendation: distinct `agent` target (clearer, no behavior change to existing `wrap cursor`).
2. **cursor-agent auto-install** — run Cursor's curl installer from setup, or warn-only and require manual install. Recommendation: warn-only by default (curl|sh installers are sensitive), opt-in via `--headroom-install-cursor-agent`.

---

## Spike outcomes (2026-06-12) — feasibility of routing Cursor Agent through Headroom

Tested against the real `cursor-agent` (logged in as marcin.g@obenan.com).

**Proven feasible:**
- HTTPS_PROXY routing: cursor-agent honors it; ignores OPENAI_BASE_URL/ANTHROPIC_BASE_URL.
- TLS-MITM of unary calls works (NODE_EXTRA_CA_CERTS; no cert pinning). Wire = Connect/protobuf
  (`aiserver.v1.*` on api2.cursor.sh; model call `agent.v1.AgentService/Run`,
  `application/connect+proto`, HTTP/2 on agentn.global.api5.cursor.sh).
- Schema-less decode + re-encode of the Run REQUEST: prompt/context are plain UTF-8
  strings (71% of payload); rewrote a nested string with full length-fixups → valid frame.

**The wall (blocks checkpoint #1):**
- mitmproxy CANNOT proxy the agentn `AgentService/Run` HTTP/2 Connect **streaming response**.
  Connects + forwards request, but the response never returns (no error/RST) → client loops
  "Connection lost, reconnecting". Body-integrity therefore UNTESTED.

**Gating risk = bespoke H2/Connect streaming proxy** (not body-integrity). Risks stack:
streaming-proxy → integrity unknown → protobuf field semantics. Large R&D, may not pan out.

**Recommendation:** deliver #79's context-budget value on JSON-routable agents (claude/codex)
now; track Cursor Agent compression as a separate research effort gated on the streaming proxy.
