#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

VENV_PATH="${REPO_ROOT}/.venv-headroom"
PORT="8787"
SHELL_TARGET="auto"
PYTHON_CMD="${HEADROOM_SETUP_PYTHON:-}"
PATCH_SHELL=1
INSTALL_CLAUDE=1
INSTALL_CODEX=1
SKIP_PYTHON_INSTALL=0
SKIP_PREREQ_CHECK=0
DRY_RUN=0

info() {
  printf '==> %s\n' "$*"
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage: scripts/setup-local-proxy.sh [options]

Set up Headroom as a hardened local token proxy for Claude Code and Codex.

Options:
  --venv PATH              Python virtualenv path (default: .venv-headroom)
  --port PORT              Local proxy port for wrappers (default: 8787)
  --python PATH            Python 3.10-3.13 interpreter for the venv
                           (default: python3.13, python3.12, python3.11,
                           python3.10, then python3)
  --shell auto|zsh|bash|both
                           Shell rc file to patch (default: auto)
  --no-shell               Do not modify shell rc files
  --install-claude         Install Claude Code with npm if it is missing (default)
  --install-codex          Install OpenAI Codex CLI with npm if it is missing (default)
  --no-install-claude      Do not install Claude Code if it is missing
  --no-install-codex       Do not install OpenAI Codex CLI if it is missing
  --skip-python-install    Skip venv creation and pip install
  --skip-prereq-check      Skip prerequisite command checks
  --dry-run                Print actions without modifying files
  -h, --help               Show this help

Security defaults installed by the wrapper:
  HEADROOM_MODE=token
  HEADROOM_TELEMETRY=off
  HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true
  Headroom binds through its wrapper defaults on 127.0.0.1.

Installed shell wrappers:
  claude / tclaude
  codex  / tcodex
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --venv)
      [[ $# -ge 2 ]] || die "--venv requires a path"
      VENV_PATH="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || die "--python requires a path"
      PYTHON_CMD="$2"
      shift 2
      ;;
    --shell)
      [[ $# -ge 2 ]] || die "--shell requires a value"
      SHELL_TARGET="$2"
      shift 2
      ;;
    --no-shell)
      PATCH_SHELL=0
      shift
      ;;
    --install-claude)
      INSTALL_CLAUDE=1
      shift
      ;;
    --install-codex)
      INSTALL_CODEX=1
      shift
      ;;
    --no-install-claude)
      INSTALL_CLAUDE=0
      shift
      ;;
    --no-install-codex)
      INSTALL_CODEX=0
      shift
      ;;
    --skip-python-install)
      SKIP_PYTHON_INSTALL=1
      shift
      ;;
    --skip-prereq-check)
      SKIP_PREREQ_CHECK=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

case "${SHELL_TARGET}" in
  auto|zsh|bash|both) ;;
  *) die "--shell must be one of: auto, zsh, bash, both" ;;
esac

case "${PORT}" in
  ''|*[!0-9]*) die "--port must be numeric" ;;
esac

