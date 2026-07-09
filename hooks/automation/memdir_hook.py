#!/usr/bin/env python3
# Function: Provide cross-platform Codex hook entrypoints for project-memdir.
# Purpose: Keep hook JSON behavior consistent behind OS-specific launchers.
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any


def _force_utf8_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parents[1]
NOTIFY_DIR = PLUGIN_ROOT / "scripts" / "notify"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


def _continue_payload(*, suppress_output: bool = True) -> dict[str, Any]:
    return {"continue": True, "suppressOutput": suppress_output}


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _read_json_payload() -> dict[str, Any]:
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        return {}
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_cwd(payload: dict[str, Any]) -> str:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd else os.getcwd()


def _refresh_scheduler_if_available() -> None:
    scheduler_cli = SCRIPT_DIR / "scheduler_cli.py"
    if not scheduler_cli.exists():
        return
    subprocess.run(
        [sys.executable, str(scheduler_cli), "sync", "--refresh-only"],
        cwd=str(PLUGIN_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _emit_skip(reason: str, *, suppress_output: bool = True) -> None:
    print(f"[memdir_hook] skipped: {reason}", file=sys.stderr)
    _emit(_continue_payload(suppress_output=suppress_output))


def _session_start() -> int:
    if os.environ.get("CODEX_MEMDIR_SKIP") == "1" or os.environ.get("CODEX_HARNESS_SKIP_SESSION_START") == "1":
        _emit(_continue_payload())
        return 0

    payload = _read_json_payload()
    cwd = _payload_cwd(payload)
    _refresh_scheduler_if_available()
    try:
        from harness_lib.settings import ensure_user_harness_config  # noqa: PLC0415

        config_init = ensure_user_harness_config()
        if config_init.get("created"):
            print(f"[memdir_session_start] created user config: {config_init.get('path')}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[memdir_session_start] config init skipped: {type(exc).__name__}: {exc}", file=sys.stderr)

    try:
        from harness_lib.memdir import build_session_start_context  # noqa: PLC0415

        context = build_session_start_context(cwd)
    except Exception as exc:  # noqa: BLE001
        _emit_skip(f"session-start unavailable: {type(exc).__name__}: {exc}", suppress_output=True)
        return 0

    additional_context = context.get("additionalContext") if isinstance(context, dict) else ""
    if not isinstance(additional_context, str) or not additional_context.strip():
        _emit(_continue_payload())
        return 0

    embedding_model = context.get("embeddingModel")
    clean_embedding_model = ""
    if isinstance(embedding_model, str):
        clean_embedding_model = embedding_model.strip()
    if clean_embedding_model:
        print(f"[memdir_session_start] embedding={clean_embedding_model}", file=sys.stderr)

    hook_specific_output = {
        "hookEventName": "SessionStart",
        "additionalContext": additional_context,
    }

    _emit(
        {
            "continue": True,
            "suppressOutput": False,
            "hookSpecificOutput": hook_specific_output,
        }
    )
    return 0


def _user_prompt_submit() -> int:
    if os.environ.get("CODEX_MEMDIR_SKIP") == "1":
        _emit(_continue_payload())
        return 0

    payload = _read_json_payload()
    if not payload:
        _emit(_continue_payload())
        return 0

    cwd = payload.get("cwd")
    user_prompt = payload.get("user_prompt") or payload.get("prompt") or ""
    if not isinstance(cwd, str) or not isinstance(user_prompt, str) or not user_prompt.strip():
        _emit(_continue_payload())
        return 0

    try:
        from harness_lib.memdir import build_memdir_context, is_memdir_enabled, record_user_prompt_submit  # noqa: PLC0415

        if not is_memdir_enabled(cwd):
            _emit(_continue_payload())
            return 0
        record_user_prompt_submit(
            cwd,
            user_prompt=user_prompt,
            turn_id=str(payload.get("turn_id") or ""),
            session_id=str(payload.get("session_id") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        _emit_skip(f"user-prompt-submit unavailable: {type(exc).__name__}: {exc}")
        return 0

    try:
        recall = build_memdir_context(user_prompt, cwd, include_core_paths=False)
    except Exception as exc:  # noqa: BLE001
        _emit_skip(f"user-prompt-submit unavailable: {type(exc).__name__}: {exc}")
        return 0

    additional_context = recall.get("system_message") if isinstance(recall, dict) else ""
    if not isinstance(additional_context, str) or not additional_context.strip():
        _emit(_continue_payload())
        return 0

    _emit(
        {
            "continue": True,
            "suppressOutput": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": additional_context,
            },
        }
    )
    return 0


def _stop() -> int:
    if os.environ.get("CODEX_MEMDIR_SKIP") == "1":
        return 0

    try:
        os.environ.setdefault("PROJECT_MEMDIR_CLIENT", "codex")
        if str(NOTIFY_DIR) not in sys.path:
            sys.path.insert(0, str(NOTIFY_DIR))
        import memdir_stop  # noqa: PLC0415

        return int(memdir_stop.main())
    except Exception as exc:  # noqa: BLE001
        print(f"[memdir_hook] stop failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if command == "session-start":
            return _session_start()
        if command == "user-prompt-submit":
            return _user_prompt_submit()
        if command == "stop":
            return _stop()
    except Exception as exc:  # noqa: BLE001
        print(f"[memdir_hook] skipped: {command or 'unknown'} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if command in {"session-start", "user-prompt-submit"}:
            _emit(_continue_payload())
            return 0
        if command == "stop":
            return 1
        return 0
    print(f"[memdir_hook] unknown command: {command}", file=sys.stderr)
    _emit(_continue_payload())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
