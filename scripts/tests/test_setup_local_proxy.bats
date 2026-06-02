#!/usr/bin/env bats

ROOT="$(cd "${BATS_TEST_DIRNAME}/../.." && pwd)"
SCRIPT="${ROOT}/scripts/setup-local-proxy.sh"

setup() {
  TEST_HOME="${BATS_TEST_TMPDIR}/home"
  mkdir -p "${TEST_HOME}"
}

write_executable() {
  local path="$1"
  local content="$2"

  mkdir -p "$(dirname "${path}")"
  printf '%s\n' "${content}" >"${path}"
  chmod +x "${path}"
}

run_setup() {
  env HOME="${TEST_HOME}" SHELL="/bin/zsh" \
    bash "${SCRIPT}" \
      --skip-prereq-check \
      --skip-python-install \
      --no-install-claude \
      --no-install-codex \
      "$@"
}

@test "setup script has valid bash syntax" {
  run bash -n "${SCRIPT}"

  [ "${status}" -eq 0 ]
}

@test "setup script uses renamed path" {
  [ -x "${ROOT}/scripts/setup-local-proxy.sh" ]
  [ ! -e "${ROOT}/scripts/setup-local-claude-proxy.sh" ]
}

@test "finds cargo from default rustup home when PATH was not reloaded" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local cargo_bin="${TEST_HOME}/.cargo/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${cargo_bin}"
  write_executable "${fake_bin}/dirname" '#!/bin/sh
case "$1" in */*) printf "%s\n" "${1%/*}" ;; *) printf ".\n" ;; esac'
  write_executable "${fake_bin}/pwd" '#!/bin/sh
/bin/pwd "$@"'
  write_executable "${fake_bin}/git" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/python3" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/npm" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cat" '#!/bin/sh
/bin/cat "$@"'
  write_executable "${cargo_bin}/cargo" '#!/bin/sh
exit 0'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}" \
    "${bash_bin}" "${SCRIPT}" \
      --no-install-claude \
      --no-install-codex \
      --skip-python-install \
      --no-shell

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Added ${cargo_bin} to PATH for Rust/cargo"* ]]
}

@test "rejects Python 3.14 with macOS guidance" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/dirname" '#!/bin/sh
case "$1" in */*) printf "%s\n" "${1%/*}" ;; *) printf ".\n" ;; esac'
  write_executable "${fake_bin}/pwd" '#!/bin/sh
/bin/pwd "$@"'
  write_executable "${fake_bin}/uname" '#!/bin/sh
printf "Darwin\n"'
  write_executable "${fake_bin}/git" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/python3" '#!/bin/sh
exit 1'
  write_executable "${fake_bin}/cargo" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cat" '#!/bin/sh
/bin/cat "$@"'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}" \
    "${bash_bin}" "${SCRIPT}" --python python3 --skip-python-install --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"brew install git python@3.13 rust node tmux"* ]]
  [[ "${output}" == *"python3 is not supported. Use Python 3.10-3.13"* ]]
}

@test "recreates existing venv with unsupported Python" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local venv_path="${BATS_TEST_TMPDIR}/.venv-headroom"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${venv_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${venv_path}/bin/python" '#!/bin/sh
case "$*" in
  *base_prefix*) exit 1 ;;
  *) exit 1 ;;
esac'
  touch "${venv_path}/pyvenv.cfg"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${venv_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Existing Python venv uses unsupported Python"* ]]
  [[ "${output}" == *"+ rm -rf ${venv_path}"* ]]
  [[ "${output}" == *"+ python3.13 -m venv ${venv_path}"* ]]
}

@test "refuses to recreate unsafe venv paths" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${TEST_HOME}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${TEST_HOME}/bin/python" '#!/bin/sh
exit 0'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv . --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate unsafe Python venv path"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${TEST_HOME}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate unsafe Python venv path"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv / --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate unsafe Python venv path"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]
}

@test "refuses to recreate existing non-venv directories" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local venv_path="${BATS_TEST_TMPDIR}/custom-dir"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${venv_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${venv_path}/bin/python" '#!/bin/sh
exit 1'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${venv_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"not a recognized virtualenv"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
}

@test "refuses existing non-venv even when bin python looks compatible" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local venv_path="${BATS_TEST_TMPDIR}/fake-compatible"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${venv_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${venv_path}/bin/python" '#!/bin/sh
exit 0'
  touch "${venv_path}/pyvenv.cfg"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${venv_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"bin/python does not report a virtualenv rooted at that path"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]
}

@test "reuses existing venv only when bin python reports matching venv root" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local venv_path="${BATS_TEST_TMPDIR}/real-venv"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${venv_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${venv_path}/bin/python" '#!/bin/sh
if [ "$#" -ge 2 ]; then
  printf "%s\n" HEADROOM_VENV_OK
fi
exit 0'
  touch "${venv_path}/pyvenv.cfg"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" \
      --venv "${venv_path}" \
      --skip-prereq-check \
      --skip-python-install \
      --no-install-claude \
      --no-install-codex \
      --dry-run \
      --no-shell

  [ "${status}" -eq 0 ]
}

