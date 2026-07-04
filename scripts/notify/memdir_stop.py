#!/usr/bin/env python3
# Function: Adapt Codex Stop hook payloads to the memdir extraction queue.
# Purpose: Keep turn-complete extraction asynchronous by queueing work and returning quickly.
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import memdir_notify  # noqa: E402
from harness_lib.settings import HARNESS_CONFIG_PATH, load_settings  # noqa: E402


MISSING_EXTRACTOR_PROVIDER_MESSAGE = (
    "[memdir_extract_stop] failed: missing [memdir.extractor].provider; "
    f"set it to codex, agy or local_cli in {HARNESS_CONFIG_PATH}"
)
SUPPORTED_EXTRACTOR_PROVIDERS = {"codex", "agy", "local_cli"}


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def _is_stop_payload(payload: dict[str, Any]) -> bool:
    hook_event = _first_present(
        payload,
        (
            "hookEventName",
            "hook_event_name",
            "hook-event-name",
            "hook",
            "event",
            "type",
        ),
    )
    if hook_event is None:
        return True
    return str(hook_event).strip().lower() in {"", "stop"}


def _extract_input_messages(payload: dict[str, Any]) -> list[Any]:
    for key in ("input-messages", "input_messages", "messages", "conversation"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    user_text = _first_present(
        payload,
        (
            "last-user-message",
            "last_user_message",
            "user-message",
            "user_message",
            "prompt",
        ),
    )
    if user_text is None:
        return []
    text = memdir_notify._to_text(user_text).strip()
    return [{"role": "user", "content": text}] if text else []


def _payload_turn_id(payload: dict[str, Any]) -> str:
    turn_id = _first_present(payload, ("turn_id", "turn-id", "turnId", "turn"))
    return memdir_notify._to_text(turn_id).strip()


def _transcript_payload_turn_id(payload: dict[str, Any]) -> str:
    turn_id = _payload_turn_id(payload)
    if turn_id:
        return turn_id
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict):
        return _payload_turn_id(metadata)
    return ""


def _transcript_user_text(payload: dict[str, Any]) -> str:
    payload_type = str(payload.get("type") or "").strip()
    if payload_type == "user_message":
        return memdir_notify._to_text(payload.get("message")).strip()
    if payload_type == "message" and str(payload.get("role") or "").strip().lower() == "user":
        return memdir_notify._to_text(payload.get("content")).strip()
    return ""


def _extract_transcript_user_message(payload: dict[str, Any]) -> str:
    transcript_path = _first_present(payload, ("transcript_path", "transcript-path", "transcriptPath"))
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return ""

    path = pathlib.Path(transcript_path)
    if not path.is_file():
        return ""

    target_turn_id = _payload_turn_id(payload)
    latest_user = ""
    matched_user = ""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(item, dict):
                    continue
                item_payload = item.get("payload")
                if not isinstance(item_payload, dict):
                    continue
                user_text = _transcript_user_text(item_payload)
                if not user_text:
                    continue
                latest_user = user_text
                if target_turn_id and _transcript_payload_turn_id(item_payload) == target_turn_id:
                    matched_user = user_text
    except OSError:
        return ""
    if target_turn_id:
        return matched_user
    return latest_user


def _normalize_stop_payload_base(payload: dict[str, Any]) -> dict[str, Any]:
    event = dict(payload)
    event["type"] = "agent-turn-complete"
    event["source"] = "codex-stop-hook"
    event["hook-event-name"] = "Stop"

    thread_id = _first_present(
        payload,
        (
            "thread-id",
            "thread_id",
            "session-id",
            "session_id",
            "sessionId",
            "conversation-id",
            "conversation_id",
        ),
    )
    if thread_id is not None and not event.get("thread-id"):
        event["thread-id"] = memdir_notify._to_text(thread_id).strip()

    if not isinstance(event.get("input-messages"), list):
        event["input-messages"] = _extract_input_messages(payload)

    assistant_text = _first_present(
        payload,
        (
            "last-assistant-message",
            "last_assistant_message",
            "assistant-message",
            "assistant_message",
            "final-message",
            "final_message",
            "finalMessage",
        ),
    )
    if assistant_text is not None and not event.get("last-assistant-message"):
        event["last-assistant-message"] = memdir_notify._to_text(assistant_text).strip()

    return event


def _normalize_stop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = _normalize_stop_payload_base(payload)
    if memdir_notify._extract_latest_user_message(event).strip():
        return event

    prompt = _extract_transcript_user_message(payload)
    if prompt:
        event["input-messages"] = [{"role": "user", "content": prompt}]
    return event


def normalize_stop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _normalize_stop_payload(payload)


def _extractor_provider() -> str:
    settings = load_settings()
    memdir = settings.get("memdir")
    if not isinstance(memdir, dict):
        return ""
    extractor = memdir.get("extractor")
    if not isinstance(extractor, dict):
        return ""
    return str(extractor.get("provider") or "").strip()


def main() -> int:
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        sys.stderr.write("[memdir_stop] skipped: missing stdin JSON\n")
        return 0

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[memdir_stop] skipped: invalid JSON input: {exc}\n")
        return 0
    if not isinstance(payload, dict):
        sys.stderr.write("[memdir_stop] skipped: JSON input is not an object\n")
        return 0
    if not _is_stop_payload(payload):
        return 0
    extractor_provider = _extractor_provider().lower()
    if not extractor_provider:
        sys.stderr.write(f"{MISSING_EXTRACTOR_PROVIDER_MESSAGE}\n")
        return 1
    if extractor_provider not in SUPPORTED_EXTRACTOR_PROVIDERS:
        sys.stderr.write(
            "[memdir_extract_stop] failed: unsupported [memdir.extractor].provider: "
            f"{extractor_provider}; set it to codex, agy or local_cli in {HARNESS_CONFIG_PATH}\n"
        )
        return 1

    event = _normalize_stop_payload(payload)
    result = memdir_notify.queue_agent_turn_complete_event(
        event,
        log_prefix="memdir_stop",
        owner_prefix="stop-background",
    )
    return 0 if result == 3 else result


if __name__ == "__main__":
    raise SystemExit(main())
