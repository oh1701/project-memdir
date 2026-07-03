#!/usr/bin/env python3
# Function: Route notify events to the memdir extraction path.
# Purpose: Run only memdir updates safely on agent-turn-complete.
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
MEMDIR_SCRIPT = ROOT / "scripts" / "notify" / "memdir_notify.py"


def _run(script: pathlib.Path, event_json: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), event_json],
        text=True,
        capture_output=True,
        check=False,
    )


def _emit(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)


def main() -> int:
    if os.environ.get("CODEX_MEMDIR_SKIP") == "1":
        return 0
    if len(sys.argv) < 2:
        return 1

    event_json = sys.argv[1]

    memdir_result = _run(MEMDIR_SCRIPT, event_json)
    _emit(memdir_result)

    if memdir_result.returncode != 0:
        return memdir_result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
