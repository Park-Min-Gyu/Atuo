"""명령줄 인터페이스.

    python -m duo "질문"        한 번 묻고 종료
    python -m duo               대화 모드
"""

from __future__ import annotations

import argparse
import sys

from .config import Config
from .engine import Answer, Duo
from .providers import ProviderError

HELP = """명령어
  /c <질문>   이번 질문만 Claude로 강제
  /g <질문>   이번 질문만 Gemini로 강제
  /stats      토큰 사용량과 절감 추정치
  /reset      대화 기록 초기화
  /help       이 도움말
  /exit       종료"""


def _print_answer(answer: Answer, verbose: bool) -> None:
    print(answer.text)
    if not verbose:
        return
    tag = "캐시" if answer.cached else answer.provider
    print(f"\n  ↳ {tag} · {answer.model} · {answer.reason}", file=sys.stderr)
    for note in answer.notes:
        print(f"  ↳ {note}", file=sys.stderr)


def _repl(duo: Duo, verbose: bool) -> int:
    print("Duo (Gemini + Claude) — /help 로 명령어 보기, /exit 로 종료")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        command = line.lower()
        if command in {"/exit", "/quit"}:
            break
        if command == "/help":
            print(HELP)
            continue
        if command == "/stats":
            print(duo.report())
            continue
        if command == "/reset":
            duo.reset()
            print("대화 기록을 지웠습니다.")
            continue
        try:
            _print_answer(duo.ask(line), verbose)
        except ProviderError as exc:
            print(f"오류: {exc}", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n(취소됨)", file=sys.stderr)
    print(duo.report())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="duo", description="Gemini와 Claude를 함께 써서 토큰 비용을 줄이는 AI"
    )
    parser.add_argument("prompt", nargs="*", help="질문 (없으면 대화 모드)")
    parser.add_argument("-q", "--quiet", action="store_true", help="라우팅 정보 숨기기")
    parser.add_argument("--no-cache", action="store_true", help="응답 캐시 끄기")
    parser.add_argument("--stats", action="store_true", help="답변 뒤에 사용량 출력")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env()
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    if args.no_cache:
        config.cache_enabled = False

    duo = Duo(config)
    if not any(p.available() for p in duo.providers.values()):
        print(
            "사용 가능한 모델이 없습니다. .env에 ANTHROPIC_API_KEY / GEMINI_API_KEY를 넣고 "
            "`pip install -r requirements.txt`를 실행하세요.",
            file=sys.stderr,
        )
        return 2

    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        return _repl(duo, not args.quiet)

    try:
        _print_answer(duo.ask(prompt), not args.quiet)
    except ProviderError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    if args.stats:
        print(duo.report(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
