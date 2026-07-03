# Function: Provide the memdir settings loader.
# Purpose: Merge root harness.toml with defaults so memdir features use consistent settings.
from __future__ import annotations

import copy
import os
import pathlib
import tomllib
from typing import Any


CODEX_ROOT = pathlib.Path(__file__).resolve().parents[3]
HARNESS_CONFIG_PATH = CODEX_ROOT / "harness.toml"

DEFAULTS: dict[str, Any] = {
    "memdir": {
        "enabled": True,
        "base_dir": str(CODEX_ROOT / "memories" / "memdir" / "projects"),
        "disabled_project_roots": [],
        "graph_db_name": "{project_slug}.sqlite3",
        "max_entrypoint_lines": 200,
        "max_entrypoint_bytes": 25000,
        "max_relevant_memories": 5,
        "recent_fallback_items": 0,
        "min_relevant_score": 6,
        "max_graph_hops": 2,
        "max_seed_nodes": 24,
        "user_prompt_submit_max_recalled_distance": 0,
        "user_prompt_submit_allow_expansion_seed": False,
        "user_prompt_submit_min_primary_seed_terms": 2,
        "user_prompt_submit_single_term_min_weight": 3.0,
        "vector": {},
        "embedding": {},
        "storage": {
            "mode": "plugin",
            "project_dir_name": ".project-memdir",
        },
        "extractor": {
            "codex_sandbox": "danger-full-access",
            "codex_model": "codex-default-model",
            "agy_model": "agy-default-model",
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_path_setting(key_path: tuple[str, ...]) -> bool:
    return key_path in {
        ("memdir", "base_dir"),
        ("memdir", "disabled_project_roots"),
    }


def _expand_value(value: Any, key_path: tuple[str, ...] = ()) -> Any:
    env = {
        **os.environ,
        "CODEX_ROOT": str(CODEX_ROOT),
        "HOME": str(pathlib.Path.home()),
    }
    if isinstance(value, str):
        expanded = os.path.expandvars(value.replace("${CODEX_ROOT}", env["CODEX_ROOT"]).replace("${HOME}", env["HOME"]))
        if _is_path_setting(key_path) and "://" not in expanded:
            return str(pathlib.Path(expanded).expanduser())
        return expanded
    if isinstance(value, list):
        return [_expand_value(item, key_path) for item in value]
    if isinstance(value, dict):
        return {key: _expand_value(item, (*key_path, str(key))) for key, item in value.items()}
    return value


def _move_legacy_memdir_keys(memdir: dict[str, Any], section_name: str, aliases: dict[str, str]) -> None:
    section = memdir.get(section_name)
    if not isinstance(section, dict):
        section = {}
        memdir[section_name] = section
    for legacy_key, section_key in aliases.items():
        if legacy_key not in memdir:
            continue
        if section_key not in section:
            section[section_key] = memdir[legacy_key]
        del memdir[legacy_key]


def _normalize_memdir_runtime_sections(payload: dict[str, Any]) -> None:
    memdir = payload.get("memdir")
    if not isinstance(memdir, dict):
        return
    _move_legacy_memdir_keys(
        memdir,
        "vector",
        {
            "vector_index_name": "index_name",
            "vector_index_backend": "index_backend",
            "vector_dimensions": "dimensions",
            "vector_score_weight": "score_weight",
            "min_vector_similarity": "min_similarity",
        },
    )
    _move_legacy_memdir_keys(
        memdir,
        "embedding",
        {
            "embedding_failure_backoff_sec": "failure_backoff_sec",
            "query_embedding_cache_ttl_sec": "query_cache_ttl_sec",
            "query_embedding_cache_max_entries": "query_cache_max_entries",
        },
    )
    _move_legacy_memdir_keys(
        memdir,
        "extractor",
        {
            "extractor_provider": "provider",
            "extract_timeout_sec": "timeout_sec",
            "extract_codex_model": "codex_model",
            "codex_bin": "codex_bin",
            "extract_agy_bin": "agy_bin",
            "extract_agy_extraction_timeout_sec": "agy_extraction_timeout_sec",
            "extract_agy_model": "agy_model",
            "extract_local_cli_command": "local_cli_command",
            "extract_local_cli_extraction_timeout_sec": "local_cli_extraction_timeout_sec",
        },
    )

    embedding = memdir.get("embedding")
    if not isinstance(embedding, dict):
        return

    if "model" in embedding:
        embedding["CLOUDFLARE_MODEL"] = embedding["model"]
    elif "CLOUDFLARE_MODEL" in embedding:
        embedding["model"] = embedding["CLOUDFLARE_MODEL"]

    if "dimensions" in embedding:
        embedding["CLOUDFLARE_DIMENSIONS"] = embedding["dimensions"]
    elif "CLOUDFLARE_DIMENSIONS" in embedding:
        embedding["dimensions"] = embedding["CLOUDFLARE_DIMENSIONS"]

    if "timeout_sec" in embedding:
        embedding["CLOUDFLARE_TIMEOUT_SEC"] = embedding["timeout_sec"]
    elif "CLOUDFLARE_TIMEOUT_SEC" in embedding:
        embedding["timeout_sec"] = embedding["CLOUDFLARE_TIMEOUT_SEC"]


def load_settings() -> dict[str, Any]:
    payload = copy.deepcopy(DEFAULTS)
    if HARNESS_CONFIG_PATH.exists():
        with HARNESS_CONFIG_PATH.open("rb") as handle:
            loaded = tomllib.load(handle)
        if isinstance(loaded, dict):
            payload = _deep_merge(payload, loaded)
    _normalize_memdir_runtime_sections(payload)
    return _expand_value(payload)
