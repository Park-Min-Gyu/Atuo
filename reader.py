"""올린 엑셀/CSV 파일을 AI가 읽을 수 있는 글자로 바꾼다."""
from __future__ import annotations

import csv
import io

from openpyxl import load_workbook

MAX_CELLS = 20000  # 이보다 크면 AI가 한 번에 다시 써 주기 어렵다


class ReadError(Exception):
    pass


def _rows_to_text(rows) -> tuple[str, int]:
    lines, cells = [], 0
    for row in rows:
        values = ["" if v is None else str(v).strip() for v in row]
        while values and values[-1] == "":
            values.pop()
        if not values:
            continue
        cells += len(values)
        lines.append("\t".join(v.replace("\t", " ").replace("\n", " ") for v in values))
    return "\n".join(lines), cells


def read_table_file(filename: str, data: bytes) -> str:
    name = filename.lower()
    chunks, total = [], 0
    if name.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        except Exception:
            raise ReadError("엑셀 파일을 열 수 없습니다. 파일이 손상되지 않았는지 확인해 주세요.")
        for ws in wb.worksheets:
            text, cells = _rows_to_text(ws.iter_rows(values_only=True))
            if text:
                chunks.append(f"[시트: {ws.title}]\n{text}")
                total += cells
    elif name.endswith((".csv", ".tsv", ".txt")):
        for encoding in ("utf-8-sig", "cp949"):
            try:
                decoded = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ReadError("파일의 글자 인코딩을 알 수 없습니다. UTF-8 또는 엑셀 파일로 저장해 주세요.")
        delimiter = "\t" if name.endswith(".tsv") else ","
        text, total = _rows_to_text(csv.reader(io.StringIO(decoded), delimiter=delimiter))
        if text:
            chunks.append(text)
    else:
        raise ReadError("xlsx, csv 파일만 올릴 수 있습니다. (.xls는 엑셀에서 .xlsx로 다시 저장해 주세요)")

    if not chunks:
        raise ReadError("파일에 내용이 없습니다.")
    if total > MAX_CELLS:
        raise ReadError(
            f"파일이 너무 큽니다(칸 {total:,}개, 최대 {MAX_CELLS:,}개). 필요한 시트나 범위만 남겨서 올려 주세요."
        )
    return "\n\n".join(chunks)
