from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib import memdir  # noqa: E402


def _settings(base_dir: pathlib.Path) -> dict[str, object]:
    settings = dict(memdir.memdir_settings())
    vector = dict(settings.get("vector", {}))
    vector.setdefault("dimensions", 96)
    vector.setdefault("score_weight", 12)
    vector.setdefault("min_similarity", 0.2)
    embedding = dict(settings.get("embedding", {}))
    embedding.update(
        {
            "model": "@cf/google/embeddinggemma-300m",
            "dimensions": 768,
            "timeout_sec": 15,
            "failure_backoff_sec": 300,
            "CLOUDFLARE_ACCOUNT_ID": "account-123",
            "CLOUDFLARE_API_TOKEN": "secret-token",
        }
    )
    settings.update(
        {
            "enabled": True,
            "base_dir": str(base_dir),
            "disabled_project_roots": [],
            "max_relevant_memories": 3,
            "min_relevant_score": 0,
            "vector": vector,
            "embedding": embedding,
        }
    )
    return settings


def _topic(path: pathlib.Path, *, content: str = "Android developer profile memory.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": path.stem,
                "name": "Developer profile",
                "description": "User Android developer profile.",
                "type": "user",
                "content": content,
                "keywords": ["android", "developer"],
                "updated_at": "2026-04-27T00:00:00Z",
                "last_thread_id": "thread-1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class FakeCloudflareResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors

    def __enter__(self) -> "FakeCloudflareResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps({"success": True, "result": {"data": self.vectors}}).encode("utf-8")


class MemdirEmbeddingProviderTests(unittest.TestCase):
    def test_cloudflare_embedding_config_can_read_environment(self) -> None:
        with (
            mock.patch.object(memdir, "load_settings", return_value={"memdir": {"enabled": True, "embedding": {}}}),
            mock.patch.dict(
                os.environ,
                {
                    "CLOUDFLARE_ACCOUNT_ID": "env-account",
                    "CLOUDFLARE_API_TOKEN": "env-token",
                    "CLOUDFLARE_MODEL": "@cf/test/model",
                    "CLOUDFLARE_DIMENSIONS": "3",
                    "CLOUDFLARE_TIMEOUT_SEC": "9",
                },
                clear=False,
            ),
        ):
            config = memdir._cloudflare_embedding_config()

        self.assertEqual(config["account_id"], "env-account")
        self.assertEqual(config["api_token"], "env-token")
        self.assertEqual(config["model"], "@cf/test/model")
        self.assertEqual(config["dimensions"], 3)
        self.assertEqual(config["timeout_sec"], 9)

    def test_cloudflare_embedding_sync_calls_api_and_stores_metadata(self) -> None:
        captured: dict[str, object] = {}
        vector = [0.0] * 768
        vector[0] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeCloudflareResponse([vector])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                result = memdir.sync_vector_index(str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                row = connection.execute(
                    "SELECT provider, model, dimensions, content_hash, vector_json FROM memory_vectors WHERE path = ?",
                    (str(topic_path),),
                ).fetchone()
            finally:
                connection.close()

        self.assertTrue(result["synced"])
        self.assertEqual(result["provider"], "cloudflare")
        self.assertEqual(captured["url"], "https://api.cloudflare.com/client/v4/accounts/account-123/ai/run/@cf/google/embeddinggemma-300m")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret-token")
        self.assertIn("text", captured["body"])
        self.assertEqual(len(captured["body"]["text"]), 1)
        self.assertEqual(captured["timeout"], 15)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "cloudflare")
        self.assertEqual(row[1], "@cf/google/embeddinggemma-300m")
        self.assertEqual(row[2], 768)
        self.assertEqual(len(row[3]), 64)
        self.assertEqual(json.loads(row[4]), vector)

    def test_content_hash_match_reuses_existing_cloudflare_vector(self) -> None:
        calls = 0
        vector = [0.0] * 768
        vector[1] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            return FakeCloudflareResponse([vector])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                items = memdir.scan_topic_files(str(project))
                self.assertEqual(memdir.sync_vector_index(str(project), items)["upserts"], 1)
                newer_items = [dict(items[0], mtime_ns=int(items[0]["mtime_ns"]) + 1)]
                self.assertEqual(memdir.sync_vector_index(str(project), newer_items)["upserts"], 0)

        self.assertEqual(calls, 1)

    def test_unchanged_topic_signature_skips_second_sync_api_call(self) -> None:
        calls = 0
        vector = [0.0] * 768
        vector[2] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            return FakeCloudflareResponse([vector])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                first = memdir.sync_vector_index(str(project))
                second = memdir.sync_vector_index(str(project))

        self.assertEqual(first["upserts"], 1)
        self.assertTrue(second["skipped"])
        self.assertEqual(second["upserts"], 0)
        self.assertEqual(calls, 1)

    def test_cloudflare_failure_uses_lexical_retrieval_without_local_hash_row(self) -> None:
        def fake_urlopen(request: object, timeout: int) -> object:
            raise urllib.error.URLError("network down")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                result = memdir.find_relevant_memories("Android developer", str(project))
                doctor = memdir.memdir_doctor(str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                row = connection.execute(
                    "SELECT provider, dimensions FROM memory_vectors WHERE path = ?",
                    (str(topic_path),),
                ).fetchone()
            finally:
                connection.close()

        self.assertEqual([pathlib.Path(item["path"]).name for item in result], ["profile.json"])
        self.assertIsNone(row)
        self.assertEqual(doctor["embedding"]["active_provider"], "cloudflare")
        self.assertEqual(doctor["embedding"]["fallback_reason"], "network down")

    def test_cloudflare_failure_backoff_skips_repeat_retrieval_api_call_until_expired(self) -> None:
        calls = 0
        topic_vector = [0.0] * 768
        topic_vector[5] = 1.0
        query_vector = [0.0] * 768
        query_vector[5] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise urllib.error.URLError("network down")
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["Android developer"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            current_time = {"now": 1000}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
                mock.patch.object(memdir, "_unix_time", side_effect=lambda: current_time["now"]),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                first = memdir.find_relevant_memories("Android developer", str(project))
                second = memdir.find_relevant_memories("Android developer", str(project))
                calls_during_backoff = calls
                current_time["now"] = 1301
                third = memdir.find_relevant_memories("Android developer", str(project))

        self.assertEqual([pathlib.Path(item["path"]).name for item in first], ["profile.json"])
        self.assertEqual([pathlib.Path(item["path"]).name for item in second], ["profile.json"])
        self.assertEqual([pathlib.Path(item["path"]).name for item in third], ["profile.json"])
        self.assertEqual(calls_during_backoff, 1)
        self.assertGreater(calls, calls_during_backoff)

    def test_require_lexical_match_excludes_vector_only_relevance(self) -> None:
        topic_vector = [0.0] * 768
        topic_vector[9] = 1.0
        query_vector = [0.0] * 768
        query_vector[9] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["fcm token"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "api-contract.json"
                _topic(topic_path, content="Android API contract version update.")
                loose = memdir.find_relevant_memories("fcm token", str(project))
                strict = memdir.find_relevant_memories("fcm token", str(project), require_lexical_match=True)

        self.assertEqual([pathlib.Path(item["path"]).name for item in loose], ["api-contract.json"])
        self.assertEqual(strict, [])

    def test_lexical_score_does_not_bypass_vector_similarity_floor(self) -> None:
        topic_vector = [0.0] * 768
        topic_vector[1] = 1.0
        query_vector = [0.0] * 768
        query_vector[2] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["android gradle plugin"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            patched_settings = _settings(tmp / "memdir")
            patched_settings["min_relevant_score"] = 10.2
            patched_settings["vector"]["min_similarity"] = 0.85
            settings = {"memdir": patched_settings}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "android-environment.json"
                _topic(topic_path, content="Android Android Android environment setup note.")
                result = memdir.find_relevant_memories(
                    "android gradle plugin",
                    str(project),
                    require_lexical_match=True,
                )

        self.assertEqual(result, [])

    def test_find_relevant_memories_reuses_ensure_topic_scan(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("network down")),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                original_scan = memdir.scan_topic_files
                scan_calls = 0

                def counted_scan(raw_cwd: str | None = None) -> list[dict[str, object]]:
                    nonlocal scan_calls
                    scan_calls += 1
                    return original_scan(raw_cwd)

                with mock.patch.object(memdir, "scan_topic_files", side_effect=counted_scan):
                    result = memdir.find_relevant_memories("Android developer", str(project))

        self.assertEqual([pathlib.Path(item["path"]).name for item in result], ["profile.json"])
        self.assertEqual(scan_calls, 1)

    def test_scan_topic_files_reuses_unchanged_topic_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with mock.patch.object(memdir, "load_settings", return_value=settings):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                original_load = memdir._load_json_document
                load_calls = 0

                def counted_load(path: pathlib.Path) -> dict[str, object] | None:
                    nonlocal load_calls
                    load_calls += 1
                    return original_load(path)

                with mock.patch.object(memdir, "_load_json_document", side_effect=counted_load):
                    first = memdir.scan_topic_files(str(project))
                    second = memdir.scan_topic_files(str(project))

        self.assertEqual([pathlib.Path(item["path"]).name for item in first], ["profile.json"])
        self.assertEqual([pathlib.Path(item["path"]).name for item in second], ["profile.json"])
        self.assertEqual(load_calls, 1)

    def test_scan_topic_files_reloads_changed_topic_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with mock.patch.object(memdir, "load_settings", return_value=settings):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                first = memdir.scan_topic_files(str(project))
                _topic(topic_path, content="Kotlin backend memory.")
                stat = topic_path.stat()
                os.utime(topic_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
                second = memdir.scan_topic_files(str(project))

        self.assertEqual(first[0]["excerpt"], "Android developer profile memory.")
        self.assertEqual(second[0]["excerpt"], "Kotlin backend memory.")

    def test_memdir_doctor_reports_invalid_topic_json_without_counting_it_as_topic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with mock.patch.object(memdir, "load_settings", return_value=settings):
                ensured = memdir.ensure_project_memdir(str(project))
                topics_dir = pathlib.Path(ensured["topics_dir"])
                _topic(topics_dir / "profile.json")
                (topics_dir / "broken.json").write_text('{"content": "raw " quote"}', encoding="utf-8")
                doctor = memdir.memdir_doctor(str(project))

        self.assertEqual(doctor["topic_count"], 1)
        self.assertEqual(doctor["invalid_topic_count"], 1)
        self.assertEqual([pathlib.Path(item["path"]).name for item in doctor["invalid_topics"]], ["broken.json"])
        self.assertEqual(doctor["invalid_topics"][0]["reason"], "invalid_json")

    def test_query_embedding_cache_skips_repeated_query_api_call(self) -> None:
        calls = 0
        topic_vector = [0.0] * 768
        topic_vector[3] = 1.0
        query_vector = [0.0] * 768
        query_vector[3] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["Android developer"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                memdir.sync_vector_index(str(project))
                first = memdir.find_relevant_memories("Android developer", str(project))
                second = memdir.find_relevant_memories("Android developer", str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                cache_count = connection.execute("SELECT COUNT(*) FROM query_embedding_cache").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual([pathlib.Path(item["path"]).name for item in first], ["profile.json"])
        self.assertEqual([pathlib.Path(item["path"]).name for item in second], ["profile.json"])
        self.assertEqual(cache_count, 1)
        self.assertEqual(calls, 2)

    def test_expired_query_embedding_cache_is_deleted_and_refetched(self) -> None:
        calls = 0
        topic_vector = [0.0] * 768
        topic_vector[6] = 1.0
        query_vector = [0.0] * 768
        query_vector[6] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["Android developer"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                memdir.sync_vector_index(str(project))
                memdir.find_relevant_memories("Android developer", str(project))
                connection = sqlite3.connect(ensured["vector_db"])
                try:
                    connection.execute("UPDATE query_embedding_cache SET created_at = 1, last_used_at = 1")
                    connection.commit()
                finally:
                    connection.close()
                memdir.find_relevant_memories("Android developer", str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                rows = connection.execute("SELECT created_at, vector_json FROM query_embedding_cache").fetchall()
            finally:
                connection.close()

        self.assertEqual(calls, 3)
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0][0], 1)
        self.assertEqual(json.loads(rows[0][1]), query_vector)

    def test_query_embedding_cache_evicts_oldest_last_used_when_max_exceeded(self) -> None:
        calls = 0
        topic_vector = [0.0] * 768
        topic_vector[7] = 1.0
        query_vector = [0.0] * 768
        query_vector[7] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            body = json.loads(request.data.decode("utf-8"))
            if len(body["text"]) == 1:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            patched_settings = _settings(tmp / "memdir")
            patched_settings["embedding"]["query_cache_max_entries"] = 1
            settings = {"memdir": patched_settings}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
                mock.patch.object(memdir, "_unix_time", side_effect=[1000, 1000, 1001, 1001]),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                memdir.sync_vector_index(str(project))
                memdir.find_relevant_memories("Android developer", str(project))
                memdir.find_relevant_memories("Kotlin developer", str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                rows = connection.execute("SELECT normalized_query FROM query_embedding_cache").fetchall()
            finally:
                connection.close()

        self.assertEqual(calls, 3)
        self.assertEqual(rows, [("kotlin developer",)])

    def test_corrupted_query_embedding_cache_row_is_deleted_and_refetched(self) -> None:
        calls = 0
        topic_vector = [0.0] * 768
        topic_vector[8] = 1.0
        query_vector = [0.0] * 768
        query_vector[8] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            nonlocal calls
            calls += 1
            body = json.loads(request.data.decode("utf-8"))
            if body["text"] == ["Android developer"]:
                return FakeCloudflareResponse([query_vector])
            return FakeCloudflareResponse([topic_vector for _ in body["text"]])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                memdir.sync_vector_index(str(project))
                memdir.find_relevant_memories("Android developer", str(project))
                connection = sqlite3.connect(ensured["vector_db"])
                try:
                    connection.execute("UPDATE query_embedding_cache SET vector_json = ?", ("not-json",))
                    connection.commit()
                finally:
                    connection.close()
                memdir.find_relevant_memories("Android developer", str(project))

            connection = sqlite3.connect(ensured["vector_db"])
            try:
                vector_json = connection.execute("SELECT vector_json FROM query_embedding_cache").fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(calls, 3)
        self.assertEqual(json.loads(vector_json), query_vector)

    def test_doctor_reports_cloudflare_settings_without_leaking_token(self) -> None:
        vector = [0.0] * 768
        vector[4] = 1.0

        def fake_urlopen(request: object, timeout: int) -> FakeCloudflareResponse:
            return FakeCloudflareResponse([vector])

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topic_path = pathlib.Path(ensured["topics_dir"]) / "profile.json"
                _topic(topic_path)
                memdir.sync_vector_index(str(project))
                memdir.find_relevant_memories("Android developer", str(project))
                connection = sqlite3.connect(ensured["vector_db"])
                try:
                    connection.execute(
                        """
                        INSERT INTO query_embedding_cache (
                            cache_key, query_hash, normalized_query, provider, model, dimensions,
                            vector_json, created_at, last_used_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "expired",
                            "expired-hash",
                            "expired query",
                            "cloudflare",
                            "@cf/google/embeddinggemma-300m",
                            768,
                            json.dumps(vector),
                            1,
                            1,
                        ),
                    )
                    connection.commit()
                finally:
                    connection.close()
                doctor = memdir.memdir_doctor(str(project))

        serialized = json.dumps(doctor, ensure_ascii=False)
        self.assertEqual(doctor["embedding"]["configured_provider"], "cloudflare")
        self.assertEqual(doctor["embedding"]["model"], "@cf/google/embeddinggemma-300m")
        self.assertEqual(doctor["embedding"]["dimensions"], 768)
        self.assertTrue(doctor["embedding"]["cloudflare_configured"])
        self.assertEqual(doctor["embedding"]["api_token"], "<redacted>")
        self.assertNotIn("secret-token", serialized)
        self.assertEqual(doctor["indexed_provider_counts"], {"cloudflare:@cf/google/embeddinggemma-300m:768": 1})
        self.assertEqual(doctor["stale_topic_count"], 0)
        self.assertEqual(doctor["query_cache_entries"], 1)
        self.assertEqual(doctor["query_cache_expired_entries"], 1)
        self.assertIsNotNone(doctor["last_sync"])


if __name__ == "__main__":
    unittest.main()
