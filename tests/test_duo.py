"""가짜 공급자로 Duo의 흐름을 검증한다 (API 키 불필요)."""

from __future__ import annotations

import pytest

from duo import Config, Duo, ProviderError, Reply
from duo.cache import ResponseCache
from duo.config import load_dotenv
from duo.engine import ESCALATE_MARK
from duo.router import estimate_tokens, route


class Fake:
    def __init__(self, name, answers=None, *, up=True, error=None):
        self.name = name
        self.answers = list(answers or [])
        self.up = up
        self.error = error
        self.calls = []

    def available(self):
        return self.up

    def complete(self, system, messages):
        self.calls.append((system, [dict(m) for m in messages]))
        if self.error:
            raise self.error
        text = self.answers.pop(0) if self.answers else f"{self.name}-answer"
        return Reply(text=text, provider=self.name, model=f"{self.name}-model", input_tokens=10, output_tokens=5)


def make(claude=None, gemini=None, **cfg):
    claude = claude or Fake("claude")
    gemini = gemini or Fake("gemini")
    duo = Duo(Config(**cfg), claude=claude, gemini=gemini, cache=ResponseCache(":memory:", 3600))
    return duo, claude, gemini


# ---------------------------------------------------------------- 라우터

def test_simple_question_goes_to_gemini():
    assert route("이 문장 영어로 번역해줘: 안녕하세요").provider == "gemini"
    assert route("What is the capital of France?").provider == "gemini"


def test_hard_question_goes_to_claude():
    assert route("이 서비스 아키텍처 설계하고 병목을 분석해줘").provider == "claude"
    assert route("Debug this:\n```python\ndef f(x): return x/0\n```").provider == "claude"


def test_forced_prefix():
    r = route("/c 안녕")
    assert (r.provider, r.prompt, r.forced) == ("claude", "안녕", True)
    r = route("/g 아키텍처 설계해줘")
    assert (r.provider, r.forced) == ("gemini", True)


def test_bare_prefix_is_not_forced():
    assert route("/c").forced is False


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("안녕하세요") == 5
    assert estimate_tokens("abcdefgh") == 2


# ---------------------------------------------------------------- 엔진

def test_easy_uses_gemini_only():
    duo, claude, gemini = make()
    answer = duo.ask("번역해줘: hello")
    assert answer.provider == "gemini"
    assert claude.calls == []
    assert ESCALATE_MARK in gemini.calls[0][0]  # 승격 규칙이 Gemini에게만 전달된다


def test_hard_uses_claude_without_escalate_rule():
    duo, claude, gemini = make()
    answer = duo.ask("이 알고리즘을 최적화하고 단계별로 분석해줘")
    assert answer.provider == "claude"
    assert gemini.calls == []
    assert ESCALATE_MARK not in claude.calls[0][0]


def test_gemini_escalates_to_claude():
    duo, claude, gemini = make(gemini=Fake("gemini", [ESCALATE_MARK]))
    answer = duo.ask("안녕?")
    assert answer.provider == "claude" and answer.escalated
    assert duo.stats.escalations == 1
    assert ESCALATE_MARK not in duo.history[-1]["content"]


def test_forced_gemini_does_not_escalate():
    duo, claude, gemini = make()
    duo.ask("/g 아키텍처 설계해줘")
    assert claude.calls == []
    assert ESCALATE_MARK not in gemini.calls[0][0]


def test_cache_hit_skips_api():
    duo, claude, gemini = make()
    first = duo.ask("번역해줘: hello")
    duo.reset()
    second = duo.ask("번역해줘: hello")
    assert second.cached and second.text == first.text
    assert len(gemini.calls) == 1
    assert duo.stats.cache_hits == 1


def test_fallback_when_gemini_fails():
    broken = Fake("gemini", error=ProviderError("gemini", "429"))
    duo, claude, _ = make(gemini=broken)
    answer = duo.ask("안녕?")
    assert answer.provider == "claude" and answer.fell_back
    assert duo.stats.fallbacks == 1


def test_fallback_when_claude_fails():
    broken = Fake("claude", error=ProviderError("claude", "500"))
    duo, _, gemini = make(claude=broken)
    answer = duo.ask("이 알고리즘을 최적화하고 단계별로 분석해줘")
    assert answer.provider == "gemini" and answer.fell_back


def test_escalated_claude_failure_falls_back_to_gemini():
    gemini = Fake("gemini", [ESCALATE_MARK, "직접 답변"])
    claude = Fake("claude", error=ProviderError("claude", "500"))
    duo, _, _ = make(claude=claude, gemini=gemini)
    answer = duo.ask("안녕?")
    assert answer.text == "직접 답변" and answer.provider == "gemini"
    assert ESCALATE_MARK not in gemini.calls[1][0]


