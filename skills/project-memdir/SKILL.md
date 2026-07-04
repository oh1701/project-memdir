---
name: project-memdir
description: Use project-memdir memory hooks after the plugin is installed in Codex.
---

# Project Memdir

Use this skill when working in a repository that installs the `project-memdir` plugin and expects Codex memory context from the plugin hooks.

## Post-install requirements

- Install through Codex plugin hooks. Hook-based installation is the supported path; do not add a user-managed shell wrapper for memory injection or extraction.
- If a shell function or alias shadows the real `codex` binary, run install commands with `/opt/homebrew/bin/codex` or `command codex` so Codex can manage plugin hooks directly.
- Approve the Codex hook review when Codex asks for hook approval during plugin installation.
- After approval, memory context injection and queued memory extraction run from subsequent Codex sessions, prompts, and turn stops.
- Configure `[memdir.extractor].provider` in `~/.codex/project-memdir/harness.toml` before relying on automatic memory extraction. If the file does not exist yet, the plugin creates it from `harness.toml.example` on the next `SessionStart`; run `hooks/automation/memdir_cli.sh init-config` on POSIX or `hooks\automation\memdir_cli.cmd init-config` on Windows to create it manually.
- Configure Cloudflare Workers AI only when remote embeddings are required. Leave Cloudflare unset to use the built-in `local_hash` embedding fallback.
- Hook manifests use OS-specific launcher wrappers: POSIX uses `hooks/automation/memdir_hook.sh`, Windows `SessionStart` and `UserPromptSubmit` use `hooks/automation/memdir_hook.cmd`, and Windows `Stop` uses `hooks/automation/memdir_stop_hidden.ps1`. The launchers choose an available Python executable instead of relying on a single manifest-level `python` or `py -3` command.

## Hook scope

The plugin automatically registers the `SessionStart`, `UserPromptSubmit`, and `Stop` hooks. These hooks handle session context setup, prompt-time memory retrieval, and turn-stop extraction queueing without a user-managed shell wrapper. The `Stop` hook queues turn-complete extraction and starts a detached background drain, so extraction does not run inline.
