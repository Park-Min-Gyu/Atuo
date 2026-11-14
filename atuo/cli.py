"""Command line entry point: `atuo FILE... [options]`."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .llm import LLMError, make_llm
from .loader import SUPPORTED, file_hash, load_pages
from .pipeline import Config, Result, Summarizer, run_document
from .report import STATUS, render


def parse_page_range(spec: str | None, total: int) -> set[int]:
    if not spec:
        return set(range(1, total + 1))
    wanted: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            wanted.update(range(int(start), int(end) + 1))
        else:
            wanted.add(int(part))
    return {n for n in wanted if 1 <= n <= total}


def collect_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files += sorted(p for p in path.rglob("*") if p.suffix.lower() in SUPPORTED)
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"파일을 찾을 수 없습니다: {item}")
    return files


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atuo",
        description="문서를 한 페이지씩 요약하고, 페이지마다 2차 검증을 거친 뒤 다음 페이지로 넘어갑니다.",
    )
    p.add_argument("inputs", nargs="+", help="문서 파일 또는 폴더 (pdf, docx, txt, md)")
    p.add_argument("-o", "--out", default="atuo_out", help="결과 폴더 (기본: atuo_out)")
    p.add_argument("--backend", choices=["claude", "openai"], default="claude",
                   help="claude: Anthropic API / openai: OpenAI 호환 서버 (Ollama, vLLM, Hugging Face 등)")
    p.add_argument("--model", help="요약 모델 (claude 기본: claude-opus-5-5)")
    p.add_argument("--verify-model", help="검증에 쓸 모델 (기본: 요약 모델과 동일)")
    p.add_argument("--base-url", help="openai backend 의 서버 주소 (예: http://localhost:11434/v1)")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"],
                   help="claude backend 의 추론 강도")
    p.add_argument("--lang", default="ko", help="요약 언어 (기본: ko)")
    p.add_argument("--max-rounds", type=int, default=2, help="페이지당 최대 수정 횟수 (기본: 2)")
    p.add_argument("--strict", action="store_true", help="중요도 medium 누락도 수정 대상으로 처리")
    p.add_argument("--pages", help="처리할 페이지 (예: 1-10,15)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        llm = make_llm(args.backend, args.model, args.base_url, args.effort)
        verifier = (
            make_llm(args.backend, args.verify_model, args.base_url, args.effort)
            if args.verify_model
            else None
        )
    except (LLMError, ImportError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    config = Config(lang=args.lang, max_rounds=args.max_rounds, strict=args.strict)
    summarizer = Summarizer(llm, verifier, config)

    exit_code = 0
    for path in collect_files(args.inputs):
        print(f"\n== {path.name}")
        pages = load_pages(path)
        wanted = parse_page_range(args.pages, len(pages))
        pages = [p for p in pages if p.number in wanted]
        out_dir = Path(args.out) / path.stem
        fingerprint = json.dumps(
            [file_hash(path), args.backend, args.model, args.verify_model, asdict(config)]
        )

        def progress(result: Result, total: int) -> None:
            extra = f", 수정 {result.revisions}회" if result.revisions else ""
            print(f"  {result.label}/{total}: {STATUS[result.status]}{extra}", flush=True)

        try:
            results, overall = run_document(
                pages, summarizer, out_dir / "state.json", fingerprint, progress
            )
        except LLMError as exc:
            # Finished pages are saved in state.json; rerunning resumes from here.
            print(f"오류: {exc}\n  완료된 페이지는 저장됐습니다. 같은 명령을 다시 실행하면 이어서 진행합니다.",
                  file=sys.stderr)
            exit_code = 1
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "summary.md").write_text(render(path.name, results, overall), encoding="utf-8")
        (out_dir / "report.json").write_text(
            json.dumps(
                {"file": path.name, "overall": asdict(overall) if overall else None,
                 "pages": [asdict(r) for r in results]},
                ensure_ascii=False, indent=1,
            ),
            encoding="utf-8",
        )
        review = [r.label for r in results if r.status == "needs_review"]
        print(f"  -> {out_dir / 'summary.md'}")
        if review:
            print(f"  직접 확인 필요: {', '.join(review)}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
