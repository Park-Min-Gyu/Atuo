# Atuo

문서를 **한 페이지씩 요약하고, 페이지마다 2차 검증을 통과해야 다음 페이지로 넘어가는** 요약 도구입니다.

많은 문서를 요약할 때 가장 큰 문제는 요약이 중요한 내용을 빠뜨려도 알 수 없다는 점입니다. Atuo는 요약을 쓴 뒤 별도의 검증 단계가 원문과 요약을 대조해서, 빠진 내용과 원문에 없는 내용을 찾아 고친 다음에야 그 페이지를 끝냅니다.

## 동작 방식

페이지마다 아래 순서를 반복합니다.

1. **요약** — 원문 한 페이지를 요약합니다. 앞 페이지 요약을 맥락으로 넘겨서 페이지 경계에서 끊긴 문장도 이어서 이해합니다.
2. **2차 검증** — 검증자가 원문과 요약을 대조해 두 가지를 찾습니다.
   - 누락: 원문에는 있는데 요약에 빠진 중요한 내용 (결론, 수치, 날짜, 의무, 조건, 예외, 위험)
   - 근거 없음: 요약에는 있는데 원문이 뒷받침하지 않는 내용
3. **검증자에 대한 검증** — 검증자도 모델이라 틀릴 수 있으므로, 코드로 다시 확인합니다.
   - 누락을 지적하려면 원문 구절을 그대로 인용해야 하고, 그 인용이 실제 원문에 없으면 지적을 버립니다.
   - 요약에 나온 숫자가 원문에 없으면 검증자가 놓쳤더라도 문제로 잡습니다.
4. **수정 후 재검증** — 문제가 있으면 요약을 고치고 다시 검증합니다 (기본 최대 2회).
5. 통과하면 다음 페이지로 넘어갑니다. 끝까지 통과하지 못한 페이지는 숨기지 않고 **"직접 확인 필요"** 로 표시하며 남은 문제를 원문 인용과 함께 보여 줍니다.

모든 페이지가 끝나면 페이지별 요약을 합쳐 전체 요약을 만들고, 이것도 같은 방식으로 검증합니다.

## 설치

```bash
git clone https://github.com/Park-Min-Gyu/Atuo.git
cd Atuo
pip install -e ".[all]"      # Claude 만 쓸 때는 ".[claude]", 오픈 모델만 쓸 때는 ".[openai]"
```

Python 3.10 이상이 필요합니다.

## 사용법

### Claude 로 실행

```bash
export ANTHROPIC_API_KEY=...
atuo 보고서.pdf
```

### 오픈소스 모델로 실행 (무료, 로컬)

OpenAI 호환 API 를 제공하는 서버라면 무엇이든 쓸 수 있습니다.

```bash
# Ollama
ollama pull qwen2.5:14b
atuo 보고서.pdf --backend openai --base-url http://localhost:11434/v1 --model qwen2.5:14b

# Hugging Face Inference
export HF_TOKEN=...
atuo 보고서.pdf --backend openai --base-url https://router.huggingface.co/v1 --model Qwen/Qwen2.5-72B-Instruct
```

### 여러 문서 한 번에

```bash
atuo 문서폴더/ -o 결과/          # 폴더 안의 pdf, docx, txt, md 를 모두 처리
atuo a.pdf b.docx c.txt
```

### 결과

문서마다 `atuo_out/<문서이름>/` 에 저장됩니다.

| 파일 | 내용 |
|---|---|
| `summary.md` | 전체 요약, 페이지별 요약, 검증 결과 |
| `report.json` | 같은 내용의 데이터 (다른 프로그램에서 쓸 때) |
| `state.json` | 진행 상황. 중간에 멈춰도 같은 명령을 다시 실행하면 끝난 페이지는 건너뜁니다 |

### 옵션

| 옵션 | 설명 |
|---|---|
| `--verify-model` | 검증에 다른 모델을 씁니다. 요약 모델과 다른 모델로 검증하면 같은 실수를 반복할 가능성이 줄어듭니다 |
| `--strict` | 중요도가 낮은 누락도 수정 대상으로 처리합니다 (더 꼼꼼하지만 느리고 요약이 길어집니다) |
| `--max-rounds N` | 페이지당 최대 수정 횟수 (기본 2) |
| `--pages 1-10,15` | 일부 페이지만 처리 |
| `--lang en` | 요약 언어 (기본 `ko`) |
| `--effort high` | Claude 의 추론 강도 (기본 `medium`) |

## 알아둘 점

- **스캔한 PDF** 는 글자가 추출되지 않습니다. 그런 페이지는 "추출된 텍스트 없음" 으로 표시되고 요약에 포함되지 않으니, 먼저 OCR 을 거쳐야 합니다.
- docx, txt 처럼 페이지 구분이 없는 형식은 약 3,000자 단위로 나눠 한 페이지로 취급합니다.
- 페이지마다 모델을 최소 2번 (요약 1회, 검증 1회) 호출합니다. 수정이 생기면 그만큼 늘어납니다.
- 검증은 누락을 크게 줄여 주지만 0 으로 만들지는 못합니다. 계약서처럼 중요한 문서는 "직접 확인 필요" 표시가 없더라도 핵심 조항을 원문에서 확인하세요.

## 사용한 오픈소스

- [pypdf](https://github.com/py-pdf/pypdf) — PDF 페이지별 텍스트 추출
- [python-docx](https://github.com/python-openxml/python-docx) — Word 문서 읽기
- [anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python), [openai-python](https://github.com/openai/openai-python) — 모델 호출
- [Ollama](https://github.com/ollama/ollama), [Hugging Face](https://huggingface.co/models) 의 오픈 모델 — `--backend openai` 로 연결

## 개발

```bash
pip install -e ".[dev]"
pytest
```

테스트는 가짜 모델을 써서 API 키 없이 돌아갑니다.

## 라이선스

MIT
