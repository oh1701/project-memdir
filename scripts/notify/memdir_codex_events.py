#!/usr/bin/env python3
# Function: Adapt Codex Stop hook payloads and transcripts to memdir queue events.
# Purpose: Keep Codex-specific transcript formats separate from Claude Code handling.
from __future__ import annotations

import json
import pathlib
from typing import Any

import memdir_notify
from memdir_event_common import first_present, normalize_stop_payload_base, payload_turn_id
from harness_lib.memdir import get_memdir_session_state


def _extract_input_messages(payload: dict[str, Any]) -> list[Any]:
    for key in ("input-messages", "input_messages", "messages", "conversation"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    user_text = first_present(
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


def _transcript_payload_turn_id(payload: dict[str, Any]) -> str:
    turn_id = payload_turn_id(payload)
    if turn_id:
        return turn_id
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, dict):
        return payload_turn_id(metadata)
    return ""


def _transcript_user_text(payload: dict[str, Any]) -> str:
    payload_type = str(payload.get("type") or "").strip()
    if payload_type == "user_message":
        return memdir_notify._to_text(payload.get("message")).strip()
    if payload_type == "message" and str(payload.get("role") or "").strip().lower() == "user":
        return memdir_notify._to_text(payload.get("content")).strip()
    return ""


def _extract_transcript_user_message(payload: dict[str, Any]) -> str:
    transcript_path = first_present(payload, ("transcript_path", "transcript-path", "transcriptPath"))
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return ""

    path = pathlib.Path(transcript_path)
    if not path.is_file():
        return ""

    target_turn_id = payload_turn_id(payload)
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
    if target_turn_id and matched_user:
        return matched_user
    return latest_user


def _extract_session_state_user_message(payload: dict[str, Any]) -> str:
    cwd = first_present(payload, ("cwd", "working_directory", "workdir"))
    if not isinstance(cwd, str) or not cwd.strip():
        return ""
    try:
        state = get_memdir_session_state(cwd)
    except Exception:
        return ""
    prompt = state.get("last_user_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return ""
    payload_session_id = first_present(payload, ("session_id", "session-id", "thread-id", "thread_id"))
    state_session_id = state.get("last_session_id")
    if isinstance(state_session_id, str) and state_session_id.strip() and payload_session_id:
        if state_session_id.strip() != memdir_notify._to_text(payload_session_id).strip():
            return ""
    return prompt.strip()


def normalize_stop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = normalize_stop_payload_base(payload, source="codex-stop-hook")
    if not isinstance(event.get("input-messages"), list):
        event["input-messages"] = _extract_input_messages(payload)
    if memdir_notify._extract_latest_user_message(event).strip():
        return event

    prompt = _extract_transcript_user_message(payload)
    if prompt:
        event["input-messages"] = [{"role": "user", "content": prompt}]
        return event
    prompt = _extract_session_state_user_message(payload)
    if prompt:
        event["input-messages"] = [{"role": "user", "content": prompt}]
    return event
