#!/usr/bin/env python3
"""PDF 자동 번역기: PDF -> 번역본 PDF + 요약본(PDF/Markdown) + 검증 리포트.

사용법:
    python autotrans.py 문서.pdf              # 한국어로 번역
    python autotrans.py 폴더/ --to 한국어     # 폴더 안의 PDF 전부
    python autotrans.py a.pdf b.pdf -o 결과폴더

원본 레이아웃(그림, 표, 선)은 그대로 두고 글자 블록만 번역문으로 교체한다.
모든 텍스트 블록에 번호를 붙여 번역하고, 빠진 번호가 있으면 다시 번역시켜
누락을 막는다.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import markdown
import pymupdf

MODEL = "claude-opus-5-5"
BATCH_CHARS = 6000  # 한 번의 요청에 넣는 원문 분량
MAX_ATTEMPTS = 3  # 누락/의심 구간 재시도 횟수
WORKERS = 4

TRANSLATE_SYSTEM = """당신은 전문 번역가입니다. 문서의 조각들을 {target}(으)로 번역합니다.

입력은 <seg id="번호">원문</seg> 형태의 조각들입니다. PDF에서 뽑은 텍스트 블록이라 \
제목, 본문 문단, 표의 칸, 각주, 캡션 등이 섞여 있습니다.

규칙:
- 모든 조각을 하나도 빠짐없이, 같은 id로 <seg id="번호">번역문</seg> 형태로 출력합니다.
- 완역합니다. 문장, 절, 괄호 안 내용, 목록 항목, 숫자, 단위를 생략하거나 요약하지 않습니다. \
이 번역은 원문을 대신해 읽히므로 원문에 있는 정보는 전부 번역문에 있어야 합니다.
- 조각을 합치거나 나누지 않습니다. 문장이 조각 중간에서 끊겨 있으면 끊긴 그대로 각 조각에 \
나누어 자연스럽게 번역합니다.
- 고유명사, 제품명, 코드, 수식, URL, 이메일은 원문 그대로 둡니다. 처음 나오는 전문 용어는 \
번역어 뒤에 괄호로 원어를 병기해도 좋습니다.
- 이미 {target}(으)로 쓰인 조각이나 번역할 것이 없는 조각은 원문 그대로 출력합니다.
- <context> 안의 내용은 앞부분 문맥 참고용이며 번역해서 출력하지 않습니다.
- seg 태그 외의 설명이나 주석은 출력하지 않습니다."""

OCR_SYSTEM = """당신은 전문 번역가입니다. 스캔된 문서 페이지 이미지를 받아, 페이지에 보이는 \
모든 글을 {target}(으)로 완역합니다. 제목, 본문, 표, 각주, 도장이나 손글씨까지 읽을 수 있는 \
것은 빠짐없이 옮기고 요약하지 않습니다. 읽을 수 없는 부분은 [판독 불가]로 표시합니다. \
원문의 구조(제목, 목록, 표)를 Markdown으로 살려서 번역문만 출력합니다."""

SUMMARY_SYSTEM = """당신은 문서를 읽고 바쁜 사람을 위해 요약하는 편집자입니다. 주어진 문서를 \
{target}(으)로 요약합니다. 읽는 사람은 원문을 보지 않고 이 요약만으로 문서가 무엇이고 \
무엇을 해야 하는지 판단합니다.

Markdown으로 다음 순서로 작성합니다:
# (문서 제목)
## 한 줄 요약
## 핵심 내용  (가장 중요한 것부터 5~10개 항목)
## 섹션별 요약  (문서의 흐름을 따라 섹션마다 2~4문장)
## 중요한 숫자·날짜·조건  (금액, 기한, 수치, 의무사항 등. 없으면 이 섹션 생략)
## 확인이 필요한 사항  (읽는 사람이 결정하거나 조치해야 할 것. 없으면 생략)

