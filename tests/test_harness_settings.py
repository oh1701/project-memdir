from __future__ import annotations

import pathlib
import sys
import tempfile
import tomllib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib import memdir, settings as harness_settings  # noqa: E402


class HarnessSettingsTests(unittest.TestCase):
    def test_harness_toml_uses_nested_memdir_runtime_sections(self) -> None:
        payload = tomllib.loads((ROOT / "harness.toml").read_text(encoding="utf-8"))
        memdir = payload["memdir"]

        self.assertEqual(sorted(key for key, value in memdir.items() if isinstance(value, dict)), ["embedding", "extractor", "project_root", "storage", "vector"])
        self.assertEqual(memdir["project_root"]["strategy"], "cwd")
        self.assertEqual(memdir["storage"]["mode"], "plugin")
        self.assertEqual(memdir["storage"]["project_dir_name"], ".project-memdir")
        self.assertEqual(memdir["vector"]["index_name"], "vector_index.sqlite3")
        self.assertEqual(memdir["embedding"]["model"], "@cf/google/embeddinggemma-300m")
        self.assertEqual(memdir["extractor"]["provider"], "")
        self.assertEqual(memdir["extractor"]["codex_model"], "")
        self.assertEqual(memdir["extractor"]["agy_model"], "agy-default-model")
        self.assertEqual(memdir["extractor"]["local_cli_command"], 'python "${CODEX_ROOT}/examples/local_extractor.py"')

        legacy_root_keys = {
            "vector_index_name",
            "vector_index_backend",
            "vector_dimensions",
            "vector_score_weight",
            "min_vector_similarity",
            "query_embedding_cache_ttl_sec",
            "query_embedding_cache_max_entries",
            "embedding_failure_backoff_sec",
            "extractor_provider",
            "extract_timeout_sec",
            "extract_codex_model",
            "extract_agy_bin",
            "extract_agy_extraction_timeout_sec",
            "extract_agy_model",
            "extract_local_cli_command",
            "extract_local_cli_extraction_timeout_sec",
        }
        self.assertFalse(legacy_root_keys.intersection(memdir))

    def test_default_codex_sandbox_is_danger_full_access(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            harness_path = pathlib.Path(raw_tmp) / "missing-harness.toml"

            with mock.patch.object(harness_settings, "HARNESS_CONFIG_PATH", harness_path):
                loaded = harness_settings.load_settings()

        self.assertEqual(loaded["memdir"]["extractor"]["codex_sandbox"], "danger-full-access")
        self.assertEqual(loaded["memdir"]["project_root"]["strategy"], "cwd")
        self.assertEqual(loaded["memdir"]["storage"]["mode"], "plugin")
        self.assertEqual(loaded["memdir"]["storage"]["project_dir_name"], ".project-memdir")

    def test_harness_toml_keeps_memdir_runtime_settings_in_nested_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            harness_path = pathlib.Path(raw_tmp) / "harness.toml"
            harness_path.write_text(
                "\n".join(
                    [
                        "[memdir]",
                        "enabled = true",
                        'base_dir = "${CODEX_ROOT}/memories/projects"',
                        "",
                        "[memdir.vector]",
                        'index_name = "vector_index.sqlite3"',
                        'index_backend = "sqlite"',
                        "dimensions = 96",
                        "score_weight = 12",
                        "min_similarity = 0.75",
                        "",
                        "[memdir.embedding]",
                        'model = "@cf/test/custom-embedding"',
                        "dimensions = 1024",
                        "timeout_sec = 30",
                        "failure_backoff_sec = 300",
                        "query_cache_ttl_sec = 86400",
                        "query_cache_max_entries = 256",
                        "",
                        "[memdir.project_root]",
                        'strategy = "detect"',
                        "",
                        "[memdir.storage]",
                        'mode = "project"',
                        'project_dir_name = ".custom-memdir"',
                        "",
                        "[memdir.extractor]",
                        'provider = "local_cli"',
                        "timeout_sec = 90",
                        'codex_model = "gpt-5.4-mini"',
                        'codex_bin = "codex"',
                        'agy_bin = "agy"',
                        "agy_extraction_timeout_sec = 7",
                        'agy_model = ""',
                        'local_cli_command = "python3 example.py"',
                        "local_cli_extraction_timeout_sec = 120",
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.object(harness_settings, "HARNESS_CONFIG_PATH", harness_path):
                loaded = harness_settings.load_settings()

        memdir = loaded["memdir"]

        self.assertEqual(memdir["vector"]["dimensions"], 96)
        self.assertEqual(memdir["vector"]["min_similarity"], 0.75)
        self.assertEqual(memdir["embedding"]["model"], "@cf/test/custom-embedding")
        self.assertEqual(memdir["embedding"]["CLOUDFLARE_MODEL"], "@cf/test/custom-embedding")
        self.assertEqual(memdir["embedding"]["dimensions"], 1024)
        self.assertEqual(memdir["embedding"]["timeout_sec"], 30)
        self.assertEqual(memdir["project_root"]["strategy"], "detect")
        self.assertEqual(memdir["storage"]["mode"], "project")
        self.assertEqual(memdir["storage"]["project_dir_name"], ".custom-memdir")
        self.assertEqual(memdir["extractor"]["provider"], "local_cli")
        self.assertEqual(memdir["extractor"]["codex_model"], "gpt-5.4-mini")
        self.assertEqual(memdir["extractor"]["local_cli_command"], "python3 example.py")

        legacy_root_keys = {
            "vector_index_name",
            "vector_index_backend",
            "vector_dimensions",
            "vector_score_weight",
            "min_vector_similarity",
            "query_embedding_cache_ttl_sec",
            "query_embedding_cache_max_entries",
            "embedding_failure_backoff_sec",
            "extractor_provider",
            "extract_timeout_sec",
            "extract_codex_model",
            "extract_agy_bin",
            "extract_agy_extraction_timeout_sec",
            "extract_agy_model",
            "extract_local_cli_command",
            "extract_local_cli_extraction_timeout_sec",
        }
        self.assertFalse(legacy_root_keys.intersection(memdir))

    def test_storage_mode_plugin_resolves_under_base_dir_project_slug(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            base_dir = tmp / "memories"
            settings = dict(memdir.memdir_settings())
            settings.update(
                {
                    "base_dir": str(base_dir),
                    "storage": {"mode": "plugin", "project_dir_name": ".project-memdir"},
                }
            )

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                paths = memdir.resolve_project_paths(str(project))

        self.assertEqual(paths["memdir"].parent, base_dir)
        self.assertEqual(paths["topics_dir"], paths["memdir"] / "topics")
        self.assertNotEqual(paths["memdir"], project / ".project-memdir")

    def test_project_root_strategy_cwd_uses_raw_cwd_instead_of_marker_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            nested = project / "src" / "feature"
            nested.mkdir(parents=True)
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = dict(memdir.memdir_settings())
            settings.update(
                {
                    "base_dir": str(tmp / "memories"),
                    "project_root": {"strategy": "cwd"},
                    "storage": {"mode": "plugin", "project_dir_name": ".project-memdir"},
                }
            )

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                paths = memdir.resolve_project_paths(str(nested))

        self.assertEqual(paths["project_root"], memdir.canonicalize_existing_path(nested))

    def test_project_root_strategy_detect_preserves_marker_based_detection(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            nested = project / "src" / "feature"
            nested.mkdir(parents=True)
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = dict(memdir.memdir_settings())
            settings.update(
                {
                    "base_dir": str(tmp / "memories"),
                    "project_root": {"strategy": "detect"},
                    "storage": {"mode": "plugin", "project_dir_name": ".project-memdir"},
                }
            )

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                paths = memdir.resolve_project_paths(str(nested))

        self.assertEqual(paths["project_root"], memdir.canonicalize_existing_path(project))

    def test_project_root_strategy_routes_through_platform_neutral_path_helpers(self) -> None:
        raw_cwd = pathlib.PureWindowsPath("C:/Users/example/project/subdir")
        cwd_root = pathlib.PureWindowsPath("C:/Users/example/project/subdir")
        detected_root = pathlib.PureWindowsPath("C:/Users/example/project")
        settings = dict(memdir.memdir_settings())
        settings.update(
            {
                "base_dir": "C:/Users/example/.codex/memories/projects",
                "project_root": {"strategy": "cwd"},
                "storage": {"mode": "plugin", "project_dir_name": ".project-memdir"},
            }
        )

        with (
            mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
            mock.patch.object(memdir, "canonicalize_existing_path", return_value=cwd_root) as canonicalize,
            mock.patch.object(memdir, "detect_project_root", return_value=detected_root) as detect,
            mock.patch.object(memdir, "project_slug", return_value="subdir-windows"),
        ):
            cwd_paths = memdir.resolve_project_paths(raw_cwd)

        canonicalize.assert_called_once_with(raw_cwd)
        detect.assert_not_called()
        self.assertEqual(cwd_paths["project_root"], cwd_root)

        settings["project_root"] = {"strategy": "detect"}
        with (
            mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
            mock.patch.object(memdir, "canonicalize_existing_path", return_value=cwd_root) as canonicalize,
            mock.patch.object(memdir, "detect_project_root", return_value=detected_root) as detect,
            mock.patch.object(memdir, "project_slug", return_value="project-windows"),
        ):
            detect_paths = memdir.resolve_project_paths(raw_cwd)

        canonicalize.assert_not_called()
        detect.assert_called_once_with(raw_cwd)
        self.assertEqual(detect_paths["project_root"], detected_root)

    def test_storage_mode_project_resolves_inside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = dict(memdir.memdir_settings())
            settings.update(
                {
                    "base_dir": str(tmp / "ignored-plugin-memories"),
                    "storage": {"mode": "project", "project_dir_name": ".custom-memdir"},
                }
            )

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                paths = memdir.resolve_project_paths(str(project))

        project_root = memdir.canonicalize_existing_path(project)
        self.assertEqual(paths["memdir"], project_root / ".custom-memdir")
        self.assertEqual(paths["topics_dir"], project_root / ".custom-memdir" / "topics")
        self.assertEqual(paths["entrypoint"], project_root / ".custom-memdir" / "manifest.json")

    def test_unknown_storage_mode_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = dict(memdir.memdir_settings())
            settings.update({"storage": {"mode": "auto", "project_dir_name": ".project-memdir"}})

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                with self.assertRaisesRegex(ValueError, "unsupported memdir storage mode: auto"):
                    memdir.resolve_project_paths(str(project))

    def test_legacy_flat_memdir_keys_are_normalized_into_nested_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            harness_path = pathlib.Path(raw_tmp) / "harness.toml"
            harness_path.write_text(
                "\n".join(
                    [
                        "[memdir]",
                        "vector_dimensions = 128",
                        "min_vector_similarity = 0.5",
                        "embedding_failure_backoff_sec = 60",
                        "query_embedding_cache_max_entries = 3",
                        'extractor_provider = "local_cli"',
                        'extract_local_cli_command = "local-llm"',
                    ]
                ),
                encoding="utf-8",
            )

            with mock.patch.object(harness_settings, "HARNESS_CONFIG_PATH", harness_path):
                loaded = harness_settings.load_settings()

        memdir = loaded["memdir"]

        self.assertEqual(memdir["vector"]["dimensions"], 128)
        self.assertEqual(memdir["vector"]["min_similarity"], 0.5)
        self.assertEqual(memdir["embedding"]["failure_backoff_sec"], 60)
        self.assertEqual(memdir["embedding"]["query_cache_max_entries"], 3)
        self.assertEqual(memdir["extractor"]["provider"], "local_cli")
        self.assertEqual(memdir["extractor"]["local_cli_command"], "local-llm")


if __name__ == "__main__":
    unittest.main()
