# CLAUDE

This repository is the `project-memdir` plugin source. It ships Codex hook support and minimal Claude Code hook/plugin settings for local/project use.

## Scope

- Keep Codex plugin files under `.codex-plugin/`, `hooks/plugin/`, and `hooks/automation/`.
- Keep Claude Code integration files under `.claude/`, `.claude-plugin/`, and this `CLAUDE.md`.
- Do not rewrite or remove existing Codex hook manifests when changing Claude Code settings.
- Treat generated runtime state such as `memories/`, `.pytest_cache/`, and `__pycache__/` as non-source unless the user explicitly asks to inspect it.

## Claude Code Hooks

- Project hooks are declared in `.claude/settings.json`.
- Visible project hook wrappers live in `.claude/hooks/`.
- Claude Code plugin hook declarations live in `hooks/hooks.json` and are referenced by `.claude-plugin/plugin.json`.
- Both project and plugin hook paths delegate to `hooks/claude/`, which reuses the shared `hooks/automation/memdir_hook.sh` dispatcher.

## Validation

- After editing Claude Code JSON settings, run JSON parsing checks.
- After editing `.claude-plugin/plugin.json` or `hooks/hooks.json`, run `claude plugin validate . --strict`.
- After editing hook scripts, run `sh -n` on the changed shell files.
