#!/usr/bin/env python3
# Function: Route Stop hook payloads to client-specific memdir event adapters.
# Purpose: Keep Claude Code and Codex payload parsing separate before queueing extraction.
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import memdir_claude_events  # noqa: E402
import memdir_codex_events  # noqa: E402
from memdir_event_common import first_present, is_stop_payload  # noqa: E402
import memdir_notify  # noqa: E402
from harness_lib.settings import HARNESS_CONFIG_PATH, load_settings  # noqa: E402


MISSING_EXTRACTOR_PROVIDER_MESSAGE = (
    "[memdir_extract_stop] failed: missing [memdir.extractor].provider; "
    f"set it to codex, agy or local_cli in {HARNESS_CONFIG_PATH}"
)
SUPPORTED_EXTRACTOR_PROVIDERS = {"codex", "agy", "local_cli"}
SUPPORTED_CLIENTS = {"claude", "codex"}


def _configured_client(payload: dict[str, Any]) -> str:
    raw_client = os.environ.get("PROJECT_MEMDIR_CLIENT") or first_present(payload, ("client", "source", "runtime"))
    client = str(raw_client or "").strip().lower()
    if "claude" in client:
        return "claude"
    if "codex" in client:
        return "codex"
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return "claude"
    return "codex"


def normalize_stop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    client = _configured_client(payload)
    if client == "claude":
        return memdir_claude_events.normalize_stop_payload(payload)
    return memdir_codex_events.normalize_stop_payload(payload)


def _memdir_settings(settings: dict[str, Any]) -> dict[str, Any]:
    memdir = settings.get("memdir")
    if not isinstance(memdir, dict):
        return {}
    return memdir


def _extractor_provider(settings: dict[str, Any]) -> str:
    memdir = _memdir_settings(settings)
    extractor = memdir.get("extractor")
    if not isinstance(extractor, dict):
        return ""
    return str(extractor.get("provider") or "").strip()


def _unsupported_extractor_provider_message(provider: str) -> str:
    return (
        f"[memdir_extract_stop] failed: unsupported [memdir.extractor].provider: {provider}; "
        f"set it to codex, agy or local_cli in {HARNESS_CONFIG_PATH}"
    )


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
    if not is_stop_payload(payload):
        return 0
    settings = load_settings()
    extractor_provider = _extractor_provider(settings).lower()
    if not extractor_provider:
        sys.stderr.write(f"{MISSING_EXTRACTOR_PROVIDER_MESSAGE}\n")
        return 1
    if extractor_provider not in SUPPORTED_EXTRACTOR_PROVIDERS:
        sys.stderr.write(f"{_unsupported_extractor_provider_message(extractor_provider)}\n")
        return 1

    client = _configured_client(payload)
    if client not in SUPPORTED_CLIENTS:
        sys.stderr.write(f"[memdir_stop] skipped: unsupported client: {client}\n")
        return 0
    event = normalize_stop_payload(payload)
    result = memdir_notify.queue_agent_turn_complete_event(
        event,
        log_prefix="memdir_stop",
        owner_prefix="stop-background",
    )
    return 0 if result == 3 else result


if __name__ == "__main__":
    raise SystemExit(main())
