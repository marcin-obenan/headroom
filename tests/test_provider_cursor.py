from __future__ import annotations

from headroom.providers.cursor import (
    build_launch_env,
    build_proxy_targets,
    render_setup_lines,
)
from headroom.providers.cursor.install import build_install_env


def test_cursor_build_launch_env_sets_proxy_urls_without_mutating_input() -> None:
    source_env = {"EXISTING": "value"}

    env, lines = build_launch_env(port=9999, environ=source_env)

    # input dict is not mutated
    assert source_env == {"EXISTING": "value"}
    assert env["EXISTING"] == "value"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert lines == [
        "OPENAI_BASE_URL=http://127.0.0.1:9999/v1",
        "ANTHROPIC_BASE_URL=http://127.0.0.1:9999",
    ]


def test_cursor_build_launch_env_applies_project_path_prefix() -> None:
    env, lines = build_launch_env(port=9999, environ={}, project="my repo")

    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999/p/my%20repo/v1"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999/p/my%20repo"
    assert lines == [
        "OPENAI_BASE_URL=http://127.0.0.1:9999/p/my%20repo/v1",
        "ANTHROPIC_BASE_URL=http://127.0.0.1:9999/p/my%20repo",
    ]


def test_cursor_build_launch_env_ignores_unusable_project() -> None:
    env, _lines = build_launch_env(port=9999, environ={}, project="   ")

    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"


def test_cursor_proxy_targets_use_local_headroom_proxy() -> None:
    targets = build_proxy_targets(9999)

    assert targets.openai_base_url == "http://127.0.0.1:9999/v1"
    assert targets.anthropic_base_url == "http://127.0.0.1:9999"


def test_cursor_setup_lines_include_both_provider_urls() -> None:
    lines = render_setup_lines(8787)
    joined = "\n".join(lines)

    assert "http://127.0.0.1:8787/v1" in joined
    assert "http://127.0.0.1:8787" in joined


def test_cursor_build_install_env_returns_both_proxy_urls() -> None:
    # Arrange / Act
    env = build_install_env(port=7654, backend="ignored")

    # Assert
    assert env == {
        "OPENAI_BASE_URL": "http://127.0.0.1:7654/v1",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:7654",
    }


def test_cursor_proxy_targets_apply_project_path_prefix() -> None:
    targets = build_proxy_targets(9999, project="frontend")

    assert targets.openai_base_url == "http://127.0.0.1:9999/p/frontend/v1"
    assert targets.anthropic_base_url == "http://127.0.0.1:9999/p/frontend"


def test_cursor_setup_lines_mention_project_attribution() -> None:
    lines = render_setup_lines(8787, project="frontend")
    joined = "\n".join(lines)

    assert "http://127.0.0.1:8787/p/frontend/v1" in joined
    assert "attributed to project 'frontend'" in joined

    plain = "\n".join(render_setup_lines(8787))
    assert "attributed" not in plain
