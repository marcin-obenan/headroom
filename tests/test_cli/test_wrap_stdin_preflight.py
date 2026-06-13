"""Wrap preflight: piped stdin is sized before the child CLI launches (ai-rules#79)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from headroom.cli import wrap as wrap_mod
from headroom.cli.main import main


@pytest.fixture(autouse=True)
def _restore_stdin_after_test() -> object:
    original = sys.stdin
    yield
    sys.stdin = original


class _FakeStdin:
    """Minimal stdin double with ``isatty()`` and a single consumable ``buffer``."""

    def __init__(self, data: bytes, *, tty: bool = False) -> None:
        self._tty = tty
        self.buffer = io.BytesIO(data)

    def isatty(self) -> bool:
        return self._tty


def test_capture_wrap_stdin_tty_leaves_stdin_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"", tty=True))
    assert wrap_mod._capture_wrap_stdin() == (None, None, False)


def test_capture_wrap_stdin_piped_replay_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"summarize this log output"
    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))
    replay, text, exceeded = wrap_mod._capture_wrap_stdin()
    assert exceeded is False
    assert replay == payload
    assert text == payload.decode("utf-8")


def test_capture_wrap_stdin_empty_pipe_inherits_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b""))
    assert wrap_mod._capture_wrap_stdin() == (None, None, False)


def test_capture_wrap_stdin_exactly_at_cap_not_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", "8")
    payload = b"12345678"
    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))
    replay, text, exceeded = wrap_mod._capture_wrap_stdin()
    assert exceeded is False
    assert replay == payload
    assert text == payload.decode("utf-8")


def test_capture_wrap_stdin_exceeds_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", "16")
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"x" * 32))
    replay, text, exceeded = wrap_mod._capture_wrap_stdin()
    assert exceeded is True
    assert replay is None
    assert len(text) == 16


def test_capture_wrap_stdin_cap_zero_blocks_any_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", "0")
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"x"))
    replay, _text, exceeded = wrap_mod._capture_wrap_stdin()
    assert exceeded is True
    assert replay is None


def test_wrap_stdin_max_bytes_invalid_env_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", "32M")
    assert wrap_mod._wrap_stdin_max_bytes() == wrap_mod._WRAP_STDIN_DEFAULT_MAX_BYTES
    assert "invalid HEADROOM_WRAP_STDIN_MAX_BYTES" in capsys.readouterr().err


def test_wrap_stdin_max_bytes_clamps_to_ceiling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", str(10**12))
    assert wrap_mod._wrap_stdin_max_bytes() == wrap_mod._WRAP_STDIN_ABSOLUTE_MAX_BYTES
    assert "clamping" in capsys.readouterr().err


def test_fake_stdin_buffer_is_single_stream() -> None:
    """Second read on the same buffer must not silently reset (regression guard)."""
    fake = _FakeStdin(b"hello")
    assert fake.buffer.read(2) == b"he"
    assert fake.buffer.read() == b"llo"


def test_guard_and_capture_stdin_byte_cap_emits_metrics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("HEADROOM_WRAP_STDIN_MAX_BYTES", "16")
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"x" * 32))

    with pytest.raises(SystemExit) as exc:
        wrap_mod._guard_and_capture_stdin("claude", ("summarize",))

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "rejection metrics" in err
    assert "stdin_bytes=" in err


def test_guard_and_capture_stdin_blocks_huge_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg_dir = tmp_path / ".headroom"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "contextGuard": {
                    "enabled": True,
                    "mode": "block",
                    "blockAtTokens": 50_000,
                    "allowCompressionInsteadOfBlock": False,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    big = " ".join(f"word{i}" for i in range(60_000))
    monkeypatch.setattr(sys, "stdin", _FakeStdin(big.encode("utf-8")))

    with pytest.raises(SystemExit) as exc:
        wrap_mod._guard_and_capture_stdin("claude", ("summarize",))

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "rejection metrics" in err
    assert "overshoot=" in err


def test_guard_and_capture_stdin_returns_replay_for_small_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / ".headroom"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"contextGuard": {"enabled": True, "mode": "block", "blockAtTokens": 500_000}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    payload = b"small stdin prompt"
    monkeypatch.setattr(sys, "stdin", _FakeStdin(payload))

    replay = wrap_mod._guard_and_capture_stdin("codex", ("run",))
    assert replay == payload


def test_guard_and_capture_stdin_fail_open_on_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(b"ok"))
    with patch(
        "headroom.proxy.context_guard.config.load_raw_config",
        side_effect=RuntimeError("boom"),
    ):
        replay = wrap_mod._guard_and_capture_stdin("claude", ("x",))
    assert replay == b"ok"


def test_wrap_claude_forwards_piped_stdin_to_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_dir = tmp_path / ".headroom"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"contextGuard": {"enabled": True, "mode": "block", "blockAtTokens": 500_000}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        captured["cmd"] = cmd
        captured["input"] = kwargs.get("input")
        return type("R", (), {"returncode": 0})()

    with patch("headroom.cli.wrap.shutil.which", return_value="/usr/bin/claude"):
        with patch("headroom.cli.wrap._ensure_proxy", return_value=object()):
            with patch("headroom.cli.wrap._make_cleanup", return_value=lambda: None):
                with patch("headroom.cli.wrap._register_proxy_client"):
                    with patch("headroom.cli.wrap.subprocess.run", side_effect=fake_run):
                        runner = CliRunner()
                        result = runner.invoke(
                            main,
                            [
                                "wrap",
                                "claude",
                                "--no-context-tool",
                                "--no-mcp",
                                "--no-serena",
                                "--",
                                "summarize",
                            ],
                            input="piped prompt body",
                        )

    assert result.exit_code == 0, result.output
    assert captured.get("input") == b"piped prompt body"
