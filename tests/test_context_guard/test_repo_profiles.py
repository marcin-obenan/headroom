"""Unit tests: stack-profile path classification (ai-rules#79)."""

from __future__ import annotations

import pytest

from headroom.proxy.context_guard import classify_path


@pytest.mark.parametrize(
    "path,expected_class",
    [
        # Java
        ("target/classes/App.class", "build"),
        ("build/libs/app.jar", "build"),
        ("service/target/foo.txt", "build"),
        ("Main.class", "build"),
        # Node / JS / TS
        ("node_modules/react/index.js", "dependency"),
        ("dist/bundle.js", "build"),
        ("app.min.js", "generated"),
        ("vendor.bundle.js", "generated"),
        ("src/types.generated.ts", "generated"),
        ("pnpm-lock.yaml", "lockfile"),
        ("yarn.lock", "lockfile"),
        ("package-lock.json", "lockfile"),
        # Python
        (".venv/lib/python3.13/site-packages/x.py", "dependency"),
        ("__pycache__/mod.cpython-313.pyc", "build"),
        (".pytest_cache/v/cache/lastfailed", "build"),
        ("uv.lock", "lockfile"),
        ("poetry.lock", "lockfile"),
        # Generic
        ("vendor/github.com/x/y.go", "dependency"),
        ("test/fixtures/big.json", "fixture"),
        ("__snapshots__/Comp.test.tsx.snap", "fixture"),
        ("component.snap", "fixture"),
    ],
)
def test_classifies_known_artifacts(path: str, expected_class: str) -> None:
    m = classify_path(path)
    assert m is not None, f"{path} should be classified"
    assert m.artifact_class == expected_class


@pytest.mark.parametrize(
    "path",
    ["src/app.ts", "lib/service/billing.py", "cmd/main.go", "README.md", "app/Controller.java"],
)
def test_real_source_is_not_flagged(path: str) -> None:
    assert classify_path(path) is None


def test_allowlist_exempts_a_path() -> None:
    assert classify_path("node_modules/react/index.js") is not None
    assert classify_path("node_modules/react/index.js", allowlist=("node_modules/**",)) is None


def test_empty_path_returns_none() -> None:
    assert classify_path("") is None