if [[ "${VENV_PATH}" != /* ]]; then
  VENV_PATH="${REPO_ROOT}/${VENV_PATH}"
fi

PYTHON_BIN="${VENV_PATH}/bin/python"

print_prereq_help() {
  local system
  system="$(uname -s 2>/dev/null || printf unknown)"
  case "${system}" in
    Darwin)
      cat >&2 <<'HELP'

Install missing prerequisites on macOS with:
  brew install git python@3.13 rust node tmux
  npm install -g @anthropic-ai/claude-code
  npm install -g @openai/codex

Headroom currently needs Python 3.10-3.13 for its pinned proxy dependencies.
If your default python3 is Python 3.14, re-run with:
  scripts/setup-local-proxy.sh --python python3.13
HELP
      ;;
    Linux)
      cat >&2 <<'HELP'

Install missing prerequisites on Debian/Ubuntu with:
  sudo apt-get update
  sudo apt-get install -y git python3 python3-venv python3-pip curl build-essential tmux
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  source "$HOME/.cargo/env"  # or open a new shell after rustup finishes
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
  npm install -g @anthropic-ai/claude-code
  npm install -g @openai/codex

Headroom currently needs Python 3.10-3.13 for its pinned proxy dependencies.
If your default python3 is Python 3.14, install/use Python 3.13 and re-run with:
  scripts/setup-local-proxy.sh --python python3.13
HELP
      ;;
    *)
      cat >&2 <<'HELP'

Install missing prerequisites:
  git, Python 3.10-3.13 with venv support, Rust/cargo, Node/npm, tmux,
  Claude Code, OpenAI Codex CLI
HELP
      ;;
  esac
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    print_prereq_help
    die "Missing required command: $1"
  }
}

ensure_cargo_path() {
  local cargo_home
  local cargo_bin

  command -v cargo >/dev/null 2>&1 && return

  cargo_home="${CARGO_HOME:-${HOME}/.cargo}"
  cargo_bin="${cargo_home}/bin"
  if [[ -x "${cargo_bin}/cargo" ]]; then
    export PATH="${cargo_bin}:${PATH}"
    info "Added ${cargo_bin} to PATH for Rust/cargo"
  fi
}

python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)
PY
}

python_version_text() {
  "$1" - <<'PY' 2>/dev/null || true
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
PY
}

python_reports_venv_root() {
  local python_bin="$1"
  local venv_path="$2"
  local output

  output="$("${python_bin}" - "${venv_path}" <<'PY' 2>/dev/null || true
import os
import sys

expected = os.path.realpath(sys.argv[1])
prefix = os.path.realpath(sys.prefix)
base_prefix = os.path.realpath(getattr(sys, "base_prefix", sys.prefix))
if prefix == expected and prefix != base_prefix:
    print("HEADROOM_VENV_OK")
PY
)"
  [[ "${output}" == "HEADROOM_VENV_OK" ]]
}

canonical_existing_path() {
  local path="$1"
  if [[ -d "${path}" ]]; then
    (cd "${path}" && pwd -P)
  else
    (cd "$(dirname "${path}")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "${path}")")
  fi
}

ensure_safe_existing_venv_path() {
  local venv_path="$1"
  local venv_canon
  local home_canon
  local repo_canon
  local venv_name
  local trimmed_venv_path
  local first_entry

  [[ -d "${venv_path}" ]] || die "Refusing to recreate Python venv because the existing path is not a directory: ${venv_path}"

  venv_canon="$(canonical_existing_path "${venv_path}")"
  home_canon="$(canonical_existing_path "${HOME}")"
  repo_canon="$(canonical_existing_path "${REPO_ROOT}")"
  trimmed_venv_path="${venv_path%/}"
  venv_name="${trimmed_venv_path##*/}"

  if [[ "${venv_canon}" == "/" || "${venv_canon}" == "${home_canon}" || "${venv_canon}" == "${repo_canon}" ]]; then
    die "Refusing to recreate unsafe Python venv path: ${venv_path}"
  fi

  if [[ -L "${venv_path}" ]]; then
    die "Refusing to use ${venv_path}; symlinked venv paths are not supported."
  fi

  if [[ -f "${venv_path}/pyvenv.cfg" ]]; then
    return
  fi

  first_entry="$(find "${venv_path}" -mindepth 1 -maxdepth 1 -print -quit)"
  if [[ "${venv_name}" == ".venv-headroom" && -z "${first_entry}" ]]; then
    return
  fi

  die "Refusing to recreate ${venv_path}; it is not a recognized virtualenv. Existing non-empty venv directories must contain pyvenv.cfg."
}

validate_existing_venv_path_if_present() {
  if [[ -e "${VENV_PATH}" || -L "${VENV_PATH}" ]]; then
    ensure_safe_existing_venv_path "${VENV_PATH}"
    if [[ -x "${PYTHON_BIN}" ]] && python_is_compatible "${PYTHON_BIN}" && ! python_reports_venv_root "${PYTHON_BIN}" "${VENV_PATH}"; then
      die "Refusing to use ${VENV_PATH}; bin/python does not report a virtualenv rooted at that path."
    fi
  fi
}

select_python_cmd() {
  local candidate

  if [[ -n "${PYTHON_CMD}" ]]; then
    require_cmd "${PYTHON_CMD}"
    python_is_compatible "${PYTHON_CMD}" || {
      print_prereq_help
      die "${PYTHON_CMD} is not supported. Use Python 3.10-3.13; Python 3.14 is not yet compatible with the pinned proxy dependencies."
    }
    return
  fi

  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_is_compatible "${candidate}"; then
      PYTHON_CMD="${candidate}"
      return
    fi
  done

  print_prereq_help
  die "Missing compatible Python. Use Python 3.10-3.13; Python 3.14 is not yet compatible with the pinned proxy dependencies."
}

ensure_venv_python_compatible() {
  local existing_version

  [[ -e "${VENV_PATH}" || -L "${VENV_PATH}" ]] || return 0
  validate_existing_venv_path_if_present

  if [[ ! -x "${PYTHON_BIN}" ]]; then
    warn "Existing Python venv is incomplete; recreating: ${VENV_PATH}"
    run_cmd rm -rf "${VENV_PATH}"
    return
  fi

  if python_is_compatible "${PYTHON_BIN}"; then
    python_reports_venv_root "${PYTHON_BIN}" "${VENV_PATH}" ||
      die "Refusing to use ${VENV_PATH}; bin/python does not report a virtualenv rooted at that path."
    return
  fi

  existing_version="$(python_version_text "${PYTHON_BIN}")"
  warn "Existing Python venv uses unsupported Python ${existing_version:-unknown}; recreating: ${VENV_PATH}"
  run_cmd rm -rf "${VENV_PATH}"
}

