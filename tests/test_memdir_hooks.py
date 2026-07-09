from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTOMATION_DIR = ROOT / "hooks" / "automation"
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from harness_lib import memdir  # noqa: E402


def _plugin_default_dispatch_command(action: str) -> str:
    script = '"${PLUGIN_ROOT}/hooks/automation/memdir_hook.sh"'
    return f"sh {script} {action}"


def _plugin_windows_dispatch_command(action: str) -> str:
    if action == "stop":
        script = '"${PLUGIN_ROOT}\\hooks\\automation\\memdir_stop_hidden.ps1"'
        return f"powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File {script}"
    script = '"${PLUGIN_ROOT}\\hooks\\automation\\memdir_hook.cmd"'
    return f'cmd.exe /d /c "{script} {action}"'


def _claude_plugin_dispatch_command(action: str) -> str:
    return "node"


def _claude_project_dispatch_command(action: str) -> str:
    return "node"


def _settings(base_dir: pathlib.Path) -> dict[str, object]:
    settings = dict(memdir.memdir_settings())
    settings.update(
        {
            "enabled": True,
            "base_dir": str(base_dir),
            "disabled_project_roots": [],
        }
    )
    return settings


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("memdir_hook_under_test", ROOT / "hooks" / "automation" / "memdir_hook.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemdirHookTests(unittest.TestCase):
    def test_memory_rules_instruct_utf8_reread_for_garbled_memdir_files(self) -> None:
        self.assertIn(
            "If any memdir file or recalled memdir content appears garbled or misdecoded, read the source explicitly as UTF-8.",
            memdir._memory_rules(),
        )

    def test_session_start_core_context_uses_session_start_hook_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            memdir_settings = _settings(tmp / "memdir")
            memdir_settings["embedding"] = {}
            memdir_settings["extractor"] = {}
            memdir_settings["vector"] = {"dimensions": 96}
            settings = {"memdir": memdir_settings}

            with mock.patch.object(memdir, "load_settings", return_value=settings):
                context = memdir.build_session_start_context(str(project))

        self.assertEqual(context["hookEventName"], "SessionStart")
        self.assertIn("Prologue<", context["additionalContext"])
        self.assertIn("Manifest<", context["additionalContext"])
        self.assertIn(
            "memdir_embedding_model = memdir-local-hash",
            context["additionalContext"],
        )
        self.assertIn("memdir_extractor_provider = undefined", context["additionalContext"])
        self.assertIn("memdir_extractor_model = undefined", context["additionalContext"])
        self.assertIn(
            "memdir_embedding_model = memdir-local-hash\n"
            "memdir_extractor_provider = undefined\n"
            "memdir_extractor_model = undefined",
            context["additionalContext"],
        )
        self.assertEqual(context["embeddingModel"], "memdir-local-hash")
        self.assertNotIn("provider=", context["additionalContext"])
        self.assertNotIn("dimensions=", context["additionalContext"])

    def test_session_start_context_declares_cloudflare_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            memdir_settings = _settings(tmp / "memdir")
            memdir_settings["embedding"] = {
                "CLOUDFLARE_ACCOUNT_ID": "account-1",
                "CLOUDFLARE_API_TOKEN": "token-1",
                "model": "@cf/test/custom-embedding",
                "dimensions": 1024,
                "timeout_sec": 30,
            }
            memdir_settings["extractor"] = {
                "provider": "codex",
                "codex_model": "gpt-5-codex",
            }
            settings = {"memdir": memdir_settings}

            with mock.patch.object(memdir, "load_settings", return_value=settings):
                context = memdir.build_session_start_context(str(project))

        self.assertIn(
            "memdir_embedding_model = @cf/test/custom-embedding",
            context["additionalContext"],
        )
        self.assertIn("memdir_extractor_provider = codex", context["additionalContext"])
        self.assertIn("memdir_extractor_model = gpt-5-codex", context["additionalContext"])
        self.assertIn(
            "memdir_embedding_model = @cf/test/custom-embedding\n"
            "memdir_extractor_provider = codex\n"
            "memdir_extractor_model = gpt-5-codex",
            context["additionalContext"],
        )
        self.assertEqual(context["embeddingModel"], "@cf/test/custom-embedding")
        self.assertNotIn("provider=", context["additionalContext"])
        self.assertNotIn("dimensions=", context["additionalContext"])

    def test_session_start_hook_keeps_embedding_model_in_schema_safe_output(self) -> None:
        module = _load_hook_module()
        stdout = io.StringIO()
        stderr = io.StringIO()
        payload = {"cwd": "C:\\project"}

        with (
            mock.patch.object(module, "_refresh_scheduler_if_available"),
            mock.patch.object(
                memdir,
                "build_session_start_context",
                return_value={
                    "hookEventName": "SessionStart",
                    "additionalContext": "Session context\nmemdir_embedding_model = memdir-local-hash\nmemdir_extractor_provider = undefined\nmemdir_extractor_model = undefined",
                    "embeddingModel": "memdir-local-hash",
                },
            ),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = module._session_start()

        self.assertEqual(code, 0)
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(
            emitted["hookSpecificOutput"],
            {
                "hookEventName": "SessionStart",
                "additionalContext": "Session context\nmemdir_embedding_model = memdir-local-hash\nmemdir_extractor_provider = undefined\nmemdir_extractor_model = undefined",
            },
        )
        self.assertNotIn("embeddingModel", emitted["hookSpecificOutput"])
        self.assertIn("[memdir_session_start] embedding=memdir-local-hash", stderr.getvalue())

    def test_session_start_hook_bootstraps_user_harness_config(self) -> None:
        module = _load_hook_module()
        stdout = io.StringIO()
        stderr = io.StringIO()
        payload = {"cwd": "/tmp/project"}

        with (
            mock.patch.object(module, "_refresh_scheduler_if_available"),
            mock.patch(
                "harness_lib.settings.ensure_user_harness_config",
                return_value={
                    "created": True,
                    "path": "/Users/example/.project-memdir/harness.toml",
                    "source": "/plugin/harness.toml.example",
                },
            ) as ensure_config,
            mock.patch.object(
                memdir,
                "build_session_start_context",
                return_value={
                    "hookEventName": "SessionStart",
                    "additionalContext": "Session context",
                    "embeddingModel": "",
                },
            ),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = module._session_start()

        self.assertEqual(code, 0)
        ensure_config.assert_called_once_with()
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(emitted["hookSpecificOutput"]["additionalContext"], "Session context")
        self.assertIn("[memdir_session_start] created user config: /Users/example/.project-memdir/harness.toml", stderr.getvalue())

    def test_user_prompt_submit_preserves_context_output(self) -> None:
        module = _load_hook_module()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            payload = {
                "cwd": str(project),
                "session_id": "session-1",
                "turn_id": "turn-1",
                "prompt": "remember this",
            }

            with (
                mock.patch.object(memdir, "is_memdir_enabled", return_value=True),
                mock.patch.object(memdir, "record_user_prompt_submit") as record_prompt,
                mock.patch.object(memdir, "build_memdir_context", return_value={"system_message": "Memory context"}) as build,
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                code = module._user_prompt_submit()

        self.assertEqual(code, 0)
        emitted = json.loads(stdout.getvalue())
        self.assertEqual(
            emitted["hookSpecificOutput"],
            {"hookEventName": "UserPromptSubmit", "additionalContext": "Memory context"},
        )
        self.assertTrue(emitted["suppressOutput"])
        record_prompt.assert_called_once_with(str(project), user_prompt="remember this", turn_id="turn-1", session_id="session-1")
        build.assert_called_once_with("remember this", str(project), include_core_paths=False)
        self.assertEqual(stderr.getvalue(), "")

    def test_user_prompt_submit_skips_context_when_disabled_or_empty_prompt(self) -> None:
        module = _load_hook_module()

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            disabled_payload = {
                "cwd": str(project),
                "session_id": "session-1",
                "turn_id": "turn-1",
                "prompt": "remember this",
            }

            with (
                mock.patch.object(memdir, "is_memdir_enabled", return_value=False),
                mock.patch.object(memdir, "build_memdir_context") as build,
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(disabled_payload))),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                disabled_code = module._user_prompt_submit()

            empty_payload = {
                "cwd": str(project),
                "session_id": "session-1",
                "turn_id": "turn-2",
                "prompt": "   ",
            }

            with (
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(empty_payload))),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                empty_code = module._user_prompt_submit()

        self.assertEqual(disabled_code, 0)
        self.assertEqual(empty_code, 0)
        build.assert_not_called()

    def test_session_start_hook_emits_embedding_status_to_stderr(self) -> None:
        script = (ROOT / "hooks" / "automation" / "memdir_hook.py").read_text(encoding="utf-8")

        self.assertIn("[memdir_session_start] embedding=", script)
        self.assertIn("file=sys.stderr", script)
        self.assertIn('"suppressOutput": False', script)

    def test_plugin_manifest_registers_stop_hook(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        stop_hook_path = "./hooks/plugin/stop.json"

        self.assertIn(stop_hook_path, manifest["hooks"])

        stop_hook = json.loads((ROOT / stop_hook_path).read_text(encoding="utf-8"))
        command = stop_hook["hooks"]["Stop"][0]["hooks"][0]
        self.assertEqual(command["type"], "command")
        self.assertEqual(command["command"], _plugin_default_dispatch_command("stop"))
        self.assertEqual(command["commandWindows"], _plugin_windows_dispatch_command("stop"))
        self.assertEqual(command["timeout"], 15)

    def test_plugin_hook_manifests_use_os_specific_dispatch_commands(self) -> None:
        cases = [
            ("./hooks/plugin/session-start.json", "SessionStart", "session-start"),
            ("./hooks/plugin/user-prompt-submit.json", "UserPromptSubmit", "user-prompt-submit"),
            ("./hooks/plugin/stop.json", "Stop", "stop"),
        ]

        for hook_path, event_name, action in cases:
            with self.subTest(hook_path=hook_path):
                hook = json.loads((ROOT / hook_path).read_text(encoding="utf-8"))
                command = hook["hooks"][event_name][0]["hooks"][0]

                self.assertEqual(command["command"], _plugin_default_dispatch_command(action))
                self.assertEqual(command["commandWindows"], _plugin_windows_dispatch_command(action))

                command_values = [command["command"], command["commandWindows"]]
                self.assertTrue(all("||" not in value for value in command_values))
                self.assertEqual(command["command"], f'sh "${{PLUGIN_ROOT}}/hooks/automation/memdir_hook.sh" {action}')
                self.assertNotRegex(command["command"], r"^python\b")
                self.assertNotRegex(command["command"], r"^python3\b")
                self.assertNotIn("memdir_hook.py", command["command"])
                if action == "stop":
                    self.assertNotIn("-WindowStyle Hidden", command["commandWindows"])
                    self.assertIn("-NonInteractive", command["commandWindows"])
                    self.assertIn("memdir_stop_hidden.ps1", command["commandWindows"])
                else:
                    self.assertEqual(
                        command["commandWindows"],
                        f'cmd.exe /d /c ""${{PLUGIN_ROOT}}\\hooks\\automation\\memdir_hook.cmd" {action}"',
                    )
                    self.assertRegex(command["commandWindows"], r"^cmd\.exe /d /c\b")
                    self.assertIn("memdir_hook.cmd", command["commandWindows"])
                    self.assertNotIn("memdir_hook.py", command["commandWindows"])

    def test_claude_plugin_hooks_use_cross_platform_node_dispatcher(self) -> None:
        hook = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        cases = [
            ("SessionStart", "session-start"),
            ("UserPromptSubmit", "user-prompt-submit"),
            ("Stop", "stop"),
        ]

        for event_name, action in cases:
            with self.subTest(event_name=event_name):
                command = hook["hooks"][event_name][0]["hooks"][0]

                self.assertEqual(command["type"], "command")
                self.assertEqual(command["command"], _claude_plugin_dispatch_command(action))
                self.assertEqual(command["args"], [f"${{CLAUDE_PLUGIN_ROOT}}/hooks/claude/memdir-hook.mjs", action])
                self.assertNotIn("commandWindows", command)
                self.assertNotIn(" sh ", f" {command['command']} ")
                self.assertIn("memdir-hook.mjs", command["args"][0])

    def test_claude_project_settings_use_cross_platform_node_dispatcher(self) -> None:
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        cases = [
            ("SessionStart", "session-start"),
            ("UserPromptSubmit", "user-prompt-submit"),
            ("Stop", "stop"),
        ]

        for event_name, action in cases:
            with self.subTest(event_name=event_name):
                command = settings["hooks"][event_name][0]["hooks"][0]

                self.assertEqual(command["type"], "command")
                self.assertEqual(command["command"], _claude_project_dispatch_command(action))
                self.assertEqual(command["args"], [f"${{CLAUDE_PROJECT_DIR}}/hooks/claude/memdir-hook.mjs", action])
                self.assertNotIn("commandWindows", command)
                self.assertNotIn(".claude/hooks", command["command"])

    def test_claude_node_dispatcher_supports_windows_without_shell_wrapper(self) -> None:
        launcher = (ROOT / "hooks" / "claude" / "memdir-hook.mjs").read_text(encoding="utf-8")

        self.assertIn('process.platform === "win32"', launcher)
        self.assertIn('["py", ["-3", hookScript, action]]', launcher)
        self.assertIn('["python", [hookScript, action]]', launcher)
        self.assertIn('["python3", [hookScript, action]]', launcher)
        self.assertIn("windowsHide: true", launcher)
        self.assertIn('PROJECT_MEMDIR_CLIENT: "claude"', launcher)
        self.assertIn('{"continue":true,"suppressOutput":true}', launcher)
        self.assertNotIn("memdir_hook.sh", launcher)

    def test_stop_windows_launcher_does_not_hide_parent_terminal(self) -> None:
        stop_hook = json.loads((ROOT / "hooks" / "plugin" / "stop.json").read_text(encoding="utf-8"))
        command = stop_hook["hooks"]["Stop"][0]["hooks"][0]["commandWindows"]
        launcher = (ROOT / "hooks" / "automation" / "memdir_stop_hidden.ps1").read_text(encoding="utf-8")

        self.assertNotIn("-WindowStyle Hidden", command)
        self.assertIn("System.Diagnostics.ProcessStartInfo", launcher)
        self.assertIn("CreateNoWindow = $true", launcher)
        self.assertIn("ProcessWindowStyle]::Hidden", launcher)
        self.assertIn("RedirectStandardInput = $true", launcher)
        self.assertIn("WaitForExit", launcher)
        self.assertNotIn("Start-Process", launcher)
        self.assertNotIn("-Wait", launcher)
        self.assertIn("exit $exitCode", launcher)
        self.assertIn("exit 1", launcher)
        self.assertIn("PYTHONUTF8", launcher)
        self.assertIn("memdir_hook.py", launcher)
        self.assertIn(" stop", launcher)

    def test_windows_hook_launcher_uses_python_launchers_without_shell_fallback_operator(self) -> None:
        launcher = (ROOT / "hooks" / "automation" / "memdir_hook.cmd").read_text(encoding="utf-8")

        self.assertIn('set "PYTHONUTF8=1"', launcher)
        self.assertIn('set "PYTHONIOENCODING=utf-8"', launcher)
        self.assertIn("py -3", launcher)
        self.assertIn("python", launcher)
        self.assertIn("python3", launcher)
        self.assertNotIn("||", launcher)
        self.assertIn('echo {"continue":true,"suppressOutput":true}', launcher)

    def test_posix_hook_launcher_uses_python_launchers_without_shell_fallback_operator(self) -> None:
        launcher = (ROOT / "hooks" / "automation" / "memdir_hook.sh").read_text(encoding="utf-8")

        self.assertIn("#!/bin/sh", launcher)
        self.assertIn("python3", launcher)
        self.assertIn("python", launcher)
        self.assertNotIn("py -3", launcher)
        self.assertNotIn("||", launcher)
        self.assertIn('{"continue":true,"suppressOutput":true}', launcher)

    def test_posix_stop_launcher_preserves_dispatcher_failure_exit_code(self) -> None:
        if not pathlib.Path("/bin/sh").exists():
            self.skipTest("/bin/sh is unavailable")

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            launcher = tmp / "memdir_hook.sh"
            shutil.copy2(ROOT / "hooks" / "automation" / "memdir_hook.sh", launcher)

            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            for name in ("python3", "python"):
                fake_python = bin_dir / name
                fake_python.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
                fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)

            env = {**os.environ, "PATH": str(bin_dir)}
            result = subprocess.run(
                ["/bin/sh", str(launcher), "stop"],
                cwd=str(tmp),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 7)

    def test_hook_dispatcher_degrades_when_memdir_import_fails(self) -> None:
        module = _load_hook_module()
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            mock.patch.dict(sys.modules, {"harness_lib.memdir": None}),
            mock.patch.object(sys, "stdin", io.StringIO("{}")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = module._session_start()

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertTrue(payload["continue"])
        self.assertTrue(payload["suppressOutput"])
        self.assertIn("session-start unavailable", stderr.getvalue())

    def test_stop_dispatcher_returns_failed_when_notify_import_fails(self) -> None:
        module = _load_hook_module()
        stderr = io.StringIO()

        with (
            mock.patch.dict(sys.modules, {"memdir_stop": None}),
            contextlib.redirect_stderr(stderr),
        ):
            code = module._stop()

        self.assertEqual(code, 1)
        self.assertIn("stop failed", stderr.getvalue())

    def test_stop_dispatcher_skips_when_memdir_skip_env_is_set(self) -> None:
        module = _load_hook_module()
        fake_stop = mock.Mock()

        with (
            mock.patch.dict(os.environ, {"CODEX_MEMDIR_SKIP": "1"}),
            mock.patch.dict(sys.modules, {"memdir_stop": fake_stop}),
        ):
            code = module._stop()

        self.assertEqual(code, 0)
        fake_stop.main.assert_not_called()

    def test_stop_dispatcher_returns_failed_when_notify_main_raises(self) -> None:
        module = _load_hook_module()
        stderr = io.StringIO()
        fake_stop = mock.Mock()
        fake_stop.main.side_effect = RuntimeError("queue write failed")

        with (
            mock.patch.dict(sys.modules, {"memdir_stop": fake_stop}),
            contextlib.redirect_stderr(stderr),
        ):
            code = module._stop()

        self.assertNotEqual(code, 0)
        self.assertIn("stop failed", stderr.getvalue())

    def test_notify_event_router_uses_current_python_interpreter(self) -> None:
        router = (ROOT / "scripts" / "notify" / "notify_event_router.py").read_text(encoding="utf-8")

        self.assertIn("sys.executable", router)
        self.assertNotIn('["python3"', router)

    def test_automation_payload_only_ships_launcher_shell_helpers(self) -> None:
        shell_helpers = sorted(
            path.relative_to(ROOT).as_posix() for path in (ROOT / "hooks" / "automation").glob("*.sh")
        )

        self.assertEqual(shell_helpers, ["hooks/automation/memdir_cli.sh", "hooks/automation/memdir_hook.sh"])

    def test_cli_launchers_use_os_specific_python_fallbacks(self) -> None:
        posix_launcher = (ROOT / "hooks" / "automation" / "memdir_cli.sh").read_text(encoding="utf-8")
        windows_launcher = (ROOT / "hooks" / "automation" / "memdir_cli.cmd").read_text(encoding="utf-8")

        self.assertIn("#!/bin/sh", posix_launcher)
        self.assertIn("memdir_cli.py", posix_launcher)
        self.assertIn("python3", posix_launcher)
        self.assertIn("python", posix_launcher)
        self.assertNotIn("py -3", posix_launcher)
        self.assertNotIn("||", posix_launcher)

        self.assertIn("memdir_cli.py", windows_launcher)
        self.assertIn("py -3", windows_launcher)
        self.assertIn("python", windows_launcher)
        self.assertIn("python3", windows_launcher)
        self.assertNotIn("||", windows_launcher)

    def test_readmes_stay_user_facing_without_manual_cli_or_shell_helper(self) -> None:
        readmes = ["README.md", "README.ko.md", "README.ja.md", "README.zh-CN.md"]

        for readme in readmes:
            with self.subTest(readme=readme):
                text = (ROOT / readme).read_text(encoding="utf-8")

                self.assertNotIn("memdir_cli.py extract-event", text)
                self.assertNotIn("memdir_cli.py drain-queue", text)
                self.assertNotIn("memdir.sh", text)

    def test_user_prompt_context_uses_recalled_summaries_before_topic_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}
            topics_dir = tmp / "memdir" / "project" / "topics"
            topic_path = topics_dir / "profile.json"
            workflow_path = topics_dir / "workflow.json"
            recalled = [
                {
                    "path": str(topic_path),
                    "filename": "topics/profile.json",
                    "name": "Developer profile",
                    "description": "User Android developer profile. " * 12,
                    "type": "user",
                    "updated_at": "2026-04-27T00:00:00Z",
                    "excerpt": "Android developer profile memory. " * 30,
                },
                {
                    "path": str(workflow_path),
                    "filename": "topics/workflow.json",
                    "name": "Workflow",
                    "description": "Use focused tests for narrow hook changes.",
                    "type": "project",
                    "updated_at": "2026-04-27T00:00:00Z",
                    "excerpt": "Workflow memory. " * 30,
                }
            ]

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch.object(memdir, "find_relevant_memories", return_value=recalled),
            ):
                context = memdir.build_memdir_context("Android developer", str(project), include_core_paths=False)

        self.assertIn("Use recalled memory summaries first", context["system_message"])
        self.assertIn("read the listed file under MemoryJSONDir", context["system_message"])
        self.assertIn(f"MemoryJSONDir<{topics_dir}>", context["system_message"])
        self.assertIn("- profile.json: User Android developer profile.", context["system_message"])
        self.assertIn("- workflow.json: Use focused tests for narrow hook changes.", context["system_message"])
        self.assertEqual(context["system_message"].count(str(topics_dir)), 1)
        self.assertNotIn(f"MemoryJSON<{topic_path}>", context["system_message"])
        self.assertNotIn(f"MemoryJSON<{workflow_path}>", context["system_message"])
        self.assertIn("User Android developer profile.", context["system_message"])
        self.assertNotIn("topics/profile.json [user]", context["system_message"])
        self.assertNotIn("updated=2026-04-27T00:00:00Z", context["system_message"])
        self.assertNotIn("Developer profile", context["system_message"])
        self.assertLessEqual(len(context["system_message"].split("- profile.json: ", 1)[1].split("\n", 1)[0]), 123)
        self.assertNotIn("Android developer profile memory.", context["system_message"])

    def test_user_prompt_context_requires_lexical_memory_match(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = pathlib.Path(raw_tmp)
            project = tmp / "project"
            project.mkdir()
            settings = {"memdir": _settings(tmp / "memdir")}

            with (
                mock.patch.object(memdir, "load_settings", return_value=settings),
                mock.patch.object(memdir, "find_relevant_memories", return_value=[]) as find_relevant,
            ):
                memdir.build_memdir_context("FCM token", str(project), include_core_paths=False)

        find_relevant.assert_called_once_with("FCM token", str(project), require_lexical_match=True)


if __name__ == "__main__":
    unittest.main()
