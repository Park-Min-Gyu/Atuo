# Atuo — 내 컴퓨터 자동 정리 도구

오래 쓴 컴퓨터에 쌓인 임시파일, 캐시, 안 쓰는 코드 프로젝트의 빌드 폴더, 큰 파일, 중복 파일, 안 쓰는 앱을 찾아서 정리합니다.
Windows · macOS · Linux 에서 동작하고, 파이썬(3.8 이상)만 있으면 됩니다. 따로 설치할 것이 없습니다.

## 실행

```bash
python atuo.py
```

메뉴가 나옵니다. 처음에는 **1번(검사만 하기)** 으로 무엇이 얼마나 쌓였는지 먼저 보세요. 아무것도 지우지 않습니다.

| 명령 | 하는 일 |
|---|---|
| `python atuo.py scan` | 지울 수 있는 것을 보여주기만 함 |
| `python atuo.py clean` | 항목마다 물어보면서 정리 |
| `python atuo.py apps` | 오래 안 쓴 앱을 크기순으로 보여주고 하나씩 물어봄 |
| `python atuo.py memory` | 메모리(RAM)를 많이 쓰는 프로그램, 시작 프로그램 보기 |
| `python atuo.py restore` | 보관함으로 옮긴 파일 되살리기 |
| `python atuo.py empty` | 보관함 비우기 (이때 실제로 용량 확보) |

## 무엇을 어떻게 정리하나

| 대상 | 처리 방식 |
|---|---|
| 임시파일, 브라우저·앱 캐시, 휴지통, pip/npm/Gradle 캐시 | 한 번 확인 후 바로 삭제 (다시 만들어지는 것들) |
| 30일 넘게 손대지 않은 프로젝트의 `node_modules`, `build`, `target`, `.venv`, `__pycache__` 등 | 한 번 확인 후 바로 삭제. **소스 코드는 건드리지 않음** |
| 큰 파일 (기본 500MB 이상) | **파일마다 물어보고** 보관함으로 이동 |
| 90일 넘게 안 연 다운로드 파일 | 물어보고 보관함으로 이동 |
| 내용이 똑같은 중복 파일 | 묶음마다 물어보고, 원본 하나만 남기고 보관함으로 이동 |
| 180일 넘게 안 쓴 앱 | 앱마다 물어봄. Windows 는 그 앱의 공식 제거 프로그램 실행, macOS 는 보관함으로 이동, Linux 는 제거 명령 안내 |

## 실수로 지웠을 때

사용자의 파일은 삭제하지 않고 **보관함(`~/.atuo/trash`)** 으로 옮깁니다. 30일 안에는 `python atuo.py restore` 로 원래 자리에 되살릴 수 있고, 30일이 지나면 자동으로 삭제됩니다.
보관함에 있는 동안은 디스크 용량이 줄지 않습니다. 바로 확보하려면 `python atuo.py empty`.

모든 작업은 `~/.atuo/history.log` 에 기록됩니다.

## 절대 건드리지 않는 것

- Windows, Program Files, /usr, /System 같은 시스템 폴더
- 홈 폴더 자체와 바탕화면·문서·다운로드 폴더 자체 (안의 파일만 대상)
- `.ssh`, `.gnupg`, `.aws`, `.git` 안의 파일
- 최근에 작업한 프로젝트, 최근에 쓴 앱
- Microsoft·Intel·NVIDIA 등 드라이버/런타임 (앱 목록에서 제외)

## 옵션

```bash
python atuo.py scan --min-size 200        # 200MB 이상을 '큰 파일'로
python atuo.py scan --path D:\            # 홈 폴더 대신 다른 드라이브/폴더 검사
python atuo.py clean --days 60            # 60일 넘게 안 건드린 프로젝트만
python atuo.py clean --no-dupes           # 중복 검사 생략 (더 빠름)
python atuo.py clean --yes                # 임시파일·캐시만 묻지 않고 삭제
```

## 참고한 오픈소스

BleachBit(캐시·임시파일 정리), Czkawka(큰 파일·중복 파일 찾기), kondo·npkill(코드 프로젝트 빌드 폴더 정리), Mole(정리 + 앱 제거를 한 도구로)의 아이디어를 참고했습니다. 코드는 가져오지 않고 새로 작성했습니다.
