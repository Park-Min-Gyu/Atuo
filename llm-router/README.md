# Atuo Router

Gemini와 Claude를 **하나의 OpenAI 호환 주소** 뒤에 묶고, 요청마다 가장 싼 모델로 보내서 토큰을 아끼는 개인용 라우터입니다.
AI 모델이 아니라 모델들 앞에 두는 중계기입니다. 내 컴퓨터에서 돌고, API 키는 내 `.env` 파일 밖으로 나가지 않습니다.

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi)(MIT)의 구조 — 단일 엔드포인트, 폴백 체인, 쿨다운, 프롬프트 압축, 응답 캐시 — 를 참고해서 두 제공사 전용으로 새로 작성했습니다.

## 어떻게 토큰을 아끼나

| 방법 | 동작 |
|---|---|
| **난이도별 라우팅** | `model: "auto"` 요청을 점수로 분류합니다. 인사·번역·요약 같은 쉬운 요청은 Gemini Flash, 중간은 Gemini Pro, 코드 디버깅·설계처럼 어려운 요청만 Claude로 갑니다. |
| **응답 캐시** | 완전히 같은 요청은 저장해 둔 답을 돌려줍니다. 토큰 0. |
| **프롬프트 압축** | 불필요한 공백, 들여쓰기된 JSON, 반복된 도구 출력을 줄여서 보냅니다. |
| **대화 요약** (`aggressive`) | 대화가 길어지면 오래된 부분을 싼 Gemini 모델로 한 번 요약하고, 이후로는 요약본만 보냅니다. |
| **제공사 프롬프트 캐시** | Claude에는 캐시 지시를 자동으로 붙여, 반복되는 앞부분(시스템 프롬프트·이전 대화)을 약 1/10 가격으로 읽게 합니다. 압축 결과가 매번 같도록 만들어서 이 캐시가 깨지지 않습니다. |
| **생각 토큰 줄이기** | 쉬운 요청에는 추론 강도를 `low`로 붙입니다. 생각 토큰도 출력 요금입니다. |
| **Claude 일일 한도** | 하루 토큰 한도를 넘으면 그날은 Gemini로만 보냅니다. |
| **자동 폴백** | 한 모델이 429/5xx로 실패하면 다음 모델로 넘어갑니다. 싼 요청이 Claude로 넘어가는 일은 없습니다. |

## 시작하기

Node.js 20.12 이상이 필요합니다.

```bash
cd llm-router
```

```bash
npm install
```

```bash
cp .env.example .env
```

