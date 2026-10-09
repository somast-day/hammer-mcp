# Hammer MCP

Valve Hammer의 **저장된 맵 파일**을 읽고 편집하는 로컬 MCP 서버입니다. CS2 / Source 2를 중심으로 만들었으며 CS:S / Source 1도 지원합니다. Codex에서 자연어로 작업을 요청하면 맵 파일과 설치된 Valve 도구를 조작합니다.

**실시간 해머 UI, 아직 저장하지 않은 변경 내용, 임의의 Source 2 메시 모델링은 이 버전의 지원 범위에 포함되지 않습니다.**

## 구현한 기능

| 기능 | Source 2 | Source 1 |
|---|---|---|
| 게임·컴파일러 탐색 | Steam 설치 경로 및 라이브러리 자동 탐색 | 동일 |
| 맵 읽기 | `.vmap`, 텍스트 및 바이너리 DMX | `.vmf` |
| 엔티티 조회 | ID, 클래스, 속성, 위치, 각도, 스케일, I/O | ID, 클래스, 속성, 위치, 각도, I/O |
| 엔티티 수정 | 속성 및 변환, 새로운 포인트 엔티티 | 속성 및 포인트 엔티티 |
| I/O 편집 | 기존 연결을 보존하며 새 연결 추가 | 동일 |
| 맵 생성 | 설치된 템플릿 또는 지정한 원본 복사 | 템플릿 복사 또는 밀폐된 테스트 방 생성 |
| 컴파일 | `resourcecompiler.exe`, CS2 VPK 출력 | `vbsp → vvis → vrad`, BSP 출력 |
| 검사 | 구조·ID·클래스명 존재·연결 대상 | 동일 |
| 복구 | 수정 전 SHA-256 확인, 자동 백업 및 복원 | 동일 |

CS2 바이너리는 해당 게임의 `dmxconvert.exe`로 KeyValues2로 변환하고, 필요한 텍스트 구간만 수정한 뒤 다시 바이너리로 변환합니다. 알 수 없는 DMX 속성 타입과 메시 데이터도 텍스트 구간으로 유지합니다. Valve 변환기가 맵 형식 버전을 올리거나 데이터를 정규화할 수 있으므로 모든 바이너리가 바이트 단위로 유지되는 것은 아닙니다. **백업 복원은 원본 바이트를 그대로 복원합니다.**

## 설치

Python 3.12 이상과 해당 게임의 Workshop Tools / SDK가 필요합니다. 이 PC에는 외부 가상 환경이 준비되어 있습니다. 다른 PC에서 소스를 사용할 때는 다음 명령으로 런타임과 로컬 실행 설정을 만듭니다.

```powershell
python scripts/setup.py
```

의존성은 기본적으로 `~/.local/share/hammer-mcp/venv`에 설치됩니다. 생성한 `.mcp.json`에는 이 PC의 **절대 경로**가 들어갑니다. 소스 폴더를 옮기거나 공유받은 경우 `setup.py`를 다시 실행해야 합니다. 실행 시 패키지를 자동으로 다운로드하지 않습니다.

Codex 개인 마켓플레이스에 등록된 이 복사본은 다음 명령으로 설치할 수 있습니다.

```powershell
codex plugin add hammer-mcp@personal
```

설치 후 **새 Codex 작업**에서 아래처럼 요청합니다.

> 해머 MCP로 설치된 게임과 작업 가능한 맵을 확인해줘.

기존 프로젝트는 폴더 경로를 알려주면 `hammer_add_project`로 등록합니다. CS2 애드온을 새로 만드는 경우 `hammer_create_addon`이 폴더 생성과 등록을 함께 처리합니다.

## 요청 예시

- “CS2용 `my_test_map` 애드온을 만들고 기본 폭파 맵 템플릿을 복사해줘.”
- “이 맵의 `logic_relay`를 전부 보여줘.”
- “이 엔티티 이름을 `main_gate_controller`로 바꿔줘.”
- “이 릴레이가 작동하면 `main_gate`의 `Open` 입력이 실행되도록 연결해줘.”
- “이 맵을 빠르게 컴파일하고 종료 코드와 결과 파일을 확인해줘.”
- “방금 수정한 맵을 백업으로 복원해줘.”

수정은 고유 ID 또는 **유일한 targetname**으로 대상을 지정합니다. 같은 이름을 가진 엔티티가 여럿이면 고유 ID가 필요합니다. 파일을 읽을 때 반환되는 `sha256`을 쓰기 호출의 `expected_hash`로 전달하므로, 그 사이 다른 프로그램이 저장한 변경을 감지할 수 있습니다.

