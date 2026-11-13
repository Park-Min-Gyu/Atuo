"""Duo 본체: 캐시 -> 라우팅 -> 대화 압축 -> 호출 -> (승격 / 대체) -> 통계."""

from __future__ import annotations

from dataclasses import dataclass, field

from .cache import ResponseCache, make_key
from .config import Config
from .providers import ClaudeProvider, GeminiProvider, Message, Provider, ProviderError, Reply
from .router import estimate_tokens, route

ESCALATE_MARK = "[[ESCALATE]]"

_ESCALATE_RULE = (
    "\n\nIf the request needs deep multi-step reasoning, careful code design, or you are "
    f"not confident you can answer it correctly, reply with exactly {ESCALATE_MARK} and "
    "nothing else. A stronger model will then answer instead."
)

_SUMMARY_SYSTEM = (
    "You compress conversations. Summarize the conversation below so another assistant can "
    "continue it. Keep every fact, decision, name, number, code identifier and open question. "
    "Write in the conversation's language. Output only the summary."
)


@dataclass
class Answer:
    text: str
    provider: str
    model: str
    reason: str
    cached: bool = False
    escalated: bool = False
    fell_back: bool = False
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Stats:
    requests: int = 0
    cache_hits: int = 0
    escalations: int = 0
    fallbacks: int = 0
    compressions: int = 0
    tokens: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            "claude": {"calls": 0, "input": 0, "output": 0, "cache_read": 0},
            "gemini": {"calls": 0, "input": 0, "output": 0, "cache_read": 0},
        }
    )

    def add(self, reply: Reply) -> None:
        bucket = self.tokens[reply.provider]
        bucket["calls"] += 1
        bucket["input"] += reply.input_tokens
        bucket["output"] += reply.output_tokens
        bucket["cache_read"] += reply.cache_read_tokens


