"""The summarize -> verify -> revise loop, one page at a time."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import checks, prompts
from .llm import LLM
from .loader import Page

MIN_PAGE_CHARS = 30  # below this a page is treated as having no text
CONTEXT_CHARS = 1200  # how much of the previous summary is carried forward
REDUCE_CHARS = 40000  # page summaries are merged in groups of this size


@dataclass
class Issues:
    missing: list[dict] = field(default_factory=list)
    unsupported: list[dict] = field(default_factory=list)
    bad_numbers: list[str] = field(default_factory=list)
    discarded: int = 0  # verifier claims dropped because their quote was not real

    def blocking(self, strict: bool) -> bool:
        missing = [m for m in self.missing if strict or m["importance"] == "high"]
        return bool(missing or self.unsupported or self.bad_numbers)

    def as_text(self) -> str:
        lines = []
        for m in self.missing:
            lines.append(f"- [누락/{m['importance']}] {m['point']} (원문: \"{m['quote']}\")")
        for u in self.unsupported:
            lines.append(f"- [근거 없음] \"{u['claim']}\": {u['reason']}")
        for n in self.bad_numbers:
            lines.append(f"- [숫자 불일치] 요약의 숫자 {n} 이(가) 원문에 없습니다. 원문 표기 그대로 쓰세요.")
        return "\n".join(lines)


@dataclass
class Result:
    label: str
    summary: str
    status: str  # verified | needs_review | skipped
    revisions: int = 0
    remaining: Issues = field(default_factory=Issues)
    found: int = 0  # issues the verification caught across all rounds


@dataclass
class Config:
    lang: str = "ko"
    max_rounds: int = 2
    strict: bool = False


class Summarizer:
    def __init__(self, llm: LLM, verifier: LLM | None = None, config: Config | None = None):
        self.llm = llm
        self.verifier = verifier or llm
        self.config = config or Config()

    def verify(self, source: str, summary: str, label: str) -> Issues:
        raw = self.verifier.complete_json(
            prompts.verifier_system(self.config.strict),
            prompts.verify_user(source, summary, label),
            prompts.VERIFY_SCHEMA,
        )
        issues = Issues()
        for item in raw.get("missing") or []:
            # The verifier has to prove the omission with a real quote, and the
            # point must not simply be something the summary already says.
            quote = str(item.get("quote", ""))
            if checks.contains(source, quote) and not checks.contains(summary, quote):
                importance = item.get("importance")
                issues.missing.append(
                    {
                        "point": str(item.get("point", "")),
                        "quote": quote,
                        "importance": importance if importance in ("high", "medium") else "medium",
                    }
                )
            else:
                issues.discarded += 1
        for item in raw.get("unsupported") or []:
            claim = str(item.get("claim", ""))
            if checks.contains(summary, claim):
                issues.unsupported.append({"claim": claim, "reason": str(item.get("reason", ""))})
            else:
                issues.discarded += 1
        issues.bad_numbers = checks.unsupported_numbers(source, summary)
        return issues

    def verified_summary(self, system: str, user: str, source: str, label: str) -> Result:
        """Draft a summary, then verify and revise until it passes."""
        summary = self.llm.complete(system, user)
        revisions = 0
        found = 0
        while True:
            issues = self.verify(source, summary, label)
            found += len(issues.missing) + len(issues.unsupported) + len(issues.bad_numbers)
            if not issues.blocking(self.config.strict):
                return Result(label, summary, "verified", revisions, issues, found)
            if revisions >= self.config.max_rounds:
                return Result(label, summary, "needs_review", revisions, issues, found)
            summary = self.llm.complete(
                system, prompts.revise_user(source, summary, issues.as_text(), label)
            )
            revisions += 1

    def summarize_page(self, page: Page, context: str = "") -> Result:
        label = f"p.{page.number}"
        if len(page.text) < MIN_PAGE_CHARS:
            return Result(label, "", "skipped")
        return self.verified_summary(
            prompts.summarizer_system(self.config.lang),
            prompts.summarize_user(page.text, label, context),
            page.text,
            label,
        )

    def overall(self, results: list[Result]) -> Result | None:
        """Merge page summaries into one document summary, verified the same way."""
        items = [(r.label, r.summary) for r in results if r.summary]
        if not items:
            return None
        while True:
            groups: list[list[tuple[str, str]]] = [[]]
            size = 0
            for label, text in items:
                if groups[-1] and size + len(text) > REDUCE_CHARS:
                    groups.append([])
                    size = 0
                groups[-1].append((label, text))
                size += len(text)
            merged = []
            for group in groups:
                joined = "\n\n".join(f"[{label}]\n{text}" for label, text in group)
                label = group[0][0] if len(group) == 1 else f"{group[0][0]}~{group[-1][0]}"
                result = self.verified_summary(
                    prompts.overall_system(self.config.lang),
                    prompts.overall_user(joined),
                    joined,
                    label,
                )
                merged.append(result)
            if len(merged) == 1:
                merged[0].label = "전체"
                return merged[0]
            if len(merged) >= len(items):  # cannot shrink further; stop rather than loop
                merged[0].label = "전체"
                merged[0].summary = "\n\n".join(m.summary for m in merged)
                merged[0].status = "needs_review"
                return merged[0]
            items = [(m.label, m.summary) for m in merged]


def _result_from_dict(data: dict) -> Result:
    data = dict(data)
    data["remaining"] = Issues(**data.get("remaining", {}))
    return Result(**data)


def run_document(
    pages: list[Page],
    summarizer: Summarizer,
    state_path: Path | None = None,
    fingerprint: str = "",
    progress: Callable[[Result, int], None] | None = None,
) -> tuple[list[Result], Result | None]:
    """Process every page in order, saving after each one so a run can resume."""
    done: dict[str, dict] = {}
    if state_path and state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("fingerprint") == fingerprint:
            done = state.get("pages", {})

    results: list[Result] = []
    context = ""
    for page in pages:
        key = str(page.number)
        if key in done:
            result = _result_from_dict(done[key])
        else:
            result = summarizer.summarize_page(page, context)
            done[key] = asdict(result)
            if state_path:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps({"fingerprint": fingerprint, "pages": done}, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
        results.append(result)
        if result.summary:
            context = f"[{result.label}] {result.summary[-CONTEXT_CHARS:]}"
        if progress:
            progress(result, len(pages))
    return results, summarizer.overall(results)