문서에 없는 내용은 쓰지 않습니다. 숫자와 고유명사는 문서에 적힌 그대로 옮깁니다."""


# --------------------------------------------------------------------------
# Claude 호출
# --------------------------------------------------------------------------
class Claude:
    def __init__(self, model: str = MODEL):
        import anthropic

        self.client = anthropic.Anthropic(max_retries=5)
        self.model = model

    def ask(self, system: str, content, max_tokens: int = 64000) -> str:
        """텍스트 응답을 돌려준다. 거절되면 빈 문자열."""
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=system,
            messages=[{"role": "user", "content": content}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in msg.content if b.type == "text")


class MockClaude:
    """API 키 없이 파이프라인만 점검할 때 쓰는 가짜 번역기 (AUTOTRANS_MOCK=1)."""

    def ask(self, system: str, content, max_tokens: int = 0) -> str:
        if not isinstance(content, str):
            return "## (모의) 스캔 페이지 번역\n\n스캔된 페이지의 번역문이 여기에 들어갑니다."
        if "<seg id=" not in content:
            return "# (모의) 요약\n\n## 한 줄 요약\n\n모의 요약입니다.\n\n## 핵심 내용\n\n- 항목 1\n- 항목 2"
        body = content.split("</context>")[-1]
        segs = re.findall(r'<seg id="(\d+)">(.*?)</seg>', body, re.S)
        # 누락 재시도 검증용: 첫 시도에서는 id가 7의 배수인 조각을 일부러 빼먹는다.
        first_try = len(segs) > 1
        return "\n".join(
            f'<seg id="{i}">[번역] {t}</seg>'
            for i, t in segs
            if not (first_try and int(i) % 7 == 0)
        )


# --------------------------------------------------------------------------
# 추출
# --------------------------------------------------------------------------
@dataclass
class Segment:
    id: int
    page: int
    bbox: tuple
    text: str
    size: float
    color: int
    bold: bool
    rotate: int
    translation: str | None = None


@dataclass
class Report:
    segments: int = 0
    retried: int = 0
    failed: list = field(default_factory=list)  # 끝내 번역 못 한 조각
    shrunk: list = field(default_factory=list)  # 글자가 많이 줄어든 조각
    scanned_pages: list = field(default_factory=list)
    empty_pages: list = field(default_factory=list)


_CJK = re.compile(r"[　-ヿ㐀-鿿＀-￯]")


def _join_lines(lines: list[str]) -> str:
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif _CJK.match(out[-1]) and _CJK.match(line[0]):
            out += line  # 일본어/중국어는 줄 사이에 공백을 넣지 않는다
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and line[0].islower():
            out = out[:-1] + line  # 영어 줄끝 하이픈 연결
        else:
            out += " " + line
    return out


def extract_segments(doc: pymupdf.Document, report: Report) -> list[Segment]:
    segs: list[Segment] = []
    for pno, page in enumerate(doc):
        blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)["blocks"]
        found = False
        for b in blocks:
            if b["type"] != 0:
                continue
            spans = [s for ln in b["lines"] for s in ln["spans"] if s["text"].strip()]
            text = _join_lines(["".join(s["text"] for s in ln["spans"]) for ln in b["lines"]])
            if not spans or not text:
                continue
            found = True
            if not any(ch.isalpha() for ch in text):
                continue  # 쪽번호, 숫자, 기호뿐인 블록은 그대로 둔다
            main = max(spans, key=lambda s: len(s["text"]))
            dx, dy = b["lines"][0]["dir"]
            rotate = round(math.degrees(math.atan2(-dy, dx)) / 90) * 90 % 360
            segs.append(
                Segment(
                    id=len(segs) + 1,
                    page=pno,
                    bbox=tuple(b["bbox"]),
                    text=text,
                    size=main["size"],
                    color=main["color"],
                    bold=bool(main["flags"] & 16),
                    rotate=rotate,
                )
            )
        if not found:
            (report.scanned_pages if page.get_images() else report.empty_pages).append(pno)
    report.segments = len(segs)
    return segs


# --------------------------------------------------------------------------
# 번역 + 누락 검증
# --------------------------------------------------------------------------
def _batches(segs: list[Segment], limit: int = BATCH_CHARS) -> list[list[Segment]]:
    out, cur, n = [], [], 0
    for s in segs:
        if cur and n + len(s.text) > limit:
            out.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += len(s.text)
    if cur:
        out.append(cur)
    return out


def _suspicious(src: str, dst: str) -> bool:
    """번역문이 원문에 비해 지나치게 짧으면 내용을 건너뛴 것으로 의심한다."""
    if not dst.strip():
        return True
    return len(src) >= 200 and len(dst) < len(src) * 0.2


def _translate_batch(llm, batch: list[Segment], context: str, target: str) -> dict[int, str]:
    body = "\n".join(f'<seg id="{s.id}">{html.escape(s.text, quote=False)}</seg>' for s in batch)
    ctx = f"<context>\n{html.escape(context, quote=False)}\n</context>\n\n" if context else ""
    reply = llm.ask(TRANSLATE_SYSTEM.format(target=target), ctx + body)
    found = re.findall(r'<seg id="(\d+)">(.*?)</seg>', reply, re.S)
    return {int(i): html.unescape(t).strip() for i, t in found}


def translate_segments(llm, segs: list[Segment], target: str, report: Report, log) -> None:
    by_id = {s.id: s for s in segs}

    def context_for(batch: list[Segment]) -> str:
        first = batch[0].id
        prev = [by_id[i].text for i in range(max(1, first - 4), first)]
        return "\n".join(prev)[-1500:]

    pending = segs
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not pending:
            break
        # 마지막 시도에서는 조각을 하나씩 따로 보내 확실하게 번역시킨다.
        batches = [[s] for s in pending] if attempt == MAX_ATTEMPTS else _batches(pending)
        log(f"  번역 {attempt}차: 조각 {len(pending)}개 / 요청 {len(batches)}건")
        with ThreadPoolExecutor(WORKERS) as pool:
            results = list(pool.map(lambda b: _translate_batch(llm, b, context_for(b), target), batches))
        for batch, result in zip(batches, results):
            for s in batch:
                t = result.get(s.id, "")
                if not _suspicious(s.text, t):
                    s.translation = t
                elif t and attempt == MAX_ATTEMPTS:
                    s.translation = t  # 짧지만 번역은 있음: 채택하고 리포트에 남긴다
                    report.shrunk.append(s)
        pending = [s for s in pending if s.translation is None]
        if attempt == 1:
            report.retried = len(pending)
    report.failed = pending


# --------------------------------------------------------------------------
# PDF 만들기
# --------------------------------------------------------------------------
PAGE_CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.55; color: #111; }
h1 { font-size: 18pt; margin: 0 0 10pt; } h2 { font-size: 13pt; margin: 14pt 0 5pt; }
h3 { font-size: 11pt; margin: 10pt 0 4pt; } p { margin: 0 0 6pt; } li { margin-bottom: 3pt; }
table { border-collapse: collapse; } td, th { border: 0.5pt solid #888; padding: 3pt 5pt; }
.note { color: #666; font-size: 9pt; }
"""