@test "refuses to recreate non-empty .venv-headroom without venv marker" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local venv_path="${TEST_HOME}/.venv-headroom"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${venv_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${venv_path}/bin/python" '#!/bin/sh
exit 1'
  touch "${venv_path}/important-file"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${venv_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"not a recognized virtualenv"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
}

@test "refuses symlinked .venv-headroom without venv marker" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local link_path="${TEST_HOME}/.venv-headroom"
  local target_path="${BATS_TEST_TMPDIR}/linked-target"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${target_path}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${target_path}/bin/python" '#!/bin/sh
exit 0'
  ln -s "${target_path}" "${link_path}"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${link_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"symlinked venv paths are not supported"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]
}

@test "refuses broken .venv-headroom symlink" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local link_path="${TEST_HOME}/.venv-headroom"
  local target_path="${BATS_TEST_TMPDIR}/missing-target"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  ln -s "${target_path}" "${link_path}"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${link_path}" --skip-prereq-check --dry-run --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate Python venv because the existing path is not a directory"* ]]
  [[ "${output}" != *"+ rm -rf"* ]]
  [[ "${output}" != *"+ python3.13 -m venv"* ]]
}

@test "skip python install still refuses unsafe wrapper venv path" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${TEST_HOME}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${TEST_HOME}/bin/python" '#!/bin/sh
exit 0'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" \
      --venv "${TEST_HOME}" \
      --skip-prereq-check \
      --skip-python-install \
      --no-install-claude \
      --no-install-codex \
      --dry-run \
      --shell zsh

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate unsafe Python venv path"* ]]
  [[ "${output}" != *"HEADROOM_LOCAL_PROXY_PYTHON"* ]]
}

@test "skip python install refuses broken symlink wrapper venv path" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local link_path="${TEST_HOME}/.venv-headroom"
  local target_path="${BATS_TEST_TMPDIR}/missing-target"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  ln -s "${target_path}" "${link_path}"

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" \
      --venv "${link_path}" \
      --skip-prereq-check \
      --skip-python-install \
      --no-install-claude \
      --no-install-codex \
      --dry-run \
      --shell zsh

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate Python venv because the existing path is not a directory"* ]]
  [[ "${output}" != *"HEADROOM_LOCAL_PROXY_PYTHON"* ]]
}

@test "installs hardened zsh wrapper idempotently" {
  run run_setup --shell zsh --port 9999
  [ "${status}" -eq 0 ]

  run run_setup --shell zsh --port 9999
  [ "${status}" -eq 0 ]

  local zshrc="${TEST_HOME}/.zshrc"
  [ "$(grep -c '# >>> headroom local proxy >>>' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '# <<< headroom local proxy <<<' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^claude() {' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^tclaude() {' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^codex() {' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^tcodex() {' "${zshrc}")" -eq 1 ]
  grep -q 'HEADROOM_MODE=token' "${zshrc}"
  grep -q 'HEADROOM_HOST=127.0.0.1' "${zshrc}"
  grep -q 'HEADROOM_TELEMETRY=off' "${zshrc}"
  grep -q 'HEADROOM_LOCAL_CONTROL_GUARD_ENABLED=true' "${zshrc}"
  grep -q 'export HEADROOM_LOCAL_PROXY_PORT=9999' "${zshrc}"
  grep -q -- '--port "${HEADROOM_LOCAL_PROXY_PORT:-8787}"' "${zshrc}"
  grep -q -- '--no-context-tool --no-mcp --no-serena' "${zshrc}"
  grep -q -- '-m headroom.cli wrap claude' "${zshrc}"
  grep -q -- '-m headroom.cli wrap codex' "${zshrc}"
  grep -q 'tmux new-session -A -s' "${zshrc}"
  grep -q ' -ic ' "${zshrc}"
  ! grep -q '0.0.0.0' "${zshrc}"
}

@test "can patch bash and zsh wrappers" {
  run run_setup --shell both

  [ "${status}" -eq 0 ]
  for rc_file in "${TEST_HOME}/.zshrc" "${TEST_HOME}/.bashrc"; do
    grep -q 'claude()' "${rc_file}"
    grep -q 'tclaude()' "${rc_file}"
    grep -q 'codex()' "${rc_file}"
    grep -q 'tcodex()' "${rc_file}"
    grep -q 'HEADROOM_LOCAL_PROXY_PYTHON' "${rc_file}"
  done
}

@test "replaces legacy claude-only marker block" {
  local zshrc="${TEST_HOME}/.zshrc"
  cat >"${zshrc}" <<'RC'
before
# >>> headroom local claude proxy >>>
claude() {
  echo old
}
# <<< headroom local claude proxy <<<
after
RC

  run run_setup --shell zsh --port 9999

  [ "${status}" -eq 0 ]
  [ "$(grep -c '# >>> headroom local claude proxy >>>' "${zshrc}")" -eq 0 ]
  [ "$(grep -c '# >>> headroom local proxy >>>' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^claude() {' "${zshrc}")" -eq 1 ]
  [ "$(grep -c '^codex() {' "${zshrc}")" -eq 1 ]
  grep -q '^before$' "${zshrc}"
  grep -q '^after$' "${zshrc}"
}

