"""DocPlan의 sheets를 서식·합계·그래프가 들어간 .xlsx로 만든다."""
from __future__ import annotations

import datetime as dt
import re

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from planner import DocPlan, Sheet

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
STRIPE_FILL = PatternFill("solid", fgColor="F3F7FB")
TOTAL_FILL = PatternFill("solid", fgColor="DDE7F1")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FORMATS = {"number": "#,##0.##", "currency": "#,##0", "percent": "0.0%", "date": "yyyy-mm-dd"}
NUMERIC = ("number", "currency", "percent")


def _convert(value: str, kind: str):
    """AI가 준 문자열을 진짜 숫자/날짜로 바꾼다. 못 바꾸면 글자 그대로 둔다."""
    text = (value or "").strip()
    if kind in NUMERIC:
        cleaned = re.sub(r"[,\s원₩%$]", "", text)
        try:
            number = float(cleaned)
        except ValueError:
            return text
        if kind == "percent":
            return number / 100
        return int(number) if number.is_integer() else number
    if kind == "date":
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return text
    return text


def _display_width(value) -> int:
    text = value.isoformat() if isinstance(value, dt.date) else str(value)
    if isinstance(value, (int, float)):
        text = f"{value:,}"
    # 한글은 영문보다 두 배쯤 넓다.
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Sheet"
    candidate, n = base, 2
    while candidate in used:
        suffix = f" ({n})"
        candidate = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(candidate)
    return candidate


def _write_sheet(ws, sheet: Sheet) -> None:
    ncols = len(sheet.columns)
    kinds = [c.kind for c in sheet.columns]

    for col, column in enumerate(sheet.columns, start=1):
        cell = ws.cell(row=1, column=col, value=column.name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER
    ws.row_dimensions[1].height = 22

    widths = [_display_width(c.name) for c in sheet.columns]
    for r, row in enumerate(sheet.rows, start=2):
        row = (list(row) + [""] * ncols)[:ncols]
        for col, raw in enumerate(row, start=1):
            kind = kinds[col - 1]
            value = _convert(raw, kind)
            cell = ws.cell(row=r, column=col, value=value)
            cell.border = BORDER
            if not isinstance(value, str):
                cell.number_format = FORMATS[kind]
            if kind == "date":
                cell.alignment = Alignment(horizontal="center")
            elif kind == "text":
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            if r % 2 == 1:
                cell.fill = STRIPE_FILL
            widths[col - 1] = max(widths[col - 1], _display_width(value))

    last_data_row = len(sheet.rows) + 1
    has_numeric = any(k in ("number", "currency") for k in kinds)
    if sheet.add_total_row and sheet.rows and has_numeric:
        total_row = last_data_row + 1
        for col, kind in enumerate(kinds, start=1):
            cell = ws.cell(row=total_row, column=col)
            cell.font = Font(bold=True)
            cell.fill = TOTAL_FILL
            cell.border = BORDER
            if kind in ("number", "currency"):
                letter = get_column_letter(col)
                cell.value = f"=SUM({letter}2:{letter}{last_data_row})"
                cell.number_format = FORMATS[kind]
                widths[col - 1] += 2  # 합계는 자릿수가 늘어난다
        if kinds[0] not in ("number", "currency"):
            ws.cell(row=total_row, column=1, value="합계").alignment = Alignment(
                horizontal="center"
            )

    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = min(max(width + 3, 9), 50)

    ws.freeze_panes = "A2"
    if sheet.rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{last_data_row}"
    ws.sheet_view.showGridLines = False

    _add_chart(ws, sheet, last_data_row)


def _add_chart(ws, sheet: Sheet, last_data_row: int) -> None:
    spec = sheet.chart
    if spec is None or not sheet.rows:
        return
    names = [c.name for c in sheet.columns]
    kinds = {c.name: c.kind for c in sheet.columns}
    if spec.category_column not in names:
        return
    value_cols = [v for v in spec.value_columns if v in names and kinds[v] in NUMERIC]
    if not value_cols:
        return
    if spec.kind == "pie":
        value_cols = value_cols[:1]

    chart = {"bar": BarChart, "line": LineChart, "pie": PieChart}[spec.kind]()
    chart.title = spec.title
    chart.height, chart.width = 9, 18
    for name in value_cols:
        col = names.index(name) + 1
        chart.add_data(
            Reference(ws, min_col=col, min_row=1, max_row=last_data_row), titles_from_data=True
        )
    cat_col = names.index(spec.category_column) + 1
    chart.set_categories(Reference(ws, min_col=cat_col, min_row=2, max_row=last_data_row))
    if spec.kind != "pie":
        chart.x_axis.title = spec.category_column
        # openpyxl 기본값은 축을 숨긴다. 축 눈금이 보이게 켠다.
        chart.x_axis.delete = False
        chart.y_axis.delete = False
        chart.y_axis.number_format = "#,##0"
        if len(value_cols) == 1:
            chart.legend = None
    # 표 오른쪽에 한 칸 띄워 배치
    ws.add_chart(chart, f"{get_column_letter(len(names) + 2)}2")


def write_xlsx(plan: DocPlan, path: str) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    for sheet in plan.sheets:
        if not sheet.columns:
            continue
        _write_sheet(wb.create_sheet(_safe_sheet_name(sheet.name, used)), sheet)
    if not wb.sheetnames:
        raise ValueError("엑셀로 만들 표가 없습니다.")
    wb.save(path)
    return path
