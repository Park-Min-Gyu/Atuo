"""환경 변수(.env 포함)에서 읽는 설정."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSTEM = (
    "You are a helpful assistant. Answer in the language the user writes in. "
    "Be accurate and concise."
)


def load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """KEY=VALUE 형식의 .env 파일을 읽는다. 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    file = Path(path)
    if not file.is_file():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 값은 정수여야 합니다: {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 값은 숫자여야 합니다: {raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    # 모델
    claude_model: str = "claude-opus-5-5"
    gemini_model: str = "gemini-3.8-flash"
    claude_effort: str = ""  # 비우면 모델 기본값. low | medium | high | xhigh | max
    claude_max_tokens: int = 16000
    gemini_max_tokens: int = 8192
    system_prompt: str = DEFAULT_SYSTEM

    # 라우팅
    hard_score: int = 3  # 난이도 점수가 이 값 이상이면 Claude로 보낸다
    escalate: bool = True  # Gemini가 자신 없다고 하면 Claude로 넘긴다
    fallback: bool = True  # 한쪽이 실패하면 다른 쪽으로 재시도한다

    # 대화 압축 (오래된 대화를 Gemini가 요약)
    compress_after_tokens: int = 6000
    keep_recent_turns: int = 4

    # 응답 캐시
    cache_enabled: bool = True
    cache_path: str = ".duo/cache.sqlite3"
    cache_ttl_seconds: int = 7 * 24 * 3600

    # 비용 표시용 단가 (USD / 100만 토큰). Gemini 단가는 직접 넣어야 계산된다.
    claude_input_price: float = 4.0
    claude_output_price: float = 20.0
    gemini_input_price: float = 0.0
    gemini_output_price: float = 0.0

    @classmethod
    def from_env(cls, dotenv: str | None = ".env") -> "Config":
        if dotenv:
            load_dotenv(dotenv)
        d = cls()
        return cls(
            claude_model=os.environ.get("DUO_CLAUDE_MODEL", d.claude_model).strip() or d.claude_model,
            gemini_model=os.environ.get("DUO_GEMINI_MODEL", d.gemini_model).strip() or d.gemini_model,
            claude_effort=os.environ.get("DUO_CLAUDE_EFFORT", d.claude_effort).strip(),
            claude_max_tokens=_int("DUO_CLAUDE_MAX_TOKENS", d.claude_max_tokens),
            gemini_max_tokens=_int("DUO_GEMINI_MAX_TOKENS", d.gemini_max_tokens),
            system_prompt=os.environ.get("DUO_SYSTEM_PROMPT", d.system_prompt).strip() or d.system_prompt,
            hard_score=_int("DUO_HARD_SCORE", d.hard_score),
            escalate=_bool("DUO_ESCALATE", d.escalate),
            fallback=_bool("DUO_FALLBACK", d.fallback),
            compress_after_tokens=_int("DUO_COMPRESS_AFTER_TOKENS", d.compress_after_tokens),
            keep_recent_turns=_int("DUO_KEEP_RECENT_TURNS", d.keep_recent_turns),
            cache_enabled=_bool("DUO_CACHE", d.cache_enabled),
            cache_path=os.environ.get("DUO_CACHE_PATH", d.cache_path).strip() or d.cache_path,
            cache_ttl_seconds=_int("DUO_CACHE_TTL_SECONDS", d.cache_ttl_seconds),
            claude_input_price=_float("DUO_CLAUDE_INPUT_PRICE", d.claude_input_price),
            claude_output_price=_float("DUO_CLAUDE_OUTPUT_PRICE", d.claude_output_price),
            gemini_input_price=_float("DUO_GEMINI_INPUT_PRICE", d.gemini_input_price),
            gemini_output_price=_float("DUO_GEMINI_OUTPUT_PRICE", d.gemini_output_price),
        )
