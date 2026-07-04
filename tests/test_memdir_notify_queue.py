from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))


def _load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemdirNotifyQueueTests(unittest.TestCase):
    def _assert_detached_drain_kwargs(self, module: types.ModuleType, kwargs: dict[str, object], fake_devnull: object) -> None:
        self.assertEqual(kwargs["cwd"], str(ROOT))
        self.assertIs(kwargs["stdin"], fake_devnull)
        self.assertIs(kwargs["stdout"], fake_devnull)
        self.assertIs(kwargs["stderr"], fake_devnull)
        env = kwargs["env"]
        self.assertIsInstance(env, dict)
        self.assertEqual(env["CODEX_MEMDIR_SKIP"], "1")
        self.assertEqual(env["CODEX_PROJECT_KNOWLEDGE_SKIP"], "1")
        self.assertEqual(env["CODEX_HARNESS_SKIP_SESSION_START"], "1")
        if module.os.name == "nt":
            self.assertEqual(kwargs["creationflags"], 0x08000000 | 0x00000008)
            self.assertNotIn("start_new_session", kwargs)
        else:
            self.assertIs(kwargs["start_new_session"], True)
            self.assertNotIn("creationflags", kwargs)

    def test_queue_deduplicates_and_drains_event_jobs(self) -> None:
        from harness_lib import memdir_queue

        event = {
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            queue_db = pathlib.Path(temp_dir) / "queue.sqlite3"
            with mock.patch.object(memdir_queue, "QUEUE_DB", queue_db):
                first = memdir_queue.enqueue_memdir_extraction_job(event)
                second = memdir_queue.enqueue_memdir_extraction_job(event)

                self.assertTrue(first["queued"])
                self.assertEqual(first["reason"], "queued")
                self.assertFalse(second["queued"])
                self.assertEqual(second["reason"], "already_queued")

                with mock.patch(
                    "harness_lib.memdir.extract_memories_from_event",
                    return_value={"updated": True, "written_paths": ["topic.json"]},
                ) as extract:
                    drained = memdir_queue.drain_memdir_extraction_queue(max_jobs=2, owner="unit")

        self.assertEqual(drained["processed_count"], 1)
        self.assertEqual(drained["processed"][0]["status"], "succeeded")
        extract.assert_called_once_with(
            raw_cwd="/tmp/project",
            user_text="remember this",
            assistant_text="ok",
            thread_id="thread-1",
        )

    def test_notify_queues_event_and_starts_detached_drain(self) -> None:
        module = _load_module("memdir_notify_under_test", ROOT / "scripts" / "notify" / "memdir_notify.py")
        event = {
            "type": "agent-turn-complete",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
            "client": "codex",
        }
        popen_calls: list[tuple[list[str], dict[str, object]]] = []
        fake_devnull = object()
        fake_subprocess = types.SimpleNamespace(
            DEVNULL=fake_devnull,
            CREATE_NO_WINDOW=0x08000000,
            DETACHED_PROCESS=0x00000008,
        )

        def fake_popen(command: list[str], **kwargs: object) -> object:
            popen_calls.append((command, kwargs))
            return types.SimpleNamespace(pid=12345)

        fake_subprocess.Popen = fake_popen
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "_append_observation"),
            mock.patch.object(module, "is_memdir_enabled", return_value=True, create=True),
            mock.patch.object(module, "enqueue_memdir_extraction_job", return_value={"queued": True, "reason": "queued"}, create=True) as enqueue,
            mock.patch.object(module, "extract_memories_from_event", side_effect=AssertionError("direct extraction should not run"), create=True),
            mock.patch.object(module, "subprocess", fake_subprocess, create=True),
            mock.patch.object(module.sys, "argv", ["memdir_notify.py", json.dumps(event)]),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enqueue.assert_called_once_with(event)
        self.assertEqual(len(popen_calls), 1)
        command, kwargs = popen_calls[0]
        self.assertEqual(command[1:5], [str(ROOT / "hooks" / "automation" / "memdir_cli.py"), "drain-queue", "--max-jobs", "20"])
        self.assertEqual(command[5], "--owner")
        self.assertTrue(str(command[6]).startswith("notify-background-"))
        self._assert_detached_drain_kwargs(module, kwargs, fake_devnull)
        self.assertIn("[memdir_notify] queued=queued background_drain=started", stderr.getvalue())

    def test_notify_does_not_start_drain_when_event_is_already_queued(self) -> None:
        module = _load_module("memdir_notify_duplicate_under_test", ROOT / "scripts" / "notify" / "memdir_notify.py")
        event = {
            "type": "agent-turn-complete",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
            "client": "codex",
        }
        fake_subprocess = types.SimpleNamespace(DEVNULL=object(), Popen=mock.Mock())
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "_append_observation"),
            mock.patch.object(module, "is_memdir_enabled", return_value=True, create=True),
            mock.patch.object(
                module,
                "enqueue_memdir_extraction_job",
                return_value={"queued": False, "reason": "already_queued"},
                create=True,
            ) as enqueue,
            mock.patch.object(module, "extract_memories_from_event", side_effect=AssertionError("direct extraction should not run"), create=True),
            mock.patch.object(module, "subprocess", fake_subprocess, create=True),
            mock.patch.object(module.sys, "argv", ["memdir_notify.py", json.dumps(event)]),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enqueue.assert_called_once_with(event)
        fake_subprocess.Popen.assert_not_called()
        self.assertIn("[memdir_notify] queued=already_queued background_drain=skipped", stderr.getvalue())

    def test_stop_hook_warns_without_extractor_provider_by_default(self) -> None:
        module = _load_module("memdir_stop_missing_provider_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
        }
        fake_subprocess = types.SimpleNamespace(DEVNULL=object(), Popen=mock.Mock())
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": ""}}}, create=True),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
            mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
            mock.patch.object(module.memdir_notify, "subprocess", fake_subprocess, create=True),
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        fake_subprocess.Popen.assert_not_called()
        self.assertIn(
            "memdir_extract_failed: missing [memdir.extractor].provider; skipping turn memory extraction.",
            stderr.getvalue(),
        )
        self.assertIn(".codex/project-memdir/harness.toml", stderr.getvalue())

    def test_stop_hook_warns_with_unsupported_extractor_provider_by_default(self) -> None:
        module = _load_module("memdir_stop_unsupported_provider_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
        }
        fake_subprocess = types.SimpleNamespace(DEVNULL=object(), Popen=mock.Mock())
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "bogus"}}}, create=True),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
            mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
            mock.patch.object(module.memdir_notify, "subprocess", fake_subprocess, create=True),
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        fake_subprocess.Popen.assert_not_called()
        self.assertIn(
            "memdir_extract_failed: unsupported [memdir.extractor].provider: bogus; skipping turn memory extraction.",
            stderr.getvalue(),
        )
        self.assertIn(".codex/project-memdir/harness.toml", stderr.getvalue())

    def test_stop_hook_provider_error_fail_mode_preserves_nonzero_exit(self) -> None:
        module = _load_module("memdir_stop_fail_provider_error_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
        }
        stderr = io.StringIO()

        with (
            mock.patch.object(
                module,
                "load_settings",
                return_value={
                    "memdir": {
                        "extractor": {"provider": ""},
                        "stop_hook": {
                            "provider_error_mode": "fail",
                            "provider_error_message": "memdir_extract_failed: {reason} in {config_path}",
                        },
                    }
                },
                create=True,
            ),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
            mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 1)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        self.assertIn(
            "memdir_extract_failed: missing_provider in",
            stderr.getvalue(),
        )

    def test_stop_hook_provider_error_silent_mode_suppresses_output(self) -> None:
        module = _load_module("memdir_stop_silent_provider_error_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
        }
        stderr = io.StringIO()

        with (
            mock.patch.object(
                module,
                "load_settings",
                return_value={
                    "memdir": {
                        "extractor": {"provider": "bogus"},
                        "stop_hook": {"provider_error_mode": "silent"},
                    }
                },
                create=True,
            ),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
            mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        self.assertEqual(stderr.getvalue(), "")

    def test_stop_hook_queues_event_and_starts_detached_drain(self) -> None:
        module = _load_module("memdir_stop_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "thread-id": "thread-1",
            "input-messages": [{"role": "user", "content": "remember this"}],
            "last-assistant-message": "ok",
            "client": "codex",
        }
        popen_calls: list[tuple[list[str], dict[str, object]]] = []
        fake_devnull = object()
        fake_subprocess = types.SimpleNamespace(
            DEVNULL=fake_devnull,
            CREATE_NO_WINDOW=0x08000000,
            DETACHED_PROCESS=0x00000008,
        )

        def fake_popen(command: list[str], **kwargs: object) -> object:
            popen_calls.append((command, kwargs))
            return types.SimpleNamespace(pid=12345)

        fake_subprocess.Popen = fake_popen
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "codex"}}}, create=True),
            mock.patch.object(module.memdir_notify, "_append_observation"),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True),
            mock.patch.object(
                module.memdir_notify,
                "enqueue_memdir_extraction_job",
                return_value={"queued": True, "reason": "queued"},
                create=True,
            ) as enqueue,
            mock.patch.object(module.memdir_notify, "extract_memories_from_event", side_effect=AssertionError("direct extraction should not run"), create=True),
            mock.patch.object(module.memdir_notify, "subprocess", fake_subprocess, create=True),
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enqueue.assert_called_once()
        queued_event = enqueue.call_args.args[0]
        self.assertEqual(queued_event["type"], "agent-turn-complete")
        self.assertEqual(queued_event["source"], "codex-stop-hook")
        self.assertEqual(queued_event["cwd"], "/tmp/project")
        self.assertEqual(queued_event["thread-id"], "thread-1")
        self.assertEqual(queued_event["input-messages"], payload["input-messages"])
        self.assertEqual(queued_event["last-assistant-message"], "ok")
        self.assertEqual(len(popen_calls), 1)
        command, kwargs = popen_calls[0]
        self.assertEqual(command[1:5], [str(ROOT / "hooks" / "automation" / "memdir_cli.py"), "drain-queue", "--max-jobs", "20"])
        self.assertEqual(command[5], "--owner")
        self.assertTrue(str(command[6]).startswith("stop-background-"))
        self._assert_detached_drain_kwargs(module.memdir_notify, kwargs, fake_devnull)
        self.assertIn("[memdir_stop] queued=queued background_drain=started", stderr.getvalue())

    def test_stop_hook_merges_transcript_prompt_when_payload_has_no_user_message(self) -> None:
        module = _load_module("memdir_stop_transcript_prompt_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        fake_subprocess = types.SimpleNamespace(DEVNULL=object(), Popen=mock.Mock())
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as raw_tmp:
            transcript_path = pathlib.Path(raw_tmp) / "rollout.jsonl"
            transcript_payload = {
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "transcript prompt"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
                }
            }
            transcript_path.write_text(json.dumps(transcript_payload) + "\n", encoding="utf-8")
            payload = {
                "hookEventName": "Stop",
                "cwd": "/tmp/project",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "thread-id": "thread-1",
                "transcript_path": str(transcript_path),
                "last-assistant-message": "ok",
                "client": "codex",
            }

            with (
                mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "codex"}}}, create=True),
                mock.patch.object(module.memdir_notify, "_append_observation"),
                mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True),
                mock.patch.object(
                    module.memdir_notify,
                    "enqueue_memdir_extraction_job",
                    return_value={"queued": False, "reason": "already_queued"},
                    create=True,
                ) as enqueue,
                mock.patch.object(module.memdir_notify, "subprocess", fake_subprocess, create=True),
                mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
                mock.patch.object(module.sys, "stderr", stderr),
            ):
                exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enqueue.assert_called_once()
        queued_event = enqueue.call_args.args[0]
        self.assertEqual(queued_event["input-messages"], [{"role": "user", "content": "transcript prompt"}])
        fake_subprocess.Popen.assert_not_called()
        self.assertIn("[memdir_stop] queued=already_queued background_drain=skipped", stderr.getvalue())

    def test_stop_hook_prefers_payload_user_message_over_transcript_prompt(self) -> None:
        module = _load_module("memdir_stop_payload_prompt_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")

        with tempfile.TemporaryDirectory() as raw_tmp:
            transcript_path = pathlib.Path(raw_tmp) / "rollout.jsonl"
            transcript_payload = {
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "transcript prompt"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-1"},
                }
            }
            transcript_path.write_text(json.dumps(transcript_payload) + "\n", encoding="utf-8")
            payload = {
                "hookEventName": "Stop",
                "cwd": "/tmp/project",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "thread-id": "thread-1",
                "transcript_path": str(transcript_path),
                "input-messages": [{"role": "user", "content": "payload prompt"}],
                "last-assistant-message": "ok",
                "client": "codex",
            }

            with (
                mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "codex"}}}, create=True),
                mock.patch.object(module.memdir_notify, "_append_observation"),
                mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True),
                mock.patch.object(
                    module.memdir_notify,
                    "enqueue_memdir_extraction_job",
                    return_value={"queued": False, "reason": "already_queued"},
                    create=True,
                ) as enqueue,
                mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
                mock.patch.object(module.sys, "stderr", io.StringIO()),
            ):
                exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enqueue.assert_called_once()
        queued_event = enqueue.call_args.args[0]
        self.assertEqual(queued_event["input-messages"], payload["input-messages"])

    def test_stop_hook_requires_matching_transcript_turn_id(self) -> None:
        module = _load_module("memdir_stop_transcript_turn_mismatch_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")

        with tempfile.TemporaryDirectory() as raw_tmp:
            transcript_path = pathlib.Path(raw_tmp) / "rollout.jsonl"
            transcript_payload = {
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "wrong turn prompt"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-other"},
                }
            }
            transcript_path.write_text(json.dumps(transcript_payload) + "\n", encoding="utf-8")
            payload = {
                "hookEventName": "Stop",
                "cwd": "/tmp/project",
                "session_id": "session-1",
                "turn_id": "turn-1",
                "thread-id": "thread-1",
                "transcript_path": str(transcript_path),
                "last-assistant-message": "ok",
                "client": "codex",
            }
            stderr = io.StringIO()

            with (
                mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "codex"}}}, create=True),
                mock.patch.object(module.memdir_notify, "_append_observation"),
                mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
                mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
                mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
                mock.patch.object(module.sys, "stderr", stderr),
            ):
                exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        self.assertIn("[memdir_stop] skipped: missing user message", stderr.getvalue())

    def test_stop_hook_keeps_missing_user_message_skip_when_transcript_prompt_is_absent(self) -> None:
        module = _load_module("memdir_stop_no_transcript_prompt_under_test", ROOT / "scripts" / "notify" / "memdir_stop.py")
        payload = {
            "hookEventName": "Stop",
            "cwd": "/tmp/project",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "thread-id": "thread-1",
            "last-assistant-message": "ok",
            "client": "codex",
        }
        stderr = io.StringIO()

        with (
            mock.patch.object(module, "load_settings", return_value={"memdir": {"extractor": {"provider": "codex"}}}, create=True),
            mock.patch.object(module.memdir_notify, "_append_observation"),
            mock.patch.object(module.memdir_notify, "is_memdir_enabled", return_value=True, create=True) as enabled,
            mock.patch.object(module.memdir_notify, "enqueue_memdir_extraction_job", create=True) as enqueue,
            mock.patch.object(module.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(module.sys, "stderr", stderr),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        enabled.assert_not_called()
        enqueue.assert_not_called()
        self.assertIn("[memdir_stop] skipped: missing user message", stderr.getvalue())

    def test_memdir_cli_exposes_drain_queue_command(self) -> None:
        module = _load_module("memdir_cli_under_test", ROOT / "hooks" / "automation" / "memdir_cli.py")
        stdout = io.StringIO()

        with (
            mock.patch.object(
                module,
                "drain_memdir_extraction_queue",
                return_value={"processed_count": 2, "reason": "worker_processed", "processed": []},
                create=True,
            ) as drain,
            mock.patch("sys.argv", ["memdir_cli.py", "drain-queue", "--max-jobs", "2", "--owner", "unit"]),
            mock.patch("sys.stdout", stdout),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        drain.assert_called_once_with(max_jobs=2, owner="unit")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["processed_count"], 2)

    def test_memdir_cli_exposes_init_config_command(self) -> None:
        module = _load_module("memdir_cli_init_config_under_test", ROOT / "hooks" / "automation" / "memdir_cli.py")
        stdout = io.StringIO()

        with (
            mock.patch.object(
                module,
                "ensure_user_harness_config",
                return_value={
                    "created": True,
                    "path": "/Users/example/.codex/project-memdir/harness.toml",
                    "source": "/plugin/harness.toml.example",
                },
                create=True,
            ) as init_config,
            mock.patch("sys.argv", ["memdir_cli.py", "init-config"]),
            mock.patch("sys.stdout", stdout),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        init_config.assert_called_once_with()
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["created"])
        self.assertEqual(payload["path"], "/Users/example/.codex/project-memdir/harness.toml")


if __name__ == "__main__":
    unittest.main()
