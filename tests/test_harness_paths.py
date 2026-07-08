from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib import memdir  # noqa: E402
from harness_lib.paths import canonicalize_existing_path, project_slug  # noqa: E402


class HarnessPathTests(unittest.TestCase):
    def _expected_slug(self, project: pathlib.Path, base: str) -> str:
        canonical = canonicalize_existing_path(project)
        digest = hashlib.sha1(str(canonical).encode("utf-8")).hexdigest()[:8]
        return f"{base}-{digest}"

    def test_project_slug_preserves_utf8_project_name(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            cases = {
                "테스트": "테스트",
                "测试": "测试",
                "テスト": "テスト",
            }
            for folder_name, expected_base in cases.items():
                with self.subTest(folder_name=folder_name):
                    project = tmp / folder_name
                    project.mkdir()

                    self.assertEqual(project_slug(project), self._expected_slug(project, expected_base))

    def test_project_slug_keeps_ascii_project_name_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            project = pathlib.Path(raw_tmp) / "test"
            project.mkdir()

            self.assertEqual(project_slug(project), self._expected_slug(project, "test"))

    def test_project_slug_folds_unsupported_characters_to_hyphen(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            project = pathlib.Path(raw_tmp) / "테스트 project!"
            project.mkdir()

            self.assertEqual(project_slug(project), self._expected_slug(project, "테스트-project"))

    def test_project_slug_normalizes_project_name_to_nfc(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            project = pathlib.Path(raw_tmp) / "Cafe\u0301"

            self.assertEqual(project_slug(project), self._expected_slug(project, "Café"))

    def test_plugin_storage_uses_utf8_project_slug(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "테스트"
            project.mkdir()
            base_dir = tmp / "memories"
            settings = {
                "base_dir": str(base_dir),
                "project_root": {"strategy": "cwd"},
                "storage": {"mode": "plugin"},
            }

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                paths = memdir.resolve_project_paths(str(project))

            self.assertEqual(paths["memdir"], base_dir / project_slug(project))
            self.assertTrue(paths["memdir"].name.startswith("테스트-"))

    def test_user_prompt_submit_state_path_uses_utf8_project_slug(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "テスト"
            project.mkdir()
            settings = {
                "base_dir": str(tmp / "memories"),
                "project_root": {"strategy": "cwd"},
                "storage": {"mode": "plugin"},
            }

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                state_path = memdir.get_user_prompt_submit_state_path(str(project))

            self.assertEqual(state_path.name, f"{project_slug(project)}.json")
            self.assertTrue(state_path.name.startswith("テスト-"))


if __name__ == "__main__":
    unittest.main()
