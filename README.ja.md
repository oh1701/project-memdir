# project-memdir

Codex と Claude Code の session 間で project 固有の知識を再利用するための project memory です。

`project-memdir` は Codex または Claude Code hooks を install し、session start と prompt submit のタイミングで関連する project memory を読み込みます。extractor を設定すると、各 turn の終了後に Stop hook が memory extraction を background queue に追加します。

再利用される可能性が高い project 固有の情報だけを保存します。一度きりの質問や再利用性の低い details は保存しません。保存された memory は、保証された facts ではなく reference context として注入されます。

翻訳: [English](README.md) | [Korean](README.ko.md) | [Simplified Chinese](README.zh-CN.md)

## Install

### Codex

Git marketplace source を追加し、その source から plugin を install します。

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

Install 中または update 後に Codex が hook review を求めた場合は、この plugin の hooks を承認してください。承認後、新しい Codex session から memory recall が動作します。

### Claude Code

GitHub に push した後に Claude Code で install するには、Claude Code 内で次の slash command を実行します。

```text
/plugin marketplace add oh1701/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

push 前に local checkout で test する場合は、local path を追加します。

```text
/plugin marketplace add /Users/ogyuseong/Desktop/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

Claude Code が plugin hooks の trust または approval を求めた場合は、この plugin の hooks を承認してください。これにより SessionStart, UserPromptSubmit, Stop hooks が実行されます。Claude Code plugin も、以下で説明する同じ `~/.project-memdir/harness.toml` configuration を使います。

## Configuration

plugin は default template を `harness.toml.example` として同梱しています。
ユーザーが編集する設定ファイルは versioned plugin cache の外に保存されます。

```text
~/.project-memdir/harness.toml
```

> **重要:** automatic memory extraction を使うには、`~/.project-memdir/harness.toml` で `[memdir.extractor].provider` を必ず設定してください。
> この provider を設定しない場合、memory recall は動作しますが、Stop hook は turn 終了時の extraction setup を失敗として扱います。

このファイルがない場合、次の `SessionStart` hook が `harness.toml.example` から自動生成します。
Install 直後に作成したい場合は、この release が install された plugin cache path で OS 別の command を実行します。

```sh
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.11
sh hooks/automation/memdir_cli.sh init-config
```

Windows では PowerShell を基準に実行します。

```powershell
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.11
.\hooks\automation\memdir_cli.cmd init-config
```

memory recall は既存の project memory をもとに動作します。
各 turn 後の automatic extraction では、Stop hook が work を queue に入れる前に supported extractor の設定が必要です。
extraction は完了した turn を topic JSON にまとめる軽量な処理なので、通常は低コスト model で十分です。
選択した model が遅い場合、extraction は遅延することがあります。

```toml
[memdir.extractor]
provider = "codex"
```

`codex` extractor で特定の Codex model を選ぶ場合は `codex_model` を設定します。

```toml
[memdir.extractor]
provider = "codex"
codex_model = "codex-default-model"
```

`agy` も使用できます。

```toml
[memdir.extractor]
provider = "agy"
agy_bin = "agy"
agy_model = "agy-default-model"
```

Claude Code を file-writing extractor として使用することもできます。

```toml
[memdir.extractor]
provider = "claudecode"
claudecode_model = "claudecode-default-model"
```

file を直接書き込む local command も使用できます。

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

`local_cli_command` には、memory topic JSON files を直接作成できる agent CLI command を設定します。
この設定では、`${CODEX_ROOT}` は installed plugin directory に展開されます。

extractor provider が未設定または unsupported の場合、Stop hook は即時に失敗します。設定済み extractor が queue 処理中に失敗した場合、後続の prompt context で `project-memdir memory extraction failed` という error notice を表示することがあります。

## Embeddings

Cloudflare credentials がない場合、memdir は built-in `local_hash` embedding fallback を使います。

Cloudflare Workers AI embeddings を使う場合は、credentials を environment variable で設定してください。secret を `harness.toml` に残さないため、この方法を推奨します。

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

user `harness.toml` が自分の machine だけで使う private file なら、直接設定することもできます。

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

secret ではない embedding default は user `harness.toml` に置けます。

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## Usage

Install と hook approval が終わったら、project で Codex または Claude Code を開きます。plugin は現在の project を検出し、その project の memory directory を読み込み、関連する memory だけを prompt context に注入します。

extractor provider を設定すると、完了した turn は Stop hook によって queue に入り、background で処理されます。extraction は現在の turn を block しません。

## Storage

project root の解決方法は user `harness.toml` の `[memdir.project_root]` で選択します。

```toml
[memdir.project_root]
# cwd: hook または CLI session が開始した正確な directory を使います。
#      POSIX と Windows の default です。
# detect: その directory から上にたどり、project markers または Git を使います。
strategy = "cwd"
```

project memories の保存先は user `harness.toml` の `[memdir.storage]` で選択します。

`<project-slug>` は UTF-8/Unicode の project directory 名を NFC 形式で保持し、対応しない文字を `-` に変換して path digest を付けます。

```toml
[memdir.storage]
# plugin: stable user data directory の下に保存します:
#   ${HOME}/.project-memdir/memories/projects/<project-slug>
# project: active project の内部に保存します:
#   <project-root>/.project-memdir
mode = "plugin"
project_dir_name = ".project-memdir"
```

各 project memory は次の file で構成されます。

- `manifest.json`
- `topics/*.json`
- `vector_index.sqlite3`

保存済み memory も削除したい場合だけ、これらの file を削除してください。

## Requirements

- Python 3.11 or newer
- Codex or Claude Code plugin support
- Optional: `codex` CLI for the `codex` extractor
- Optional: `agy` CLI for the `agy` extractor
- Optional: `claude` CLI for the `claudecode` extractor
- Optional: Cloudflare Workers AI credentials for remote embeddings

macOS と Linux では、Codex hooks は `sh` と bundled launcher を使い、`python3` を試してから `python` を試します。Manual CLI launcher も同じ順序で fallback します。

Windows では、Codex hooks は `SessionStart` と `UserPromptSubmit` に `py -3` を使います。`Stop` hook は PowerShell で extraction を queue に入れ、現在の turn を block しません。Claude Code hooks は各 OS で利用可能な Python launcher を試す Node dispatcher を使います。

## Uninstall

### Codex

plugin と marketplace source を remove します。

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```

### Claude Code

plugin と marketplace source を remove します。`--keep-data` は plugin の Claude Code persistent data directory を保持します。

```sh
claude plugin remove project-memdir@project-memdir-local --keep-data
claude plugin marketplace remove project-memdir-local
```

### User Data

user config と保存済み memory も削除する場合は、stable user data directory を削除します。

macOS と Linux:

```sh
rm -rf ~/.project-memdir
```

Windows PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.project-memdir" -ErrorAction SilentlyContinue
```
