# project-memdir

재사용성이 낮은 정보나 일회성 질문은 저장되지 않습니다.

Codex 세션 사이에서 프로젝트별 지식을 재사용하기 위한 Codex 전용 프로젝트 메모리입니다.

`project-memdir`는 Codex hook을 설치해 세션 시작 시점과 프롬프트 제출 시점에 관련 프로젝트 메모리를 불러옵니다. extractor를 설정하면 각 턴이 끝난 뒤 Stop hook이 메모리 추출 작업을 백그라운드 queue에 넣습니다.

저장된 메모리는 참고용으로 사용되므로 약간의 불확실성을 가질 수 있습니다.

번역: [English](README.md) | [Japanese](README.ja.md) | [Simplified Chinese](README.zh-CN.md)

## 설치

marketplace source와 plugin을 설치합니다.

```sh
codex plugin marketplace add https://github.com/oh1701/project-memdir
codex plugin add project-memdir@project-memdir-local
```

설치 중 또는 업데이트 후 Codex가 hook review를 요구하면 이 plugin의 hook을 승인하세요. 승인 후 새 Codex 세션부터 메모리 recall이 동작합니다.

## 업그레이드

설정된 Git marketplace snapshot을 갱신한 뒤 plugin selector를 다시 설치합니다.

```sh
codex plugin marketplace upgrade project-memdir-local
codex plugin add project-memdir@project-memdir-local
```

업그레이드 중 또는 이후 Codex가 hook review를 요구하면 이 plugin의 hook을 승인하세요. 일반 업그레이드 절차에서 `codex plugin remove`를 사용하지 마세요. 기본 `plugin` 저장 모드에서는 프로젝트 메모리가 버전별 plugin cache가 아니라 `~/.codex/project-memdir/memories/projects` 아래에 저장됩니다.

## 설정

plugin의 기본 템플릿은 `harness.toml.example`에 둡니다. 사용자가 수정하는 설정 파일은 버전별 plugin cache 밖에 있습니다.

```text
~/.codex/project-memdir/harness.toml
```

이 파일이 없으면 다음 `SessionStart` hook이 `harness.toml.example`에서 자동 생성합니다. 설치 직후 바로 만들고 싶으면 설치된 plugin root에서 OS별 CLI launcher를 실행합니다.

```sh
sh hooks/automation/memdir_cli.sh init-config
```

```bat
hooks\automation\memdir_cli.cmd init-config
```

메모리 recall은 저장된 프로젝트 메모리를 기준으로 동작합니다. 각 턴 이후 자동 메모리 추출은 extractor를 선택하기 전까지 비활성화되어 있습니다. 메모리 추출은 완료된 턴을 topic JSON으로 정리하는 가벼운 작업이므로 모든 extractor에서 저비용 모델 사용을 권장합니다. 추출 시간은 선택한 모델 속도에 따라 지연될 수 있습니다.

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

extractor provider나 model을 잘못 설정하면 다음 prompt context에서 hook이 `project-memdir memory extraction failed` 에러 문구를 표시할 수 있습니다.

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

설치와 hook 승인을 마친 뒤 프로젝트에서 Codex를 열면 됩니다. plugin은 현재 프로젝트를 감지하고 해당 프로젝트의 memdir을 불러와 관련 있는 메모리만 prompt context에 주입합니다.

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
#   ${HOME}/.codex/project-memdir/memories/projects/<project-slug>
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

macOS와 Linux에서 설치된 hook은 `sh`와 번들 launcher를 사용하며, `python3`를 먼저 시도한 뒤 `python`을 시도합니다. 수동 CLI launcher도 같은 POSIX fallback을 사용합니다. Windows에서 설치된 hook은 `SessionStart`와 `UserPromptSubmit`에 `py -3`를 사용하며, 수동 CLI launcher는 `py -3`, `python`, `python3` 순서로 시도합니다. `Stop` hook은 PowerShell로 extraction을 queue에 넣고 현재 턴을 막지 않습니다.

## 제거

plugin과 marketplace source를 제거합니다.

```sh
codex plugin remove project-memdir@project-memdir-local
codex plugin marketplace remove project-memdir-local
```