Source 1의 `hidden` 블록에 저장된 엔티티도 조회·수정 대상에 포함하며, 응답의 `hidden` 값으로 숨김 상태를 표시합니다. 수정해도 이 상태는 유지합니다. ID 검사는 VMF의 엔티티·브러시·면·그룹을 종류별로 구분하고, VMAP의 DMX 요소 ID는 맵 전체에서 검사합니다.

**해머에서 열어 둔 파일을 MCP로 수정했다면, 해머에서 다시 불러온 뒤 작업을 이어가야 합니다.** 해머의 저장되지 않은 내용을 먼저 저장하십시오. 해시 확인은 외부 프로그램과 공동 트랜잭션을 제공하지 않으므로 마지막 확인과 저장 사이의 극히 짧은 경합까지 제거하지는 못합니다.

## 도구 18개

| 도구 | 역할 |
|---|---|
| `hammer_health` | 설치 게임, 도구 경로, 프로젝트, 한계 확인 |
| `hammer_add_project` | 기존 프로젝트 디렉터리 등록 |
| `hammer_list_maps` | 프로젝트와 게임 콘텐츠에서 맵 찾기 |
| `hammer_map_info` | 맵 해시 및 엔티티·지오메트리 개수 |
| `hammer_entities` | 엔티티와 연결 조회, 필터 및 페이지 나누기 |
| `hammer_set_entity` | 속성과 변환 수정, 선택적 scalar 제거(`remove_properties`) |
| `hammer_add_entity` | 포인트 엔티티 생성 |
| `hammer_add_connection` | I/O 연결 추가 |
| `hammer_set_connection` | 연결 1행 제자리 수정 |
| `hammer_replace_property` | 속성값 일괄 교체 |
| `hammer_validate` | 구조와 참조 검사 |
| `hammer_create_addon` | 새 Source 2 애드온 생성 |
| `hammer_create_map` | 템플릿 복사 또는 Source 1 테스트 방 생성 |
| `hammer_compile_plan` | 실제 실행할 명령과 출력 위치 조회 |
| `hammer_compile` | 비동기 컴파일 시작 |
| `hammer_compile_status` | 작업 상태·로그·산출물 확인 |
| `hammer_compile_cancel` | 서버가 실행한 컴파일 프로세스 취소 |
| `hammer_analyze_log` | 입력한 컴파일 로그 진단 |
| `hammer_console_send` | 루프백 VConsole 명령 전송 (기본 dry_run) |
| `hammer_replace_material` | 메시 재질 경로 교체 |

쓰기 도구의 `dry_run=true`는 미리보기만 반환합니다. 저장 및 컴파일은 셸 문자열을 만들지 않고 인수 배열로 네이티브 프로세스를 실행합니다. 별도 네트워크 서버를 열지 않으며 stdio로만 통신합니다.

## 컴파일

- **CS2 fast:** 월드·가시성·물리 빌드, 조명 계산 제외. 해당 애드온의 `game` 폴더에 맵 VPK를 생성합니다. 기존 컴파일 결과가 있으면 갱신될 수 있습니다.
- **CS2 full:** 조명 베이크를 요청합니다. GPU·게임 버전에 따라 요구 조건과 시간이 달라집니다. 이 PC의 최종 검증은 fast 모드이며, full 베이크와 게임 플레이는 아직 검증하지 않았습니다.
- **CS2 check:** 의존성 최신 여부만 검사합니다. 전체 컴파일 성공을 의미하지 않습니다.
- **Source 1 fast/full:** 원본의 복사본을 작업별 디렉터리에서 컴파일합니다. fast는 VVIS의 빠른 모드를 사용합니다. 결과는 게임의 `maps` 폴더로 자동 복사하지 않습니다.

Source 1 맵에서 상대 경로 `func_instance` 등을 사용하면 격리된 빌드 폴더에서 경로를 찾지 못할 수 있습니다. 해당 참조를 검토한 뒤 사용하십시오. Source 1 브러시 엔티티의 origin 변경은 브러시 메시 전체를 이동하는 기능이 아닙니다.

컴파일 성공 여부는 종료 코드와 실제 산출물 갱신을 함께 확인합니다. Source 2는 최종 `OK: ... compiled, ... failed` 집계도 확인합니다. 엔진이 비치명적인 `Error:` 메시지를 출력하고 빌드는 성공할 수 있으므로 **완료 상태와 진단 목록을 함께 확인**해야 합니다. 진단을 숨기거나 “오류 없음”으로 바꾸지 않습니다.

컴파일은 작업 ID를 즉시 반환합니다. 상태를 조회하여 완료·실패·취소·시간초과를 확인합니다. 서버 하나당 컴파일 하나를 실행합니다. 서버 연결을 끊으면 실행 중인 컴파일도 종료되므로 완료까지 연결을 유지하십시오.

