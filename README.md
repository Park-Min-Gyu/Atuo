# Atuo · Duo

Gemini와 Claude를 **하나의 AI처럼** 묶어 쓰는 하이브리드 도우미입니다.
쉬운 일은 저렴한 Gemini가, 어려운 일은 Claude가 맡아 Claude 토큰 사용량을 줄입니다.

## 토큰을 아끼는 방법

| 기능 | 동작 |
|---|---|
| 난이도 라우팅 | 질문을 규칙으로 채점해(토큰 0) 쉬우면 Gemini, 어려우면 Claude로 보냅니다. |
| 승격 | Gemini가 자신 없으면 `[[ESCALATE]]`만 답하고, 그때만 Claude가 답합니다. |
| 대화 압축 | 대화가 길어지면 오래된 부분을 Gemini가 요약해 이후 입력 토큰을 줄입니다. |
| 응답 캐시 | 같은 대화·같은 질문은 API를 부르지 않고 저장된 답을 돌려줍니다. |
| 프롬프트 캐싱 | Claude 호출에 프롬프트 캐싱을 켜서 반복되는 앞부분을 싸게 읽습니다. |
| 상호 대체 | 한쪽이 한도 초과·장애·인증 실패면 다른 쪽이 대신 답합니다. |

## 설치

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

`.env`에 `ANTHROPIC_API_KEY`와 `GEMINI_API_KEY`를 넣습니다. 키가 하나만 있어도 그 모델만으로 동작합니다.

## 사용

```bash
python -m duo "이 문장 영어로 번역해줘: 반갑습니다"
```

```bash
python -m duo
```

두 번째 명령은 대화 모드입니다.

| 명령 | 뜻 |
|---|---|
| `/c 질문` | 이번 질문만 Claude로 강제 |
| `/g 질문` | 이번 질문만 Gemini로 강제 |
| `/stats` | 모델별 토큰 사용량과 절감 추정 |
| `/reset` | 대화 기록 초기화 |
| `/exit` | 종료 |

옵션: `-q`(라우팅 정보 숨김), `--no-cache`, `--stats`.

### 파이썬에서

```python
from duo import Duo

duo = Duo()
answer = duo.ask("서비스 아키텍처를 설계해줘")
print(answer.provider, answer.text)
print(duo.report())
```

## 설정

`.env.example`에 전체 목록이 있습니다. 자주 쓰는 것:

- `DUO_HARD_SCORE` (기본 3): 낮추면 Claude를 더 자주, 높이면 Gemini를 더 자주 씁니다.
- `DUO_CLAUDE_MODEL` / `DUO_GEMINI_MODEL`: 모델 변경 (기본 `claude-opus-5-5`, `gemini-3.8-flash`).
- `DUO_CLAUDE_EFFORT=low`: Claude의 사고량을 줄여 출력 토큰을 더 아낍니다.
- `DUO_GEMINI_INPUT_PRICE` / `DUO_GEMINI_OUTPUT_PRICE`: 넣으면 `/stats`가 실제 절감액을 계산합니다.

## 테스트

```bash
pip install pytest
```

```bash
python -m pytest -q
```

테스트는 가짜 모델로 돌아가므로 API 키가 필요 없습니다.

## 구조

```
duo/config.py     환경 변수·.env 설정
duo/router.py     규칙 기반 난이도 채점
duo/providers.py  Claude / Gemini 호출 래퍼
duo/cache.py      SQLite 응답 캐시
duo/engine.py     라우팅·승격·압축·대체·통계
duo/cli.py        명령줄
```

설계는 오픈소스 [RouteLLM](https://github.com/lm-sys/RouteLLM)(쉬운 질문을 싼 모델로 보내는 라우팅)과
[LiteLLM](https://github.com/BerriAI/litellm)(공급자 통합·대체)의 방식을 참고했고, 코드는 두 회사의 공식 SDK만 사용합니다.
