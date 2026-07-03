# Function: Provide the memdir notify extraction queue.
# Purpose: Process agent-turn-complete notifications safely through a SQLite queue instead of inline LLM extraction.
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from .settings import CODEX_ROOT
from .utils import ensure_dir, utc_now_iso


QUEUE_DB = CODEX_ROOT / "tasks" / "memdir-notify" / "queue.sqlite3"
MAX_ATTEMPTS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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


def _event_text_fields(event: dict[str, Any]) -> dict[str, str]:
    return {
        "cwd": _to_text(event.get("cwd")).strip(),
        "thread_id": _to_text(event.get("thread-id") or event.get("thread_id")).strip(),
        "user_text": _extract_latest_user_message(event).strip(),
        "assistant_text": _to_text(event.get("last-assistant-message") or event.get("last_assistant_message")).strip(),
    }


def _dedupe_key(thread_id: str, user_text: str, assistant_text: str) -> str:
    material = "\0".join([thread_id, user_text, assistant_text])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _connect_queue() -> sqlite3.Connection:
    ensure_dir(QUEUE_DB.parent)
    connection = sqlite3.connect(QUEUE_DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA journal_mode=WAL")
    _ensure_queue_schema(connection)
    return connection


def _ensure_queue_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            dedupe_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            cwd TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            user_text TEXT NOT NULL,
            assistant_text TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            leased_until TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memdir_jobs_status_created
            ON jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_memdir_jobs_lease
            ON jobs(status, leased_until);
        """
    )
    connection.commit()


def enqueue_memdir_extraction_job(event: dict[str, Any]) -> dict[str, Any]:
    fields = _event_text_fields(event)
    missing = [key for key, value in fields.items() if not value]
    if missing:
        return {"queued": False, "reason": "missing_fields", "missing": missing}

    dedupe_key = _dedupe_key(fields["thread_id"], fields["user_text"], fields["assistant_text"])
    job_id = f"memdir-{dedupe_key[:24]}"
    now = utc_now_iso()
    connection = _connect_queue()
    try:
        try:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, dedupe_key, status, cwd, thread_id, user_text, assistant_text,
                    created_at, updated_at
                )
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    dedupe_key,
                    fields["cwd"],
                    fields["thread_id"],
                    fields["user_text"],
                    fields["assistant_text"],
                    now,
                    now,
                ),
            )
            connection.commit()
            return {"queued": True, "reason": "queued", "job_id": job_id, "dedupe_key": dedupe_key}
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT job_id, status FROM jobs WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            return {
                "queued": False,
                "reason": "already_queued",
                "job_id": row["job_id"] if row else job_id,
                "status": row["status"] if row else None,
                "dedupe_key": dedupe_key,
            }
    finally:
        connection.close()


def _row_to_job(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def claim_next_job(owner: str, lease_seconds: int = 300) -> dict[str, Any] | None:
    now_dt = _utc_now()
    now = now_dt.isoformat().replace("+00:00", "Z")
    lease_until_text = (now_dt + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
    connection = _connect_queue()
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'queued' OR status = 'running'
            ORDER BY created_at ASC
            """
        ).fetchall()
        selected = None
        for row in rows:
            if row["status"] == "queued":
                selected = row
                break
            leased_until_at = _parse_timestamp(row["leased_until"])
            if leased_until_at is None or leased_until_at <= now_dt:
                selected = row
                break
        if selected is None:
            connection.rollback()
            return None
        attempt_count = int(selected["attempt_count"] or 0) + 1
        connection.execute(
            """
            UPDATE jobs
            SET status = 'running',
                attempt_count = ?,
                lease_owner = ?,
                leased_until = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (attempt_count, owner, lease_until_text, now, selected["job_id"]),
        )
        connection.commit()
        claimed = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (selected["job_id"],)).fetchone()
        return _row_to_job(claimed)
    finally:
        connection.close()


def mark_job_succeeded(job_id: str) -> None:
    now = utc_now_iso()
    connection = _connect_queue()
    try:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'succeeded',
                lease_owner = NULL,
                leased_until = NULL,
                last_error = NULL,
                updated_at = ?
            WHERE job_id = ?
            """,
            (now, job_id),
        )
        connection.commit()
    finally:
        connection.close()


def mark_job_failed_retryable(job_id: str, error: str, *, max_attempts: int = MAX_ATTEMPTS) -> None:
    now = utc_now_iso()
    connection = _connect_queue()
    try:
        row = connection.execute("SELECT attempt_count FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        attempt_count = int(row["attempt_count"] or 0) if row else 0
        status = "failed_permanent" if attempt_count >= max_attempts else "queued"
        connection.execute(
            """
            UPDATE jobs
            SET status = ?,
                lease_owner = NULL,
                leased_until = NULL,
                last_error = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (status, error[:800], now, job_id),
        )
        connection.commit()
    finally:
        connection.close()


def mark_job_failed_permanent(job_id: str, error: str) -> None:
    now = utc_now_iso()
    connection = _connect_queue()
    try:
        connection.execute(
            """
            UPDATE jobs
            SET status = 'failed_permanent',
                lease_owner = NULL,
                leased_until = NULL,
                last_error = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (error[:800], now, job_id),
        )
        connection.commit()
    finally:
        connection.close()


def drain_memdir_extraction_queue(max_jobs: int = 1, *, owner: str | None = None) -> dict[str, Any]:
    from .memdir import extract_memories_from_event

    worker = owner or f"memdir-notify-{os.getpid()}"
    processed: list[dict[str, Any]] = []
    for _ in range(max(max_jobs, 0)):
        job = claim_next_job(worker)
        if job is None:
            break
        try:
            result = extract_memories_from_event(
                raw_cwd=str(job["cwd"]),
                user_text=str(job["user_text"]),
                assistant_text=str(job["assistant_text"]),
                thread_id=str(job["thread_id"]),
            )
        except (sqlite3.Error, OSError) as exc:
            mark_job_failed_retryable(str(job["job_id"]), str(exc))
            processed.append({"job_id": job["job_id"], "status": "retryable", "error": str(exc)[:240]})
            continue
        except Exception as exc:  # noqa: BLE001
            mark_job_failed_retryable(str(job["job_id"]), str(exc))
            processed.append({"job_id": job["job_id"], "status": "retryable", "error": str(exc)[:240]})
            continue

        mark_job_succeeded(str(job["job_id"]))
        processed.append({"job_id": job["job_id"], "status": "succeeded", "result": result})

    return {
        "processed": processed,
        "processed_count": len(processed),
        "reason": "worker_processed" if processed else "no_job",
    }
