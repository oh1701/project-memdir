# project-memdir

再利用性の低い情報や一度きりの質問は保存されません。

Codex session 間で project 固有の知識を再利用するための Codex 専用 project memory です。

`project-memdir` は Codex hooks を install し、session start と prompt submit のタイミングで関連する project memory を読み込みます。extractor を設定すると、各 turn の終了後に Stop hook が memory extraction を background queue に追加します。

保存された memory は reference context として使われるため、多少の不確実性を含む場合があります。

翻訳: [English](README.md) | [Korean](README.ko.md) | [Simplified Chinese](README.zh-CN.md)

## Install

marketplace source と plugin を install します。

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

Install 中または update 後に Codex が hook review を求めた場合は、この plugin の hooks を承認してください。承認後、新しい Codex session から memory recall が動作します。

## Upgrade

設定済みの Git marketplace snapshot を更新してから、plugin selector をもう一度 install します。

```sh
codex plugin marketplace upgrade project-memdir-local
codex plugin add project-memdir@project-memdir-local
```

Upgrade 中またはその後に Codex が hook review を求めた場合は、この plugin の hooks を承認してください。通常の upgrade 手順として `codex plugin remove` は使わないでください。storage mode が `plugin` の場合は、installed plugin を remove する前に `~/.codex/plugins/cache/.../memories/projects` を backup してください。

## Configuration

Install 後、Codex plugin cache に install された plugin の `harness.toml` を編集します。

```text
~/.codex/plugins/cache/.../project-memdir/.../harness.toml
```

memory recall は保存済み project memory をもとに動作します。各 turn 後の automatic memory extraction は、extractor を選ぶまで無効です。memory extraction は完了した turn を topic JSON にまとめる軽量な処理なので、どの extractor でも低コスト model の使用を推奨します。extraction time は選択した model の速度によって遅れる場合があります。

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

file を直接書き込む local command も使用できます。

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

`local_cli_command` には、メモリ topic JSON ファイルを直接作成できるエージェント CLI の実行コマンドを入力します。

extractor provider または model の設定が誤っている場合、次の prompt context で hook が `project-memdir memory extraction failed` というエラー文を表示することがあります。

## Embeddings

Cloudflare credentials がない場合、memdir は built-in `local_hash` embedding fallback を使います。

Cloudflare Workers AI embeddings を使う場合は、credentials を environment variable で設定してください。secret を `harness.toml` に残さないため、この方法を推奨します。

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

installed `harness.toml` が自分の machine だけで使う private file なら、直接設定することもできます。

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

secret ではない embedding default は `harness.toml` に置けます。

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## Usage

Install と hook approval が終わったら、project で Codex を開くだけです。plugin は現在の project を検出し、その project の memdir から関連する memory だけを prompt context に注入します。

extractor provider を設定すると、完了した turn は Stop hook によって queue に入り、background で処理されます。extraction は現在の turn を block しません。

## Storage

project memories の保存先は `harness.toml` の `[memdir.storage]` で選択します。

```toml
[memdir.storage]
# plugin: installed plugin cache の下に保存します:
#   ${CODEX_ROOT}/memories/projects/<project-slug>
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
- Codex plugin support
- Optional: `codex` CLI for the `codex` extractor
- Optional: `agy` CLI for the `agy` extractor
- Optional: Cloudflare Workers AI credentials for remote embeddings

macOS と Linux では、installed hooks は `sh` と bundled launcher を使い、`python3` を試してから `python` を試します。Windows では、installed hooks は `SessionStart` と `UserPromptSubmit` に `py -3` を使います。`Stop` hook は PowerShell で extraction を queue に入れ、現在の turn を block しません。

## Uninstall

plugin と marketplace source を remove します。

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```
