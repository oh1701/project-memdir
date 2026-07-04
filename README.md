# project-memdir

Low-reuse information and one-off questions are not stored.

Codex-only project memory for reusing project-specific knowledge across sessions.

`project-memdir` installs Codex hooks that recall relevant project memories when a session starts or when you submit a prompt. After each turn, the Stop hook can queue memory extraction in the background when an extractor is configured.

Stored memories are used as reference context, so they carry a small amount of uncertainty.

Translations: [Korean](README.ko.md) | [Japanese](README.ja.md) | [Simplified Chinese](README.zh-CN.md)

## Installation

Install the marketplace source and plugin:

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

If Codex asks you to review hooks during installation or after an update, approve the hooks for this plugin. Memory recall starts in new Codex sessions after hook approval.

## Upgrade

Refresh the configured Git marketplace snapshot, then install the plugin selector again:

```sh
codex plugin marketplace upgrade project-memdir-local
codex plugin add project-memdir@project-memdir-local
```

If Codex asks you to review hooks during or after the upgrade, approve the hooks for this plugin. Do not use `codex plugin remove` as a normal upgrade step. With the default `plugin` storage mode, project memories live under `~/.codex/project-memdir/memories/projects` rather than the versioned plugin cache.

## Configuration

The plugin keeps its default template at `harness.toml.example`. Your editable configuration lives outside the versioned plugin cache:

```text
~/.codex/project-memdir/harness.toml
```

If this file does not exist, the next `SessionStart` hook creates it from `harness.toml.example`. To create it immediately after installation, run the OS-specific CLI launcher from the installed plugin root:

```sh
sh hooks/automation/memdir_cli.sh init-config
```

```bat
hooks\automation\memdir_cli.cmd init-config
```

Memory recall works from the stored project memories. Automatic memory extraction after each turn is disabled until you choose an extractor. Memory extraction is a lightweight distillation task that turns completed turns into topic JSON, so a lower-cost model is recommended for any extractor. Extraction time can be delayed depending on the selected model's speed:

```toml
[memdir.extractor]
provider = "codex"
```

For the `codex` extractor, set `codex_model` when you want to choose a specific Codex model:

```toml
[memdir.extractor]
provider = "codex"
codex_model = "codex-default-model"
```

You can also use `agy`:

```toml
[memdir.extractor]
provider = "agy"
agy_bin = "agy"
agy_model = "agy-default-model"
```

Or a file-writing local command:

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

`local_cli_command`에는 메모리 topic JSON 파일을 직접 생성할 수 있는 에이전트 CLI 실행 명령을 입력합니다.

If the extractor provider or model is misconfigured, the hooks may show a `project-memdir memory extraction failed` notice in the next prompt context.

## Embeddings

If Cloudflare credentials are not set, memdir uses the built-in `local_hash` embedding fallback.

To use Cloudflare Workers AI embeddings, set credentials in your environment. This is the recommended setup because secrets stay outside `harness.toml`:

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

If your user `harness.toml` is private to your machine, you can also set credentials directly:

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

Non-secret embedding defaults can stay in your user `harness.toml`:

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## Usage

Open Codex in a project after installing and approving the hooks. The plugin detects the current project, loads that project's memdir, and injects only the relevant memories into the prompt context.

When an extractor provider is configured, completed turns are queued by the Stop hook and processed in the background. Extraction does not block the current turn.

## Storage

Choose how the project root is resolved with `[memdir.project_root]` in your user `harness.toml`:

```toml
[memdir.project_root]
# cwd: uses the exact directory where the Codex hook or CLI session starts.
#      This is the default on POSIX and Windows.
# detect: walks upward from that directory and uses project markers or Git.
strategy = "cwd"
```

Choose where project memories are stored with `[memdir.storage]` in your user `harness.toml`:

```toml
[memdir.storage]
# plugin: stores memories under a stable user data directory:
#   ${HOME}/.codex/project-memdir/memories/projects/<project-slug>
# project: stores memories inside the active project:
#   <project-root>/.project-memdir
mode = "plugin"
project_dir_name = ".project-memdir"
```

Each project memory contains:

- `manifest.json`
- `topics/*.json`
- `vector_index.sqlite3`

Delete those files only when you intentionally want to remove stored memories.

## Requirements

- Python 3.11 or newer
- Codex plugin support
- Optional: `codex` CLI for the `codex` extractor
- Optional: `agy` CLI for the `agy` extractor
- Optional: Cloudflare Workers AI credentials for remote embeddings

On macOS and Linux, the installed hooks use `sh` and the bundled launcher, which tries `python3` and then `python`. The manual CLI launcher follows the same POSIX fallback behavior. On Windows, the installed hooks use `py -3` for `SessionStart` and `UserPromptSubmit`, and the manual CLI launcher tries `py -3`, `python`, then `python3`. The `Stop` hook uses PowerShell to queue extraction without blocking the turn.

## Uninstall

Remove the plugin and marketplace source:

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```
