# project-memdir

Codex 세션 사이에서 프로젝트별 지식을 재사용하기 위한 프로젝트 메모리입니다.

`project-memdir`는 Codex hook을 설치해 세션 시작 시점과 프롬프트 제출 시점에 관련 프로젝트 메모리를 불러옵니다. extractor를 설정하면 각 턴이 끝난 뒤 Stop hook이 메모리 추출 작업을 백그라운드 queue에 넣습니다.

다시 사용할 가능성이 높은 프로젝트별 정보만 저장합니다. 일회성 질문이나 재사용성이 낮은 세부 정보는 저장하지 않습니다. 저장된 메모리는 보장된 사실이 아니라 참고 context로 주입됩니다.

번역: [English](README.md) | [Japanese](README.ja.md) | [Simplified Chinese](README.zh-CN.md)

## 설치

Git marketplace source를 추가한 다음, 그 source에서 plugin을 설치합니다.

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

설치 중 또는 업데이트 후 Codex가 hook review를 요구하면 이 plugin의 hook을 승인하세요. 승인 후 새 Codex 세션부터 메모리 recall이 동작합니다.

GitHub에 push한 뒤 Claude Code에서 설치하려면 Claude Code 안에서 다음 slash command를 실행합니다.

```text
/plugin marketplace add oh1701/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

push 전 로컬 checkout으로 테스트하려면 local path를 추가합니다.

```text
/plugin marketplace add /Users/ogyuseong/Desktop/project-memdir
/plugin install project-memdir@project-memdir-local
/reload-plugins
```

Claude Code가 plugin hook 신뢰 또는 승인을 요청하면 이 plugin의 hook을 승인하세요. 그래야 SessionStart, UserPromptSubmit, Stop hook이 실행됩니다. Claude Code plugin도 아래에서 설명하는 동일한 `~/.project-memdir/harness.toml` 설정을 사용합니다.

## 설정

plugin은 기본 템플릿을 `harness.toml.example`로 제공합니다.
사용자가 수정하는 설정 파일은 버전별 plugin cache 밖에 저장됩니다.

```text
~/.project-memdir/harness.toml
```

> **중요:** 자동 메모리 추출을 사용하려면 `~/.project-memdir/harness.toml`에 `[memdir.extractor].provider`를 반드시 설정해야 합니다.
> 이 provider를 설정하지 않으면 메모리 recall은 동작하지만 Stop hook은 턴 종료 추출 준비를 실패로 처리합니다.

이 파일이 없으면 다음 `SessionStart` hook이 `harness.toml.example`에서 자동 생성합니다.
설치 직후 바로 만들고 싶으면 이 릴리스가 설치된 plugin cache path에서 OS별 명령을 실행합니다.

```sh
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.10
sh hooks/automation/memdir_cli.sh init-config
```

Windows에서는 PowerShell 기준으로 실행합니다.

```powershell
cd ~/.codex/plugins/cache/project-memdir-local/project-memdir/1.0.10
.\hooks\automation\memdir_cli.cmd init-config
```

메모리 recall은 이미 저장된 프로젝트 메모리를 기준으로 동작합니다.
각 턴 이후 자동 메모리 추출은 Stop hook이 작업을 queue에 넣기 전에 지원되는 extractor 설정을 요구합니다.
메모리 추출은 완료된 턴을 topic JSON으로 정리하는 가벼운 작업이므로 보통 저비용 모델로 충분합니다.
선택한 모델이 느리면 추출이 지연될 수 있습니다.

```toml
[memdir.extractor]
provider = "codex"
```

`codex` extractor에서 특정 Codex 모델을 선택하려면 `codex_model`을 설정합니다.

```toml
[memdir.extractor]
provider = "codex"
codex_model = "codex-default-model"
```

`agy`도 사용할 수 있습니다.

```toml
[memdir.extractor]
provider = "agy"
agy_bin = "agy"
agy_model = "agy-default-model"
```

파일을 직접 작성하는 local command도 사용할 수 있습니다.

```toml
[memdir.extractor]
provider = "local_cli"
local_cli_command = 'python "${CODEX_ROOT}/examples/local_extractor.py"'
```

`local_cli_command`에는 메모리 topic JSON 파일을 직접 생성할 수 있는 에이전트 CLI 실행 명령을 입력합니다.
이 설정에서 `${CODEX_ROOT}`는 설치된 plugin 디렉터리로 확장됩니다.

extractor provider가 없거나 지원되지 않으면 Stop hook이 즉시 실패합니다. 설정된 extractor가 queue 처리 중 실패하면 이후 prompt context에서 `project-memdir memory extraction failed` 에러 문구가 표시될 수 있습니다.

## Embeddings

Cloudflare credentials가 없으면 memdir은 내장 `local_hash` embedding fallback을 사용합니다.

Cloudflare Workers AI embeddings를 사용하려면 환경 변수로 credentials를 설정하세요. secret이 `harness.toml`에 남지 않으므로 이 방식을 권장합니다.

```sh
export CLOUDFLARE_ACCOUNT_ID="..."
export CLOUDFLARE_API_TOKEN="..."
```

사용자 `harness.toml`이 내 PC에서만 쓰이는 private 파일이라면 직접 넣을 수도 있습니다.

```toml
[memdir.embedding]
CLOUDFLARE_ACCOUNT_ID = "..."
CLOUDFLARE_API_TOKEN = "..."
```

secret이 아닌 embedding 기본값은 사용자 `harness.toml`에 둘 수 있습니다.

```toml
[memdir.embedding]
model = "@cf/google/embeddinggemma-300m"
dimensions = 768
timeout_sec = 15
```

## 사용

설치와 hook 승인을 마친 뒤 프로젝트에서 Codex를 열면 됩니다. plugin은 현재 프로젝트를 감지하고 해당 프로젝트의 메모리 디렉터리를 불러와 관련 있는 메모리만 prompt context에 주입합니다.

extractor provider를 설정하면 완료된 턴은 Stop hook을 통해 queue에 들어가고 백그라운드에서 처리됩니다. 추출 작업은 현재 턴을 막지 않습니다.

## 저장 위치

프로젝트 루트 판정 방식은 사용자 `harness.toml`의 `[memdir.project_root]`로 선택합니다.

```toml
[memdir.project_root]
# cwd: Codex hook 또는 CLI 세션이 시작된 정확한 디렉터리를 사용합니다.
#      POSIX와 Windows의 기본값입니다.
# detect: 해당 디렉터리에서 위로 올라가며 프로젝트 마커나 Git을 사용합니다.
strategy = "cwd"
```

프로젝트 메모리 저장 위치는 사용자 `harness.toml`의 `[memdir.storage]`로 선택합니다.

```toml
[memdir.storage]
# plugin: 안정적인 사용자 데이터 디렉터리 아래에 저장합니다:
#   ${HOME}/.project-memdir/memories/projects/<project-slug>
# project: 현재 프로젝트 내부에 저장합니다:
#   <project-root>/.project-memdir
mode = "plugin"
project_dir_name = ".project-memdir"
```

각 프로젝트 메모리는 다음 파일로 구성됩니다.

- `manifest.json`
- `topics/*.json`
- `vector_index.sqlite3`

저장된 메모리까지 제거하려는 경우에만 이 파일들을 삭제하세요.

## 요구사항

- Python 3.11 이상
- Codex plugin 지원
- 선택: `codex` extractor 사용 시 `codex` CLI
- 선택: `agy` extractor 사용 시 `agy` CLI
- 선택: 원격 embeddings 사용 시 Cloudflare Workers AI credentials

macOS와 Linux에서 설치된 hook은 `sh`와 번들 launcher를 사용하며, `python3`를 먼저 시도한 뒤 `python`을 시도합니다. 수동 CLI launcher도 같은 순서로 fallback합니다.

Windows에서 설치된 hook은 `SessionStart`와 `UserPromptSubmit`에 `py -3`를 사용하며, 수동 CLI launcher는 `py -3`, `python`, `python3` 순서로 시도합니다. `Stop` hook은 PowerShell로 extraction을 queue에 넣고 현재 턴을 막지 않습니다.

## 제거

plugin과 marketplace source를 제거합니다.

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```

사용자 설정과 저장된 메모리까지 제거하려면 stable user data directory를 삭제합니다.

macOS와 Linux:

```sh
rm -rf ~/.project-memdir
```

Windows PowerShell:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.project-memdir" -ErrorAction SilentlyContinue
```
