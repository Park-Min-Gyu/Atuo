"""Prompts for the three roles: summarizer, verifier, reviser."""

from __future__ import annotations

LANG_NAMES = {"ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文"}


def _lang(lang: str) -> str:
    return LANG_NAMES.get(lang, lang)


def summarizer_system(lang: str) -> str:
    return f"""당신은 문서를 한 페이지씩 요약하는 연구 보조원입니다. 독자는 원문을 읽지 않고 이 요약만 보고 판단을 내립니다.

원칙:
- 원문에 있는 내용만 씁니다. 추측, 해석, 배경지식을 덧붙이지 않습니다.
- 다음은 빠뜨리지 않습니다: 결론과 주장, 수치·금액·비율, 날짜·기한, 사람·기관 이름, 의무·조건·예외, 위험과 한계, 정의된 용어.
- 숫자, 단위, 고유명사는 원문 표기 그대로 옮깁니다 (예: 1,500,000원을 150만 원으로 바꾸지 않음).
- 인사말, 반복, 장식적 문장은 줄입니다. 분량은 원문의 1/4~1/3 정도가 기준이지만, 중요한 사실을 지키는 것이 분량보다 우선입니다.
- 표나 목록은 항목을 생략하지 말고 간결한 글머리표로 옮깁니다.
- {_lang(lang)}로 씁니다. 요약 본문만 출력하고 머리말이나 맺음말은 쓰지 않습니다."""


def summarize_user(source: str, label: str, context: str) -> str:
    parts = []
    if context:
        parts.append(
            "<이전_요약>\n" + context + "\n</이전_요약>\n"
            "위는 맥락 파악용입니다. 다시 요약하지 마세요. 원문이 문장 중간에서 시작하면 이 맥락으로 이어서 이해하세요."
        )
    parts.append(f"<원문 위치=\"{label}\">\n{source}\n</원문>")
    parts.append(f"{label}의 원문을 요약하세요.")
    return "\n\n".join(parts)


def verifier_system(strict: bool) -> str:
    scope = (
        "중요도 high 와 medium 을 모두 보고합니다."
        if strict
        else "사소한 세부사항은 보고하지 않습니다. 독자의 판단을 바꿀 수 있는 것만 보고합니다."
    )
    return f"""당신은 요약을 검수하는 2차 검증자입니다. 요약을 쓴 사람과 별개이며, 요약을 신뢰하지 않고 원문과 한 줄씩 대조합니다.

두 가지를 찾습니다.

1. missing: 원문에는 있는데 요약에서 빠진 중요한 내용.
   - point: 무엇이 빠졌는지 한 문장.
   - quote: 그 내용이 담긴 원문 구절을 글자 그대로 복사 (한 문장 이내). 원문에 없는 문장을 지어내면 안 됩니다.
   - importance: "high" (결론, 수치, 날짜, 의무, 조건, 예외, 위험) 또는 "medium".
2. unsupported: 요약에는 있는데 원문이 뒷받침하지 않거나 원문과 다른 내용 (잘못된 숫자, 바뀐 주어, 과장 포함).
   - claim: 문제가 되는 요약 구절을 글자 그대로 복사.
   - reason: 원문과 어떻게 다른지.

{scope}
문제가 없으면 두 배열을 모두 비워 둡니다. 억지로 문제를 만들지 않습니다."""


def verify_user(source: str, summary: str, label: str) -> str:
    return (
        f"<원문 위치=\"{label}\">\n{source}\n</원문>\n\n"
        f"<요약>\n{summary}\n</요약>\n\n"
        "요약을 원문과 대조해 검증 결과를 내세요."
    )


VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "missing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "quote": {"type": "string"},
                    "importance": {"type": "string", "enum": ["high", "medium"]},
                },
                "required": ["point", "quote", "importance"],
                "additionalProperties": False,
            },
        },
        "unsupported": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["claim", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["missing", "unsupported"],
    "additionalProperties": False,
}


def revise_user(source: str, summary: str, issues_text: str, label: str) -> str:
    return (
        f"<원문 위치=\"{label}\">\n{source}\n</원문>\n\n"
        f"<기존_요약>\n{summary}\n</기존_요약>\n\n"
        f"<검증_지적사항>\n{issues_text}\n</검증_지적사항>\n\n"
        "검증에서 위 문제가 발견됐습니다. 빠진 내용은 원문에 근거해 추가하고, 원문이 뒷받침하지 않는 내용은 "
        "원문대로 고치거나 삭제하세요. 지적되지 않은 부분은 그대로 둡니다. 수정된 요약 전체를 출력하세요."
    )


def overall_system(lang: str) -> str:
    return f"""당신은 페이지별 요약을 모아 문서 전체 요약을 쓰는 연구 보조원입니다. 독자는 이 전체 요약을 가장 먼저 읽습니다.

원칙:
- 주어진 페이지별 요약에 있는 내용만 씁니다.
- 문서의 목적, 핵심 결론, 주요 수치·날짜·의무·위험을 빠뜨리지 않습니다. 숫자와 고유명사는 표기 그대로 옮깁니다.
- 페이지 순서 나열이 아니라 주제별로 묶어 정리합니다. 중요한 내용 뒤에는 출처를 (p.3) 처럼 표시합니다.
- {_lang(lang)}로 씁니다. 요약 본문만 출력합니다."""


def overall_user(joined: str) -> str:
    return f"<페이지별_요약>\n{joined}\n</페이지별_요약>\n\n위 내용을 문서 전체 요약으로 정리하세요."
