# 검증 기록 — 2026-09-05

이 PC에서 수행한 검증입니다. 게임 버전과 설치 구성에 따라 다른 PC의 결과는 달라질 수 있습니다.

## 검토 후 안정화 검증

코드 검토에서 재현한 6개 문제를 수정했다. 아래 검증은 초기 개발 기록 이후의 수정본에 대한 결과다.

- pytest **34개 통과**. 기존 15개와 새 회귀 검증 19개를 포함한다.
- 실제 stdio MCP 서버 두 개로 정상 연결 종료와 강제 프로세스 트리 종료를 검사했다. 다른 클라이언트가 실행 중인 빌드를 `running`으로 조회하고, 종료 후 각각 `cancelled`와 `interrupted`를 확인했다. 같은 맵의 후속 편집도 성공했다.
- OS 잠금의 프로세스 간·스레드 간 배타성, 소유 프로세스 종료 후 해제, 이전 버전 잠금의 생존 소유자 보호와 종료 후 복구를 확인했다.
- 별도 프로세스 4개가 동시에 프로젝트를 등록해도 모든 등록과 기존 설정이 보존된다. 이미 연결된 서버도 새 등록을 읽는다.
- 숨겨진 VMF 엔티티를 조회·수정하고 I/O를 추가한 뒤, 독립 srctools 파서로 숨김 상태와 연결 보존을 확인했다.
- 자체 생성한 Source 1 방의 ID 검사 오류는 0개다. 같은 종류 안의 실제 중복과 VMAP의 전역 DMX ID 중복은 계속 검출한다.
- 실제 CS:S 컴파일에서 `fatal.vmf`가 `completed`로 판정된다. vbsp/vvis/vrad 종료 코드는 모두 0이며, 파일명 때문에 생성되던 오류 진단도 0개다. 실제 FATAL 및 leak 메시지는 종료 코드가 0이어도 실패로 처리하는 회귀 테스트를 통과했다.
- 수정본의 실제 CS2 fast 빌드: **9.92초**, `completed`, 집계 `16 compiled, 0 failed, 0 skipped`, VPK 갱신 확인.
- 수정본의 실제 CS:S 방 빌드: **0.59초**, `completed`, BSP 갱신 확인.
- 수정본의 실제 stdio 검증에서 도구 18개, CS2 바이너리 엔티티/연결 편집, 모든 `CDmePolygonMesh` 블록 보존, 원본 바이트와 일치하는 백업 복원을 확인했다.

재현 및 빌드 보고서:

- `C:\Users\c\.local\share\hammer-mcp\verification\smoke_20260905_133026.json`
- `C:\Users\c\.local\share\hammer-mcp\review\probes-jrnemzph\report.json`
- `C:\Users\c\.local\share\hammer-mcp\review\shutdown-8oz5gl0t\report.json`

정상 종료 후 `.lock` 파일이 남는 것은 의도한 동작이다. 현재 잠금은 파일 존재 여부가 아닌 운영체제의 잠금 상태로 판단하며, 대기자가 다른 파일을 잠그는 경합을 피하기 위해 잠금 파일을 삭제하지 않는다.

이번 수정에서도 기존 사용자 맵과 게임 설정 파일은 편집하지 않았다. Source 2 full 조명 베이크와 게임 플레이는 검증하지 않았다. 아래 내용은 초기 개발 시점의 기록이다.

## 초기 버전에서 통과한 검증