check_prereqs() {
  require_cmd git
  select_python_cmd
  ensure_cargo_path
  require_cmd cargo
  if { [[ "${INSTALL_CLAUDE}" -eq 1 ]] && ! command -v claude >/dev/null 2>&1; } ||
    { [[ "${INSTALL_CODEX}" -eq 1 ]] && ! command -v codex >/dev/null 2>&1; }; then
    require_cmd npm
  fi
  if [[ "${INSTALL_CLAUDE}" -ne 1 ]] && ! command -v claude >/dev/null 2>&1; then
    warn "Claude Code is not on PATH. Re-run with --install-claude or install it with npm."
  fi
  if [[ "${INSTALL_CODEX}" -ne 1 ]] && ! command -v codex >/dev/null 2>&1; then
    warn "Codex CLI is not on PATH. Re-run with --install-codex or install it with npm."
  fi
  if ! command -v tmux >/dev/null 2>&1; then
    warn "tmux is not on PATH. The claude/codex wrappers will work; tclaude/tcodex will not until tmux is installed."
  fi
}

run_cmd() {
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '+'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi
  "$@"
}

shell_quote() {
  printf '%q' "$1"
}

ensure_cli_after_install() {
  local command_name="$1"
  local package_name="$2"

  [[ "${DRY_RUN}" -eq 1 ]] && return

  hash -r 2>/dev/null || true
  if command -v "${command_name}" >/dev/null 2>&1; then
    return
  fi

  die "${command_name} is still not on PATH after installing ${package_name}. Check npm's global bin directory and add it to PATH."
}

install_claude_if_missing() {
  if command -v claude >/dev/null 2>&1; then
    info "Claude Code already available: $(command -v claude)"
    return
  fi
  if [[ "${INSTALL_CLAUDE}" -ne 1 ]]; then
    warn "Skipping Claude Code install. Install with: npm install -g @anthropic-ai/claude-code"
    return
  fi
  info "Installing Claude Code with npm"
  run_cmd npm install -g @anthropic-ai/claude-code
  ensure_cli_after_install claude @anthropic-ai/claude-code
}

install_codex_if_missing() {
  if command -v codex >/dev/null 2>&1; then
    info "Codex CLI already available: $(command -v codex)"
    return
  fi
  if [[ "${INSTALL_CODEX}" -ne 1 ]]; then
    warn "Skipping Codex CLI install. Install with: npm install -g @openai/codex"
    return
  fi
  info "Installing Codex CLI with npm"
  run_cmd npm install -g @openai/codex
  ensure_cli_after_install codex @openai/codex
}

install_python_env() {
  if [[ "${SKIP_PYTHON_INSTALL}" -eq 1 ]]; then
    info "Skipping Python venv install"
    return
  fi
  select_python_cmd
  info "Using Python interpreter: ${PYTHON_CMD}"
  ensure_venv_python_compatible
  info "Creating Python venv at ${VENV_PATH}"
  run_cmd "${PYTHON_CMD}" -m venv "${VENV_PATH}"
  info "Installing Headroom proxy extras"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '+ cd %q && %q -m pip install -U pip && %q -m pip install -e %q\n' \
      "${REPO_ROOT}" "${PYTHON_BIN}" "${PYTHON_BIN}" ".[proxy]"
  else
    (
      cd "${REPO_ROOT}"
      "${PYTHON_BIN}" -m pip install -U pip
      "${PYTHON_BIN}" -m pip install -e ".[proxy]"
    )
  fi
}

rc_files_for_target() {
  local shell_name
  shell_name="$(basename "${SHELL:-}")"
  case "${SHELL_TARGET}" in
    zsh)
      printf '%s\n' "${HOME}/.zshrc"
      ;;
    bash)
      printf '%s\n' "${HOME}/.bashrc"
      ;;
    both)
      printf '%s\n' "${HOME}/.zshrc" "${HOME}/.bashrc"
      ;;
    auto)
      case "${shell_name}" in
        zsh) printf '%s\n' "${HOME}/.zshrc" ;;
        bash) printf '%s\n' "${HOME}/.bashrc" ;;
        *)
          if [[ -f "${HOME}/.zshrc" ]]; then
            printf '%s\n' "${HOME}/.zshrc"
          else
            printf '%s\n' "${HOME}/.bashrc"
          fi
          ;;
      esac
      ;;
  esac
}