class Duo:
    def __init__(
        self,
        config: Config | None = None,
        *,
        claude: Provider | None = None,
        gemini: Provider | None = None,
        cache: ResponseCache | None = None,
    ):
        self.config = config or Config.from_env()
        self.providers: dict[str, Provider] = {
            "claude": claude or ClaudeProvider(self.config),
            "gemini": gemini or GeminiProvider(self.config),
        }
        if cache is not None:
            self.cache: ResponseCache | None = cache
        elif self.config.cache_enabled:
            self.cache = ResponseCache(self.config.cache_path, self.config.cache_ttl_seconds)
        else:
            self.cache = None
        self.history: list[Message] = []
        self.summary = ""
        self.stats = Stats()

    # ------------------------------------------------------------------ 공개 API

    def ask(self, prompt: str) -> Answer:
        if not prompt or not prompt.strip():
            raise ValueError("질문이 비어 있습니다.")
        self.stats.requests += 1
        notes: list[str] = []

        decision = route(prompt, self.config.hard_score)
        question = decision.prompt

        if self._compress_if_needed():
            notes.append("이전 대화를 Gemini로 요약해 압축했습니다.")

        system = self._system()
        messages = self.history + [{"role": "user", "content": question}]
        key = make_key(system, messages)

        if self.cache is not None and not decision.forced:
            hit = self.cache.get(key)
            if hit is not None:
                self.stats.cache_hits += 1
                self._remember(question, hit.text)
                return Answer(hit.text, hit.provider, hit.model, "캐시 적중", cached=True, notes=notes)

        target = decision.provider
        reason = decision.reason
        if not self.providers[target].available():
            other = _other(target)
            if not self.providers[other].available():
                raise ProviderError(
                    target, "사용 가능한 모델이 없습니다. API 키와 패키지 설치를 확인하세요.", retryable=False
                )
            notes.append(f"{target}를 쓸 수 없어 {other}로 보냈습니다.")
            target = other

        escalated = False
        fell_back = False
        allow_escalate = (
            target == "gemini"
            and self.config.escalate
            and not decision.forced
            and self.providers["claude"].available()
        )
        try:
            reply = self._call(target, system, messages, allow_escalate)
            if allow_escalate and ESCALATE_MARK in reply.text:
                self.stats.escalations += 1
                escalated = True
                notes.append("Gemini가 어렵다고 판단해 Claude로 넘겼습니다.")
                reply = self._call("claude", system, messages, False)
        except ProviderError as exc:
            other = _other(exc.provider)
            if not (self.config.fallback and exc.retryable and self.providers[other].available()):
                raise
            if escalated:  # 승격한 Claude가 실패: Gemini에게 직접 답하게 한다
                other = "gemini"
            self.stats.fallbacks += 1
            fell_back = True
            notes.append(f"{exc} -> {other}로 대체했습니다.")
            try:
                reply = self._call(other, system, messages, False)
            except ProviderError as second:
                raise ProviderError(
                    second.provider, f"두 모델 모두 실패했습니다. {exc} / {second}", retryable=False
                ) from second

        if reply.truncated:
            notes.append("응답이 최대 토큰 한도에서 잘렸습니다.")
        elif self.cache is not None:
            self.cache.put(key, reply)

        self._remember(question, reply.text)
        return Answer(
            text=reply.text,
            provider=reply.provider,
            model=reply.model,
            reason=reason,
            escalated=escalated,
            fell_back=fell_back,
            truncated=reply.truncated,
            notes=notes,
        )

    def reset(self) -> None:
        self.history.clear()
        self.summary = ""

    def report(self) -> str:
        cfg = self.config
        s = self.stats
        c, g = s.tokens["claude"], s.tokens["gemini"]
        lines = [
            f"요청 {s.requests}건 | 캐시 적중 {s.cache_hits} | 승격 {s.escalations} | "
            f"대체 {s.fallbacks} | 대화 압축 {s.compressions}",
            f"Claude  호출 {c['calls']:>3} | 입력 {c['input']:>8,} (캐시 읽기 {c['cache_read']:,}) | 출력 {c['output']:>8,}",
            f"Gemini  호출 {g['calls']:>3} | 입력 {g['input']:>8,} | 출력 {g['output']:>8,}",
        ]
        claude_cost = _cost(c, cfg.claude_input_price, cfg.claude_output_price)
        avoided = _cost(g, cfg.claude_input_price, cfg.claude_output_price)
        lines.append(f"Claude 비용(추정, 캐시 할인 미반영): ${claude_cost:.4f}")
        if cfg.gemini_input_price or cfg.gemini_output_price:
            gemini_cost = _cost(g, cfg.gemini_input_price, cfg.gemini_output_price)
            lines.append(f"Gemini 비용(추정): ${gemini_cost:.4f}")
            lines.append(f"Gemini 분담으로 아낀 금액(추정): ${avoided - gemini_cost:.4f}")
        else:
            lines.append(
                f"Gemini가 대신 처리한 토큰의 Claude 환산액: ${avoided:.4f} "
                "(Gemini 단가를 설정하면 실제 절감액을 계산합니다)"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ 내부

    def _system(self) -> str:
        if not self.summary:
            return self.config.system_prompt
        return f"{self.config.system_prompt}\n\nSummary of the earlier conversation:\n{self.summary}"

    def _call(self, name: str, system: str, messages: list[Message], escalate_rule: bool) -> Reply:
        reply = self.providers[name].complete(
            system + _ESCALATE_RULE if escalate_rule else system, messages
        )
        self.stats.add(reply)
        return reply

    def _remember(self, question: str, answer: str) -> None:
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})

    def _compress_if_needed(self) -> bool:
        """대화가 길어지면 오래된 부분을 저렴한 모델로 요약해 입력 토큰을 줄인다."""
        keep = max(self.config.keep_recent_turns, 0) * 2
        if len(self.history) <= keep:
            return False
        size = sum(estimate_tokens(m["content"]) for m in self.history)
        if size <= self.config.compress_after_tokens:
            return False

        old = self.history[: len(self.history) - keep] if keep else list(self.history)
        # 줄일 분량이 적으면 요약 호출 비용이 더 크므로 건너뛴다
        if sum(estimate_tokens(m["content"]) for m in old) < self.config.compress_after_tokens // 4:
            return False
        transcript = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in old)
        if self.summary:
            transcript = f"PREVIOUS SUMMARY: {self.summary}\n\n{transcript}"

        summarizer = "gemini" if self.providers["gemini"].available() else "claude"
        if not self.providers[summarizer].available():
            return False
        try:
            reply = self._call(
                summarizer, _SUMMARY_SYSTEM, [{"role": "user", "content": transcript}], False
            )
        except ProviderError:
            return False  # 요약 실패 시 원본 대화를 그대로 유지한다
        if reply.truncated:
            return False

        self.summary = reply.text.strip()
        self.history = self.history[len(old):]
        self.stats.compressions += 1
        return True


def _other(name: str) -> str:
    return "gemini" if name == "claude" else "claude"


def _cost(bucket: dict[str, int], input_price: float, output_price: float) -> float:
    return (bucket["input"] * input_price + bucket["output"] * output_price) / 1_000_000
