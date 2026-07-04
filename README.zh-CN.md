# project-memdir

用于在 Codex sessions 之间复用项目知识的 project memory。

`project-memdir` 会安装 Codex hooks，在 session start 和 prompt submit 时加载相关 project memory。配置 extractor 后，每个 turn 结束时 Stop hook 会把 memory extraction 加入后台 queue。

它只保存之后可能复用的项目相关信息。一次性问题或复用价值较低的细节不会被保存。已保存的 memories 会作为参考上下文注入，而不是作为确定事实。

翻译: [English](README.md) | [Korean](README.ko.md) | [Japanese](README.ja.md)

## 安装

添加 Git marketplace source，然后从该 source 安装 plugin。

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

如果 Codex 在安装或更新后要求 hook review，请批准此 plugin 的 hooks。批准后，新的 Codex session 会开始使用 memory recall。

## 配置

plugin 随附的默认模板是 `harness.toml.example`。
用户可编辑的配置文件存储在版本化 plugin cache 之外。

```text
~/.codex/project-memdir/harness.toml
```

> **重要:** 如需使用 automatic memory extraction，必须在 `~/.codex/project-memdir/harness.toml` 中设置 `[memdir.extractor].provider`。
> 如果没有设置该 provider，memory recall 仍会工作，但 turn 结束后的 extraction 会保持禁用。

如果这个文件不存在，下一个 `SessionStart` hook 会从 `harness.toml.example` 自动创建它。
如果想在安装后立即创建，请在当前 release 的 installed plugin cache path 中运行对应 OS 的命令。

```sh
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.4
sh hooks/automation/memdir_cli.sh init-config
```

在 Windows 上，请以 PowerShell 为准执行。

```powershell
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.4
.\hooks\automation\memdir_cli.cmd init-config
```

memory recall 会基于已经存在的 project memories 工作。
每个 turn 后的 automatic extraction 在选择 extractor 前是禁用的。
extraction 是把完成的 turn 整理成 topic JSON 的轻量任务，通常低成本模型就足够。
若所选模型较慢，extraction 可能会延迟。

```toml
[memdir.extractor]
provider = "codex"
```

如果要为 `codex` extractor 指定 Codex 模型，请设置 `codex_model`。

```toml
[memdir.extractor]
provider = "codex"
codex_model = "codex-default-model"
```

也可以使用 `agy`。

```toml
[memdir.extractor]
provider = "agy"
agy_bin = "agy"
agy_model = "agy-default-model"
```

也可以使用能直接写文件的 local command。

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

`local_cli_command` 用于填写能够直接创建 memory topic JSON files 的 agent CLI command。
在这个设置中，`${CODEX_ROOT}` 会展开为 installed plugin directory。

如果 extractor provider 或 model 配置错误，hook 可能会在后续 prompt context 中显示 `project-memdir memory extraction failed` 错误提示。

## Embeddings

如果没有 Cloudflare credentials，memdir 会使用内置 `local_hash` embedding fallback。

如需使用 Cloudflare Workers AI embeddings，请通过环境变量设置 credentials。推荐这种方式，因为 secret 不会留在 `harness.toml` 中。

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

如果用户 `harness.toml` 只是你本机使用的 private file，也可以直接写入 credentials。

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

非 secret 的 embedding defaults 可以保留在用户 `harness.toml` 中。

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## 使用

完成安装和 hook approval 后，在项目中打开 Codex 即可。plugin 会检测当前 project，加载该 project 的 memory directory，并只把相关 memories 注入 prompt context。

配置 extractor provider 后，完成的 turns 会通过 Stop hook 进入 queue，并在后台处理。extraction 不会阻塞当前 turn。

## 存储

project root 的解析方式由用户 `harness.toml` 中的 `[memdir.project_root]` 选择。

```toml
[memdir.project_root]
# cwd: 使用 Codex hook 或 CLI session 启动时所在的精确目录。
#      这是 POSIX 和 Windows 的默认值。
# detect: 从该目录向上查找，并使用 project markers 或 Git。
strategy = "cwd"
```

project memories 的存储位置由用户 `harness.toml` 中的 `[memdir.storage]` 选择。

```toml
[memdir.storage]
# plugin: 存储在稳定的用户数据目录下:
#   ${HOME}/.codex/project-memdir/memories/projects/<project-slug>
# project: 存储在当前项目内部:
#   <project-root>/.project-memdir
mode = "plugin"
project_dir_name = ".project-memdir"
```

每个 project memory 包含：

- `manifest.json`
- `topics/*.json`
- `vector_index.sqlite3`

只有在确实想删除已保存 memories 时，才删除这些 files。

## 要求

- Python 3.11 或更高版本
- Codex plugin support
- 可选：使用 `codex` extractor 时需要 `codex` CLI
- 可选：使用 `agy` extractor 时需要 `agy` CLI
- 可选：使用 remote embeddings 时需要 Cloudflare Workers AI credentials

在 macOS 和 Linux 上，installed hooks 使用 `sh` 和 bundled launcher，会先尝试 `python3`，再尝试 `python`。手动 CLI launcher 也使用相同的 fallback 顺序。

在 Windows 上，installed hooks 会对 `SessionStart` 和 `UserPromptSubmit` 使用 `py -3`，手动 CLI launcher 会按 `py -3`、`python`、`python3` 的顺序尝试。`Stop` hook 使用 PowerShell 将 extraction 加入 queue，不会阻塞当前 turn。

## 卸载

移除 plugin 和 marketplace source。

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```