wrapper_block() {
  local quoted_root
  local quoted_python
  local quoted_port

  quoted_root="$(shell_quote "${REPO_ROOT}")"
  quoted_python="$(shell_quote "${PYTHON_BIN}")"
  quoted_port="$(shell_quote "${PORT}")"

  cat <<EOF
# >>> headroom local proxy >>>
export HEADROOM_LOCAL_PROXY_ROOT=${quoted_root}
export HEADROOM_LOCAL_PROXY_PYTHON=${quoted_python}
export HEADROOM_LOCAL_PROXY_PORT=${quoted_port}

claude() {
  HEADROOM_MODE=token \\
  HEADROOM_HOST=127.0.0.1 \\
  HEADROOM_TELEMETRY=off \\
  HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true \\
  "\${HEADROOM_LOCAL_PROXY_PYTHON}" -m headroom.cli wrap claude \\
    --port "\${HEADROOM_LOCAL_PROXY_PORT:-8787}" \\
    --no-context-tool --no-mcp --no-serena -- "\$@"
}

tclaude() {
  local session="\${TCLAUDE_SESSION:-claude}"
  local shell_path="\${SHELL:-/bin/sh}"
  local cmd="claude"
  local arg
  for arg in "\$@"; do
    cmd+=" \$(printf "%q" "\${arg}")"
  done
  tmux new-session -A -s "\${session}" "\$(printf "%q" "\${shell_path}") -ic \$(printf "%q" "\${cmd}")"
}

codex() {
  HEADROOM_MODE=token \\
  HEADROOM_HOST=127.0.0.1 \\
  HEADROOM_TELEMETRY=off \\
  HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true \\
  "\${HEADROOM_LOCAL_PROXY_PYTHON}" -m headroom.cli wrap codex \\
    --port "\${HEADROOM_LOCAL_PROXY_PORT:-8787}" \\
    --no-context-tool --no-mcp --no-serena -- "\$@"
}

tcodex() {
  local session="\${TCODEX_SESSION:-codex}"
  local shell_path="\${SHELL:-/bin/sh}"
  local cmd="codex"
  local arg
  for arg in "\$@"; do
    cmd+=" \$(printf "%q" "\${arg}")"
  done
  tmux new-session -A -s "\${session}" "\$(printf "%q" "\${shell_path}") -ic \$(printf "%q" "\${cmd}")"
}
# <<< headroom local proxy <<<
EOF
}

strip_managed_wrapper_blocks() {
  local input_file="$1"
  local output_file="$2"

  awk '
    $0 == "# >>> headroom local proxy >>>" { skipping = 1; next }
    $0 == "# <<< headroom local proxy <<<" { skipping = 0; next }
    $0 == "# >>> headroom local claude proxy >>>" { skipping = 1; next }
    $0 == "# <<< headroom local claude proxy <<<" { skipping = 0; next }
    skipping != 1 { print }
  ' "${input_file}" >"${output_file}"
}

patch_rc_file() {
  local rc_file="$1"
  local tmp_file

  info "Installing shell wrapper block into ${rc_file}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    wrapper_block
    return
  fi

  mkdir -p "$(dirname "${rc_file}")"
  touch "${rc_file}"
  tmp_file="$(mktemp "${rc_file}.XXXXXX")"
  strip_managed_wrapper_blocks "${rc_file}" "${tmp_file}"
  {
    cat "${tmp_file}"
    printf '\n'
    wrapper_block
  } >"${rc_file}"
  rm -f "${tmp_file}"
}

patch_shell_rcs() {
  local rc_file
  if [[ "${PATCH_SHELL}" -ne 1 ]]; then
    info "Skipping shell rc modification"
    return
  fi
  validate_existing_venv_path_if_present
  while IFS= read -r rc_file; do
    [[ -n "${rc_file}" ]] || continue
    patch_rc_file "${rc_file}"
  done < <(rc_files_for_target)
}

print_next_steps() {
  cat <<EOF

Done.

Reload your shell:
  source ~/.zshrc   # zsh
  source ~/.bashrc  # bash

Test the proxy wrapper:
  type claude
  claude -p "Reply with exactly: proxy-ok"
  type codex
  codex "Reply with exactly: proxy-ok"

Start/reuse a tmux Claude session:
  tclaude --dangerously-skip-permissions --resume

Start/reuse a tmux Codex session:
  tcodex

Security reminders:
  - Keep Headroom bound to 127.0.0.1.
  - Keep HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true.
  - Do not store API keys in shell rc files.
EOF
}

main() {
  if [[ "${SKIP_PREREQ_CHECK}" -ne 1 ]]; then
    check_prereqs
  fi
  validate_existing_venv_path_if_present
  install_claude_if_missing
  install_codex_if_missing
  install_python_env
  patch_shell_rcs
  print_next_steps
}

main
