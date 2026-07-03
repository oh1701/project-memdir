# Function: Provide a shared runner for calling codex exec from memdir extractors.
# Purpose: Reuse the same subrun rules when delegating topic JSON extraction to Codex.
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any


def build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEX_HARNESS_SKIP_SESSION_START"] = "1"
    env["CODEX_SESSION_MAINTENANCE_AUTO"] = "0"
    if extra:
        env.update(extra)
    return env


def _no_window_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


def run_codex_exec(
    *,
    codex_bin: str,
    cwd: pathlib.Path,
    prompt: str,
    output_last_message: pathlib.Path | None = None,
    output_schema: pathlib.Path | None = None,
    stdout_path: pathlib.Path | None = None,
    stderr_path: pathlib.Path | None = None,
    sandbox: str = "read-only",
    model: str | None = None,
    extra_config: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout_sec: int | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd: list[str] = [
        codex_bin,
        "exec",
        "--json",
        "--ephemeral",
        "--disable",
        "hooks",
        "--skip-git-repo-check",
        "-C",
        str(cwd),
        "--sandbox",
        sandbox,
    ]
    if model:
        cmd.extend(["-m", model])
    for item in extra_config or []:
        cmd.extend(["-c", item])
    if output_schema is not None:
        cmd.extend(["--output-schema", str(output_schema)])
    if output_last_message is not None:
        cmd.extend(["--output-last-message", str(output_last_message)])
    cmd.append(prompt)

    stdout_handle = None
    stderr_handle = None
    try:
        if stdout_path is not None:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_path.open("w", encoding="utf-8")
        if stderr_path is not None:
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            stderr_handle = stderr_path.open("w", encoding="utf-8")
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=build_env(extra_env),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=stdout_handle or subprocess.PIPE,
            stderr=stderr_handle or subprocess.PIPE,
            timeout=timeout_sec,
            check=False,
            **_no_window_kwargs(),
        )
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