`.env`를 열어 `GEMINI_API_KEY`를 넣습니다([Google AI Studio](https://aistudio.google.com/apikey)에서 발급). Claude도 쓰려면 `ANTHROPIC_API_KEY`를 추가합니다.

```bash
npm start
```

다른 터미널에서 확인합니다.

```bash
npm run ask -- "안녕! 오늘 뭐 하면 좋을까?"
```

```bash
npm run ask -- --model smart "이 설계의 문제점을 분석해줘"
```

브라우저에서 <http://127.0.0.1:8787/> 을 열면 오늘 쓴 토큰, 추정 비용, 아낀 금액이 보입니다.

> **Claude 키에 대해.** Claude API 키는 claude.ai 구독(Pro/Max)과 별개입니다. [Anthropic Console](https://console.anthropic.com)에서 발급하고 쓴 만큼 과금됩니다. 키를 비워 두면 라우터는 Gemini만으로 동작하고, 어려운 요청은 Gemini Pro로 갑니다.

## 다른 프로그램에서 쓰기

OpenAI API를 쓰는 프로그램이면 주소만 바꾸면 됩니다.

- Base URL: `http://127.0.0.1:8787/v1`
- API Key: `ROUTER_API_KEY`를 설정했다면 그 값, 아니면 아무 문자열
- Model: `auto`

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "content-type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"안녕"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8787/v1", api_key="아무거나")
reply = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "이 문장 영어로 번역해줘: 좋은 아침"}],
)
print(reply.choices[0].message.content)
```

스트리밍, 도구 호출(function calling), 이미지 입력을 지원합니다.

### 모델 이름

| `model` 값 | 가는 곳 |
|---|---|
| `auto` (또는 모르는 이름, 예: `gpt-4o`) | 난이도에 따라 자동 선택 |
| `cheap` | Gemini Flash |
| `mid` | Gemini Pro |
| `smart` / `claude` | Claude (없거나 한도 초과면 Gemini Pro) |
| `gemini` | 자동 분류하되 Gemini 안에서만 |
| `gemini-…` / `claude-…` | 그 모델 그대로 |

### 요청별 옵션 (HTTP 헤더)

| 헤더 | 값 |
|---|---|
| `x-atuo-tier` | `cheap` · `mid` · `smart` — 자동 분류를 무시하고 지정 |
| `x-atuo-cache` | `off` — 이번 요청은 캐시를 쓰지 않음 (다시 생성하고 싶을 때) |
| `x-atuo-compress` | `off` · `lossless` · `standard` · `aggressive` |

응답 헤더 `x-atuo-provider`, `x-atuo-model`, `x-atuo-tier`, `x-atuo-cache`, `x-atuo-route`로 어디로 왜 갔는지 알 수 있습니다.

## 자동 분류 기준

마지막 사용자 메시지와 대화 전체를 보고 점수를 매깁니다.

| 신호 | 점수 |
|---|---|
| 코드가 들어 있음 (코드 블록, 함수 정의, 스택 트레이스 등) | +2 |
| 도구(function)를 쓰는 요청 | +2 |
| 어려운 작업 단어 (디버그, 설계, 리팩터링, 최적화, 증명 …) | +2 |
| 긴 문맥 (약 2,000토큰 초과 +1, 8,000토큰 초과 +2) | +1 / +2 |
| `reasoning_effort: high` | +2 |
| 쉬운 작업 단어 (번역, 요약, 맞춤법 …) | −2 |
| 짧은 질문 (120자 미만, 코드 없음) | −1 |

2점 이상이면 `mid`, 4점 이상이면 `smart`입니다. `.env`의 `ROUTE_MID_THRESHOLD`, `ROUTE_SMART_THRESHOLD`로 조절합니다. 기준을 올리면 싼 모델로 더 많이 갑니다.

분류는 일부러 싼 쪽으로 기웁니다. 답이 아쉬우면 `model: "smart"`로 다시 물어보면 됩니다.

## 압축 단계

| 단계 | 내용 | 주의 |
|---|---|---|
| `off` | 그대로 전달 | |
| `lossless` | 사용자·시스템 메시지의 줄 끝 공백과 연속 빈 줄 정리(코드 블록 안은 건드리지 않음), 도구 출력의 들여쓰기된 JSON 압축 | 의미 변화 없음 |
| `standard` (기본) | + 똑같은 도구 출력이 반복되면 뒤의 것을 "앞과 동일" 한 줄로 대체 | |
| `aggressive` | + 오래된 큰 도구 출력의 가운데를 잘라내고, 대화가 `HISTORY_TOKEN_BUDGET`을 넘으면 오래된 부분을 요약 | 정보가 일부 사라집니다 |

## 설정

모든 설정은 `.env`에 있고, 항목별 설명은 [`.env.example`](.env.example)에 적혀 있습니다. 자주 바꾸는 것:

- `ROUTER_CLAUDE_MODEL` — 기본 `claude-opus-5-5`. 더 아끼려면 `claude-sonnet-5-5`나 `claude-haiku-5-5`.
- `ROUTER_CLAUDE_DAILY_TOKEN_BUDGET` — Claude 하루 토큰 한도.
- `GEMINI_CHEAP_MODEL`, `GEMINI_MID_MODEL` — 시작할 때 내 키로 쓸 수 없는 모델이면 경고와 함께 쓸 수 있는 모델 목록을 보여 줍니다.
- `MAX_OUTPUT_TOKENS` — 모든 응답의 출력 길이 상한.
- `ROUTER_API_KEY` — 라우터 자체의 비밀번호. 기본은 내 컴퓨터(`127.0.0.1`)에서만 접속되지만, `HOST=0.0.0.0`으로 열 때는 반드시 설정하세요.

비용 표시는 내장 단가표로 계산한 **추정치**입니다. 단가가 바뀌었으면 `PRICING_JSON`으로 덮어쓸 수 있습니다(1M 토큰당 USD).

```bash
PRICING_JSON='{"gemini-2.5-flash":{"in":0.3,"out":2.5}}'
```

## 알아둘 점

- **캐시는 완전히 같은 요청에만** 적중합니다. 같은 질문을 다시 보내면 같은 답이 나오므로, 새 답을 원하면 `x-atuo-cache: off`를 붙이세요. 캐시와 사용량은 `data/` 폴더에 저장됩니다.
- **Claude에는 `temperature`, `top_p`를 보내지 않습니다.** 최신 Claude 모델이 받지 않는 값이라 라우터가 뺍니다. 특정 도구를 강제하는 `tool_choice`도 같은 이유로 `auto`로 바뀝니다.
- **Claude의 거절 폴백이 켜져 있습니다.** 안전 분류기가 요청을 거절하면 Anthropic 서버가 같은 요청을 다른 Claude 모델로 한 번 더 시도합니다(그 모델 요금으로 과금). 끄려면 `ROUTER_CLAUDE_REFUSAL_FALLBACK=false`.
- **도구 호출 중인 대화는 시작한 모델에 계속 붙습니다.** 도구 결과를 돌려보내는 요청은 그 호출을 낸 모델로 다시 갑니다.

## 개발

```bash
npm test
```

```bash
npm run typecheck
```

테스트는 가짜 Gemini·Claude 서버를 띄워서 돌기 때문에 API 키나 과금이 필요 없습니다.

```
src/
  index.ts        시작점
  server.ts       HTTP 서버 (/v1/chat/completions, /v1/models, /stats, 대시보드)
  app.ts          요청 한 건의 흐름: 압축 → 캐시 → 라우팅 → 호출 → 폴백 → 기록
  router.ts       어느 모델을 어떤 순서로 시도할지
  classify.ts     난이도 점수
  compress.ts     프롬프트 압축
  history.ts      긴 대화 요약
  cache.ts        응답 캐시
  usage.ts        사용량·비용 기록
  providers/      Gemini(OpenAI 호환 엔드포인트), Claude(공식 SDK)
```
