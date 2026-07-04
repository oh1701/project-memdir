# Changelog

## 1.0.3 - 2026-07-04

- Store default plugin-mode project memories under `${HOME}/.codex/project-memdir/memories/projects` so they are not tied to versioned plugin cache directories.
- Move editable harness configuration to `${HOME}/.codex/project-memdir/harness.toml`, with `harness.toml.example` kept as the plugin-bundled template.
- Add automatic `SessionStart` config bootstrap plus OS-specific `init-config` CLI launchers.
- Update documentation to describe the stable user data directory for default plugin storage.

## 1.0.2 - 2026-07-04

- Clarify previous memory extraction failure notices so prompt context says the failure is from a previous extraction attempt.
- Limit previous extraction failure notices to `kind` and `reason`; omit provider, model, detail, and hint text from prompt context.
- Add a SessionStart prologue rule instructing agents to read garbled or misdecoded topic JSON files explicitly as UTF-8.