def markdown_to_pdf(md: str, note: str = "") -> pymupdf.Document:
    """Markdown을 A4 여러 쪽짜리 PDF로 조판한다."""
    body = markdown.markdown(md, extensions=["tables", "sane_lists"])
    if note:
        body = f'<p class="note">{html.escape(note)}</p>' + body
    story = pymupdf.Story(html=body, user_css=PAGE_CSS)
    media = pymupdf.paper_rect("a4")
    where = media + (54, 54, -54, -54)
    stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(stream)
    more = True
    while more:
        dev = writer.begin_page(media)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", stream.getvalue())


def _box_for(seg: Segment, page: pymupdf.Page) -> pymupdf.Rect:
    """번역문이 들어갈 영역. 한 줄짜리 블록은 여유 공간까지 조금 넓혀 준다."""
    r = pymupdf.Rect(seg.bbox)
    if seg.rotate == 0:
        r.y1 += seg.size * 0.25
        r.x1 += min(seg.size * 2, max(0, page.rect.x1 - 12 - r.x1))
    return r


def write_translated_pdf(doc: pymupdf.Document, segs: list[Segment], out: Path) -> list[Segment]:
    """원본 글자를 지우고 같은 자리에 번역문을 넣는다. 많이 축소된 조각을 돌려준다."""
    tiny = []
    by_page: dict[int, list[Segment]] = {}
    for s in segs:
        if s.translation and s.translation != s.text:
            by_page.setdefault(s.page, []).append(s)
    for pno, items in by_page.items():
        page = doc[pno]
        for s in items:
            page.add_redact_annot(pymupdf.Rect(s.bbox), fill=False)
        # 그림과 선은 건드리지 않고 글자만 지운다
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        for s in items:
            rgb = f"#{s.color:06x}"
            weight = "bold" if s.bold else "normal"
            text = html.escape(s.translation).replace("\n", "<br>")
            snippet = (
                f'<div style="font-family:sans-serif;font-size:{s.size:.1f}pt;line-height:1.25;'
                f'color:{rgb};font-weight:{weight};margin:0">{text}</div>'
            )
            _, scale = page.insert_htmlbox(_box_for(s, page), snippet, rotate=s.rotate, scale_low=0)
            if scale < 0.6:
                tiny.append(s)
    doc.subset_fonts()
    doc.save(out, garbage=3, deflate=True)
    return tiny


def translate_scanned_pages(llm, doc: pymupdf.Document, pages: list[int], target: str, log) -> dict[int, str]:
    """글자 정보가 없는 스캔 페이지는 이미지를 읽혀서 번역한다."""
    out = {}
    for pno in pages:
        log(f"  스캔 페이지 {pno + 1}쪽을 이미지로 읽는 중")
        png = doc[pno].get_pixmap(dpi=150).tobytes("png")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}},
            {"type": "text", "text": f"이 페이지를 {target}(으)로 완역해 주세요."},
        ]
        out[pno] = llm.ask(OCR_SYSTEM.format(target=target), content)
    return out


