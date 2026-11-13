"""질문 난이도를 토큰 소비 없이(규칙 기반으로) 판단해 보낼 모델을 고른다.

쉬운 질문 -> Gemini(저렴), 어려운 질문 -> Claude.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 깊은 추론·설계·코드 작업을 뜻하는 표현 (각 2점)
_HARD = re.compile(
    r"설계|아키텍처|리팩터|리팩토링|디버그|디버깅|버그|최적화|알고리즘|증명|분석해|"
    r"비교 ?분석|전략|검토해|코드 ?리뷰|구현해|작성해 ?줘.*코드|코드.*작성|보안|취약점|"
    r"단계별|논리적|심층|자세히|"
    r"\b(architect|refactor|debug|optimi[sz]e|algorithm|prove|proof|analy[sz]e|"
    r"trade-?offs?|design|implement|code review|security|vulnerabilit|step[- ]by[- ]step|"
    r"in depth|root cause)",
    re.IGNORECASE,
)

# 단순 작업을 뜻하는 표현 (각 -2점)
_EASY = re.compile(
    r"번역|요약|맞춤법|오타|뜻이? ?뭐|무슨 ?뜻|정의|간단히|짧게|한 ?줄|인사|"
    r"\b(translate|summari[sz]e|tl;?dr|spell|typo|define|definition|briefly|"
    r"what is|who is|when (is|was)|rephrase|paraphrase)",
    re.IGNORECASE,
)

_CODE = re.compile(r"```|\bdef \w+\(|\bclass \w+|\bfunction\b|=>|#include|\bSELECT\b.+\bFROM\b|Traceback")

_FORCE = {
    "/c": "claude",
    "/claude": "claude",
    "/g": "gemini",
    "/gemini": "gemini",
}


@dataclass
class Route:
    provider: str  # "claude" | "gemini"
    reason: str
    score: int
    prompt: str  # 강제 지정 접두사를 뗀 실제 질문
    forced: bool = False


def estimate_tokens(text: str) -> int:
    """대략적인 토큰 수 추정. 한글은 글자당 약 1토큰, 그 외는 4글자당 1토큰으로 본다."""
    cjk = sum(1 for ch in text if "぀" <= ch <= "鿿" or "가" <= ch <= "힣")
    return cjk + (len(text) - cjk + 3) // 4


def score_prompt(prompt: str) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    hard = len(_HARD.findall(prompt))
    if hard:
        score += 2 * min(hard, 3)
        reasons.append(f"추론/설계 표현 {hard}개")
    if _CODE.search(prompt):
        score += 2
        reasons.append("코드 포함")
    tokens = estimate_tokens(prompt)
    if tokens > 1500:
        score += 2
        reasons.append("긴 입력")
    elif tokens > 400:
        score += 1
        reasons.append("중간 길이 입력")
    easy = len(_EASY.findall(prompt))
    if easy:
        score -= 2 * min(easy, 2)
        reasons.append(f"단순 작업 표현 {easy}개")
    return score, reasons


def route(prompt: str, hard_score: int = 3) -> Route:
    stripped = prompt.strip()
    head, _, rest = stripped.partition(" ")
    forced = _FORCE.get(head.lower())
    if forced and rest.strip():
        return Route(forced, f"사용자 지정({head})", 0, rest.strip(), forced=True)

    score, reasons = score_prompt(stripped)
    provider = "claude" if score >= hard_score else "gemini"
    reason = ", ".join(reasons) if reasons else "일반 질문"
    return Route(provider, reason, score, stripped)
