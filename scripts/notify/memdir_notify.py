#!/usr/bin/env python3
# Function: Send agent-turn-complete notify events to the memdir extraction queue.
# Purpose: Queue memdir extraction jobs at turn completion and process them through a detached drain.
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
OBSERVATION_LOG = ROOT / "tasks" / "memdir-notify" / "notify-observations.jsonl"

if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib.memdir import is_memdir_enabled  # noqa: E402
from harness_lib.memdir_queue import enqueue_memdir_extraction_job  # noqa: E402


def _start_background_queue_drain(max_jobs: int = 20, *, owner_prefix: str = "notify-background") -> dict[str, Any]:
    command = [
        sys.executable,
        str(AUTOMATION_DIR / "memdir_cli.py"),
        "drain-queue",
        "--max-jobs",
        str(max(max_jobs, 0)),
        "--owner",
        f"{owner_prefix}-{os.getpid()}",
    ]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {
            **os.environ,
            "CODEX_MEMDIR_SKIP": "1",
            "CODEX_PROJECT_KNOWLEDGE_SKIP": "1",
            "CODEX_HARNESS_SKIP_SESSION_START": "1",
        },
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess,
            "DETACHED_PROCESS",
            0,
        )
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except OSError as exc:
        return {"started": False, "reason": "spawn_failed", "error": str(exc)}
    return {"started": True, "reason": "started", "pid": process.pid}


def _clean_text(value: str) -> str:
    return value.encode("utf-8", errors="replace").decode("utf-8")


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return " ".join(part for part in (_to_text(item).strip() for item in value) if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return _to_text(value["content"])
    try:
        return _clean_text(json.dumps(value, ensure_ascii=False))
    except TypeError:
        return _clean_text(str(value))


def _extract_latest_user_message(event: dict[str, Any]) -> str:
    for key in ("last-user-message", "last_user_message", "user-message", "user_message"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    input_messages = event.get("input-messages", [])
    if not isinstance(input_messages, list):
        return ""

    for item in reversed(input_messages):
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            if str(item.get("role", "")).strip().lower() != "user":
                continue
            text = _to_text(item.get("content")).strip()
            if text:
                return text
    return ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _append_observation(payload: dict[str, Any]) -> None:
    OBSERVATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    with OBSERVATION_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def queue_agent_turn_complete_event(
    event: dict[str, Any],
    *,
    log_prefix: str = "memdir_notify",
    owner_prefix: str = "notify-background",
) -> int:
    event_type = str(event.get("type") or event.get("event") or "agent-turn-complete").strip()
    if event_type != "agent-turn-complete":
        return 0

    cwd = _to_text(event.get("cwd")).strip()
    thread_id = _to_text(event.get("thread-id") or event.get("thread_id")).strip()
    assistant_text = _to_text(event.get("last-assistant-message") or event.get("last_assistant_message")).strip()
    user_text = _extract_latest_user_message(event).strip()
    observation = {
        "timestamp": _utc_now_iso(),
        "event_type": event_type,
        "cwd_present": bool(cwd),
        "thread_id_present": bool(thread_id),
        "user_text_present": bool(user_text),
        "assistant_text_present": bool(assistant_text),
        "cwd": cwd or None,
        "thread_id": thread_id or None,
        "user_text_preview": user_text[:200],
        "assistant_text_preview": assistant_text[:200],
        "event_keys": sorted(event.keys()),
    }
    _append_observation(observation)

    if not cwd:
        sys.stderr.write(f"[{log_prefix}] skipped: missing cwd\n")
        return 3
    if not thread_id:
        sys.stderr.write(f"[{log_prefix}] skipped: missing thread-id\n")
        return 3
    if not user_text:
        sys.stderr.write(f"[{log_prefix}] skipped: missing user message\n")
        return 3
    if not assistant_text:
        sys.stderr.write(f"[{log_prefix}] skipped: missing assistant message\n")
        return 3

    if not is_memdir_enabled(cwd):
        sys.stderr.write(f"[{log_prefix}] skipped: disabled session_id={thread_id} cwd={cwd}\n")
        return 0

    enqueue_result = enqueue_memdir_extraction_job(event)
    reason = str(enqueue_result.get("reason") or "queued")
    drain_result = (
        _start_background_queue_drain(max_jobs=20, owner_prefix=owner_prefix)
        if enqueue_result.get("queued") is True
        else None
    )
    sys.stderr.write(
        f"[{log_prefix}] queued="
        f"{reason} background_drain="
        f"{drain_result.get('reason') if isinstance(drain_result, dict) else 'skipped'} "
        f"session_id={thread_id} cwd={cwd}\n"
    )
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: memdir_notify.py <json_notification>\n")
        return 1

    try:
        event = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[memdir_notify] invalid JSON input: {exc}\n")
        return 2

    return queue_agent_turn_complete_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
