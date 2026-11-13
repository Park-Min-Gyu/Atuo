"""DocPlan을 한글(.hwpx) 문서로 만든다. 한컴오피스가 없어도 동작한다."""
from __future__ import annotations

from hwpx import HwpxDocument

from planner import DocPlan

HEADER_COLOR = "#DDE7F1"


class _Writer:
    def __init__(self) -> None:
        self.doc = HwpxDocument.new(inherit_style=False)
        styles = self.doc.styles
        self.title_style = styles.ensure_run(bold=True, size=20)
        self.heading_style = styles.ensure_run(bold=True, size=13)
        self.bold_style = styles.ensure_run(bold=True)
        self.muted_style = styles.ensure_run(color="#595959")
        self._first = True

    def para(self, text: str = "", *, style=None, align=None, before=None, after=None,
             indent=None):
        if self._first:
            # 새 문서의 첫 문단은 이미 있으므로 그 문단부터 채운다.
            self._first = False
            p = self.doc.paragraphs[0]
            p.text = text
            if style is not None:
                for run in p.runs:
                    run.char_pr_id_ref = style
        else:
            p = self.doc.add_paragraph(text, char_pr_id_ref=style)
        if any(v is not None for v in (align, before, after, indent)):
            self.doc.styles.apply_paragraph_format(
                paragraphs=[p],
                alignment=align,
                spacing_before_pt=before,
                spacing_after_pt=after,
                indent_left_mm=indent,
            )
        return p

    def heading(self, text: str) -> None:
        self.para(text, style=self.heading_style, before=14, after=4)

    def table(self, columns: list[str], rows: list[list[str]]) -> None:
        if not columns:
            return
        table = self.doc.add_table(len(rows) + 1, len(columns))
        for c, name in enumerate(columns):
            table.set_cell_text(0, c, name)
            table.set_cell_shading(0, c, HEADER_COLOR)
            for p in table.cell(0, c).paragraphs:
                for run in p.runs:
                    run.char_pr_id_ref = self.bold_style
                self.doc.styles.apply_paragraph_format(paragraphs=[p], alignment="center")
        for r, row in enumerate(rows, start=1):
            row = (list(row) + [""] * len(columns))[: len(columns)]
            for c, value in enumerate(row):
                table.set_cell_text(r, c, value or "")
        # 글자 수에 비례해 열 너비를 나눈다(한 열이 지나치게 좁거나 넓어지지 않게 제한).
        weights = []
        for c, name in enumerate(columns):
            longest = max([len(name)] + [len(row[c]) for row in rows if c < len(row)])
            weights.append(min(max(longest, 4), 30))
        table.set_column_widths(weights)
        self.para("")


def write_hwpx(plan: DocPlan, path: str) -> str:
    w = _Writer()

    w.para(plan.title, style=w.title_style, align="center", after=6)
    meta = [plan.doc_type, plan.date]
    if plan.author:
        meta.append(f"작성: {plan.author}")
    w.para("  |  ".join(m for m in meta if m), style=w.muted_style, align="center", after=10)

    if plan.summary:
        w.heading("요약")
        w.para(plan.summary)

    for number, section in enumerate(plan.sections, start=1):
        w.heading(f"{number}. {section.heading}")
        for text in section.paragraphs:
            w.para(text, after=3)
        for bullet in section.bullets:
            w.para(f"• {bullet}", indent=4)
        if section.table and section.table.columns:
            w.para("")
            w.table(section.table.columns, section.table.rows)

    if plan.action_items:
        w.heading("할 일 및 담당")
        w.table(
            ["할 일", "담당자", "기한", "상태"],
            [[a.task, a.owner, a.due, a.status] for a in plan.action_items],
        )

    if plan.missing_info:
        w.heading("확인이 필요한 내용")
        for item in plan.missing_info:
            w.para(f"□ {item}", style=w.muted_style, indent=4)

    w.doc.page.set_page_number()
    w.doc.save_to_path(path)
    return path
