#!/usr/bin/env python3
# Function: Adapt Claude Code Stop hook payloads and transcripts to memdir queue events.
# Purpose: Keep Claude Code transcript formats separate from Codex handling.
from __future__ import annotations

import json
import pathlib
from typing import Any

import memdir_notify
from memdir_event_common import first_present, normalize_stop_payload_base, payload_prompt_id


def _is_human_user_item(item: dict[str, Any]) -> bool:
    if str(item.get("type") or "").strip().lower() != "user":
        return False
    message = item.get("message")
    if not isinstance(message, dict):
        return False
    if str(message.get("role") or "").strip().lower() != "user":
        return False
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    if "<local-command-" in content or "<command-name>" in content:
        return False
    origin = item.get("origin")
    if not isinstance(origin, dict):
        return True
    return str(origin.get("kind") or "").strip().lower() == "human"


def _claude_user_text(item: dict[str, Any]) -> str:
    if not _is_human_user_item(item):
        return ""
    message = item.get("message")
    if not isinstance(message, dict):
        return ""
    return memdir_notify._to_text(message.get("content")).strip()


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


def _extract_transcript_user_message(payload: dict[str, Any]) -> str:
    transcript_path = first_present(payload, ("transcript_path", "transcript-path", "transcriptPath"))
    if not isinstance(transcript_path, str) or not transcript_path.strip():
        return ""

    path = pathlib.Path(transcript_path)
    if not path.is_file():
        return ""

    target_prompt_id = payload_prompt_id(payload)
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
                user_text = _claude_user_text(item)
                if not user_text:
                    continue
                latest_user = user_text
                if target_prompt_id and str(item.get("promptId") or "").strip() == target_prompt_id:
                    matched_user = user_text
    except OSError:
        return ""
    if target_prompt_id and matched_user:
        return matched_user
    return latest_user


def normalize_stop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = normalize_stop_payload_base(payload, source="claude-stop-hook")
    if not isinstance(event.get("input-messages"), list):
        event["input-messages"] = _extract_input_messages(payload)
    if memdir_notify._extract_latest_user_message(event).strip():
        return event

    prompt = _extract_transcript_user_message(payload)
    if prompt:
        event["input-messages"] = [{"role": "user", "content": prompt}]
    return event
