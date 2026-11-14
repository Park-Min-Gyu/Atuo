"""Model backends.

Two are provided:

* ``ClaudeLLM``  - Claude through the official Anthropic SDK.
* ``OpenAICompatLLM`` - any server that speaks the OpenAI chat API, which is
  how open models are usually served (Ollama, vLLM, LM Studio, the Hugging
  Face Inference router, ...).

Anything with ``complete`` and ``complete_json`` works, so tests use a fake.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

# Models that accept the server-side refusal fallback parameter.
_FALLBACK_MODELS = ("claude-fable-5", "claude-opus-5", "claude-sonnet-5-5")


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, system: str, user: str) -> str: ...

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


def parse_json_loosely(text: str) -> dict:
    """Parse JSON from a model that may wrap it in prose or a code fence."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


class ClaudeLLM:
    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium"):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.effort = effort

    def _call(self, system: str, user: str, schema: dict | None) -> str:
        output_config: dict = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict = dict(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        try:
            if self.model.startswith(_FALLBACK_MODELS):
                # If a safety classifier declines, the API retries on a
                # fallback model inside the same call.
                response = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                )
            else:
                response = self.client.messages.create(**kwargs)
        except self._anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic 인증 실패: ANTHROPIC_API_KEY 를 확인하세요.") from exc
        except self._anthropic.BadRequestError as exc:
            raise LLMError(f"Anthropic 요청 오류: {exc}") from exc
        except self._anthropic.RateLimitError as exc:
            raise LLMError("Anthropic 사용량 한도에 걸렸습니다. 잠시 후 다시 실행하세요.") from exc
        except self._anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API 오류 ({exc.status_code}): {exc}") from exc
        except self._anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic API 연결 실패: {exc}") from exc
        except TypeError as exc:  # the SDK raises this when no credential is configured
            if "authentication" not in str(exc):
                raise
            raise LLMError("ANTHROPIC_API_KEY 가 설정되지 않았습니다.") from exc
        # The SDK has already retried rate limits, 5xx and connection errors.

        if response.stop_reason == "refusal":
            raise LLMError("모델이 이 페이지 처리를 거부했습니다 (stop_reason=refusal).")
        if response.stop_reason == "max_tokens":
            raise LLMError("모델 출력이 max_tokens 에서 잘렸습니다.")
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def complete(self, system: str, user: str) -> str:
        return self._call(system, user, None)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        return json.loads(self._call(system, user, schema))


class OpenAICompatLLM:
    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None):
        import os

        import openai

        self._openai = openai
        self.client = openai.OpenAI(
            base_url=base_url,
            # Local servers such as Ollama ignore the key but the SDK wants one.
            api_key=api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("HF_TOKEN") or "none",
        )
        self.model = model

    def complete(self, system: str, user: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except self._openai.OpenAIError as exc:
            raise LLMError(f"모델 서버 오류 ({self.model}): {exc}") from exc
        return (response.choices[0].message.content or "").strip()

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        # Not every open-model server supports schema-constrained output, so
        # the schema goes in the prompt and the reply is parsed tolerantly.
        instructions = (
            f"{system}\n\n반드시 아래 JSON 스키마에 맞는 JSON 객체 하나만 출력하세요. "
            f"다른 설명은 쓰지 마세요.\n{json.dumps(schema, ensure_ascii=False)}"
        )
        last_error: Exception | None = None
        for _ in range(2):
            raw = self.complete(instructions, user)
            try:
                return parse_json_loosely(raw)
            except json.JSONDecodeError as exc:
                last_error = exc
        raise LLMError(f"모델이 올바른 JSON 을 내지 못했습니다: {last_error}")


def make_llm(backend: str, model: str | None, base_url: str | None = None, effort: str = "medium") -> LLM:
    if backend == "claude":
        return ClaudeLLM(model=model or "claude-opus-5-5", effort=effort)
    if backend == "openai":
        if not model:
            raise LLMError("--backend openai 에는 --model 이 필요합니다 (예: qwen2.5:14b).")
        return OpenAICompatLLM(model=model, base_url=base_url)
    raise LLMError(f"알 수 없는 backend: {backend}")
