# Function: Provide memdir path and project identity utilities.
# Purpose: Let CLI, hook, and notify paths share the same project key and file path rules.
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import unicodedata

PROJECT_MARKERS = (
    "AGENTS.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Makefile",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "Cargo.toml",
)


def canonicalize_existing_path(raw_path: str | os.PathLike[str]) -> pathlib.Path:
    path = pathlib.Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (pathlib.Path.cwd() / path).resolve(strict=False)
    else:
        path = path.resolve(strict=False)

    current = pathlib.Path(path.anchor or "/")
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        next_path = current / part
        if not current.exists() or not current.is_dir():
            current = next_path
            continue
        try:
            actual = next(
                (child.name for child in current.iterdir() if child.name.casefold() == part.casefold()),
                part,
            )
        except OSError:
            actual = part
        current = current / actual
    return current


def detect_project_root(raw_cwd: str | os.PathLike[str] | None = None) -> pathlib.Path:
    current = canonicalize_existing_path(raw_cwd or os.getcwd())
    nearest_marker_root: pathlib.Path | None = None
    nearest_git_root: pathlib.Path | None = None
    for candidate in [current, *current.parents]:
        if nearest_marker_root is None and any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            nearest_marker_root = candidate
        if nearest_git_root is None and (candidate / ".git").exists():
            nearest_git_root = candidate

    if nearest_marker_root is not None:
        return canonicalize_existing_path(nearest_marker_root)
    if nearest_git_root is None:
        return current
    if nearest_git_root == current:
        return canonicalize_existing_path(nearest_git_root)

    try:
        relative = current.relative_to(nearest_git_root)
    except ValueError:
        return current
    if not relative.parts:
        return canonicalize_existing_path(nearest_git_root)
    return canonicalize_existing_path(nearest_git_root / relative.parts[0])


def project_slug(project_root: pathlib.Path) -> str:
    project_root = canonicalize_existing_path(project_root)
    project_name = unicodedata.normalize("NFC", project_root.name or "root")
    base = re.sub(r"[^\w._-]+", "-", project_name, flags=re.UNICODE).strip("-")
    digest = hashlib.sha1(str(project_root).encode("utf-8")).hexdigest()[:8]
    return f"{base or 'root'}-{digest}"


def session_id() -> str:
    for key in ("CODEX_SESSION_ID", "OPENAI_SESSION_ID", "CODEX_RUN_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return f"manual-{os.getpid()}"


def is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