다른 Codex 작업에서도 같은 작업 ID로 실행 상태를 조회할 수 있습니다. `owned_by_this_server`와 `can_cancel`은 현재 연결에서 취소할 수 있는지 나타냅니다. 취소는 컴파일을 시작한 작업에서 요청하십시오. 실행한 서버의 종료를 확인했는데 최종 기록이 없다면 `interrupted`로 표시합니다. 이전 버전에서 생성되어 서버 식별 정보가 없는 미완료 기록은 `unknown`으로 표시합니다.

맵 잠금은 운영체제가 관리하므로 프로세스가 강제 종료돼도 해제됩니다. `locks`와 `servers` 폴더의 `.lock` 파일은 계속 존재할 수 있으며, 파일이 있다는 사실만으로 잠긴 상태는 아닙니다. 이전 버전의 잠금은 소유 프로세스의 종료를 확인한 뒤 다음 작업에서 복구합니다. 프로젝트 등록도 프로세스 간 잠금과 설정 병합을 사용하며, 다른 작업에서 등록한 폴더를 다시 연결하지 않고 사용할 수 있습니다.

## 데이터와 제한

로컬 상태 기본 경로: `~/.local/share/hammer-mcp`

- `config.json`: 프로젝트 디렉터리와 파일 크기 제한
- `backups/`: 원본 바이트와 경로·체크섬 메타데이터
- `builds/<job_id>/`: 실행 명령, 로그, 상태 및 Source 1 결과
- `locks/`, `servers/`: 맵 작업과 실행 서버의 운영체제 잠금
- `verification/`: 실제 통신·컴파일 검증 보고서
- `venv/`: 서버 전용 Python 환경

기본 원본 파일 제한은 **64 MiB**, 변환된 텍스트 제한은 **512 MiB**입니다. 아주 큰 VMAP의 변환·파싱은 이보다 훨씬 많은 RAM을 사용할 수 있습니다. 큰 맵은 충분한 메모리를 확인한 뒤 `max_map_mb`와 `max_text_mb`를 설정하고 서버를 다시 연결하십시오. 이 PC에서 발견한 약 379 MB 사용자 맵은 수정하거나 대형 맵 검증에 사용하지 않았습니다.

```json
{
  "project_roots": ["D:/Mapping/MyAddon"],
  "max_map_mb": 64,
  "max_text_mb": 512
}
```

FGD 검색은 설치 파일의 원문과 줄 번호를 반환합니다. 상속을 해석하는 FGD 타입 검사기는 포함되어 있지 않습니다. 구조 검사는 leak 검사, 메시 유효성 검사, 리소스 완전성 검사, FPS 측정 또는 플레이테스트를 대체하지 않습니다. 현재는 엔티티 삭제, 연결 삭제·수정, 일반 메시 편집을 제공하지 않습니다.

## 개발 및 검증

```powershell
python scripts/setup.py --dev
& "$env:USERPROFILE/.local/share/hammer-mcp/venv/Scripts/python.exe" -m pytest tests -q
& "$env:USERPROFILE/.local/share/hammer-mcp/venv/Scripts/python.exe" scripts/smoke_test.py
```

`smoke_test.py --live`는 설치된 CS2와 CS:S용 **새 테스트 맵과 CS2 애드온**을 만들고 실제 컴파일러를 실행합니다. 기존 사용자 맵을 수정하지 않습니다. 새로 만든 테스트 애드온과 결과 파일은 검토할 수 있도록 남겨 둡니다.

프로토콜을 통해 도구를 직접 호출할 때는 다음과 같이 사용합니다.

```powershell
& "$env:USERPROFILE/.local/share/hammer-mcp/venv/Scripts/python.exe" scripts/call_tool.py hammer_health
```

JSON 인수는 `--arguments-file args.json`으로 전달합니다. 컴파일 작업처럼 서버를 계속 유지해야 하는 작업은 Codex 또는 지속적인 MCP 클라이언트에서 실행하십시오.

검증 범위와 이 PC의 환경 수정 기록은 [VERIFICATION.md](VERIFICATION.md)에 있습니다.

## 참고

- [공식 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [srctools 문서](https://srctools.readthedocs.io/)
- Valve 설치 도구의 `resourcecompiler.exe -help`, `dmxconvert.exe -help`

게임 실행 파일, 게임 맵, 모델과 재질 등 Valve 콘텐츠는 이 플러그인 소스에 포함하지 않습니다. 템플릿은 사용자의 설치 경로에서 읽습니다.