- pytest **15개 통과**: VMF 구간 수정, DMX 참조·알 수 없는 속성·메시 구간 유지, 엔티티/연결 생성, SHA-256 충돌, 미리보기, 경로 경계, 백업 대상 확인·복원, 구문 오류, 중복 선택, 진단, 테스트 방, 컴파일 실패/산출물/취소 처리.
- 공식 MCP Python SDK 클라이언트가 **별도 서버 프로세스의 stdio**에 연결하여 초기화, 도구 목록 18개, 도구 호출을 검증했습니다.
- CS2의 설치된 `template_defuse.vmap`을 복사하여 바이너리 DMX 9 읽기·쓰기 검증을 수행했습니다. 원본은 **엔티티 54개, 메시 15개**입니다.
- 포인트 엔티티 추가, 속성/위치 수정, I/O 연결 추가 후 다시 읽어 값을 확인했습니다.
- 원본과 수정 후의 **모든 `CDmePolygonMesh` 블록**을 비교하여 같은 체크섬임을 확인했습니다.
- 백업 복원 결과가 수정 전 바이너리와 SHA-256까지 동일했습니다.
- CS2 네이티브 fast 컴파일에서 맵 VPK가 실제로 생성·갱신되었습니다. 최종 원래 게임 환경에서의 재검증은 약 **12.08초**, 종료 코드 **0**, 컴파일러 집계의 실패 개수 **0**입니다.
- Source 1의 생성된 밀폐 테스트 방이 실제 CS:S `vbsp.exe`, `vvis.exe`, `vrad.exe`를 모두 통과하여 BSP를 생성했습니다. 마지막 stdio 검증은 약 **0.83초**입니다.
- Codex 플러그인 스키마 검증을 통과했습니다.

## 보고서와 결과 위치

이 설치의 사용자 홈은 `C:\Users\c`입니다.

- 최종 stdio 테스트: `C:\Users\c\.local\share\hammer-mcp\verification\smoke_20260905_123813.json`
- 원래 게임 파일로 재검증: `C:\Users\c\.local\share\hammer-mcp\verification\final_environment_check.json`
- 최종 CS2 로그: `C:\Users\c\.local\share\hammer-mcp\builds\76626b0ec4904335b6dbb273f08671d8\compile.log`
- CS2 테스트 맵: `C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\content\csgo_addons\hammer_mcp_smoke_20260905_123813\maps\mcp_test.vmap`
- CS2 결과 VPK: 같은 설치의 `game\csgo_addons\hammer_mcp_smoke_20260905_123813\maps\mcp_test.vpk`
- Source 1 결과 경로와 모든 실행 인수는 최종 stdio 보고서의 `builds`에 기록되어 있습니다.

`completed`는 컴파일러 종료와 산출물 검증을 뜻합니다. 현재 CS2 설치는 사운드 연산자의 `apply` 속성 관련 비치명적 진단을 출력하지만 최종 컴파일 집계는 실패 0입니다. 서버는 이 진단을 그대로 반환합니다. Source 1도 `GameData` 키 관련 경고가 기록됩니다.

## 컴파일 옵션 조사와 게임 파일 복원

개발 중 `-novpk`를 사용한 CS2 컴파일이 오래된 loose 번역 파일을 읽다가 맵을 열기 전에 종료되었습니다. 현재 VPK와 loose 파일의 내용을 비교한 뒤, 원인 분리를 위해 `game/core/resource/valve_english.txt`를 백업하고 최신 패키지 내용으로 잠시 교체했습니다.

그 뒤 `-novpk`를 제거한 **최종 명령으로 원래 번역 파일을 복원한 상태에서도 컴파일 성공**을 확인했습니다. 따라서 **기존 게임 번역 파일은 원본 바이트로 복원되어 있으며 영구적인 게임 파일 수정은 남아 있지 않습니다.** 최종 서버는 `-novpk`를 사용하지 않습니다.

- 복원된 원본 SHA-256: `dc1f3e4f0ffbfaf10af173296f008bea857626776bd0cda56efcd490c13f8397`
- 조사 백업: `C:\Users\c\.local\share\hammer-mcp\environment-backups\valve_english_20260905_123517.txt`
- 복원 기록: 같은 경로의 `.json`, `retained=false`, `restored_after_removing_novpk=true`

새로 생성한 테스트 애드온·맵·컴파일 결과·백업은 검토용으로 남겨 두었습니다. 기존 사용자 맵은 변경하지 않았습니다.

## 아직 검증하지 않은 범위

- 해머 GUI에서 직접 파일을 열어 시각적으로 확인하는 작업 및 게임 플레이
- Source 2 full 조명 베이크, GPU별 차이
- 대형 사용자 맵, 바이너리 DMX의 모든 역사적 버전
- CS2와 CS:S 이외의 게임에서 실제 컴파일
- 메시 편집, 지형 조형, 엔티티/연결 삭제, FGD 상속 타입 검사

Steam 탐색기에 다른 게임 프로필도 들어 있지만, 실제 동작 검증 범위는 위의 두 게임입니다.
