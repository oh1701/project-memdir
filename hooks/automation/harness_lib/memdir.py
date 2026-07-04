# Function: Provide project memdir create, lookup, retrieval, and extraction logic.
# Purpose: Operate project memory as a JSON store with a SQLite vector side index.
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import shlex
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .codex_exec import run_codex_exec
from .paths import canonicalize_existing_path, detect_project_root, project_slug
from .settings import CODEX_ROOT, HARNESS_CONFIG_PATH, load_settings
from .utils import atomic_write_json, ensure_dir, utc_now_iso


MANIFEST_NAME = "manifest.json"
LEGACY_ENTRYPOINT_NAME = "MEMORY.md"
PROLOGUE_NAME = "memdir-prologue.md"
TOPICS_DIR_NAME = "topics"
VECTOR_DB_NAME = "vector_index.sqlite3"
EXTRACTION_STATUS_NAME = "extraction_status.json"
MEMORY_SCHEMA_VERSION = 2
EMBEDDING_FAILURE_RETRY_AFTER_META_KEY = "embedding_failure_retry_after"
EMBEDDING_FAILURE_REASON_META_KEY = "embedding_failure_reason"
LOCAL_HASH_PROVIDER = "local_hash"
LOCAL_HASH_MODEL = "memdir-local-hash"
CODEX_DEFAULT_MODEL = "codex-default-model"
AGY_DEFAULT_MODEL = "agy-default-model"
CLOUDFLARE_EMBEDDING_BATCH_SIZE = 100
MEMORY_TYPES = ("user", "feedback", "project", "reference")
VECTOR_LEGACY_ALIASES = {
    "vector_index_name": "index_name",
    "vector_index_backend": "index_backend",
    "vector_dimensions": "dimensions",
    "vector_score_weight": "score_weight",
    "min_vector_similarity": "min_similarity",
}
EMBEDDING_LEGACY_ALIASES = {
    "embedding_failure_backoff_sec": "failure_backoff_sec",
    "query_embedding_cache_ttl_sec": "query_cache_ttl_sec",
    "query_embedding_cache_max_entries": "query_cache_max_entries",
}
EXTRACTOR_LEGACY_ALIASES = {
    "extractor_provider": "provider",
    "extract_timeout_sec": "timeout_sec",
    "extract_codex_model": "codex_model",
    "codex_bin": "codex_bin",
    "extract_agy_bin": "agy_bin",
    "extract_agy_extraction_timeout_sec": "agy_extraction_timeout_sec",
    "extract_agy_model": "agy_model",
    "extract_local_cli_command": "local_cli_command",
    "extract_local_cli_extraction_timeout_sec": "local_cli_extraction_timeout_sec",
}
LEGACY_KIND_MAP = {
    "workflow": "project",
    "preference": "user",
    "constraint": "feedback",
    "gotcha": "reference",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣._-]{2,}")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
ROOT_LINE_RE = re.compile(r"^- (?:root|project_root): `([^`]+)`$", re.MULTILINE)
MEMORY_RULE_LINES = [
    "Persistent JSON reference storage with a SQLite vector side index.",
    "Reference-only, not a durable source of truth.",
    "manifest.json and Prologue are the core pair; read them together on session start.",
    "topics/*.json holds reference notes; use injected summaries first and reread only the relevant items when the summary is insufficient.",
    "For memdir settings requests, prefer script files or the Prologue first; update topic JSON only when the user explicitly asks or when a script or Prologue change would break a reference.",
    "Topic JSON schema: schema_version, id, name, description, type, content, keywords, updated_at, last_thread_id.",
    "Types: user, feedback, project, reference.",
    "Do not merge only because the user, project, thread, or broad type is the same.",
    "Merge only when target, purpose, lifecycle, and future recall query are all the same.",
    "Create a new topic JSON when existing topic name, description, or keywords must broaden to contain the information.",
    "No catch-all topics.",
    "Only notify/extractor may write topic JSON files; manifest/vector index are harness-managed.",
    "Recalled memory is context only.",
    "Ignore memory when asked.",
    "End with `현재 답변은 메모리를 읽어 답변했습니다.` only when recalled memory is a direct reason/source for the answer, not merely because a memdir topic was read or available.",
]
_LAST_EMBEDDING_STATUS: dict[str, Any] = {
    "active_provider": LOCAL_HASH_PROVIDER,
    "fallback_reason": None,
}
_TOPIC_PAYLOAD_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any] | None]] = {}


def memdir_settings() -> dict[str, Any]:
    return load_settings()["memdir"]


def _memdir_section_settings(section_name: str, legacy_aliases: dict[str, str]) -> dict[str, Any]:
    settings = memdir_settings()
    raw_section = settings.get(section_name, {})
    section = dict(raw_section) if isinstance(raw_section, dict) else {}
    for legacy_key, section_key in legacy_aliases.items():
        if legacy_key in settings:
            section[section_key] = settings[legacy_key]
    return section


def _vector_settings() -> dict[str, Any]:
    return _memdir_section_settings("vector", VECTOR_LEGACY_ALIASES)


def _embedding_settings() -> dict[str, Any]:
    return _memdir_section_settings("embedding", EMBEDDING_LEGACY_ALIASES)


def _extractor_settings() -> dict[str, Any]:
    return _memdir_section_settings("extractor", EXTRACTOR_LEGACY_ALIASES)


def _storage_settings() -> dict[str, Any]:
    settings = memdir_settings()
    raw_section = settings.get("storage", {})
    return dict(raw_section) if isinstance(raw_section, dict) else {}


def _project_root_settings() -> dict[str, Any]:
    settings = memdir_settings()
    raw_section = settings.get("project_root", {})
    return dict(raw_section) if isinstance(raw_section, dict) else {}


def _resolve_project_root(raw_cwd: str | os.PathLike[str] | None = None) -> pathlib.Path:
    project_root = _project_root_settings()
    strategy = str(project_root.get("strategy") or "cwd").strip().lower()
    if strategy == "cwd":
        return canonicalize_existing_path(raw_cwd or os.getcwd())
    if strategy == "detect":
        return detect_project_root(raw_cwd)
    raise ValueError(f"unsupported memdir project_root strategy: {strategy}")


def _resolve_python_launcher_command(command: list[str]) -> list[str]:
    if not command:
        return command
    executable = command[0]
    if "/" in executable or "\\" in executable:
        return command
    executable_name = executable.lower()
    if executable_name not in {"python", "python3", "py"}:
        return command
    if shutil.which(executable):
        return command

    payload = command[2:] if executable_name == "py" and len(command) > 1 and command[1] == "-3" else command[1:]
    candidates = [["python", *payload], ["py", "-3", *payload], ["python3", *payload]]
    if os.name != "nt":
        candidates = [["python3", *payload], ["python", *payload], ["py", "-3", *payload]]
    for candidate in candidates:
        resolved = shutil.which(candidate[0])
        if resolved:
            return [resolved, *candidate[1:]]
    return command


def _no_window_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": creationflags} if creationflags else {}


def _split_command_template(command_template: str) -> list[str]:
    parts = shlex.split(command_template, posix=os.name != "nt")
    if os.name != "nt":
        return parts
    return [
        part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"} else part
        for part in parts
    ]


def _path_key(path: pathlib.Path | str) -> str:
    return str(canonicalize_existing_path(path)).casefold()


def _disabled_project_root_keys() -> set[str]:
    disabled = memdir_settings().get("disabled_project_roots", [])
    return {_path_key(path) for path in disabled if path}


def _normalize_memory_type(raw_value: Any) -> str | None:
    value = str(raw_value or "").strip().lower()
    if not value:
        return None
    if value in MEMORY_TYPES:
        return value
    return LEGACY_KIND_MAP.get(value, "reference")


def _coerce_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = value
    elif isinstance(value, str):
        candidates = re.split(r"[,\n]", value)
    else:
        candidates = []
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        keyword = _truncate_chars(str(candidate).strip(), 48)
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(keyword)
    return normalized[:12]


def is_memdir_enabled(raw_cwd: str | None = None) -> bool:
    settings = memdir_settings()
    if not settings.get("enabled", True):
        return False
    project_root = _resolve_project_root(raw_cwd)
    return _path_key(project_root) not in _disabled_project_root_keys()


def resolve_project_paths(raw_cwd: str | os.PathLike[str] | None = None) -> dict[str, pathlib.Path]:
    project_root = _resolve_project_root(raw_cwd)
    settings = memdir_settings()
    storage = _storage_settings()
    storage_mode = str(storage.get("mode") or "plugin").strip().lower()
    if storage_mode == "plugin":
        memdir = pathlib.Path(settings["base_dir"]).expanduser() / project_slug(project_root)
    elif storage_mode == "project":
        project_dir_name = str(storage.get("project_dir_name") or ".project-memdir").strip() or ".project-memdir"
        project_dir = pathlib.Path(project_dir_name)
        if project_dir.is_absolute() or ".." in project_dir.parts:
            raise ValueError(f"invalid memdir project_dir_name: {project_dir_name}")
        memdir = project_root / project_dir
    else:
        raise ValueError(f"unsupported memdir storage mode: {storage_mode}")
    topics_dir_name = str(settings.get("topics_dir_name", TOPICS_DIR_NAME))
    manifest_name = str(settings.get("manifest_name", MANIFEST_NAME))
    vector_index_name = str(_vector_settings().get("index_name", VECTOR_DB_NAME))
    return {
        "project_root": project_root,
        "memdir": memdir,
        "topics_dir": memdir / topics_dir_name,
        "entrypoint": memdir / manifest_name,
        "vector_db": memdir / vector_index_name,
    }


