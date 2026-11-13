"""Claude / Gemini 호출을 같은 모양으로 감싼다.

두 공급자 모두 `complete(system, messages) -> Reply` 하나만 제공한다.
messages는 {"role": "user" | "assistant", "content": str} 목록이다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from .config import Config

Message = dict[str, str]

# 서버 측 거절 대체(fallbacks)를 지원하는 Claude 모델
_FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}


@dataclass
class Reply:
    text: str
    provider: str  # "claude" | "gemini"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    truncated: bool = False


class ProviderError(Exception):
    """공급자 호출 실패. retryable이면 다른 공급자로 넘겨도 되는 실패다."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.retryable = retryable


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, system: str, messages: list[Message]) -> Reply: ...


class ClaudeProvider:
    name = "claude"

    def __init__(self, config: Config):
        self.config = config
        self._client = None

    def available(self) -> bool:
        # 키가 없어도 `ant auth login` 프로필 등으로 인증될 수 있으므로 SDK만 확인한다.
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def complete(self, system: str, messages: list[Message]) -> Reply:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(self.name, "anthropic 패키지가 설치되어 있지 않습니다.") from exc

        cfg = self.config
        kwargs: dict = {
            "model": cfg.claude_model,
            "max_tokens": cfg.claude_max_tokens,
            "system": system,
            "messages": messages,
            # 대화 앞부분을 자동으로 프롬프트 캐싱한다 (반복 입력 토큰 절감)
            "cache_control": {"type": "ephemeral"},
        }
        if cfg.claude_effort:
            kwargs["output_config"] = {"effort": cfg.claude_effort}

        try:
            client = self._get_client()
            if cfg.claude_model in _FALLBACK_MODELS:
                response = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                )
            else:
                response = client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ProviderError(self.name, "인증 실패: ANTHROPIC_API_KEY를 확인하세요.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise ProviderError(self.name, f"권한 없음: {exc.message}") from exc
        except anthropic.NotFoundError as exc:
            raise ProviderError(self.name, f"모델을 찾을 수 없습니다: {cfg.claude_model}") from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(self.name, "요청 한도 초과(429).") from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(self.name, f"잘못된 요청: {exc.message}", retryable=False) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(self.name, f"API 오류 {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(self.name, "네트워크 오류로 연결하지 못했습니다.") from exc
        except (anthropic.AnthropicError, TypeError) as exc:
            # 키/프로필이 전혀 없으면 SDK가 요청을 만들다가 TypeError를 낸다
            raise ProviderError(
                self.name, "인증 정보를 찾지 못했습니다: ANTHROPIC_API_KEY를 설정하세요."
            ) from exc

        if response.stop_reason == "refusal":
            raise ProviderError(self.name, "모델이 이 요청을 거절했습니다.", retryable=False)

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ProviderError(self.name, "빈 응답을 받았습니다.")
        usage = response.usage
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        return Reply(
            text=text,
            provider=self.name,
            model=response.model,
            input_tokens=(usage.input_tokens or 0) + cache_read + cache_write,
            output_tokens=usage.output_tokens or 0,
            cache_read_tokens=cache_read,
            truncated=response.stop_reason == "max_tokens",
        )


class GeminiProvider:
    name = "gemini"

    def __init__(self, config: Config):
        self.config = config
        self._client = None

    def available(self) -> bool:
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return False
        try:
            from google import genai  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client()
        return self._client

    def complete(self, system: str, messages: list[Message]) -> Reply:
        try:
            from google.genai import errors, types
        except ImportError as exc:
            raise ProviderError(self.name, "google-genai 패키지가 설치되어 있지 않습니다.") from exc

        cfg = self.config
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in messages
        ]
        try:
            response = self._get_client().models.generate_content(
                model=cfg.gemini_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=cfg.gemini_max_tokens,
                ),
            )
        except errors.APIError as exc:
            code = exc.code or 0
            detail = getattr(exc, "message", None) or str(exc)
            if code in (401, 403) or (code == 400 and "api key" in detail.lower()):
                raise ProviderError(self.name, "인증 실패: GEMINI_API_KEY를 확인하세요.") from exc
            if code == 404:
                raise ProviderError(self.name, f"모델을 찾을 수 없습니다: {cfg.gemini_model}") from exc
            if code == 400:
                raise ProviderError(self.name, f"잘못된 요청: {detail}", retryable=False) from exc
            raise ProviderError(self.name, f"API 오류 {code}: {detail}") from exc
        except ValueError as exc:  # 키 누락 등 클라이언트 구성 오류
            raise ProviderError(self.name, f"설정 오류: {exc}") from exc
        except Exception as exc:  # 네트워크 계층 오류 (SDK가 감싸지 않는다)
            raise ProviderError(self.name, f"연결 오류: {type(exc).__name__}: {exc}") from exc

        text = response.text or ""
        if not text.strip():
            raise ProviderError(self.name, "빈 응답을 받았습니다 (안전 필터 또는 토큰 한도).")
        usage = response.usage_metadata
        finish = ""
        if response.candidates:
            finish = str(getattr(response.candidates[0], "finish_reason", "") or "")
        return Reply(
            text=text,
            provider=self.name,
            model=cfg.gemini_model,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
                if usage
                else 0
            ),
            cache_read_tokens=(usage.cached_content_token_count or 0) if usage else 0,
            truncated="MAX_TOKENS" in finish,
        )
