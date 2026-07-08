# Function: Provide shared memdir file, time, and JSON utilities.
# Purpose: Let CLI, hook, and notify paths reuse common I/O logic.
from __future__ import annotations

import datetime as dt
import contextlib
import json
import os
import pathlib
import tempfile
import threading
from typing import Any, Iterator


def project_memdir_home() -> pathlib.Path:
    raw_home = os.environ.get("PROJECT_MEMDIR_HOME")
    if raw_home:
        return pathlib.Path(os.path.expandvars(raw_home)).expanduser()
    return pathlib.Path.home() / ".project-memdir"


def project_memdir_lock_path() -> pathlib.Path:
    return project_memdir_home() / ".lock"


_LOCK_STATE = threading.local()


@contextlib.contextmanager
def project_memdir_file_lock() -> Iterator[None]:
    depth = int(getattr(_LOCK_STATE, "depth", 0))
    if depth > 0:
        _LOCK_STATE.depth = depth + 1
        try:
            yield
        finally:
            _LOCK_STATE.depth -= 1
        return

    lock_path = project_memdir_lock_path()
    ensure_dir(lock_path.parent)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            if handle.tell() == 0 and lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                _LOCK_STATE.depth = 1
                yield
            finally:
                _LOCK_STATE.depth = 0
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                _LOCK_STATE.depth = 1
                yield
            finally:
                _LOCK_STATE.depth = 0
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_dir(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def atomic_write_text(path: pathlib.Path, content: str) -> None:
    with project_memdir_file_lock():
        ensure_dir(path.parent)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
            pathlib.Path(tmp_name).replace(path)
        finally:
            try:
                pathlib.Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass


def atomic_write_json(path: pathlib.Path, payload: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def append_jsonl(path: pathlib.Path, payload: dict[str, Any]) -> None:
    with project_memdir_file_lock():
        ensure_dir(path.parent)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