def get_project_memdir(raw_cwd: str | None = None) -> pathlib.Path:
    return resolve_project_paths(raw_cwd)["memdir"]


def get_entrypoint_path(raw_cwd: str | None = None) -> pathlib.Path:
    return resolve_project_paths(raw_cwd)["entrypoint"]


def get_extraction_status_path(raw_cwd: str | None = None) -> pathlib.Path:
    return resolve_project_paths(raw_cwd)["memdir"] / EXTRACTION_STATUS_NAME


def get_user_prompt_submit_state_path(raw_cwd: str | None = None) -> pathlib.Path:
    project_root = resolve_project_paths(raw_cwd)["project_root"]
    return CODEX_ROOT / "tmp" / "memdir-user-prompt-submit" / f"{project_slug(project_root)}.json"


def _load_session_state(state_path: pathlib.Path) -> dict[str, Any]:
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        loaded = {}
    if not isinstance(loaded, dict):
        loaded = {}
    count = loaded.get("count", 0)
    try:
        count = max(int(count), 0)
    except (TypeError, ValueError):
        count = 0
    return {"count": count}


def _save_session_state(state_path: pathlib.Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"count": int(state.get("count", 0))}),
        encoding="utf-8",
    )


def reset_memdir_session_state(raw_cwd: str | None = None) -> dict[str, Any]:
    state_path = get_user_prompt_submit_state_path(raw_cwd)
    state_path.unlink(missing_ok=True)
    return {"state_path": str(state_path), "reset": True}


def get_memdir_session_state(raw_cwd: str | None = None) -> dict[str, Any]:
    state_path = get_user_prompt_submit_state_path(raw_cwd)
    state = _load_session_state(state_path)
    return {"state_path": str(state_path), **state}


def advance_memdir_session_turn(raw_cwd: str | None = None) -> dict[str, Any]:
    state_path = get_user_prompt_submit_state_path(raw_cwd)
    state = _load_session_state(state_path)
    state["count"] += 1
    _save_session_state(state_path, state)
    return {"state_path": str(state_path), **state}


def _load_json_document(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _read_entrypoint_root(entrypoint_path: pathlib.Path) -> pathlib.Path | None:
    if not entrypoint_path.exists():
        return None
    if entrypoint_path.suffix == ".json":
        payload = _load_json_document(entrypoint_path)
        if payload and payload.get("project_root"):
            return canonicalize_existing_path(str(payload["project_root"]))
        return None
    try:
        text = entrypoint_path.read_text(encoding="utf-8")
    except Exception:
        return None
    match = ROOT_LINE_RE.search(text)
    if not match:
        return None
    return canonicalize_existing_path(match.group(1))


def _unique_target_path(target_path: pathlib.Path) -> pathlib.Path:
    if not target_path.exists():
        return target_path
    stem = target_path.stem
    suffix = target_path.suffix
    parent = target_path.parent
    index = 1
    while True:
        candidate = parent / f"{stem}-migrated-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _topic_identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return normalized or "memory"


def _topic_identifier_from_relative(relative_path: pathlib.Path) -> str:
    parts = [part for part in relative_path.with_suffix("").parts if part not in {".", ".."}]
    return _topic_identifier("-".join(parts))


def _write_json_if_changed(path: pathlib.Path, payload: dict[str, Any]) -> None:
    existing = _load_json_document(path)
    if existing == payload:
        return
    atomic_write_json(path, payload)


def _iso_from_timestamp(timestamp: float) -> str:
    if timestamp <= 0:
        return utc_now_iso()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _entrypoint_template(project_root: pathlib.Path) -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "project_root": str(project_root),
        "project_slug": project_slug(project_root),
        "storage": {
            "source_of_truth": "json",
            "topics_dir": str(memdir_settings().get("topics_dir_name", TOPICS_DIR_NAME)),
            "vector_index": str(_vector_settings().get("index_name", VECTOR_DB_NAME)),
            "vector_backend": str(_vector_settings().get("index_backend", "sqlite")),
        },
        "rules": _memory_rules(),
        "memory_count": 0,
        "recent_topics": [],
        "updated_at": utc_now_iso(),
    }


def _find_alias_memdirs(project_root: pathlib.Path, canonical_memdir: pathlib.Path) -> list[pathlib.Path]:
    base_dir = pathlib.Path(memdir_settings()["base_dir"]).expanduser()
    if not base_dir.exists():
        return []
    project_key = _path_key(project_root)
    aliases: list[pathlib.Path] = []
    manifest_name = str(memdir_settings().get("manifest_name", MANIFEST_NAME))
    for candidate in sorted(base_dir.iterdir()):
        if not candidate.is_dir() or candidate == canonical_memdir:
            continue
        recorded_root = _read_entrypoint_root(candidate / manifest_name) or _read_entrypoint_root(candidate / LEGACY_ENTRYPOINT_NAME)
        if recorded_root is None:
            continue
        if _path_key(recorded_root) == project_key:
            aliases.append(candidate)
    return aliases


def _migrate_alias_memdirs(project_root: pathlib.Path, canonical_memdir: pathlib.Path) -> list[str]:
    migrated_from: list[str] = []
    for alias_dir in _find_alias_memdirs(project_root, canonical_memdir):
        ensure_dir(canonical_memdir)
        for source in sorted(alias_dir.rglob("*")):
            if source.is_dir():
                continue
            relative = source.relative_to(alias_dir)
            destination = _unique_target_path(canonical_memdir / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        shutil.rmtree(alias_dir, ignore_errors=True)
        migrated_from.append(str(alias_dir))
    return migrated_from


def truncate_entrypoint_content(content: str) -> dict[str, Any]:
    settings = memdir_settings()
    max_lines = int(settings["max_entrypoint_lines"])
    max_bytes = int(settings["max_entrypoint_bytes"])
    trimmed = content.strip()
    lines = trimmed.splitlines()
    line_count = len(lines)
    byte_count = len(trimmed.encode("utf-8"))
    was_line_truncated = line_count > max_lines
    was_byte_truncated = byte_count > max_bytes
    selected_lines = lines[:max_lines] if was_line_truncated else lines
    selected_text = "\n".join(selected_lines)
    while len(selected_text.encode("utf-8")) > max_bytes and "\n" in selected_text:
        selected_text = selected_text.rsplit("\n", 1)[0]
    if len(selected_text.encode("utf-8")) > max_bytes:
        selected_text = selected_text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    if was_line_truncated or was_byte_truncated:
        selected_text += "\n\n> WARNING: manifest.json preview was truncated for prompt safety."
    return {
        "content": selected_text,
        "line_count": line_count,
        "byte_count": byte_count,
        "was_line_truncated": was_line_truncated,
        "was_byte_truncated": was_byte_truncated,
    }


def _parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(0).splitlines()[1:-1]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1).strip()


