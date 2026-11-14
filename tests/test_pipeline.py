import json

from atuo import checks
from atuo.cli import main, parse_page_range
from atuo.llm import parse_json_loosely
from atuo.loader import Page, chunk_paragraphs, load_pages
from atuo.pipeline import Config, Summarizer, run_document
from atuo.report import render

SOURCE = (
    "2026년 3분기 매출은 1,500,000원으로 전년 대비 42% 증가했다. "
    "계약은 2026년 12월 31일에 종료되며, 종료 30일 전까지 서면 통지가 없으면 자동 연장된다."
)
DRAFT = "3분기 매출이 증가했다."
FIXED = "3분기 매출은 1,500,000원으로 42% 증가했다. 종료 30일 전까지 서면 통지가 없으면 자동 연장된다."


class FakeLLM:
    """Drafts an incomplete summary, then fixes it once verification complains."""

    def __init__(self, verdicts, drafts=(DRAFT, FIXED)):
        self.verdicts = list(verdicts)
        self.drafts = list(drafts)
        self.calls = []

    def complete(self, system, user):
        self.calls.append("revise" if "검증_지적사항" in user else "draft")
        return self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]

    def complete_json(self, system, user, schema):
        self.calls.append("verify")
        return self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]


MISSING = {
    "missing": [
        {"point": "자동 연장 조건 누락", "quote": "종료 30일 전까지 서면 통지가 없으면 자동 연장된다", "importance": "high"}
    ],
    "unsupported": [],
}
CLEAN = {"missing": [], "unsupported": []}


def test_omission_is_caught_and_fixed():
    llm = FakeLLM([MISSING, CLEAN])
    result = Summarizer(llm).summarize_page(Page(1, SOURCE))
    assert result.status == "verified"
    assert result.revisions == 1
    assert "자동 연장" in result.summary
    assert llm.calls == ["draft", "verify", "revise", "verify"]


def test_unresolved_issue_is_reported_not_hidden():
    llm = FakeLLM([MISSING], drafts=[DRAFT])  # the reviser never fixes it
    result = Summarizer(llm, config=Config(max_rounds=2)).summarize_page(Page(1, SOURCE))
    assert result.status == "needs_review"
    assert result.revisions == 2
    text = render("doc.pdf", [result], None)
    assert "직접 확인 필요" in text and "자동 연장 조건 누락" in text


def test_verifier_claims_without_real_quote_are_discarded():
    made_up = {
        "missing": [{"point": "없는 내용", "quote": "원문에 없는 문장이다", "importance": "high"}],
        "unsupported": [{"claim": "요약에 없는 구절", "reason": "x"}],
    }
    result = Summarizer(FakeLLM([made_up], drafts=[FIXED])).summarize_page(Page(1, SOURCE))
    assert result.status == "verified"
    assert result.remaining.discarded == 2


def test_wrong_number_fails_even_if_verifier_misses_it():
    wrong = "3분기 매출은 1,500,000원으로 24% 증가했다."
    llm = FakeLLM([CLEAN], drafts=[wrong, FIXED])
    result = Summarizer(llm).summarize_page(Page(1, SOURCE))
    assert result.revisions == 1 and result.status == "verified"
    assert checks.unsupported_numbers(SOURCE, wrong) == ["24"]
    assert checks.unsupported_numbers(SOURCE, FIXED) == []


def test_medium_issue_blocks_only_in_strict_mode():
    medium = {"missing": [{"point": "기준 연도", "quote": "전년 대비", "importance": "medium"}], "unsupported": []}
    loose = Summarizer(FakeLLM([medium], drafts=[DRAFT])).summarize_page(Page(1, SOURCE))
    assert loose.status == "verified" and len(loose.remaining.missing) == 1
    strict = Summarizer(FakeLLM([medium], drafts=[DRAFT]), config=Config(strict=True, max_rounds=1))
    assert strict.summarize_page(Page(1, SOURCE)).status == "needs_review"


def test_blank_page_is_skipped_and_flagged():
    llm = FakeLLM([CLEAN], drafts=[FIXED])
    results, overall = run_document([Page(1, SOURCE), Page(2, "")], Summarizer(llm))
    assert [r.status for r in results] == ["verified", "skipped"]
    assert overall is not None and overall.label == "전체"
    assert "추출된 텍스트 없음" in render("doc.pdf", results, overall)


def test_resume_skips_finished_pages(tmp_path):
    state = tmp_path / "state.json"
    pages = [Page(1, SOURCE), Page(2, SOURCE)]
    run_document(pages, Summarizer(FakeLLM([CLEAN], drafts=[FIXED])), state, "fp")
    assert set(json.loads(state.read_text(encoding="utf-8"))["pages"]) == {"1", "2"}

    second = FakeLLM([CLEAN], drafts=[FIXED])
    results, _ = run_document(pages, Summarizer(second), state, "fp")
    assert second.calls == ["draft", "verify"]  # only the overall summary is redone
    assert [r.status for r in results] == ["verified", "verified"]

    third = FakeLLM([CLEAN], drafts=[FIXED])
    run_document(pages, Summarizer(third), state, "changed")  # different file or settings
    assert third.calls.count("draft") == 3


def test_loader_keeps_all_text(tmp_path):
    paragraphs = [f"문단 {i} " + "가" * 500 for i in range(20)]
    pages = chunk_paragraphs(paragraphs, limit=1200)
    assert len(pages) > 1
    assert "".join(pages).replace("\n", "") == "".join(paragraphs)
    assert len(chunk_paragraphs(["나" * 5000], limit=1000)) == 5

    f = tmp_path / "a.txt"
    f.write_text("첫 페이지\f둘째 페이지", encoding="utf-8")
    assert [p.text for p in load_pages(f)] == ["첫 페이지", "둘째 페이지"]


def test_helpers():
    assert parse_page_range("1-3,5", 4) == {1, 2, 3}
    assert parse_page_range(None, 2) == {1, 2}
    assert parse_json_loosely('결과입니다:\n```json\n{"missing": [], "unsupported": []}\n```') == CLEAN
    assert checks.contains("서면 통지가\n없으면", "서면 통지가 없으면")


def test_cli_end_to_end(tmp_path, monkeypatch):
    doc = tmp_path / "doc.txt"
    doc.write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr("atuo.cli.make_llm", lambda *a, **k: FakeLLM([MISSING, CLEAN]))
    assert main([str(doc), "-o", str(tmp_path / "out")]) == 0
    text = (tmp_path / "out" / "doc" / "summary.md").read_text(encoding="utf-8")
    assert "## 전체 요약" in text and "p.1 (검증 통과, 수정 1회)" in text