@test "shell wrapper exports quote path values safely" {
  local pwned="${BATS_TEST_TMPDIR}/pwned"
  local venv_path="${BATS_TEST_TMPDIR}/venv \$(touch ${pwned}) \"quoted\""
  local zshrc="${TEST_HOME}/.zshrc"

  run run_setup --shell zsh --venv "${venv_path}" --port 9999

  [ "${status}" -eq 0 ]
  [ ! -e "${pwned}" ]

  run bash -c 'source "$1"; printf "%s\n" "$HEADROOM_LOCAL_PROXY_PYTHON"' _ "${zshrc}"

  [ "${status}" -eq 0 ]
  [ "${output}" = "${venv_path}/bin/python" ]
  [ ! -e "${pwned}" ]
}

@test "tmux wrappers quote runtime shell path safely" {
  local pwned="${BATS_TEST_TMPDIR}/pwned"
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local tmux_args="${BATS_TEST_TMPDIR}/tmux-args"
  local zshrc="${TEST_HOME}/.zshrc"

  run run_setup --shell zsh --port 9999
  [ "${status}" -eq 0 ]

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/tmux" '#!/bin/sh
{
  printf "%s\n" "$@"
} >> "${TMUX_ARGS_FILE}"'

  run env PATH="${fake_bin}:${PATH}" TMUX_ARGS_FILE="${tmux_args}" \
    SHELL="/bin/sh \$(touch ${pwned})" \
    bash -c 'source "$1"; tclaude "hello world"; tcodex "hello world"' _ "${zshrc}"

  [ "${status}" -eq 0 ]
  [ ! -e "${pwned}" ]
  [ "$(grep -Fc '\$\(touch' "${tmux_args}")" -eq 2 ]
  ! grep -Fq '$(touch' "${tmux_args}"
}

@test "dry run installs missing Claude Code and Codex CLI by default" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/dirname" '#!/bin/sh
case "$1" in */*) printf "%s\n" "${1%/*}" ;; *) printf ".\n" ;; esac'
  write_executable "${fake_bin}/pwd" '#!/bin/sh
/bin/pwd "$@"'
  write_executable "${fake_bin}/git" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cargo" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/npm" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cat" '#!/bin/sh
/bin/cat "$@"'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}" \
    "${bash_bin}" "${SCRIPT}" --skip-python-install --dry-run --no-shell

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Installing Claude Code with npm"* ]]
  [[ "${output}" == *"+ npm install -g @anthropic-ai/claude-code"* ]]
  [[ "${output}" == *"Installing Codex CLI with npm"* ]]
  [[ "${output}" == *"+ npm install -g @openai/codex"* ]]
}

@test "fails when npm install leaves Claude Code off PATH" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/dirname" '#!/bin/sh
case "$1" in */*) printf "%s\n" "${1%/*}" ;; *) printf ".\n" ;; esac'
  write_executable "${fake_bin}/pwd" '#!/bin/sh
/bin/pwd "$@"'
  write_executable "${fake_bin}/git" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cargo" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/npm" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cat" '#!/bin/sh
/bin/cat "$@"'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}" \
    "${bash_bin}" "${SCRIPT}" --skip-python-install --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"claude is still not on PATH after installing @anthropic-ai/claude-code"* ]]
}

@test "refuses unsafe venv before npm install side effects" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local npm_called="${BATS_TEST_TMPDIR}/npm-called"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}" "${TEST_HOME}/bin"
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/npm" "#!/bin/sh
touch '${npm_called}'
exit 0"
  write_executable "${TEST_HOME}/bin/python" '#!/bin/sh
exit 0'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}:${PATH}" \
    "${bash_bin}" "${SCRIPT}" --venv "${TEST_HOME}" --skip-prereq-check --skip-python-install --no-shell

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Refusing to recreate unsafe Python venv path"* ]]
  [ ! -e "${npm_called}" ]
}

@test "can opt out of automatic CLI installs" {
  local fake_bin="${BATS_TEST_TMPDIR}/bin"
  local bash_bin
  bash_bin="$(command -v bash)"

  mkdir -p "${fake_bin}"
  write_executable "${fake_bin}/dirname" '#!/bin/sh
case "$1" in */*) printf "%s\n" "${1%/*}" ;; *) printf ".\n" ;; esac'
  write_executable "${fake_bin}/pwd" '#!/bin/sh
/bin/pwd "$@"'
  write_executable "${fake_bin}/git" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/python3.13" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cargo" '#!/bin/sh
exit 0'
  write_executable "${fake_bin}/cat" '#!/bin/sh
/bin/cat "$@"'

  run env HOME="${TEST_HOME}" SHELL="/bin/bash" PATH="${fake_bin}" \
    "${bash_bin}" "${SCRIPT}" \
      --no-install-claude \
      --no-install-codex \
      --skip-python-install \
      --dry-run \
      --no-shell

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Skipping Claude Code install"* ]]
  [[ "${output}" == *"Skipping Codex CLI install"* ]]
  [[ "${output}" != *"+ npm install -g"* ]]
}