def test_non_retryable_error_is_raised():
    broken = Fake("gemini", error=ProviderError("gemini", "bad", retryable=False))
    duo, claude, _ = make(gemini=broken)
    with pytest.raises(ProviderError):
        duo.ask("안녕?")
    assert claude.calls == []
    assert duo.history == []


def test_fallback_disabled():
    broken = Fake("gemini", error=ProviderError("gemini", "429"))
    duo, _, _ = make(gemini=broken, fallback=False)
    with pytest.raises(ProviderError):
        duo.ask("안녕?")


def test_unavailable_provider_is_skipped():
    duo, claude, gemini = make(gemini=Fake("gemini", up=False))
    answer = duo.ask("안녕?")
    assert answer.provider == "claude" and gemini.calls == []


def test_no_provider_available():
    duo, _, _ = make(claude=Fake("claude", up=False), gemini=Fake("gemini", up=False))
    with pytest.raises(ProviderError):
        duo.ask("안녕?")


def test_empty_prompt_rejected():
    duo, _, _ = make()
    with pytest.raises(ValueError):
        duo.ask("   ")


def test_history_is_sent_on_next_turn():
    duo, _, gemini = make()
    duo.ask("내 이름은 민규야")
    duo.ask("내 이름이 뭐야?")
    roles = [m["role"] for m in gemini.calls[1][1]]
    assert roles == ["user", "assistant", "user"]


def test_long_history_is_compressed_by_gemini():
    gemini = Fake("gemini", ["a1", "a2", "요약본", "a4"])
    duo, claude, _ = make(gemini=gemini, compress_after_tokens=20, keep_recent_turns=1)
    for i in range(2):
        duo.ask(f"질문 {i} " + "가" * 30)
    answer = duo.ask("마지막 질문")
    assert duo.stats.compressions == 1
    assert duo.summary == "요약본"
    assert "요약본" in gemini.calls[-1][0]
    assert len(gemini.calls[-1][1]) == 3  # 최근 1턴 + 새 질문
    assert answer.text == "a4" and claude.calls == []


def test_truncated_reply_is_not_cached():
    class Cut(Fake):
        def complete(self, system, messages):
            reply = super().complete(system, messages)
            reply.truncated = True
            return reply

    duo, _, gemini = make(gemini=Cut("gemini"))
    assert duo.ask("안녕?").truncated
    duo.reset()
    assert not duo.ask("안녕?").cached


def test_report_runs():
    duo, _, _ = make(gemini_input_price=0.1, gemini_output_price=0.4)
    duo.ask("안녕?")
    assert "Gemini" in duo.report()


# ---------------------------------------------------------------- 설정 / 캐시

def test_cache_ttl_expiry(monkeypatch):
    import duo.cache as cache_module

    cache = ResponseCache(":memory:", 10)
    cache.put("k", Reply("t", "gemini", "m"))
    assert cache.get("k").text == "t"
    now = cache_module.time.time()
    monkeypatch.setattr(cache_module.time, "time", lambda: now + 11)
    assert cache.get("k") is None


def test_load_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# 주석\nDUO_TEST_A="값"\nexport DUO_TEST_B=2\nDUO_TEST_C=keep\n', encoding="utf-8")
    monkeypatch.delenv("DUO_TEST_A", raising=False)
    monkeypatch.delenv("DUO_TEST_B", raising=False)
    monkeypatch.setenv("DUO_TEST_C", "original")
    load_dotenv(env)
    import os

    assert os.environ["DUO_TEST_A"] == "값"
    assert os.environ["DUO_TEST_B"] == "2"
    assert os.environ["DUO_TEST_C"] == "original"


def test_bad_env_number(monkeypatch):
    monkeypatch.setenv("DUO_HARD_SCORE", "abc")
    with pytest.raises(ValueError):
        Config.from_env(dotenv=None)


def test_small_old_part_is_not_compressed():
    duo, _, gemini = make(compress_after_tokens=40, keep_recent_turns=1)
    duo.ask("짧음")
    duo.ask("질문 " + "가" * 60)
    duo.ask("다음")
    assert duo.stats.compressions == 0


def test_both_fail_reports_both():
    duo, _, _ = make(
        claude=Fake("claude", error=ProviderError("claude", "c-down")),
        gemini=Fake("gemini", error=ProviderError("gemini", "g-down")),
    )
    with pytest.raises(ProviderError) as info:
        duo.ask("안녕?")
    assert "c-down" in str(info.value) and "g-down" in str(info.value)
