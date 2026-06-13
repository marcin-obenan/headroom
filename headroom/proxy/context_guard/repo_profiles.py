"""Repo-aware artifact detection for the Context Guard (ai-rules#79).

Classifies a file path into an artifact class (dependency / build / generated /
test / cache / lockfile / fixture) using per-stack profiles. Matching is glob-
style on the *relative* path; no filesystem access is required (the path comes
from request content such as a ``read_file`` tool result).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

# Artifact class -> the reason-code class string used by reason_codes.ARTIFACT_REASON_BY_CLASS
ArtifactClass = str


@dataclass(frozen=True)
class StackProfile:
    name: str
    dependency_artifacts: tuple[str, ...] = ()
    build_artifacts: tuple[str, ...] = ()
    generated_artifacts: tuple[str, ...] = ()
    test_artifacts: tuple[str, ...] = ()
    cache_artifacts: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()
    fixture_artifacts: tuple[str, ...] = ()


JAVA = StackProfile(
    name="java",
    build_artifacts=(
        "target/**",
        "build/**",
        ".gradle/**",
        ".mvn/wrapper/**",
        "*.class",
        "*.jar",
        "*.war",
        "*.ear",
    ),
)

NODE = StackProfile(
    name="node",
    dependency_artifacts=("node_modules/**",),
    build_artifacts=("dist/**", "build/**", ".next/**", "coverage/**"),
    generated_artifacts=("*.map", "*.generated.*", "*.min.js", "*.bundle.js"),
    lockfiles=("*.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
)

PYTHON = StackProfile(
    name="python",
    dependency_artifacts=(".venv/**", "venv/**"),
    build_artifacts=("dist/**", "build/**", "*.egg-info/**"),
    cache_artifacts=("__pycache__/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**"),
    lockfiles=("uv.lock", "poetry.lock", "Pipfile.lock"),
)

GENERIC = StackProfile(
    name="generic",
    dependency_artifacts=("vendor/**",),
    test_artifacts=("fixtures/**", "test/fixtures/**"),
    cache_artifacts=(".git/**", "*.log", "*.tmp"),
    fixture_artifacts=("snapshots/**", "__snapshots__/**", "*.snap"),
)

DEFAULT_PROFILES: tuple[StackProfile, ...] = (JAVA, NODE, PYTHON, GENERIC)


# (profile attribute, artifact class) — order matters: most specific first.
_CLASS_ATTRS: tuple[tuple[str, ArtifactClass], ...] = (
    ("lockfiles", "lockfile"),
    ("generated_artifacts", "generated"),
    ("dependency_artifacts", "dependency"),
    ("build_artifacts", "build"),
    ("fixture_artifacts", "fixture"),
    ("test_artifacts", "fixture"),
    ("cache_artifacts", "build"),
)


def _norm(path: str) -> str:
    """Normalise to a forward-slash relative path WITHOUT eating leading dots.

    ``lstrip("./")`` would strip the leading dot of dotdirs (``.pytest_cache``),
    so we only peel a literal ``./`` prefix and leading slashes.
    """
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _matches(path: str, pattern: str) -> bool:
    p = _norm(path)
    # `dir/**` matches the directory rooted OR nested at any depth, so a
    # multi-module `service/target/x` matches `target/**`.
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return p == prefix or p.startswith(prefix + "/") or ("/" + p).find("/" + prefix + "/") != -1
    # plain glob on the basename OR the full relative path
    return fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], pattern)


@dataclass(frozen=True)
class ArtifactMatch:
    path: str
    artifact_class: ArtifactClass
    profile: str
    pattern: str


def classify_path(
    path: str,
    profiles: tuple[StackProfile, ...] = DEFAULT_PROFILES,
    allowlist: tuple[str, ...] = (),
) -> ArtifactMatch | None:
    """Classify ``path`` into an artifact class, or ``None`` if it's normal source.

    ``allowlist`` patterns short-circuit to ``None`` (never flagged).
    """
    if not path:
        return None
    if any(_matches(path, pat) for pat in allowlist):
        return None
    for profile in profiles:
        for attr, klass in _CLASS_ATTRS:
            for pattern in getattr(profile, attr):
                if _matches(path, pattern):
                    return ArtifactMatch(
                        path=path, artifact_class=klass, profile=profile.name, pattern=pattern
                    )
    return None
