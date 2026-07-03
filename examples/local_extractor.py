#!/usr/bin/env python3
"""Deterministic local extractor example.

This is not an LLM. It reads the memdir extraction prompt from stdin and writes
one valid topic JSON file under the `Topics Dir:` path embedded in the prompt.
Use it as a safe default before connecting a file-writing local agent or wrapper.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import pathlib
import re
import sys


def _field(prompt: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", prompt, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def main() -> int:
    prompt = sys.stdin.read()
    topics_dir_raw = _field(prompt, "Topics Dir")
    if not topics_dir_raw:
        sys.stderr.write("missing Topics Dir in prompt\n")
        return 2

    user_match = re.search(r"User:\n(?P<user>.*?)\n\nAssistant:\n(?P<assistant>.*)\Z", prompt, flags=re.DOTALL)
    user_text = user_match.group("user").strip() if user_match else "No user text parsed."
    assistant_text = user_match.group("assistant").strip() if user_match else "No assistant text parsed."

    topics_dir = pathlib.Path(topics_dir_raw)
    topics_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 2,
        "id": "local-extractor-example",
        "name": "Local Extractor Example",
        "description": "Reference note created by the bundled deterministic local extractor.",
        "type": "reference",
        "content": f"User said: {user_text}\nAssistant said: {assistant_text}",
        "keywords": ["local", "extractor", "example", "memory"],
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "last_thread_id": "local-extractor-example",
    }
    (topics_dir / "local-extractor-example.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
