# project-memdir

Project memory for reusing project-specific knowledge across Codex and Claude Code sessions.

`project-memdir` installs Codex or Claude Code hooks that recall relevant project memories when a session starts or when you submit a prompt. After each turn, the Stop hook can queue memory extraction in the background when an extractor is configured.

It stores only project-specific information that is likely to be useful again. One-off questions and low-reuse details are not stored. Stored memories are injected as reference context, not as guaranteed facts.

Translations: [Korean](README.ko.md) | [Japanese](README.ja.md) | [Simplified Chinese](README.zh-CN.md)

## Installation

### Codex

Add the Git marketplace source, then install the plugin from that source:

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

If Codex asks you to review hooks during installation or after an update, approve the hooks for this plugin. Memory recall starts in new Codex sessions after hook approval.

### Claude Code

To install in Claude Code after pushing this repository to GitHub, run these slash commands inside Claude Code:

```text
/plugin marketplace add oh1701/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

For local testing before pushing, add the local checkout instead:

```text
/plugin marketplace add /Users/ogyuseong/Desktop/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

Claude Code may ask you to trust or approve the plugin hooks. Approve them for this plugin so SessionStart, UserPromptSubmit, and Stop hooks can run. The Claude Code plugin uses the same `~/.project-memdir/harness.toml` configuration described below.

## Configuration

The plugin ships its default template as `harness.toml.example`.
Your editable configuration is stored outside the versioned plugin cache:

```text
~/.project-memdir/harness.toml
```

> **Important:** Automatic memory extraction requires `[memdir.extractor].provider` in `~/.project-memdir/harness.toml`.
> If you do not set this provider, memory recall still works but the Stop hook fails turn-end extraction setup.

If this file does not exist, the next `SessionStart` hook creates it from `harness.toml.example`.
To create it immediately after installation, run the command for your OS from this release's installed plugin cache path.

```sh
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.9
sh hooks/automation/memdir_cli.sh init-config
```

On Windows, use PowerShell:

```powershell
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.9
.\hooks\automation\memdir_cli.cmd init-config
```

Memory recall uses the project memories that already exist.
Automatic extraction after each turn requires a supported extractor before the Stop hook can queue work.
Extraction is a lightweight distillation task that turns completed turns into topic JSON, so a lower-cost model is usually enough.
Extraction can be delayed if the selected model is slow:

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

You can use Claude Code as a file-writing extractor:

```toml
[memdir.extractor]
provider = "claudecode"
claudecode_command = "claude"
claudecode_model = "claudecode-default-model"
```

For Ollama's Claude Code integration, keep Ollama's launch flags in `claudecode_command` and include `--` before the Claude Code arguments that memdir appends:

```toml
[memdir.extractor]
provider = "claudecode"
claudecode_command = "ollama launch claude --model gemma4:31b-cloud --"
claudecode_model = ""
```

Or a file-writing local command:

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

Set `local_cli_command` to an agent CLI command that can write memory topic JSON files.
In this setting, `${CODEX_ROOT}` expands to the installed plugin directory.

If the extractor provider is missing or unsupported, the Stop hook fails immediately. If a configured extractor fails while processing the queue, later prompt context may show a `project-memdir memory extraction failed` notice.

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

Open Codex or Claude Code in a project after installing and approving the hooks. The plugin detects the current project, loads that project's memory directory, and injects only relevant memories into the prompt context.

When an extractor provider is configured, completed turns are queued by the Stop hook and processed in the background. Extraction does not block the current turn.

## Storage

Choose how the project root is resolved with `[memdir.project_root]` in your user `harness.toml`:

```toml
[memdir.project_root]
# cwd: uses the exact directory where the hook or CLI session starts.
#      This is the default on POSIX and Windows.
# detect: walks upward from that directory and uses project markers or Git.
strategy = "cwd"
```

Choose where project memories are stored with `[memdir.storage]` in your user `harness.toml`:

`<project-slug>` preserves the UTF-8/Unicode project directory name in NFC form, folds unsupported characters to `-`, and appends a path digest.

```toml
[memdir.storage]
# plugin: stores memories under a stable user data directory:
#   ${HOME}/.project-memdir/memories/projects/<project-slug>
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
- Codex or Claude Code plugin support
- Optional: `codex` CLI for the `codex` extractor
- Optional: `agy` CLI for the `agy` extractor
- Optional: `claude` CLI or an Ollama Claude Code integration for the `claudecode` extractor
- Optional: Cloudflare Workers AI credentials for remote embeddings

On macOS and Linux, Codex hooks use `sh` and the bundled launcher, which tries `python3` and then `python`. The manual CLI launcher uses the same fallback order.

On Windows, Codex hooks use `py -3` for `SessionStart` and `UserPromptSubmit`. The `Stop` hook uses PowerShell to queue extraction without blocking the turn. Claude Code hooks use a Node dispatcher that tries the available Python launcher on each OS.

## Uninstall

### Codex

Remove the plugin and marketplace source:

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```

### Claude Code

Remove the plugin and marketplace source. `--keep-data` preserves the plugin's persistent Claude Code data directory:

```sh
claude plugin remove project-memdir@project-memdir-local --keep-data
claude plugin marketplace remove project-memdir-local
```

### User Data

To remove the user config and stored memories too, delete the stable user data directory.

On macOS and Linux:

```sh
rm -rf ~/.project-memdir
```

On Windows PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.project-memdir" -ErrorAction SilentlyContinue
```
