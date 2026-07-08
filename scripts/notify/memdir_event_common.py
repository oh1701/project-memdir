#!/usr/bin/env python3
# Function: Provide shared helpers for memdir notify event adapters.
# Purpose: Keep Claude Code and Codex hook payload adapters small and explicit.
from __future__ import annotations

from typing import Any

import memdir_notify


def first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None


def payload_turn_id(payload: dict[str, Any]) -> str:
    turn_id = first_present(payload, ("turn_id", "turn-id", "turnId", "turn"))
    return memdir_notify._to_text(turn_id).strip()


def payload_prompt_id(payload: dict[str, Any]) -> str:
    prompt_id = first_present(payload, ("prompt_id", "prompt-id", "promptId", "prompt"))
    return memdir_notify._to_text(prompt_id).strip()


def is_stop_payload(payload: dict[str, Any]) -> bool:
    hook_event = first_present(
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


def normalize_stop_payload_base(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    event = dict(payload)
    event["type"] = "agent-turn-complete"
    event["source"] = source
    event["hook-event-name"] = "Stop"

    thread_id = first_present(
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

    assistant_text = first_present(
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
