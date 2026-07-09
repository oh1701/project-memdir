from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib import codex_exec, memdir  # noqa: E402


def _settings(base_dir: pathlib.Path, provider: str) -> dict[str, object]:
    settings = dict(memdir.memdir_settings())
    settings.update(
        {
            "enabled": True,
            "base_dir": str(base_dir),
            "disabled_project_roots": [],
            "extractor": {
                "provider": provider,
                "agy_bin": "agy",
                "agy_extraction_timeout_sec": 7,
                "agy_model": "",
            },
        }
    )
    return settings


def _topic_payload(topic_id: str = "topic") -> dict[str, object]:
    return {
        "schema_version": 2,
        "id": topic_id,
        "name": "Topic",
        "description": "Topic memory.",
        "type": "reference",
        "content": "Topic memory content.",
        "keywords": ["topic"],
        "updated_at": "2026-04-21T00:00:00Z",
        "last_thread_id": "thread-1",
    }


class MemdirExtractorProviderTests(unittest.TestCase):
    def test_codex_exec_disables_hooks_for_child_invocation(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            cwd = pathlib.Path(raw_tmp)
            with mock.patch.object(codex_exec.subprocess, "run", side_effect=fake_run):
                codex_exec.run_codex_exec(codex_bin="codex", cwd=cwd, prompt="update memory")

        args = captured["args"]
        self.assertIsInstance(args, list)
        self.assertIn("--disable", args)
        disable_index = args.index("--disable")
        self.assertEqual(args[disable_index + 1], "hooks")
        self.assertEqual(args[-1], "update memory")
        kwargs = captured["kwargs"]
        self.assertIsInstance(kwargs, dict)
        if codex_exec.os.name == "nt":
            self.assertEqual(
                kwargs["creationflags"],
                getattr(codex_exec.subprocess, "CREATE_NO_WINDOW", 0),
            )

    def test_split_command_template_preserves_windows_path_backslashes(self) -> None:
        command = r'python "C:\Users\Example User\project-memdir\examples\local_extractor.py" --flag'

        with mock.patch.object(memdir.os, "name", "nt"):
            parts = memdir._split_command_template(command)

        self.assertEqual(
            parts,
            [
                "python",
                r"C:\Users\Example User\project-memdir\examples\local_extractor.py",
                "--flag",
            ],
        )

    def test_codex_provider_uses_codex_exec_topic_write_path(self) -> None:
        captured: dict[str, object] = {}
        global_lock_depth = 0

        @contextmanager
        def tracked_global_lock():
            nonlocal global_lock_depth
            global_lock_depth += 1
            try:
                yield
            finally:
                global_lock_depth -= 1

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            memdir_settings = _settings(tmp / "memdir", "codex")
            memdir_settings["extractor"].update({"codex_model": "gpt-5.4-mini"})
            settings = {"memdir": memdir_settings}

            def fake_run_codex_exec(**kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(global_lock_depth, 0)
                captured.update(kwargs)
                topics_dir = pathlib.Path(kwargs["cwd"])
                topics_dir.mkdir(parents=True, exist_ok=True)
                (topics_dir / "codex-topic.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "id": "codex-topic",
                            "name": "Codex topic",
                            "description": "Created by codex provider.",
                            "type": "reference",
                            "content": "Codex provider writes topic JSON.",
                            "keywords": ["codex"],
                            "updated_at": "2026-04-21T00:00:00Z",
                            "last_thread_id": "thread-1",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch.object(memdir, "project_memdir_file_lock", tracked_global_lock),
                mock.patch.object(memdir, "run_codex_exec", side_effect=fake_run_codex_exec) as run_codex,
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember this",
                    assistant_text="ok",
                    thread_id="thread-1",
                )

        self.assertTrue(result["updated"])
        self.assertEqual(result["extractor"], "codex")
        self.assertTrue(result["topic_files"])
        self.assertEqual(captured.get("cwd"), pathlib.Path(result["memdir"]) / "topics")
        self.assertEqual(captured.get("model"), "gpt-5.4-mini")
        self.assertEqual(captured.get("sandbox"), "danger-full-access")
        self.assertIn("The current working directory is the topics directory.", captured.get("prompt"))
        run_codex.assert_called_once()

    def test_external_extractors_run_outside_global_memdir_lock(self) -> None:
        cases = ("agy", "claudecode", "local_cli")

        for provider in cases:
            with self.subTest(provider=provider):
                global_lock_depth = 0

                @contextmanager
                def tracked_global_lock():
                    nonlocal global_lock_depth
                    global_lock_depth += 1
                    try:
                        yield
                    finally:
                        global_lock_depth -= 1

                def fake_run(
                    args: list[str],
                    *,
                    cwd: pathlib.Path,
                    **kwargs: object,
                ) -> subprocess.CompletedProcess[str]:
                    self.assertEqual(global_lock_depth, 0)
                    output_dir = pathlib.Path(cwd)
                    topics_dir = output_dir if provider == "claudecode" else output_dir / "topics"
                    topics_dir.mkdir(parents=True, exist_ok=True)
                    (topics_dir / f"{provider}-outside-lock.json").write_text(
                        json.dumps(_topic_payload(f"{provider}-outside-lock")),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

                with tempfile.TemporaryDirectory() as raw_tmp:
                    tmp = pathlib.Path(raw_tmp)
                    project = tmp / "project"
                    project.mkdir()
                    (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
                    settings = _settings(tmp / "memdir", provider)
                    if provider == "local_cli":
                        settings["extractor"].update({"local_cli_command": "file-agent"})

                    with (
                        mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                        mock.patch.object(memdir, "project_memdir_file_lock", tracked_global_lock),
                        mock.patch("subprocess.run", side_effect=fake_run),
                    ):
                        result = memdir.extract_memories_from_event(
                            raw_cwd=str(project),
                            user_text=f"remember via {provider}",
                            assistant_text="ok",
                            thread_id=f"thread-{provider}",
                        )

                self.assertTrue(result["updated"])
                self.assertEqual(result["extractor"], provider)

    def test_extract_event_fails_when_extractor_provider_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = {"memdir": _settings(tmp / "memdir", "")}

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch.object(memdir, "_extract_with_agy", side_effect=AssertionError("agy fallback should not run")) as extract_agy,
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember this",
                    assistant_text="ok",
                    thread_id="thread-missing-provider",
                )

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "missing_extractor_provider")
        self.assertEqual(result["thread_id"], "thread-missing-provider")
        extract_agy.assert_not_called()

    def test_codex_provider_uses_cli_default_for_default_model_sentinel(self) -> None:
        captured: dict[str, object] = {}

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            memdir_path = tmp / "memdir"
            topics_dir = memdir_path / "topics"
            settings = _settings(tmp / "settings", "codex")
            settings["extractor"].update({"codex_model": "codex-default-model"})

            def fake_run_codex_exec(**kwargs: object) -> subprocess.CompletedProcess[str]:
                captured.update(kwargs)
                return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch.object(memdir, "run_codex_exec", side_effect=fake_run_codex_exec),
            ):
                result = memdir._extract_with_codex(
                    memdir=memdir_path,
                    project_root=tmp / "project",
                    topics_dir=topics_dir,
                    user_text="remember this",
                    assistant_text="ok",
                    existing_memories="",
                )

        self.assertTrue(result["ok"])
        self.assertIsNone(captured["model"])

    def test_extractor_model_context_shows_provider_default_model_sentinels(self) -> None:
        cases = [
            ("codex", "codex_model", "codex-default-model", "codex-default-model"),
            ("codex", "codex_model", "", "codex-default-model"),
            ("agy", "agy_model", "agy-default-model", "agy-default-model"),
            ("agy", "agy_model", "", "agy-default-model"),
            ("claudecode", "claudecode_model", "claudecode-default-model", "claudecode-default-model"),
            ("claudecode", "claudecode_model", "", "claudecode-default-model"),
        ]

        for provider, model_key, model_value, expected_model in cases:
            with self.subTest(provider=provider, model_value=model_value):
                settings = _settings(pathlib.Path("memdir"), provider)
                settings["extractor"].update({model_key: model_value})

                with mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}):
                    self.assertEqual(memdir._extractor_model_for_context(), expected_model)

    def test_agy_provider_runs_agy_process_from_memdir(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["cwd"] = cwd
            captured["text"] = text
            captured["capture_output"] = capture_output
            captured["timeout"] = timeout
            captured.update(kwargs)
            topics_dir = pathlib.Path(cwd) / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            (topics_dir / "agy-topic.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": "agy-topic",
                        "name": "agy topic",
                        "description": "Created by agy provider.",
                        "type": "reference",
                        "content": "agy provider writes topic JSON.",
                        "keywords": ["agy"],
                        "updated_at": "2026-04-21T00:00:00Z",
                        "last_thread_id": "thread-2",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = {"memdir": _settings(tmp / "memdir", "agy")}

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("subprocess.run", side_effect=fake_run) as run_agy,
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via agy",
                    assistant_text="ok",
                    thread_id="thread-2",
                )

        self.assertTrue(result["updated"])
        self.assertEqual(result["extractor"], "agy")
        self.assertTrue(result["topic_files"])
        self.assertEqual(captured["args"][0:2], ["agy", "-p"])
        self.assertIn("Only create or modify JSON files under the topics directory.", captured["args"][2])
        self.assertEqual(captured["args"][3:], ["--dangerously-skip-permissions"])
        self.assertEqual(captured["cwd"], pathlib.Path(result["memdir"]))
        self.assertIs(captured["text"], True)
        self.assertIs(captured["capture_output"], True)
        self.assertEqual(captured["timeout"], 7)
        if memdir.os.name == "nt":
            self.assertEqual(
                captured["creationflags"],
                getattr(memdir.subprocess, "CREATE_NO_WINDOW", 0),
            )
        run_agy.assert_called_once()

    def test_agy_provider_uses_cli_default_for_default_model_sentinel(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            settings = _settings(tmp / "settings", "agy")
            settings["extractor"].update({"agy_model": "agy-default-model"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir._extract_with_agy(
                    memdir=tmp / "memdir",
                    project_root=tmp / "project",
                    topics_dir=tmp / "memdir" / "topics",
                    user_text="remember via agy",
                    assistant_text="ok",
                    existing_memories="",
                )

        self.assertTrue(result["ok"])
        self.assertNotIn("--model", captured["args"])

    def test_agy_provider_reports_nonzero_exit(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=2, stdout="bad output", stderr="denied")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = {"memdir": _settings(tmp / "memdir", "agy")}

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via agy",
                    assistant_text="ok",
                    thread_id="thread-3",
                )

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "agy_extraction_failed")
        self.assertEqual(result["extractor"], "agy")

    def test_claudecode_provider_runs_claude_code_process_from_topics_dir(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            env: dict[str, str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["cwd"] = cwd
            captured["text"] = text
            captured["capture_output"] = capture_output
            captured["timeout"] = timeout
            captured["env"] = env
            captured.update(kwargs)
            pathlib.Path(cwd).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(cwd) / "claudecode-topic.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": "claudecode-topic",
                        "name": "Claude Code topic",
                        "description": "Created by claudecode provider.",
                        "type": "reference",
                        "content": "Claude Code provider writes topic JSON.",
                        "keywords": ["claudecode"],
                        "updated_at": "2026-04-21T00:00:00Z",
                        "last_thread_id": "thread-claudecode",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "claudecode")
            settings["extractor"].update(
                {
                    "claudecode_extraction_timeout_sec": 13,
                    "claudecode_model": "",
                }
            )

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run) as run_claudecode,
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via claude code",
                    assistant_text="ok",
                    thread_id="thread-claudecode",
                )

        self.assertTrue(result["updated"])
        self.assertEqual(result["extractor"], "claudecode")
        self.assertTrue(result["topic_files"])
        self.assertEqual(captured["args"][0:2], ["claude", "-p"])
        self.assertIn("Only create or modify JSON files under the topics directory.", captured["args"][2])
        self.assertEqual(captured["args"][3:], ["--dangerously-skip-permissions"])
        self.assertEqual(captured["cwd"], pathlib.Path(result["memdir"]) / "topics")
        self.assertIs(captured["text"], True)
        self.assertIs(captured["capture_output"], True)
        self.assertEqual(captured["timeout"], 13)
        self.assertEqual(captured["env"]["CODEX_MEMDIR_SKIP"], "1")
        self.assertEqual(captured["env"]["CODEX_PROJECT_KNOWLEDGE_SKIP"], "1")
        self.assertEqual(captured["env"]["CODEX_HARNESS_SKIP_SESSION_START"], "1")
        if memdir.os.name == "nt":
            self.assertEqual(
                captured["creationflags"],
                getattr(memdir.subprocess, "CREATE_NO_WINDOW", 0),
            )
        run_claudecode.assert_called_once()

    def test_claudecode_provider_uses_cli_default_for_default_model_sentinel(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            env: dict[str, str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            settings = _settings(tmp / "settings", "claudecode")
            settings["extractor"].update({"claudecode_model": "claudecode-default-model"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir._extract_with_claudecode(
                    memdir=tmp / "memdir",
                    project_root=tmp / "project",
                    topics_dir=tmp / "memdir" / "topics",
                    user_text="remember via claude code",
                    assistant_text="ok",
                    existing_memories="",
                )

        self.assertTrue(result["ok"])
        self.assertNotIn("--model", captured["args"])

    def test_claudecode_provider_can_select_model(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            env: dict[str, str],
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            settings = _settings(tmp / "settings", "claudecode")
            settings["extractor"].update({"claudecode_model": "sonnet"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir._extract_with_claudecode(
                    memdir=tmp / "memdir",
                    project_root=tmp / "project",
                    topics_dir=tmp / "memdir" / "topics",
                    user_text="remember via claude code",
                    assistant_text="ok",
                    existing_memories="",
                )

        self.assertTrue(result["ok"])
        self.assertEqual(captured["args"][0], "claude")
        self.assertEqual(captured["args"][-2:], ["--model", "sonnet"])

    def test_claudecode_provider_reports_nonzero_exit(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=2, stdout="bad output", stderr="denied")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = {"memdir": _settings(tmp / "memdir", "claudecode")}

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via claude code",
                    assistant_text="ok",
                    thread_id="thread-claudecode-failed",
                )

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "claudecode_extraction_failed")
        self.assertEqual(result["extractor"], "claudecode")

    def test_extraction_failure_is_reported_only_in_user_prompt_submit_context(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=2,
                stdout="",
                stderr="model not found: bad-model",
            )

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            memdir_settings = _settings(tmp / "memdir", "agy")
            memdir_settings["extractor"].update({"agy_model": "bad-model"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": memdir_settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via agy",
                    assistant_text="ok",
                    thread_id="thread-failed",
                )
                prompt_context = memdir.build_memdir_context(
                    "next prompt",
                    str(project),
                    include_core_paths=False,
                    require_lexical_match=False,
                )
                session_context = memdir.build_session_start_context(str(project))

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "agy_extraction_failed")
        self.assertIn("previous project-memdir memory extraction failed", prompt_context["system_message"])
        self.assertIn("model_unavailable", prompt_context["system_message"])
        self.assertIn("reason=agy_extraction_failed", prompt_context["system_message"])
        self.assertNotIn("provider=", prompt_context["system_message"])
        self.assertNotIn("model=", prompt_context["system_message"])
        self.assertNotIn("bad-model", prompt_context["system_message"])
        self.assertNotIn("Check the configured extractor model name", prompt_context["system_message"])
        self.assertNotIn("previous project-memdir memory extraction failed", session_context["additionalContext"])

    def test_previous_failure_notice_only_shows_kind_and_reason_when_extractor_unset(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            memdir_settings = _settings(tmp / "memdir", "")

            with mock.patch.object(memdir, "load_settings", return_value={"memdir": memdir_settings}):
                ensured = memdir.ensure_project_memdir(str(project))
                status_path = pathlib.Path(ensured["memdir"]) / memdir.EXTRACTION_STATUS_NAME
                status_path.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "provider": "agy",
                            "model": "bad-model",
                            "reason": "agy_extraction_failed",
                            "kind": "model_unavailable",
                            "detail": "model not found: bad-model",
                            "hint": "Check the configured extractor model name and model access.",
                            "updated_at": "2026-04-21T00:00:00Z",
                        }
                    ),
                    encoding="utf-8",
                )
                prompt_context = memdir.build_memdir_context(
                    "next prompt",
                    str(project),
                    include_core_paths=False,
                    require_lexical_match=False,
                )

        notice = prompt_context["system_message"]
        self.assertEqual(
            notice,
            "previous project-memdir memory extraction failed: "
            "kind=model_unavailable reason=agy_extraction_failed.",
        )
        self.assertNotIn("provider=", notice)
        self.assertNotIn("model=", notice)
        self.assertNotIn("bad-model", notice)
        self.assertNotIn("Check the configured extractor model name", notice)

    def test_successful_extraction_clears_previous_failure_notice(self) -> None:
        attempts = 0

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=2,
                    stdout="",
                    stderr="quota exceeded",
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            memdir_settings = _settings(tmp / "memdir", "agy")

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": memdir_settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                failed = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via agy",
                    assistant_text="ok",
                    thread_id="thread-failed",
                )
                failed_context = memdir.build_memdir_context(
                    "next prompt",
                    str(project),
                    include_core_paths=False,
                    require_lexical_match=False,
                )
                succeeded = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via agy again",
                    assistant_text="ok",
                    thread_id="thread-succeeded",
                )
                prompt_context = memdir.build_memdir_context(
                    "next prompt",
                    str(project),
                    include_core_paths=False,
                    require_lexical_match=False,
                )

        self.assertEqual(failed["reason"], "agy_extraction_failed")
        self.assertIn("previous project-memdir memory extraction failed", failed_context["system_message"])
        self.assertIn("quota_exceeded", failed_context["system_message"])
        self.assertIn(succeeded["reason"], {"ok", "no_changes"})
        self.assertNotIn("previous project-memdir memory extraction failed", prompt_context["system_message"])

    def test_existing_invalid_topic_json_does_not_block_new_valid_extraction(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            input: str | None,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            topics_dir = pathlib.Path(cwd) / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            (topics_dir / "new-valid-topic.json").write_text(
                json.dumps(_topic_payload("new-valid-topic")),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "local_cli")
            settings["extractor"].update({"local_cli_command": "file-agent"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topics_dir = pathlib.Path(ensured["topics_dir"])
                (topics_dir / "old-broken-topic.json").write_text('{"content": "raw " quote"}', encoding="utf-8")
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember new valid topic",
                    assistant_text="ok",
                    thread_id="thread-valid",
                )
                prompt_context = memdir.build_memdir_context(
                    "next prompt",
                    str(project),
                    include_core_paths=False,
                    require_lexical_match=False,
                )

        self.assertIn(result["reason"], {"ok", "no_changes"})
        self.assertNotEqual(result["reason"], "invalid_topic_json")
        self.assertIn("new-valid-topic.json", [pathlib.Path(path).name for path in result["topic_files"]])
        self.assertNotIn("previous project-memdir memory extraction failed", prompt_context["system_message"])

    def test_extractor_failure_reason_is_preserved_when_existing_topic_json_is_invalid(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            input: str | None,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=2, stdout="", stderr="denied")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "local_cli")
            settings["extractor"].update({"local_cli_command": "file-agent"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                ensured = memdir.ensure_project_memdir(str(project))
                topics_dir = pathlib.Path(ensured["topics_dir"])
                (topics_dir / "old-broken-topic.json").write_text('{"content": "raw " quote"}', encoding="utf-8")
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember but fail extractor",
                    assistant_text="ok",
                    thread_id="thread-fail",
                )

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "local_cli_extraction_failed")
        self.assertEqual(result["extractor"], "local_cli")

    def test_new_invalid_topic_json_from_extractor_fails_extraction(self) -> None:
        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            input: str | None,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            topics_dir = pathlib.Path(cwd) / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            (topics_dir / "new-broken-topic.json").write_text('{"content": "raw " quote"}', encoding="utf-8")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "local_cli")
            settings["extractor"].update({"local_cli_command": "file-agent"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember broken topic",
                    assistant_text="ok",
                    thread_id="thread-invalid",
                )

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "invalid_topic_json")
        self.assertEqual([pathlib.Path(error.split(":", 1)[1]).name for error in result["errors"]], ["new-broken-topic.json"])

    def test_local_cli_provider_sends_prompt_to_file_agent_on_stdin(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            input: str | None,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["cwd"] = cwd
            captured["input"] = input
            captured["text"] = text
            captured["capture_output"] = capture_output
            captured["timeout"] = timeout
            captured.update(kwargs)
            topics_dir = pathlib.Path(cwd) / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            (topics_dir / "local-topic.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": "local-topic",
                        "name": "Local topic",
                        "description": "Created by local CLI provider.",
                        "type": "reference",
                        "content": "Local CLI provider writes topic JSON.",
                        "keywords": ["local"],
                        "updated_at": "2026-04-21T00:00:00Z",
                        "last_thread_id": "thread-4",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "local_cli")
            settings["extractor"].update(
                {
                    "local_cli_command": "python file_agent.py --write-topics",
                    "local_cli_extraction_timeout_sec": 11,
                }
            )

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch.object(memdir.shutil, "which", side_effect=lambda name: "/usr/bin/python3" if name == "python3" else None),
                mock.patch("subprocess.run", side_effect=fake_run) as run_local,
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via local model",
                    assistant_text="ok",
                    thread_id="thread-4",
                )

        self.assertTrue(result["updated"])
        self.assertEqual(result["extractor"], "local_cli")
        self.assertTrue(result["topic_files"])
        self.assertEqual(captured["args"], ["/usr/bin/python3", "file_agent.py", "--write-topics"])
        self.assertIn("Only create or modify JSON files under the topics directory.", str(captured["input"]))
        self.assertEqual(captured["cwd"], pathlib.Path(result["memdir"]))
        self.assertIs(captured["text"], True)
        self.assertIs(captured["capture_output"], True)
        self.assertEqual(captured["timeout"], 11)
        if memdir.os.name == "nt":
            self.assertIn("creationflags", captured)
            self.assertEqual(
                captured["creationflags"],
                getattr(memdir.subprocess, "CREATE_NO_WINDOW", 0),
            )
        run_local.assert_called_once()

    def test_local_cli_provider_can_pass_prompt_as_argument(self) -> None:
        captured: dict[str, object] = {}

        def fake_run(
            args: list[str],
            *,
            cwd: pathlib.Path,
            input: str | None,
            text: bool,
            capture_output: bool,
            timeout: int,
            **kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            captured["args"] = args
            captured["input"] = input
            captured.update(kwargs)
            topics_dir = pathlib.Path(cwd) / "topics"
            topics_dir.mkdir(parents=True, exist_ok=True)
            (topics_dir / "arg-topic.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": "arg-topic",
                        "name": "Arg topic",
                        "description": "Created by argument style local CLI provider.",
                        "type": "reference",
                        "content": "Local CLI argument mode writes topic JSON.",
                        "keywords": ["local"],
                        "updated_at": "2026-04-21T00:00:00Z",
                        "last_thread_id": "thread-5",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            (project / "AGENTS.md").write_text("# temp\n", encoding="utf-8")
            settings = _settings(tmp / "memdir", "local_cli")
            settings["extractor"].update({"local_cli_command": "file-agent --prompt {prompt}"})

            with (
                mock.patch.object(memdir, "load_settings", return_value={"memdir": settings}),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                result = memdir.extract_memories_from_event(
                    raw_cwd=str(project),
                    user_text="remember via local arg",
                    assistant_text="ok",
                    thread_id="thread-5",
                )

        self.assertTrue(result["updated"])
        self.assertEqual(captured["args"][0:2], ["file-agent", "--prompt"])
        self.assertIn("remember via local arg", captured["args"][2])
        self.assertIsNone(captured["input"])
        if memdir.os.name == "nt":
            self.assertIn("creationflags", captured)
            self.assertEqual(
                captured["creationflags"],
                getattr(memdir.subprocess, "CREATE_NO_WINDOW", 0),
            )


if __name__ == "__main__":
    unittest.main()
