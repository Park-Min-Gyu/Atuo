"""Render results as a Markdown report."""

from __future__ import annotations

from .pipeline import Result

STATUS = {"verified": "검증 통과", "needs_review": "직접 확인 필요", "skipped": "텍스트 없음"}


def _remaining(result: Result) -> list[str]:
    lines = []
    for m in result.remaining.missing:
        lines.append(f"> - 누락({m['importance']}): {m['point']} — 원문: \"{m['quote']}\"")
    for u in result.remaining.unsupported:
        lines.append(f"> - 근거 없음: \"{u['claim']}\" — {u['reason']}")
    for n in result.remaining.bad_numbers:
        lines.append(f"> - 숫자 {n} 이(가) 원문에서 확인되지 않음")
    return lines


def render(name: str, results: list[Result], overall: Result | None) -> str:
    verified = sum(r.status == "verified" for r in results)
    review = [r.label for r in results if r.status == "needs_review"]
    skipped = [r.label for r in results if r.status == "skipped"]
    caught = sum(r.found for r in results)

    out = [f"# {name} 요약", ""]
    out.append(f"- 페이지: {len(results)}개 중 {verified}개 검증 통과")
    out.append(f"- 2차 검증이 잡아낸 문제: {caught}건 (수정 반영 후 재검증)")
    if review:
        out.append(f"- **직접 확인 필요**: {', '.join(review)} (아래 해당 페이지에 남은 문제 표시)")
    if skipped:
        out.append(f"- **추출된 텍스트 없음**: {', '.join(skipped)} (스캔 이미지일 수 있음, 요약에 포함되지 않음)")
    out.append("")

    if overall:
        out += ["## 전체 요약", "", overall.summary, ""]
        if overall.status != "verified":
            out += ["> 전체 요약이 검증을 완전히 통과하지 못했습니다. 페이지별 요약을 기준으로 보세요."]
            out += _remaining(overall) + [""]

    out += ["## 페이지별 요약", ""]
    for r in results:
        note = STATUS[r.status]
        if r.revisions:
            note += f", 수정 {r.revisions}회"
        out += [f"### {r.label} ({note})", ""]
        if r.summary:
            out += [r.summary, ""]
        remaining = _remaining(r)
        if remaining:
            heading = "> 검증에서 해결되지 않은 문제:" if r.status == "needs_review" else "> 참고 (중요도 낮음):"
            out += [heading] + remaining + [""]
    return "\n".join(out)