def _truncate_chars(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "..."


def _legacy_topic_payload(path: pathlib.Path, memdir: pathlib.Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    frontmatter = _parse_frontmatter(text)
    content = _strip_frontmatter(text)
    stat = path.stat()
    relative = path.relative_to(memdir)
    topic_id = _topic_identifier_from_relative(relative)
    memory_type = _normalize_memory_type(frontmatter.get("type")) or "reference"
    description = str(frontmatter.get("description") or "").strip() or _truncate_chars(content, 120)
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "id": topic_id,
        "name": str(frontmatter.get("name") or path.stem).strip() or topic_id,
        "description": description,
        "type": memory_type,
        "content": content,
        "keywords": _coerce_keywords(frontmatter.get("keywords")),
        "updated_at": _iso_from_timestamp(stat.st_mtime),
        "last_thread_id": None,
        "_mtime": stat.st_mtime,
    }


def _coerce_topic_payload(path: pathlib.Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    content = str(payload.get("content") or payload.get("body") or "").strip()
    if not content:
        return None
    topic_id = _topic_identifier(str(payload.get("id") or path.stem))
    memory_type = _normalize_memory_type(payload.get("type")) or "reference"
    keywords = _coerce_keywords(payload.get("keywords"))
    description = str(payload.get("description") or "").strip() or _truncate_chars(content, 120)
    updated_at = str(payload.get("updated_at") or "").strip() or utc_now_iso()
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "id": topic_id,
        "name": str(payload.get("name") or path.stem).strip() or topic_id,
        "description": description,
        "type": memory_type,
        "content": content,
        "keywords": keywords,
        "updated_at": updated_at,
        "last_thread_id": payload.get("last_thread_id"),
    }


def _load_topic_payload(path: pathlib.Path, stat: os.stat_result | None = None) -> dict[str, Any] | None:
    stat = stat or path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(path)
    cached = _TOPIC_PAYLOAD_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        cached_payload = cached[1]
        return dict(cached_payload) if cached_payload is not None else None

    payload = _load_json_document(path)
    if payload is None:
        _TOPIC_PAYLOAD_CACHE[cache_key] = (signature, None)
        return None
    normalized = _coerce_topic_payload(path, payload)
    _TOPIC_PAYLOAD_CACHE[cache_key] = (signature, dict(normalized) if normalized is not None else None)
    return normalized


def _next_topic_path(topics_dir: pathlib.Path, topic_id: str) -> pathlib.Path:
    candidate = topics_dir / f"{topic_id}.json"
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = topics_dir / f"{topic_id}-{index}.json"
        if not candidate.exists():
            return candidate
        index += 1


def _migrate_legacy_markdown_storage(project_root: pathlib.Path, memdir: pathlib.Path, topics_dir: pathlib.Path, manifest_path: pathlib.Path) -> list[str]:
    legacy_entrypoint = memdir / LEGACY_ENTRYPOINT_NAME
    legacy_topics = [
        path
        for path in sorted(memdir.rglob("*.md"))
        if path != legacy_entrypoint and ".git" not in path.parts and TOPICS_DIR_NAME not in path.parts
    ]
    if not legacy_entrypoint.exists() and not legacy_topics:
        return []

    ensure_dir(topics_dir)
    migrated: list[str] = []
    for legacy_path in legacy_topics:
        topic_payload = _legacy_topic_payload(legacy_path, memdir)
        destination = _next_topic_path(topics_dir, str(topic_payload["id"]))
        topic_payload.pop("_mtime", None)
        atomic_write_json(destination, topic_payload)
        legacy_path.unlink(missing_ok=True)
        migrated.append(str(legacy_path))

    existing_manifest = _load_json_document(manifest_path) or {}
    manifest_payload = _entrypoint_template(project_root)
    if existing_manifest:
        manifest_payload["updated_at"] = str(existing_manifest.get("updated_at") or manifest_payload["updated_at"])
    _write_json_if_changed(manifest_path, manifest_payload)
    had_legacy_entrypoint = legacy_entrypoint.exists()
    legacy_entrypoint.unlink(missing_ok=True)
    if had_legacy_entrypoint:
        migrated.append(str(legacy_entrypoint))
    return migrated


def scan_topic_files(raw_cwd: str | None = None) -> list[dict[str, Any]]:
    if not is_memdir_enabled(raw_cwd):
        return []
    paths = resolve_project_paths(raw_cwd)
    memdir = paths["memdir"]
    topics_dir = paths["topics_dir"]
    if not topics_dir.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(topics_dir.rglob("*.json")):
        stat = path.stat()
        payload = _load_topic_payload(path, stat)
        if payload is None:
            continue
        items.append(
            {
                "path": str(path),
                "filename": path.relative_to(memdir).as_posix(),
                "id": payload["id"],
                "name": payload["name"],
                "description": payload["description"],
                "type": payload["type"],
                "keywords": payload["keywords"],
                "updated_at": payload["updated_at"],
                "mtime": stat.st_mtime,
                "mtime_ns": stat.st_mtime_ns,
                "excerpt": _truncate_chars(payload["content"], 900),
            }
        )
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def format_memory_manifest(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        memory_type = f"[{item.get('type')}] " if item.get("type") else ""
        description = f": {item['description']}" if item.get("description") else ""
        updated_label = str(item.get("updated_at") or f"{item['mtime']:.3f}")
        lines.append(f"- {memory_type}{item['filename']} ({updated_label}){description}")
    return "\n".join(lines)


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def _embedding_dimension() -> int:
    return int(_vector_settings().get("dimensions", 96))


def _vector_score_weight() -> float:
    return float(_vector_settings().get("score_weight", 12))


def _min_vector_similarity() -> float:
    return float(_vector_settings().get("min_similarity", 0.2))


def _topic_vector_text(item: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in [
            item.get("filename"),
            item.get("name"),
            item.get("description"),
            item.get("type"),
            " ".join(item.get("keywords", [])),
            item.get("excerpt"),
        ]
        if part
    )


def _build_vector(text: str) -> list[float]:
    dimension = _embedding_dimension()
    vector = [0.0] * dimension
    tokens = _tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        first_idx = int.from_bytes(digest[:2], "big") % dimension
        second_idx = int.from_bytes(digest[2:4], "big") % dimension
        first_sign = 1.0 if digest[4] % 2 == 0 else -1.0
        second_sign = 1.0 if digest[5] % 2 == 0 else -1.0
        vector[first_idx] += first_sign
        vector[second_idx] += second_sign * 0.5
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalized_query(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def _unix_time() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _cloudflare_embedding_config() -> dict[str, Any] | None:
    embedding = _embedding_settings()
    account_id = str(embedding.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
    token = str(embedding.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if not account_id or not token:
        return None
    model = str(
        embedding.get("model")
        or embedding.get("CLOUDFLARE_MODEL")
        or os.environ.get("CLOUDFLARE_MODEL")
        or ""
    ).strip()
    dimensions = int(
        embedding.get("dimensions")
        or embedding.get("CLOUDFLARE_DIMENSIONS")
        or os.environ.get("CLOUDFLARE_DIMENSIONS")
        or 0
    )
    timeout_sec = int(
        embedding.get("timeout_sec")
        or embedding.get("CLOUDFLARE_TIMEOUT_SEC")
        or os.environ.get("CLOUDFLARE_TIMEOUT_SEC")
        or 0
    )
    if not model or dimensions <= 0 or timeout_sec <= 0:
        return None
    return {
        "provider": "cloudflare",
        "model": model,
        "dimensions": dimensions,
        "account_id": account_id,
        "api_token": token,
        "timeout_sec": timeout_sec,
    }


def _local_hash_embedding_config() -> dict[str, Any]:
    return {
        "provider": LOCAL_HASH_PROVIDER,
        "model": LOCAL_HASH_MODEL,
        "dimensions": _embedding_dimension(),
    }


def _configured_embedding_provider() -> dict[str, Any]:
    return _cloudflare_embedding_config() or _local_hash_embedding_config()


def _embedding_model_for_context() -> str:
    return str(_configured_embedding_provider()["model"])


def _extractor_provider_for_context() -> str:
    settings = _extractor_settings()
    provider = str(settings.get("provider") or "").strip().lower()
    return provider or "undefined"


def _extractor_model_for_context() -> str:
    settings = _extractor_settings()
    provider = str(settings.get("provider") or "").strip().lower()
    model_defaults = {
        "codex": ("codex_model", CODEX_DEFAULT_MODEL),
        "agy": ("agy_model", AGY_DEFAULT_MODEL),
    }
    model_config = model_defaults.get(provider)
    if not model_config:
        return "undefined"
    model_key, default_model = model_config
    model = str(settings.get(model_key) or "").strip()
    return model if model and model != "default-model" else default_model


def _configured_extractor_model(settings: dict[str, Any], model_key: str, default_model: str) -> str | None:
    model = str(settings.get(model_key) or "").strip()
    if not model or model == default_model:
        return None
    return model


def _compact_status_text(value: Any, limit: int = 240) -> str:
    return _truncate_chars(str(value or "").replace("\r", " ").replace("\n", " ").strip(), limit)


def _classify_extraction_failure(result: dict[str, Any]) -> str:
    material = " ".join(
        _compact_status_text(result.get(key), 800).casefold()
        for key in ("reason", "error", "stderr", "stdout")
        if result.get(key)
    )
    if "timeout" in material:
        return "timeout"
    if any(term in material for term in ("quota", "rate limit", "ratelimit", "usage limit", "insufficient_quota", "billing")):
        return "quota_exceeded"
    if any(term in material for term in ("unauthorized", "forbidden", "api key", "auth", "permission denied", "access denied")):
        return "auth_failed"
    if "model" in material and any(
        term in material
        for term in ("not found", "does not exist", "unknown", "unsupported", "invalid", "unavailable")
    ):
        return "model_unavailable"
    return "extractor_failed"


def _extraction_failure_hint(kind: str) -> str:
    hints = {
        "timeout": "Check extractor timeout settings or the selected model's response speed.",
        "quota_exceeded": "Check token limits, credits, billing, or rate limits.",
        "auth_failed": "Check API access, login state, or credentials for the configured extractor.",
        "model_unavailable": "Check the configured extractor model name and model access.",
    }
    return hints.get(kind, f"Check token limits, API access, or the configured model name in {HARNESS_CONFIG_PATH}.")


def _record_extraction_failure_status(memdir: pathlib.Path, extractor: str, result: dict[str, Any]) -> None:
    kind = _classify_extraction_failure(result)
    detail = _compact_status_text(result.get("error") or result.get("stderr") or result.get("stdout"))
    payload = {
        "schema_version": 1,
        "provider": extractor,
        "model": _extractor_model_for_context(),
        "reason": str(result.get("reason") or "extract_failed"),
        "kind": kind,
        "detail": detail,
        "hint": _extraction_failure_hint(kind),
        "updated_at": utc_now_iso(),
    }
    atomic_write_json(memdir / EXTRACTION_STATUS_NAME, payload)


def _clear_extraction_failure_status(memdir: pathlib.Path) -> None:
    try:
        (memdir / EXTRACTION_STATUS_NAME).unlink(missing_ok=True)
    except OSError:
        pass


def _load_extraction_failure_notice(raw_cwd: str | None = None) -> str:
    payload = _load_json_document(get_extraction_status_path(raw_cwd))
    if not payload:
        return ""
    kind = _compact_status_text(payload.get("kind"), 64) or "extractor_failed"
    reason = _compact_status_text(payload.get("reason"), 96) or "extract_failed"
    return (
        "previous project-memdir memory extraction failed: "
        f"kind={kind} reason={reason}."
    )


def _set_embedding_status(active_provider: str, fallback_reason: str | None = None) -> None:
    _LAST_EMBEDDING_STATUS["active_provider"] = active_provider
    _LAST_EMBEDDING_STATUS["fallback_reason"] = fallback_reason


def _normalize_cloudflare_vectors(raw_data: Any, dimensions: int, expected_count: int) -> list[list[float]] | None:
    if not isinstance(raw_data, list) or len(raw_data) != expected_count:
        return None
    vectors: list[list[float]] = []
    for raw_vector in raw_data:
        candidate = raw_vector
        if isinstance(raw_vector, dict):
            candidate = raw_vector.get("embedding") or raw_vector.get("vector")
        if not isinstance(candidate, list) or len(candidate) != dimensions:
            return None
        try:
            vector = [float(value) for value in candidate]
        except (TypeError, ValueError):
            return None
        vectors.append(vector)
    return vectors


def _extract_cloudflare_data(response_payload: dict[str, Any]) -> Any:
    result = response_payload.get("result")
    if isinstance(result, dict):
        if "data" in result:
            return result["data"]
        if "embeddings" in result:
            return result["embeddings"]
    if "data" in response_payload:
        return response_payload["data"]
    if "embeddings" in response_payload:
        return response_payload["embeddings"]
    return None


def _call_cloudflare_embeddings(texts: list[str], config: dict[str, Any]) -> tuple[list[list[float]] | None, str | None]:
    if not texts:
        return [], None
    account_id = str(config["account_id"])
    model = str(config["model"])
    payload = {"text": texts}
    request = urllib.request.Request(
        url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_token']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(config["timeout_sec"])) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return None, f"http_{exc.code}"
    except urllib.error.URLError as exc:
        return None, str(exc.reason or exc)[:240]
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:240]

    if not isinstance(response_payload, dict):
        return None, "malformed_response"
    vectors = _normalize_cloudflare_vectors(
        _extract_cloudflare_data(response_payload),
        int(config["dimensions"]),
        len(texts),
    )
    if vectors is None:
        return None, "malformed_response"
    return vectors, None


def _local_hash_embeddings(texts: list[str]) -> list[list[float]]:
    return [_build_vector(text) for text in texts]


def _embed_texts(
    texts: list[str],
    config: dict[str, Any] | None = None,
    *,
    allow_local_fallback: bool = True,
) -> tuple[list[list[float]], dict[str, Any], str | None]:
    provider = config or _configured_embedding_provider()
    if provider["provider"] == "cloudflare":
        vectors: list[list[float]] = []
        for index in range(0, len(texts), CLOUDFLARE_EMBEDDING_BATCH_SIZE):
            batch = texts[index : index + CLOUDFLARE_EMBEDDING_BATCH_SIZE]
            batch_vectors, error = _call_cloudflare_embeddings(batch, provider)
            if batch_vectors is None:
                if not allow_local_fallback:
                    _set_embedding_status("cloudflare", error or "cloudflare_embedding_failed")
                    return [], provider, error or "cloudflare_embedding_failed"
                local_config = _local_hash_embedding_config()
                _set_embedding_status(LOCAL_HASH_PROVIDER, error or "cloudflare_embedding_failed")
                return _local_hash_embeddings(texts), local_config, error or "cloudflare_embedding_failed"
            vectors.extend(batch_vectors)
        _set_embedding_status("cloudflare", None)
        return vectors, provider, None

    _set_embedding_status(LOCAL_HASH_PROVIDER, None)
    return _local_hash_embeddings(texts), _local_hash_embedding_config(), None


def _embedding_doctor_status() -> dict[str, Any]:
    cloudflare_config = _cloudflare_embedding_config()
    configured = cloudflare_config or _local_hash_embedding_config()
    active_provider = str(_LAST_EMBEDDING_STATUS.get("active_provider") or configured["provider"])
    active = configured if active_provider == configured["provider"] else _local_hash_embedding_config()
    return {
        "configured_provider": configured["provider"],
        "active_provider": active_provider,
        "model": active["model"],
        "dimensions": active["dimensions"],
        "cloudflare_configured": cloudflare_config is not None,
        "account_id_present": bool(cloudflare_config and cloudflare_config.get("account_id")),
        "api_token": "<redacted>" if cloudflare_config and cloudflare_config.get("api_token") else None,
        "fallback_reason": _LAST_EMBEDDING_STATUS.get("fallback_reason"),
    }


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _ensure_vector_index_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_vectors (
            path TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            memory_type TEXT NOT NULL,
            excerpt TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            updated_at TEXT,
            provider TEXT,
            model TEXT,
            dimensions INTEGER,
            content_hash TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS query_embedding_cache (
            cache_key TEXT PRIMARY KEY,
            query_hash TEXT NOT NULL,
            normalized_query TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            last_used_at INTEGER NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_vectors)")}
    migrations = {
        "provider": "ALTER TABLE memory_vectors ADD COLUMN provider TEXT",
        "model": "ALTER TABLE memory_vectors ADD COLUMN model TEXT",
        "dimensions": "ALTER TABLE memory_vectors ADD COLUMN dimensions INTEGER",
        "content_hash": "ALTER TABLE memory_vectors ADD COLUMN content_hash TEXT",
    }
    for column, statement in migrations.items():
        if column not in columns:
            connection.execute(statement)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_memory_vectors_mtime ON memory_vectors (mtime_ns DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_memory_vectors_provider ON memory_vectors (provider, model, dimensions)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_query_embedding_cache_provider ON query_embedding_cache (provider, model, dimensions)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_query_embedding_cache_last_used ON query_embedding_cache (last_used_at)")
    connection.commit()


def _meta_value(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM vector_index_meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def _set_meta_value(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO vector_index_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _delete_meta_values(connection: sqlite3.Connection, keys: tuple[str, ...]) -> None:
    connection.executemany("DELETE FROM vector_index_meta WHERE key = ?", [(key,) for key in keys])


def _embedding_failure_backoff_sec() -> int:
    return max(int(_embedding_settings().get("failure_backoff_sec", 300)), 0)


def _clear_embedding_failure(connection: sqlite3.Connection) -> None:
    _delete_meta_values(
        connection,
        (EMBEDDING_FAILURE_RETRY_AFTER_META_KEY, EMBEDDING_FAILURE_REASON_META_KEY),
    )


def _record_embedding_failure(connection: sqlite3.Connection, reason: str | None) -> None:
    normalized_reason = reason or "cloudflare_embedding_failed"
    _set_embedding_status("cloudflare", normalized_reason)
    backoff_sec = _embedding_failure_backoff_sec()
    if backoff_sec <= 0:
        _clear_embedding_failure(connection)
        return
    _set_meta_value(connection, EMBEDDING_FAILURE_RETRY_AFTER_META_KEY, str(_unix_time() + backoff_sec))
    _set_meta_value(connection, EMBEDDING_FAILURE_REASON_META_KEY, normalized_reason)


def _active_embedding_failure_backoff(
    connection: sqlite3.Connection,
    provider: dict[str, Any],
) -> tuple[bool, str | None]:
    if provider.get("provider") != "cloudflare":
        return False, None
    retry_after_raw = _meta_value(connection, EMBEDDING_FAILURE_RETRY_AFTER_META_KEY)
    if retry_after_raw is None:
        return False, None
    try:
        retry_after = int(retry_after_raw)
    except (TypeError, ValueError):
        _clear_embedding_failure(connection)
        return False, None
    reason = _meta_value(connection, EMBEDDING_FAILURE_REASON_META_KEY) or "cloudflare_embedding_failed"
    if retry_after > _unix_time():
        _set_embedding_status("cloudflare", reason)
        return True, reason
    _clear_embedding_failure(connection)
    return False, None


def _topic_index_signature(items: list[dict[str, Any]], provider: dict[str, Any]) -> str:
    payload = {
        "provider": provider["provider"],
        "model": provider["model"],
        "dimensions": int(provider["dimensions"]),
        "topics": [
            {
                "path": item["path"],
                "mtime_ns": int(item["mtime_ns"]),
            }
            for item in sorted(items, key=lambda entry: entry["path"])
        ],
    }
    return _content_hash(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _query_cache_limits() -> tuple[int, int]:
    settings = _embedding_settings()
    ttl_sec = int(settings.get("query_cache_ttl_sec", 86400))
    max_entries = int(settings.get("query_cache_max_entries", 256))
    return max(ttl_sec, 0), max(max_entries, 0)


def _query_cache_key(query: str, provider: dict[str, Any]) -> tuple[str, str, str]:
    normalized = _normalized_query(query)
    query_hash = _content_hash(normalized)
    cache_key = ":".join(
        [
            str(provider["provider"]),
            str(provider["model"]),
            str(int(provider["dimensions"])),
            query_hash,
        ]
    )
    return cache_key, query_hash, normalized


def _is_cloudflare_query_cache_provider(provider: dict[str, Any]) -> bool:
    cloudflare_config = _cloudflare_embedding_config()
    return bool(
        cloudflare_config
        and provider.get("provider") == cloudflare_config["provider"]
        and provider.get("model") == cloudflare_config["model"]
        and int(provider.get("dimensions") or 0) == int(cloudflare_config["dimensions"])
    )


def _prune_query_embedding_cache(connection: sqlite3.Connection, now: int | None = None) -> None:
    ttl_sec, max_entries = _query_cache_limits()
    if max_entries == 0:
        connection.execute("DELETE FROM query_embedding_cache")
        return
    current_time = _unix_time() if now is None else now
    if ttl_sec > 0:
        connection.execute("DELETE FROM query_embedding_cache WHERE created_at < ?", (current_time - ttl_sec,))
    overflow = connection.execute(
        "SELECT COUNT(*) FROM query_embedding_cache"
    ).fetchone()[0] - max_entries
    if overflow > 0:
        connection.execute(
            """
            DELETE FROM query_embedding_cache
            WHERE cache_key IN (
                SELECT cache_key FROM query_embedding_cache
                ORDER BY last_used_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )


def _cached_query_embedding(connection: sqlite3.Connection, query: str, provider: dict[str, Any]) -> list[float] | None:
    if not _is_cloudflare_query_cache_provider(provider):
        return None
    ttl_sec, max_entries = _query_cache_limits()
    if ttl_sec == 0 or max_entries == 0:
        return None
    now = _unix_time()
    cache_key, _query_hash, _normalized = _query_cache_key(query, provider)
    row = connection.execute(
        """
        SELECT vector_json, created_at
        FROM query_embedding_cache
        WHERE cache_key = ?
        """,
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    if int(row[1]) < now - ttl_sec:
        connection.execute("DELETE FROM query_embedding_cache WHERE cache_key = ?", (cache_key,))
        return None
    try:
        vector = [float(value) for value in json.loads(row[0])]
    except Exception:
        connection.execute("DELETE FROM query_embedding_cache WHERE cache_key = ?", (cache_key,))
        return None
    connection.execute("UPDATE query_embedding_cache SET last_used_at = ? WHERE cache_key = ?", (now, cache_key))
    return vector


def _store_query_embedding(connection: sqlite3.Connection, query: str, provider: dict[str, Any], vector: list[float]) -> None:
    if not _is_cloudflare_query_cache_provider(provider):
        return
    ttl_sec, max_entries = _query_cache_limits()
    if ttl_sec == 0 or max_entries == 0:
        return
    now = _unix_time()
    cache_key, query_hash, normalized = _query_cache_key(query, provider)
    connection.execute(
        """
        INSERT INTO query_embedding_cache (
            cache_key, query_hash, normalized_query, provider, model, dimensions, vector_json, created_at, last_used_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            vector_json = excluded.vector_json,
            last_used_at = excluded.last_used_at
        """,
        (
            cache_key,
            query_hash,
            normalized,
            provider["provider"],
            provider["model"],
            int(provider["dimensions"]),
            json.dumps(vector, ensure_ascii=False),
            now,
            now,
        ),
    )
    _prune_query_embedding_cache(connection, now)


def _query_embedding_cache_counts(connection: sqlite3.Connection) -> tuple[int, int]:
    ttl_sec, max_entries = _query_cache_limits()
    total = int(connection.execute("SELECT COUNT(*) FROM query_embedding_cache").fetchone()[0])
    if ttl_sec == 0 or max_entries == 0:
        return 0, total
    expired = int(
        connection.execute(
            "SELECT COUNT(*) FROM query_embedding_cache WHERE created_at < ?",
            (_unix_time() - ttl_sec,),
        ).fetchone()[0]
    )
    return max(total - expired, 0), expired


def _vector_index_diagnostics(vector_db: pathlib.Path, topics: list[dict[str, Any]]) -> dict[str, Any]:
    if not vector_db.exists():
        return {
            "indexed_provider_counts": {},
            "stale_topic_count": 0,
            "query_cache_entries": 0,
            "query_cache_expired_entries": 0,
            "last_sync": None,
        }
    current_paths = {item["path"] for item in topics}
    configured_provider = _configured_embedding_provider()
    connection = sqlite3.connect(vector_db)
    try:
        _ensure_vector_index_schema(connection)
        provider_counts: dict[str, int] = {}
        stale_count = 0
        for path, provider, model, dimensions, count in connection.execute(
            """
            SELECT path, provider, model, dimensions, COUNT(*)
            FROM memory_vectors
            GROUP BY path, provider, model, dimensions
            """
        ):
            provider_key = f"{provider or 'unknown'}:{model or 'unknown'}:{int(dimensions or 0)}"
            provider_counts[provider_key] = provider_counts.get(provider_key, 0) + int(count)
            if (
                path not in current_paths
                or provider != configured_provider["provider"]
                or model != configured_provider["model"]
                or int(dimensions or 0) != int(configured_provider["dimensions"])
            ):
                stale_count += int(count)
        query_cache_entries, query_cache_expired_entries = _query_embedding_cache_counts(connection)
        last_sync = _meta_value(connection, "last_sync")
        return {
            "indexed_provider_counts": provider_counts,
            "stale_topic_count": stale_count,
            "query_cache_entries": int(query_cache_entries),
            "query_cache_expired_entries": int(query_cache_expired_entries),
            "last_sync": last_sync,
        }
    finally:
        connection.close()


def sync_vector_index(raw_cwd: str | None = None, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not is_memdir_enabled(raw_cwd):
        return {"synced": False, "reason": "disabled"}
    paths = resolve_project_paths(raw_cwd)
    vector_db = paths["vector_db"]
    ensure_dir(vector_db.parent)
    scanned = items if items is not None else scan_topic_files(raw_cwd)
    current_paths = {item["path"] for item in scanned}
    configured_provider = _configured_embedding_provider()
    if not scanned:
        _set_embedding_status(str(configured_provider["provider"]), None)
    connection = sqlite3.connect(vector_db)
    try:
        _ensure_vector_index_schema(connection)
        signature = _topic_index_signature(scanned, configured_provider)
        last_signature = _meta_value(connection, "topic_signature")
        backoff_active, backoff_reason = _active_embedding_failure_backoff(connection, configured_provider)
        if last_signature == signature:
            _set_embedding_status(str(configured_provider["provider"]), backoff_reason if backoff_active else None)
            connection.commit()
            return {
                "synced": True,
                "vector_db": str(vector_db),
                "provider": configured_provider["provider"],
                "model": configured_provider["model"],
                "dimensions": int(configured_provider["dimensions"]),
                "fallback_reason": backoff_reason if backoff_active else None,
                "upserts": 0,
                "removed": 0,
                "items": len(scanned),
                "skipped": True,
                "last_sync": _meta_value(connection, "last_sync"),
            }
        existing_rows = {
            row[0]: {
                "mtime_ns": row[1],
                "provider": row[2],
                "model": row[3],
                "dimensions": row[4],
                "content_hash": row[5],
            }
            for row in connection.execute("SELECT path, mtime_ns, provider, model, dimensions, content_hash FROM memory_vectors")
        }
        removed = [path for path in existing_rows if path not in current_paths]
        if removed:
            connection.executemany("DELETE FROM memory_vectors WHERE path = ?", [(path,) for path in removed])

        upserts = 0
        pending: list[tuple[dict[str, Any], str, str]] = []
        for item in scanned:
            vector_text = _topic_vector_text(item)
            content_hash = _content_hash(vector_text)
            existing = existing_rows.get(item["path"])
            if (
                existing
                and existing.get("provider") == configured_provider["provider"]
                and existing.get("model") == configured_provider["model"]
                and int(existing.get("dimensions") or 0) == int(configured_provider["dimensions"])
                and existing.get("content_hash") == content_hash
            ):
                if existing.get("mtime_ns") != int(item["mtime_ns"]):
                    connection.execute(
                        """
                        UPDATE memory_vectors
                        SET filename = ?, name = ?, description = ?, memory_type = ?, excerpt = ?, mtime_ns = ?, updated_at = ?
                        WHERE path = ?
                        """,
                        (
                            item["filename"],
                            item["name"],
                            item.get("description"),
                            item["type"],
                            item["excerpt"],
                            int(item["mtime_ns"]),
                            item.get("updated_at"),
                            item["path"],
                        ),
                    )
                continue
            pending.append((item, vector_text, content_hash))

        vectors: list[list[float]] = []
        active_provider = configured_provider
        fallback_reason = None
        if pending:
            if backoff_active and configured_provider["provider"] == "cloudflare":
                fallback_reason = backoff_reason or "cloudflare_embedding_failed"
            else:
                vectors, active_provider, fallback_reason = _embed_texts(
                    [entry[1] for entry in pending],
                    configured_provider,
                    allow_local_fallback=configured_provider["provider"] != "cloudflare",
                )
            if fallback_reason and configured_provider["provider"] == "cloudflare":
                if not backoff_active:
                    _record_embedding_failure(connection, fallback_reason)
                pending_paths = [(entry[0]["path"],) for entry in pending]
                pending_removed = sum(1 for entry in pending if entry[0]["path"] in existing_rows)
                if pending_paths:
                    connection.executemany("DELETE FROM memory_vectors WHERE path = ?", pending_paths)
                connection.commit()
                return {
                    "synced": True,
                    "vector_db": str(vector_db),
                    "provider": configured_provider["provider"],
                    "model": configured_provider["model"],
                    "dimensions": int(configured_provider["dimensions"]),
                    "fallback_reason": fallback_reason,
                    "upserts": 0,
                    "removed": len(removed) + pending_removed,
                    "items": len(scanned),
                    "skipped": False,
                    "last_sync": _meta_value(connection, "last_sync"),
                }
            if active_provider["provider"] == "cloudflare":
                _clear_embedding_failure(connection)
        else:
            _set_embedding_status(str(configured_provider["provider"]), backoff_reason if backoff_active else None)

        for (item, _vector_text, content_hash), vector in zip(pending, vectors):
            connection.execute(
                """
                INSERT INTO memory_vectors (
                    path, filename, name, description, memory_type, excerpt, vector_json, mtime_ns, updated_at,
                    provider, model, dimensions, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename = excluded.filename,
                    name = excluded.name,
                    description = excluded.description,
                    memory_type = excluded.memory_type,
                    excerpt = excluded.excerpt,
                    vector_json = excluded.vector_json,
                    mtime_ns = excluded.mtime_ns,
                    updated_at = excluded.updated_at,
                    provider = excluded.provider,
                    model = excluded.model,
                    dimensions = excluded.dimensions,
                    content_hash = excluded.content_hash
                """,
                (
                    item["path"],
                    item["filename"],
                    item["name"],
                    item.get("description"),
                    item["type"],
                    item["excerpt"],
                    json.dumps(vector, ensure_ascii=False),
                    int(item["mtime_ns"]),
                    item.get("updated_at"),
                    active_provider["provider"],
                    active_provider["model"],
                    int(active_provider["dimensions"]),
                    content_hash,
                ),
            )
            upserts += 1
        last_sync = utc_now_iso()
        _set_meta_value(connection, "topic_signature", signature)
        _set_meta_value(connection, "last_sync", last_sync)
        connection.commit()
        return {
            "synced": True,
            "vector_db": str(vector_db),
            "provider": active_provider["provider"],
            "model": active_provider["model"],
            "dimensions": int(active_provider["dimensions"]),
            "fallback_reason": fallback_reason,
            "upserts": upserts,
            "removed": len(removed),
            "items": len(scanned),
            "skipped": False,
            "last_sync": last_sync,
        }
    finally:
        connection.close()


def _rebuild_manifest(project_root: pathlib.Path, manifest_path: pathlib.Path, items: list[dict[str, Any]]) -> None:
    existing = _load_json_document(manifest_path) or {}
    recent_limit = int(memdir_settings().get("manifest_recent_items", 20))
    recent_topics = [
        {
            "id": item["id"],
            "name": item["name"],
            "type": item["type"],
            "description": item.get("description"),
            "filename": item["filename"],
            "updated_at": item.get("updated_at"),
        }
        for item in items[:recent_limit]
    ]
    updated_at = str(existing.get("updated_at") or utc_now_iso())
    if items:
        updated_at = str(items[0].get("updated_at") or updated_at)
    payload = {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "project_root": str(project_root),
        "project_slug": project_slug(project_root),
        "storage": {
            "source_of_truth": "json",
            "topics_dir": str(memdir_settings().get("topics_dir_name", TOPICS_DIR_NAME)),
            "vector_index": str(_vector_settings().get("index_name", VECTOR_DB_NAME)),
            "vector_backend": str(_vector_settings().get("index_backend", "sqlite")),
        },
        "rules": _memory_rules(),
        "memory_count": len(items),
        "recent_topics": recent_topics,
        "updated_at": updated_at,
    }
    _write_json_if_changed(manifest_path, payload)


def _ensure_project_memdir_with_items(raw_cwd: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paths = resolve_project_paths(raw_cwd)
    project_root = paths["project_root"]
    memdir = paths["memdir"]
    manifest_path = paths["entrypoint"]
    topics_dir = paths["topics_dir"]
    vector_db = paths["vector_db"]
    if not is_memdir_enabled(str(project_root)):
        return {
            "project_root": str(project_root),
            "memdir": str(memdir),
            "entrypoint": str(manifest_path),
            "topics_dir": str(topics_dir),
            "vector_db": str(vector_db),
            "enabled": False,
            "migrated_aliases": [],
            "migrated_legacy_files": [],
        }, []

    migrated_aliases = _migrate_alias_memdirs(project_root, memdir)
    ensure_dir(memdir)
    ensure_dir(topics_dir)
    if not manifest_path.exists():
        _write_json_if_changed(manifest_path, _entrypoint_template(project_root))
    migrated_legacy_files = _migrate_legacy_markdown_storage(project_root, memdir, topics_dir, manifest_path)
    items = scan_topic_files(str(project_root))
    _rebuild_manifest(project_root, manifest_path, items)
    sync_vector_index(str(project_root), items)
    return {
        "project_root": str(project_root),
        "memdir": str(memdir),
        "entrypoint": str(manifest_path),
        "topics_dir": str(topics_dir),
        "vector_db": str(vector_db),
        "enabled": True,
        "migrated_aliases": migrated_aliases,
        "migrated_legacy_files": migrated_legacy_files,
    }, items


def ensure_project_memdir(raw_cwd: str | None = None) -> dict[str, Any]:
    ensured, _items = _ensure_project_memdir_with_items(raw_cwd)
    return ensured


def find_relevant_memories(
    query: str,
    raw_cwd: str | None = None,
    already_surfaced: set[str] | None = None,
    *,
    require_lexical_match: bool = False,
) -> list[dict[str, Any]]:
    settings = memdir_settings()
    limit = int(settings["max_relevant_memories"])
    fallback = int(settings.get("recent_fallback_items", 0))
    min_score = float(settings.get("min_relevant_score", 0))
    used = already_surfaced or set()
    tokens = _tokenize(query)
    ensured, items = _ensure_project_memdir_with_items(raw_cwd)
    if not ensured.get("enabled"):
        return []
    if not tokens:
        return [item for item in items if item["path"] not in used][:fallback] if fallback > 0 else []

    path_to_item = {item["path"]: item for item in items}
    connection = sqlite3.connect(ensured["vector_db"])
    try:
        _ensure_vector_index_schema(connection)
        query_provider = _configured_embedding_provider()
        lexical_scores: dict[str, float] = {}
        candidate_scores: dict[str, tuple[float, float, dict[str, Any]]] = {}
        for item in items:
            if item["path"] in used:
                continue
            haystack = _topic_vector_text(item).lower()
            lexical_score = 0.0
            for token in tokens:
                lexical_score += haystack.count(token) * 4
            if lexical_score > 0:
                lexical_scores[item["path"]] = lexical_score
                candidate_scores[item["path"]] = (lexical_score, float(item["mtime_ns"]), item)

        vector_rows = list(
            connection.execute(
                """
                SELECT path, vector_json, mtime_ns
                FROM memory_vectors
                WHERE provider = ? AND model = ? AND dimensions = ?
                ORDER BY mtime_ns DESC
                """,
                (
                    query_provider["provider"],
                    query_provider["model"],
                    int(query_provider["dimensions"]),
                ),
            )
        )
        query_vector: list[float] = []
        if vector_rows:
            backoff_active, backoff_reason = _active_embedding_failure_backoff(connection, query_provider)
            if backoff_active:
                _set_embedding_status(str(query_provider["provider"]), backoff_reason)
            else:
                cached_vector = _cached_query_embedding(connection, query, query_provider)
                if cached_vector is not None:
                    query_vector = cached_vector
                else:
                    query_vectors, query_provider, fallback_reason = _embed_texts(
                        [query],
                        query_provider,
                        allow_local_fallback=query_provider["provider"] != "cloudflare",
                    )
                    if fallback_reason and query_provider["provider"] == "cloudflare":
                        _record_embedding_failure(connection, fallback_reason)
                    else:
                        query_vector = query_vectors[0] if query_vectors else []
                        if query_provider["provider"] == "cloudflare":
                            _clear_embedding_failure(connection)
                        if query_vector:
                            _store_query_embedding(connection, query, query_provider, query_vector)

        if query_vector:
            candidate_scores = {}

        for row in vector_rows:
            path, vector_json, mtime_ns = row
            if path in used or path not in path_to_item:
                continue
            item = path_to_item[path]
            try:
                stored_vector = json.loads(vector_json)
            except Exception:
                stored_vector = []
            similarity = _cosine_similarity(query_vector, stored_vector) if query_vector else 0.0
            vector_score = max(similarity, 0.0) * _vector_score_weight()
            lexical_score = lexical_scores.get(path, 0.0)
            total_score = lexical_score + vector_score
            if require_lexical_match and lexical_score <= 0:
                continue
            if similarity < _min_vector_similarity():
                continue
            if total_score >= min_score:
                candidate_scores[path] = (total_score, float(mtime_ns), item)
        connection.commit()
        candidates = [entry for entry in candidate_scores.values() if entry[0] >= min_score]
        candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        if candidates:
            return [item for _, _, item in candidates[:limit]]
    finally:
        connection.close()

    if fallback <= 0:
        return []
    return [item for item in items if item["path"] not in used][:fallback]


def _memory_rules() -> list[str]:
    return MEMORY_RULE_LINES


def get_prologue_path(raw_cwd: str | None = None) -> pathlib.Path:
    _ = raw_cwd
    return pathlib.Path(__file__).resolve().parent.parent / PROLOGUE_NAME


def build_session_start_context(raw_cwd: str | None = None) -> dict[str, str]:
    ensured = ensure_project_memdir(raw_cwd)
    paths = resolve_project_paths(raw_cwd)
    prologue_path = get_prologue_path(raw_cwd)
    entrypoint_path = paths["entrypoint"]
    enabled = is_memdir_enabled(str(paths["project_root"]))

    lines: list[str] = []
    if enabled:
        if prologue_path.exists():
            lines.append(f"Prologue<{prologue_path}>")
        if entrypoint_path.exists() or ensured.get("entrypoint"):
            lines.append(f"Manifest<{entrypoint_path}>")
    message_parts: list[str] = []
    if lines:
        message_parts.append(f"Read these files in order: {' | '.join(lines).strip()}")
    if enabled:
        message_parts.append(f"memdir_embedding_model = {_embedding_model_for_context()}")
        message_parts.append(f"memdir_extractor_provider = {_extractor_provider_for_context()}")
        message_parts.append(f"memdir_extractor_model = {_extractor_model_for_context()}")
    message = "\n".join(message_parts)
    embedding_model = _embedding_model_for_context() if enabled else ""
    return {
        "hookEventName": "SessionStart",
        "additionalContext": message,
        "embeddingModel": embedding_model,
    }


def _format_recalled_memory_summaries(recalled: list[dict[str, Any]]) -> str:
    lines = [
        "Use recalled memory summaries first. If insufficient, read the listed file under MemoryJSONDir."
    ]
    grouped: dict[str, list[tuple[str, str]]] = {}
    for item in recalled:
        path = str(item.get("path") or "").strip()
        if "\\" in path or re.match(r"^[A-Za-z]:", path):
            memory_path = pathlib.PureWindowsPath(path)
        else:
            memory_path = pathlib.PurePosixPath(path)
        parent = str(memory_path.parent) if path else ""
        filename = memory_path.name if path else str(item.get("filename") or "").strip()
        description = str(item.get("description") or "").strip()
        suffix = f": {_truncate_chars(description, 120)}" if description else ""
        if not path or parent == "." or not filename:
            lines.append(f"- MemoryJSON<{path}>{suffix}")
            continue
        grouped.setdefault(parent, []).append((filename, suffix))
    for parent, items in grouped.items():
        lines.append(f"MemoryJSONDir<{parent}>")
        for filename, suffix in items:
            lines.append(f"- {filename}{suffix}")
    return "\n".join(lines)


def build_memdir_context(
    query: str,
    raw_cwd: str | None = None,
    *,
    include_core_paths: bool = True,
    require_lexical_match: bool = True,
) -> dict[str, Any]:
    ensured = ensure_project_memdir(raw_cwd)
    paths = resolve_project_paths(raw_cwd)
    project_root = paths["project_root"]
    memdir = paths["memdir"]
    entrypoint_path = paths["entrypoint"]
    enabled = is_memdir_enabled(str(project_root))
    prologue_path = get_prologue_path(raw_cwd)
    recalled = (
        find_relevant_memories(query, raw_cwd, require_lexical_match=require_lexical_match) if enabled else []
    )

    core_lines: list[str] = []
    if enabled and include_core_paths:
        if prologue_path.exists():
            core_lines.append(f"Prologue<{prologue_path}>")
        if entrypoint_path.exists():
            core_lines.append(f"Manifest<{entrypoint_path}>")
    message_parts: list[str] = []
    if core_lines:
        message_parts.append(f"Read core files in order: {' | '.join(core_lines).strip()}")
    if enabled:
        extraction_notice = _load_extraction_failure_notice(raw_cwd)
        if extraction_notice:
            message_parts.append(extraction_notice)
    if enabled and recalled:
        message_parts.append(_format_recalled_memory_summaries(recalled))
    message = "\n\n".join(message_parts)
    return {
        "project_root": str(project_root),
        "memdir": str(memdir),
        "entrypoint": str(entrypoint_path),
        "topics_dir": ensured.get("topics_dir"),
        "vector_db": ensured.get("vector_db"),
        "recalled_files": [item["path"] for item in recalled],
        "system_message": message,
        "enabled": enabled,
    }


def memdir_doctor(raw_cwd: str | None = None) -> dict[str, Any]:
    ensured = ensure_project_memdir(raw_cwd)
    paths = resolve_project_paths(raw_cwd)
    enabled = is_memdir_enabled(raw_cwd)
    entrypoint_path = paths["entrypoint"]
    topics = scan_topic_files(raw_cwd)
    entrypoint = None
    if entrypoint_path.exists():
        entrypoint = truncate_entrypoint_content(entrypoint_path.read_text(encoding="utf-8"))
    index_diagnostics = _vector_index_diagnostics(paths["vector_db"], topics)
    return {
        "project_root": str(paths["project_root"]),
        "memdir": str(paths["memdir"]),
        "entrypoint": str(entrypoint_path),
        "topics_dir": str(paths["topics_dir"]),
        "vector_db": str(paths["vector_db"]),
        "enabled": enabled,
        "memdir_exists": paths["memdir"].exists(),
        "entrypoint_exists": entrypoint_path.exists(),
        "vector_db_exists": paths["vector_db"].exists(),
        "topic_count": len(topics),
        "entrypoint_preview": entrypoint,
        "session_state": get_memdir_session_state(raw_cwd) if enabled else None,
        "embedding": _embedding_doctor_status(),
        "indexed_provider_counts": index_diagnostics["indexed_provider_counts"],
        "stale_topic_count": index_diagnostics["stale_topic_count"],
        "query_cache_entries": index_diagnostics["query_cache_entries"],
        "query_cache_expired_entries": index_diagnostics["query_cache_expired_entries"],
        "last_sync": index_diagnostics["last_sync"],
        "ensure_result": ensured,
    }


def _snapshot_storage_files(memdir: pathlib.Path, topics_dir: pathlib.Path, manifest_path: pathlib.Path, vector_db: pathlib.Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in [manifest_path, vector_db, *sorted(topics_dir.rglob("*.json"))]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        snapshot[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _detect_written_paths(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[str]:
    changed: list[str] = []
    for path, current in after.items():
        if before.get(path) != current:
            changed.append(path)
    return sorted(changed)


def _build_extraction_prompt(
    *,
    project_root: pathlib.Path,
    memdir: pathlib.Path,
    topics_dir: pathlib.Path,
    user_text: str,
    assistant_text: str,
    existing_memories: str,
) -> str:
    manifest = (
        "## Existing topic JSON files\n\n"
        f"{existing_memories}\n\n"
        "Check this list before writing, but judge duplicates narrowly. Do not merge new information when it broadens an existing topic's scope.\n"
        if existing_memories
        else "## Existing topic JSON files\n\nNo topic JSON files exist yet.\n"
    )
    return (
        "Update project reference memory from the conversation and existing topic JSON files only.\n"
        "The current working directory is the topics directory.\n"
        "Only create or modify JSON files under the topics directory.\n"
        "Do not create Markdown files.\n"
        "Do not edit manifest.json or vector_index.sqlite3; they are harness-managed.\n"
        "No code, git, or external inspection.\n\n"
        f"Root: {project_root}\n"
        f"Dir: {memdir}\n"
        f"Topics Dir: {topics_dir}\n\n"
        "Rules: "
        + "; ".join(_memory_rules())
        + ".\n\n"
        "Each topic JSON must follow this schema:\n"
        "{\n"
        '  "schema_version": 2,\n'
        '  "id": "stable-topic-id",\n'
        '  "name": "short title",\n'
        '  "description": "one-line summary",\n'
        '  "type": "user|feedback|project|reference",\n'
        '  "content": "plain text reference note",\n'
        '  "keywords": ["keyword"],\n'
        '  "updated_at": "ISO-8601 timestamp",\n'
        '  "last_thread_id": "thread id or null"\n'
        "}\n\n"
        "Write concise reference notes in plain text; no frontmatter, no markdown headings, no per-turn dumps.\n\n"
        "Recommended category examples for topic naming and keywords only, not schema fields: "
        "user-response-rule, user-profile, user-preference, project-identity, project-setup, "
        "project-workflow, technical-reference, issue-investigation, decision-record, feedback.\n"
        "Do not store one-off chatter, real-time reactions, jokes, or information unlikely to be reusable.\n\n"
        + manifest
        + "\n"
        "## Conversation to distill\n\n"
        f"User:\n{user_text.strip()}\n\n"
        f"Assistant:\n{assistant_text.strip()}\n"
    )


def _extract_with_codex(
    *,
    memdir: pathlib.Path,
    project_root: pathlib.Path,
    topics_dir: pathlib.Path,
    user_text: str,
    assistant_text: str,
    existing_memories: str,
) -> dict[str, Any]:
    settings = _extractor_settings()
    prompt = _build_extraction_prompt(
        project_root=project_root,
        memdir=memdir,
        topics_dir=topics_dir,
        user_text=user_text,
        assistant_text=assistant_text,
        existing_memories=existing_memories,
    )
    extract_codex_model = _configured_extractor_model(settings, "codex_model", CODEX_DEFAULT_MODEL)
    ensure_dir(topics_dir)
    result = run_codex_exec(
        codex_bin=str(settings.get("codex_bin", "codex")),
        cwd=topics_dir,
        prompt=prompt,
        sandbox=str(settings.get("codex_sandbox") or "danger-full-access"),
        model=extract_codex_model,
        extra_env={
            "CODEX_MEMDIR_SKIP": "1",
            "CODEX_PROJECT_KNOWLEDGE_SKIP": "1",
        },
        timeout_sec=int(settings.get("timeout_sec", 90)),
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "reason": "codex_extraction_failed",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:800],
            "stdout": (result.stdout or "")[:800],
        }
    return {"ok": True, "reason": "ok"}


def _extract_with_agy(
    *,
    memdir: pathlib.Path,
    project_root: pathlib.Path,
    topics_dir: pathlib.Path,
    user_text: str,
    assistant_text: str,
    existing_memories: str,
) -> dict[str, Any]:
    settings = _extractor_settings()
    prompt = _build_extraction_prompt(
        project_root=project_root,
        memdir=memdir,
        topics_dir=topics_dir,
        user_text=user_text,
        assistant_text=assistant_text,
        existing_memories=existing_memories,
    )
    command = [
        str(settings.get("agy_bin") or "agy"),
        "-p",
        prompt,
        "--dangerously-skip-permissions",
    ]
    model = _configured_extractor_model(settings, "agy_model", AGY_DEFAULT_MODEL)
    if model:
        command.extend(["--model", model])
    try:
        result = subprocess.run(
            command,
            cwd=memdir,
            text=True,
            capture_output=True,
            timeout=int(settings.get("agy_extraction_timeout_sec", settings.get("timeout_sec", 90))),
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "agy_extraction_failed",
            "error": f"timeout:{exc.timeout}",
            "stdout": str(exc.stdout or "")[:800],
            "stderr": str(exc.stderr or "")[:800],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": "agy_extraction_failed",
            "error": str(exc)[:240],
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "reason": "agy_extraction_failed",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:800],
            "stdout": (result.stdout or "")[:800],
        }
    return {"ok": True, "reason": "ok"}


def _extract_with_local_cli(
    *,
    memdir: pathlib.Path,
    project_root: pathlib.Path,
    topics_dir: pathlib.Path,
    user_text: str,
    assistant_text: str,
    existing_memories: str,
) -> dict[str, Any]:
    settings = _extractor_settings()
    prompt = _build_extraction_prompt(
        project_root=project_root,
        memdir=memdir,
        topics_dir=topics_dir,
        user_text=user_text,
        assistant_text=assistant_text,
        existing_memories=existing_memories,
    )
    command_template = os.path.expandvars(str(settings.get("local_cli_command") or "").strip())
    if not command_template:
        return {
            "ok": False,
            "reason": "local_cli_extraction_failed",
            "error": "extract_local_cli_command is empty",
        }
    command = _resolve_python_launcher_command(_split_command_template(command_template))
    if not command:
        return {
            "ok": False,
            "reason": "local_cli_extraction_failed",
            "error": "extract_local_cli_command parsed to no executable",
        }
    uses_prompt_arg = any("{prompt}" in part for part in command)
    if uses_prompt_arg:
        command = [part.replace("{prompt}", prompt) for part in command]
        input_text = None
    else:
        input_text = prompt
    try:
        result = subprocess.run(
            command,
            cwd=memdir,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=int(settings.get("local_cli_extraction_timeout_sec", settings.get("timeout_sec", 120))),
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "reason": "local_cli_extraction_failed",
            "error": f"timeout:{exc.timeout}",
            "stdout": str(exc.stdout or "")[:800],
            "stderr": str(exc.stderr or "")[:800],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reason": "local_cli_extraction_failed",
            "error": str(exc)[:240],
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "reason": "local_cli_extraction_failed",
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[:800],
            "stdout": (result.stdout or "")[:800],
        }
    return {"ok": True, "reason": "ok"}


def _validate_topic_json_files(topics_dir: pathlib.Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(topics_dir.rglob("*.json")):
        payload = _load_json_document(path)
        if payload is None:
            errors.append(f"invalid_json:{path}")
            continue
        normalized = _coerce_topic_payload(path, payload)
        if normalized is None:
            errors.append(f"missing_content:{path}")
            continue
    return errors


def extract_memories_from_event(
    *,
    raw_cwd: str,
    user_text: str,
    assistant_text: str,
    thread_id: str,
) -> dict[str, Any]:
    settings = memdir_settings()
    if not settings.get("enabled", True) or not is_memdir_enabled(raw_cwd):
        return {"updated": False, "reason": "disabled"}
    if not user_text.strip() or not assistant_text.strip():
        return {"updated": False, "reason": "missing_text"}

    ensured = ensure_project_memdir(raw_cwd)
    project_root = pathlib.Path(ensured["project_root"])
    memdir = pathlib.Path(ensured["memdir"])
    topics_dir = pathlib.Path(ensured["topics_dir"])
    manifest_path = pathlib.Path(ensured["entrypoint"])
    vector_db = pathlib.Path(ensured["vector_db"])
    before = _snapshot_storage_files(memdir, topics_dir, manifest_path, vector_db)
    existing_memories = format_memory_manifest(scan_topic_files(raw_cwd))
    extractor = str(_extractor_settings().get("provider") or "agy").strip().lower()
    if extractor == "codex":
        result = _extract_with_codex(
            memdir=memdir,
            project_root=project_root,
            topics_dir=topics_dir,
            user_text=user_text,
            assistant_text=assistant_text,
            existing_memories=existing_memories,
        )
    elif extractor == "agy":
        result = _extract_with_agy(
            memdir=memdir,
            project_root=project_root,
            topics_dir=topics_dir,
            user_text=user_text,
            assistant_text=assistant_text,
            existing_memories=existing_memories,
        )
    elif extractor == "local_cli":
        result = _extract_with_local_cli(
            memdir=memdir,
            project_root=project_root,
            topics_dir=topics_dir,
            user_text=user_text,
            assistant_text=assistant_text,
            existing_memories=existing_memories,
        )
    else:
        _record_extraction_failure_status(
            memdir,
            extractor,
            {"reason": f"unsupported_extractor:{extractor}", "error": "unsupported extractor provider"},
        )
        return {
            "updated": False,
            "reason": f"unsupported_extractor:{extractor}",
            "thread_id": thread_id,
            "memdir": str(memdir),
        }
    validation_errors = _validate_topic_json_files(topics_dir)
    if validation_errors:
        _record_extraction_failure_status(
            memdir,
            extractor,
            {"reason": "invalid_topic_json", "error": "; ".join(validation_errors)},
        )
        return {
            "updated": False,
            "reason": "invalid_topic_json",
            "errors": validation_errors,
            "thread_id": thread_id,
            "memdir": str(memdir),
        }
    items = scan_topic_files(raw_cwd)
    _rebuild_manifest(project_root, manifest_path, items)
    sync_vector_index(raw_cwd, items)
    after = _snapshot_storage_files(memdir, topics_dir, manifest_path, vector_db)
    written_paths = _detect_written_paths(before, after)
    if not result.get("ok"):
        _record_extraction_failure_status(memdir, extractor, result)
        return {
            "updated": False,
            "reason": result.get("reason", "extract_failed"),
            "error": result.get("error"),
            "output": result.get("output"),
            "llm_usage": result.get("usage"),
            "llm_elapsed_ms": result.get("elapsed_ms"),
            "thread_id": thread_id,
            "memdir": str(memdir),
            "extractor": extractor,
        }
    _clear_extraction_failure_status(memdir)
    return {
        "updated": bool(written_paths),
        "reason": "ok" if written_paths else "no_changes",
        "written_paths": written_paths,
        "topic_files": [path for path in written_paths if str(topics_dir) in path and path.endswith(".json")],
        "thread_id": thread_id,
        "memdir": str(memdir),
        "extractor": extractor,
        "llm_usage": result.get("usage"),
        "llm_elapsed_ms": result.get("elapsed_ms"),
    }