# --------------------------------------------------------------------------
# 한 파일 처리
# --------------------------------------------------------------------------
def process(pdf: Path, outdir: Path, target: str, llm, log=print) -> Report:
    log(f"\n[{pdf.name}]")
    doc = pymupdf.open(pdf)
    report = Report()
    segs = extract_segments(doc, report)
    log(f"  {doc.page_count}쪽, 텍스트 조각 {len(segs)}개, 스캔 페이지 {len(report.scanned_pages)}쪽")

    translate_segments(llm, segs, target, report, log)
    scanned = translate_scanned_pages(llm, doc, report.scanned_pages, target, log)

    # 요약에 쓸 번역 전문 (쪽 순서대로)
    pages_text = []
    for pno in range(doc.page_count):
        parts = [s.translation or s.text for s in segs if s.page == pno]
        if pno in scanned:
            parts.append(scanned[pno])
        if parts:
            pages_text.append(f"[{pno + 1}쪽]\n" + "\n".join(parts))
    full_text = "\n\n".join(pages_text)

    outdir.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem
    translated_path = outdir / f"{stem}.번역본.pdf"

    # 스캔 페이지 번역문은 해당 쪽 바로 뒤에 새 쪽으로 끼워 넣는다 (뒤에서부터 넣어야 쪽번호가 안 밀림)
    tiny = write_translated_pdf(doc, segs, translated_path)
    if scanned:
        doc = pymupdf.open(translated_path)
        for pno in sorted(scanned, reverse=True):
            if scanned[pno].strip():
                extra = markdown_to_pdf(scanned[pno], note=f"원본 {pno + 1}쪽(스캔 이미지)의 번역")
                doc.insert_pdf(extra, start_at=pno + 1)
            else:
                report.failed.append(Segment(0, pno, (), "(스캔 페이지 전체)", 0, 0, False, 0))
        doc.save(translated_path, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    log(f"  번역본: {translated_path}")

    if full_text.strip():
        log("  요약 작성 중")
        summary = llm.ask(SUMMARY_SYSTEM.format(target=target), full_text, max_tokens=16000)
        (outdir / f"{stem}.요약.md").write_text(summary, encoding="utf-8")
        markdown_to_pdf(summary).save(outdir / f"{stem}.요약.pdf")
        log(f"  요약본: {outdir / (stem + '.요약.pdf')}")

    write_report(outdir / f"{stem}.검증.md", pdf, doc.page_count, report, tiny)
    ok = "누락 없음" if not report.failed else f"번역 실패 {len(report.failed)}건 (검증 파일 확인)"
    log(f"  검증: 조각 {report.segments}개 중 {report.segments - len([f for f in report.failed if f.id])}개 번역, {ok}")
    return report


def write_report(path: Path, pdf: Path, pages: int, r: Report, tiny: list[Segment]) -> None:
    def rows(items):
        return "\n".join(f"- {s.page + 1}쪽: {s.text[:80]}" for s in items) or "- 없음"

    path.write_text(
        f"""# 번역 검증: {pdf.name}

- 전체 텍스트 조각: {r.segments}개
- 1차 번역에서 빠지거나 의심되어 다시 번역한 조각: {r.retried}개
- 스캔 이미지로 읽은 페이지: {", ".join(str(p + 1) for p in r.scanned_pages) or "없음"}
- 글자도 그림도 없는 빈 페이지: {", ".join(str(p + 1) for p in r.empty_pages) or "없음"}

## 끝내 번역하지 못한 조각 (원문 그대로 남아 있음)
{rows(r.failed)}

## 번역문이 원문보다 눈에 띄게 짧은 조각 (내용 생략 여부 확인 권장)
{rows(r.shrunk)}

## 자리가 좁아 글자가 많이 작아진 조각
{rows(tiny)}
""",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 자동 번역 + 요약")
    ap.add_argument("inputs", nargs="+", type=Path, help="PDF 파일 또는 PDF가 든 폴더")
    ap.add_argument("--to", default="한국어", help="번역할 언어 (기본: 한국어)")
    ap.add_argument("-o", "--out", type=Path, default=Path("output"), help="결과 폴더 (기본: output)")
    ap.add_argument("--model", default=MODEL)
    args = ap.parse_args()

    pdfs: list[Path] = []
    for p in args.inputs:
        pdfs += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    if not pdfs:
        print("PDF 파일을 찾지 못했습니다.", file=sys.stderr)
        return 1

    llm = MockClaude() if os.environ.get("AUTOTRANS_MOCK") else Claude(args.model)
    failures = 0
    for pdf in pdfs:
        try:
            failures += bool(process(pdf, args.out, args.to, llm).failed)
        except Exception as e:  # 한 파일이 실패해도 나머지는 계속 처리
            failures += 1
            print(f"  실패: {pdf.name}: {e}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
