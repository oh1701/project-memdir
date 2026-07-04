#!/usr/bin/env python3
# Function: Provide the project memdir CLI.
# Purpose: Let hooks, wrappers, and notify paths reuse the same memdir logic.
from __future__ import annotations

import argparse
import json

from harness_lib.memdir import (
    build_memdir_context,
    build_session_start_context,
    ensure_project_memdir,
    extract_memories_from_event,
    find_relevant_memories,
    get_memdir_session_state,
    memdir_doctor,
    reset_memdir_session_state,
    scan_topic_files,
)
from harness_lib.memdir_queue import drain_memdir_extraction_queue
from harness_lib.settings import ensure_user_harness_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex memdir CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    ensure_cmd = sub.add_parser("ensure")
    ensure_cmd.add_argument("--cwd", default=None)

    scan_cmd = sub.add_parser("scan")
    scan_cmd.add_argument("--cwd", default=None)

    relevant_cmd = sub.add_parser("relevant")
    relevant_cmd.add_argument("--cwd", default=None)
    relevant_cmd.add_argument("--query", required=True)

    hook_cmd = sub.add_parser("hook-context")
    hook_cmd.add_argument("--cwd", default=None)
    hook_cmd.add_argument("--query", required=True)
    hook_cmd.add_argument("--include-core-paths", action="store_true")

    session_start_cmd = sub.add_parser("session-start-context")
    session_start_cmd.add_argument("--cwd", default=None)

    session_state_cmd = sub.add_parser("session-state")
    session_state_cmd.add_argument("--cwd", default=None)

    reset_state_cmd = sub.add_parser("reset-session-state")
    reset_state_cmd.add_argument("--cwd", default=None)

    sub.add_parser("init-config")

    extract_cmd = sub.add_parser("extract-event")
    extract_cmd.add_argument("--cwd", required=True)
    extract_cmd.add_argument("--thread-id", required=True)
    extract_cmd.add_argument("--user-text", required=True)
    extract_cmd.add_argument("--assistant-text", required=True)

    doctor_cmd = sub.add_parser("doctor")
    doctor_cmd.add_argument("--cwd", default=None)

    drain_cmd = sub.add_parser("drain-queue")
    drain_cmd.add_argument("--max-jobs", type=int, default=5)
    drain_cmd.add_argument("--owner", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "ensure":
        payload = ensure_project_memdir(args.cwd)
    elif args.command == "scan":
        payload = scan_topic_files(args.cwd)
    elif args.command == "relevant":
        payload = find_relevant_memories(args.query, args.cwd)
    elif args.command == "hook-context":
        payload = build_memdir_context(args.query, args.cwd, include_core_paths=args.include_core_paths)
    elif args.command == "session-start-context":
        payload = build_session_start_context(args.cwd)
    elif args.command == "session-state":
        payload = get_memdir_session_state(args.cwd)
    elif args.command == "reset-session-state":
        payload = reset_memdir_session_state(args.cwd)
    elif args.command == "init-config":
        payload = ensure_user_harness_config()
    elif args.command == "extract-event":
        payload = extract_memories_from_event(
            raw_cwd=args.cwd,
            user_text=args.user_text,
            assistant_text=args.assistant_text,
            thread_id=args.thread_id,
        )
    elif args.command == "drain-queue":
        payload = drain_memdir_extraction_queue(max_jobs=args.max_jobs, owner=args.owner)
    else:
        payload = memdir_doctor(args.cwd)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
