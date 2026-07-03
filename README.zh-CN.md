# project-memdir

复用价值较低的信息或一次性问题不会被保存。

用于在 Codex sessions 之间复用项目知识的 Codex 专用 project memory。

`project-memdir` 会安装 Codex hooks，在 session start 和 prompt submit 时加载相关 project memory。配置 extractor 后，每个 turn 结束时 Stop hook 会把 memory extraction 加入后台 queue。

已保存的 memories 会作为参考上下文使用，因此可能带有少量不确定性。

翻译: [English](README.md) | [Korean](README.ko.md) | [Japanese](README.ja.md)

## 安装

安装 marketplace source 和 plugin。

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

如果 Codex 在安装或更新后要求 hook review，请批准此 plugin 的 hooks。批准后，新的 Codex session 会开始使用 memory recall。

## 升级

先刷新已配置的 Git marketplace snapshot，然后再次安装 plugin selector。

```sh
codex plugin marketplace upgrade project-memdir-local
codex plugin add project-memdir@project-memdir-local
```

如果 Codex 在升级期间或升级后要求 hook review，请批准此 plugin 的 hooks。不要把 `codex plugin remove` 作为常规升级步骤。如果 storage mode 是 `plugin`，请在移除已安装 plugin 前备份 `~/.codex/plugins/cache/.../memories/projects`。

## 配置

安装后，编辑 Codex plugin cache 中已安装 plugin 的 `harness.toml`。

```text
~/.codex/plugins/cache/.../project-memdir/.../harness.toml
```

memory recall 会基于已保存的 project memories 工作。每个 turn 后的 automatic memory extraction 在选择 extractor 前是禁用的。memory extraction 是把完成的 turn 整理成 topic JSON 的轻量任务，因此任何 extractor 都建议使用低成本模型。extraction time 可能会因所选模型的速度而延迟。

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

`local_cli_command` 用于填写能够直接创建记忆 topic JSON 文件的 agent CLI 执行命令。

如果 extractor provider 或 model 配置错误，hook 可能会在下一次 prompt context 中显示 `project-memdir memory extraction failed` 错误提示。

## Embeddings

如果没有 Cloudflare credentials，memdir 会使用内置 `local_hash` embedding fallback。

如需使用 Cloudflare Workers AI embeddings，请通过环境变量设置 credentials。推荐这种方式，因为 secret 不会留在 `harness.toml` 中。

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

如果已安装的 `harness.toml` 只是你本机使用的 private file，也可以直接写入 credentials。

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

非 secret 的 embedding defaults 可以保留在 `harness.toml` 中。

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## 使用

完成安装和 hook approval 后，在项目中打开 Codex 即可。plugin 会检测当前 project，加载该 project 的 memdir，并只把相关 memories 注入 prompt context。

配置 extractor provider 后，完成的 turns 会通过 Stop hook 进入 queue，并在后台处理。extraction 不会阻塞当前 turn。

## 存储

project root 的解析方式由 `harness.toml` 中的 `[memdir.project_root]` 选择。

```toml
[memdir.project_root]
# cwd: 使用 Codex hook 或 CLI session 启动时所在的精确目录。
#      这是 POSIX 和 Windows 的默认值。
# detect: 从该目录向上查找，并使用 project markers 或 Git。
strategy = "cwd"
```

project memories 的存储位置由 `harness.toml` 中的 `[memdir.storage]` 选择。

```toml
[memdir.storage]
# plugin: 存储在已安装的 plugin cache 下:
#   ${CODEX_ROOT}/memories/projects/<project-slug>
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

在 macOS 和 Linux 上，installed hooks 使用 `sh` 和 bundled launcher，会先尝试 `python3`，再尝试 `python`。在 Windows 上，installed hooks 会对 `SessionStart` 和 `UserPromptSubmit` 使用 `py -3`。`Stop` hook 使用 PowerShell 将 extraction 加入 queue，不会阻塞当前 turn。

## 卸载

移除 plugin 和 marketplace source。

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```
