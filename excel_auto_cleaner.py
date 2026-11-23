import io
import math
import re
import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# ============================================================
# 1. 실무 보고서용 기본 스타일 (과한 AI 원색 테마 배제)
# ============================================================
FONT_NAME = "맑은 고딕"
COLOR_TEXT = "1F1F1F"
COLOR_SUBTEXT = "595959"
COLOR_HEADER_BG = "F2F2F2"     # 표 헤더 연회색
COLOR_BORDER_DARK = "404040"   # 외곽선 / 헤더 하단
COLOR_BORDER_LIGHT = "D0D0D0"  # 내부 데이터 얇은 선

SIDE_DARK_MED = Side(style="medium", color=COLOR_BORDER_DARK)
SIDE_DARK_THIN = Side(style="thin", color=COLOR_BORDER_DARK)
SIDE_LIGHT_THIN = Side(style="thin", color=COLOR_BORDER_LIGHT)
FILL_HEADER = PatternFill("solid", fgColor=COLOR_HEADER_BG)


# ============================================================
# 2. 아래첨자 / 윗첨자 부분 서식(RichText) 자동 변환
#    - 셀 전체가 아니라 첨자 글자만 vertAlign 적용
#    - 유니코드(², ³) 대신 엑셀 네이티브 맑은 고딕 첨자로 통일
# ============================================================
AUTO_SCRIPT_RULES = [
    (r"²", "^{2}"),
    (r"³", "^{3}"),
    (r"\bQ0\b", "Q_{0}"),
    (r"\bYC\b", "Y_{C}"),
    (r"\bWB\b", "W_{B}"),
    (r"\bS(11|22|33|12|13|23)\b", r"S_{\1}"),
    (r"\bNT(11)\b", r"NT_{\1}"),
    (r"\bRF([123])\b", r"RF_{\1}"),
    (r"\bU([123])\b", r"U_{\1}"),
]
TOKEN_PATTERN = re.compile(r"(_\{[^}]+\}|\^\{[^}]+\})")


def auto_mark_scripts(text: str) -> str:
    if not isinstance(text, str):
        return text
    result = text
    for pattern, repl in AUTO_SCRIPT_RULES:
        result = re.sub(pattern, repl, result)
    return result


def build_rich_text(
    text,
    font_name: str = FONT_NAME,
    size: float = 9.0,
    bold: bool = False,
    color: str = COLOR_TEXT,
    auto_convert: bool = True,
):
    if not isinstance(text, str):
        return text

    marked = auto_mark_scripts(text) if auto_convert else text
    if not TOKEN_PATTERN.search(marked):
        return marked

    blocks = []
    for part in TOKEN_PATTERN.split(marked):
        if not part:
            continue
        if part.startswith("_{") and part.endswith("}"):
            f = InlineFont(rFont=font_name, sz=size, b=bold, color=color, vertAlign="subscript")
            blocks.append(TextBlock(f, part[2:-1]))
        elif part.startswith("^{") and part.endswith("}"):
            f = InlineFont(rFont=font_name, sz=size, b=bold, color=color, vertAlign="superscript")
            blocks.append(TextBlock(f, part[2:-1]))
        else:
            f = InlineFont(rFont=font_name, sz=size, b=bold, color=color)
            blocks.append(TextBlock(f, part))
    return CellRichText(*blocks)


# ============================================================
# 3. 자동 행 높이 계산 (2행 병합 꼼수 제거용)
# ============================================================
def visual_length(text: str) -> float:
    clean = re.sub(r"[_^]\{([^}]+)\}", r"\1", str(text))
    return sum(1.85 if ord(ch) > 127 else 1.0 for ch in clean)


def calc_row_height(row_values, col_widths, base_height=19.5, line_height=13.5):
    max_lines = 1
    for val, width in zip(row_values, col_widths):
        if val is None:
            continue
        usable_w = max(width - 2.0, 4.0)
        lines = sum(
            max(1, math.ceil(visual_length(p) / usable_w))
            for p in str(val).split("\n")
        )
        max_lines = max(max_lines, lines)
    return base_height if max_lines == 1 else max_lines * line_height + 5.0


# ============================================================
# 4. 범용 표 생성기 (3칸·4칸 열 구조를 뭉개지 않고 그대로 출력)
# ============================================================
def write_section_title(ws, row: int, start_col: int, end_col: int, title: str):
    if end_col > start_col:
        ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
    cell = ws.cell(row, start_col)
    cell.value = build_rich_text(title, size=10.0, bold=True)
    cell.font = Font(name=FONT_NAME, size=10.0, bold=True, color=COLOR_TEXT)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    for c in range(start_col, end_col + 1):
        ws.cell(row, c).border = Border(bottom=SIDE_DARK_MED)
    ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 0, 22.0)


def write_table(
    ws,
    start_row: int,
    start_col: int,
    headers: list,
    rows: list,
    col_widths: list,
    col_aligns: list = None,
    num_formats: list = None,
    auto_height: bool = True,
):
    n_cols = len(headers)
    if col_aligns is None:
        col_aligns = ["center"] * n_cols
    if num_formats is None:
        num_formats = [None] * n_cols

    for idx, w in enumerate(col_widths):
        col_letter = get_column_letter(start_col + idx)
        cur_w = ws.column_dimensions[col_letter].width or 0
        ws.column_dimensions[col_letter].width = max(cur_w, w)

    for idx, h in enumerate(headers):
        c = ws.cell(start_row, start_col + idx)
        c.value = build_rich_text(h, size=9.0, bold=True)
        c.font = Font(name=FONT_NAME, size=9.0, bold=True, color=COLOR_TEXT)
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = Border(
            top=SIDE_DARK_THIN,
            bottom=SIDE_DARK_THIN,
            left=SIDE_LIGHT_THIN if idx > 0 else SIDE_DARK_THIN,
            right=SIDE_LIGHT_THIN if idx < n_cols - 1 else SIDE_DARK_THIN,
        )
    ws.row_dimensions[start_row].height = max(ws.row_dimensions[start_row].height or 0, 20.0)

    for r_idx, row_data in enumerate(rows):
        curr_r = start_row + 1 + r_idx
        is_last = (r_idx == len(rows) - 1)
        for c_idx in range(n_cols):
            val = row_data[c_idx] if c_idx < len(row_data) else None
            cell = ws.cell(curr_r, start_col + c_idx)

            if isinstance(val, (int, float)):
                cell.value = val
                cell.font = Font(name=FONT_NAME, size=9.0, color=COLOR_TEXT)
                if num_formats[c_idx]:
                    cell.number_format = num_formats[c_idx]
            else:
                is_key_col = (c_idx == 0 and col_aligns[c_idx] == "center")
                cell.value = build_rich_text(val, size=9.0, bold=is_key_col)
                cell.font = Font(name=FONT_NAME, size=9.0, bold=is_key_col, color=COLOR_TEXT)

            h_align = col_aligns[c_idx]
            cell.alignment = Alignment(
                horizontal=h_align,
                vertical="center",
                wrap_text=True,
                indent=1 if h_align == "left" else 0,
            )
            cell.border = Border(
                top=SIDE_LIGHT_THIN,
                bottom=SIDE_DARK_THIN if is_last else SIDE_LIGHT_THIN,
                left=SIDE_LIGHT_THIN if c_idx > 0 else SIDE_DARK_THIN,
                right=SIDE_LIGHT_THIN if c_idx < n_cols - 1 else SIDE_DARK_THIN,
            )

        if auto_height:
            needed_h = calc_row_height(row_data, col_widths)
            ws.row_dimensions[curr_r].height = max(ws.row_dimensions[curr_r].height or 0, needed_h)

    return start_row + len(rows)


# ============================================================
# 5. 기존 AI 생성 엑셀 파일 자동 정리 실행 함수
# ============================================================
def clean_dflux_workbook(src_path: str, dst_path: str):
    src_wb = openpyxl.load_workbook(src_path, data_only=True)
    src_ws = src_wb.active

    def read_block(r1, r2, c1, c2):
        out = []
        for r in range(r1, r2 + 1):
            vals = [src_ws.cell(r, c).value for c in range(c1, c2 + 1)]
            if any(v is not None for v in vals):
                out.append(vals)
        return out

    temp_y = read_block(6, 22, 14, 16)
    temp_x = read_block(6, 14, 18, 20)
    temp_z = read_block(17, 21, 18, 20)
    stress_y = read_block(28, 44, 14, 17)
    stress_x = read_block(28, 35, 18, 21)
    stress_z = read_block(39, 42, 18, 21)
    saved_images = [io.BytesIO(img._data()) for img in src_ws._images]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "해석 정리"
    ws.views.sheetView[0].showGridLines = False

    for r_i in range(4, 50):
        ws.row_dimensions[r_i].height = 19.5

    ws.column_dimensions["A"].width = 2.0
    ws.column_dimensions["E"].width = 2.5
    ws.column_dimensions["N"].width = 2.5
    ws.column_dimensions["S"].width = 2.2

    ws.merge_cells("B2:W2")
    title_cell = ws["B2"]
    title_cell.value = "DFLUX 열원 서브루틴 적용 인장시편 해석 결과 정리 (Abaqus/Standard 2021, tensile_dflux_v2)"
    title_cell.font = Font(name=FONT_NAME, size=13.0, bold=True, color=COLOR_TEXT)
    title_cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 10.0
    ws.row_dimensions[2].height = 28.0
    ws.row_dimensions[3].height = 10.0

    left_widths = [14.0, 32.0, 29.0]

    r = 4
    write_section_title(ws, r, 2, 4, "1. 서브루틴 조건")
    r = write_table(
        ws, r + 1, 2,
        headers=["항목", "적용 내용", "비고"],
        rows=[
            ["서브루틴", "DFLUX (dflux_heat.for)", "위치별 체적 열원 정의"],
            ["열원 식", "q(y) = Q_{0}·exp(-((y-Y_{C})/W_{B})^{2})", "단위: mW/mm^{3}"],
            ["매개변수", "Q_{0} = 7 mW/mm^{3}, Y_{C} = 50 mm", "W_{B} = 6 mm"],
            ["적용 대상", "*Dflux: ALLE, BFNU", "Step 1 (가열 구간)만 적용"],
        ],
        col_widths=left_widths,
        col_aligns=["center", "left", "left"],
    )

    r += 2
    write_section_title(ws, r, 2, 4, "2. 시편 형상 및 메시")
    r = write_table(
        ws, r + 1, 2,
        headers=["구분", "주요 치수 / 사양", "비고"],
        rows=[
            ["시편 치수", "길이 100, 그립 10, 두께 3 mm", "평행부 6×36, 필렛 R4 (추정)"],
            ["요소 타입", "C3D8T (열-구조 연성 8절점)", "변위 + 온도 자유도"],
            ["메시 규모", "요소 4,544개 / 절점 6,435개", "-"],
        ],
        col_widths=left_widths,
        col_aligns=["center", "left", "left"],
    )

    r += 2
    write_section_title(ws, r, 2, 4, "3. 주요 세트(Set)")
    r = write_table(
        ws, r + 1, 2,
        headers=["세트명", "대상 영역", "부여 조건 / 용도"],
        rows=[
            ["BOT_GRIP", "하단 그립 20 mm 절점", "완전 고정"],
            ["TOP_GRIP", "상단 그립 20 mm 절점", "기준점(RP) 커플링"],
            ["RP", "상단 기준점", "인장 변위 입력 / RF_{2}, U_{2} 출력"],
            ["CENTER", "시편 정중앙 절점(3218)", "중앙 온도(NT_{11}) 이력 출력"],
        ],
        col_widths=left_widths,
        col_aligns=["center", "left", "left"],
    )

    r += 2
    write_section_title(ws, r, 2, 4, "4. 물성치 (ABS 대표값)")
    r = write_table(
        ws, r + 1, 2,
        headers=["물성 항목", "입력 값", "온도 조건 / 비고"],
        rows=[
            ["탄성계수(E)", "2200 / 1600 / 900 MPa", "23 / 60 / 90 ℃, ν = 0.35"],
            ["항복응력", "40 / 26 / 12 MPa", "23 / 60 / 90 ℃ (경화곡선 포함)"],
            ["밀도", "1.05E-09 tonne/mm^{3}", "상온 기준"],
            ["열팽창계수", "9.0E-05 /℃", "선팽창계수"],
            ["열전도/비열", "0.17 mW/(mm·℃)", "비열 1.4E+09 mJ/(tonne·℃)"],
            ["파손 조건", "변형률 0.45 / 변위 0.3 mm", "Ductile damage (요소 삭제)"],
        ],
        col_widths=left_widths,
        col_aligns=["center", "left", "left"],
    )

    r += 2
    write_section_title(ws, r, 2, 4, "5. 초기조건 및 해석 스텝")
    r = write_table(
        ws, r + 1, 2,
        headers=["스텝 / 구분", "시간 구간", "해석 조건"],
        rows=[
            ["초기조건", "t = 0 s", "전 영역 초기온도 23 ℃"],
            ["해석 유형", "Coupled Temp-Disp", "열-구조 연성 해석"],
            ["Step 1 (가열)", "0 ~ 10 s", "양단 고정, 평행부 DFLUX 가열"],
            ["Step 2 (인장)", "10 ~ 30 s", "열원 OFF, RP +Y 12 mm (0.6 mm/s)"],
        ],
        col_widths=left_widths,
        col_aligns=["center", "center", "left"],
    )

    for col_idx in range(6, 14):
        ws.column_dimensions[get_column_letter(col_idx)].width = 11.5

    write_section_title(ws, 4, 6, 13, "6. History Output 이력")
    write_section_title(ws, 24, 6, 13, "7. 주요 해석 결과 (응력 / 온도)")
    write_section_title(ws, 35, 6, 13, "8. 인장 시편 실물 사진")

    img_slots = [
        ("F5", 345, 155), ("J5", 345, 155),
        ("F14", 345, 155), ("J14", 345, 155),
        ("F25", 345, 155), ("J25", 345, 155),
        ("F36", 345, 76),  ("J36", 345, 116),
    ]
    for buf, (anchor, tw, th) in zip(saved_images, img_slots):
        buf.seek(0)
        xl_img = XLImage(buf)
        xl_img.width = tw
        xl_img.height = th
        ws.add_image(xl_img, anchor)

    captions = [
        (12, 6, 9,  "중앙 온도(NT_{11}): 가열 10s 동안 23→69.4 ℃ 상승, 30s 시점 67.9 ℃"),
        (12, 10, 13, "반력(RF_{2}): 가열 중 압축 -31.1 N, 인장 12.33s 최대 456.9 N 후 14s경 파단"),
        (21, 6, 9,  "변위(U_{2}): 가열 중 0 유지, 인장 구간 0→12 mm 일정 속도 증가"),
        (21, 10, 13, "CRPTIME: Abaqus 기본 출력 누적시간 (크리프 물성 미적용)"),
        (32, 6, 9,  "Mises 응력 (14.0s): 평행부 중앙 네킹 및 파단 개시"),
        (32, 10, 13, "온도 분포 NT_{11} (17.3s): 고온부(약 69 ℃) 중심 파단, 그립부 23 ℃ 유지"),
        (40, 6, 9,  "시험 전 시편"),
        (41, 10, 13, "상: 인장 파단 후 / 하: 시험 전"),
    ]
    for row_c, c1, c2, text_c in captions:
        ws.merge_cells(start_row=row_c, start_column=c1, end_row=row_c + 1, end_column=c2)
        cell = ws.cell(row_c, c1)
        cell.value = build_rich_text(text_c, size=8.5, color=COLOR_SUBTEXT)
        cell.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)

    write_section_title(ws, 4, 15, 23, "9. 온도 분포 (℃) - 시편 중앙 X/Y/Z 방향")

    write_table(
        ws, 5, 15,
        headers=["y (mm)", "10 s", "30 s"],
        rows=temp_y,
        col_widths=[9.0, 8.5, 8.5],
        col_aligns=["right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00"],
        auto_height=False,
    )
    write_table(
        ws, 5, 20,
        headers=["x (mm)", "10 s", "30 s"],
        rows=temp_x,
        col_widths=[9.0, 8.5, 8.5],
        col_aligns=["right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00"],
        auto_height=False,
    )
    write_table(
        ws, 16, 20,
        headers=["z (mm)", "10 s", "30 s"],
        rows=temp_z,
        col_widths=[9.0, 8.5, 8.5],
        col_aligns=["right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00"],
        auto_height=False,
    )

    ws.merge_cells("O23:W24")
    note_t = ws["O23"]
    note_t.value = "Y(길이) 방향 온도 구배가 지배적(중앙 약 69 ℃, 그립부 23 ℃)이며 X(폭)·Z(두께) 방향은 거의 균일함."
    note_t.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    note_t.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    write_section_title(ws, 26, 15, 23, "10. 응력 분포 (MPa) - 최대 하중 시점 (12.33 s)")

    write_table(
        ws, 27, 15,
        headers=["y (mm)", "S_{11}", "S_{22}", "S_{33}"],
        rows=stress_y,
        col_widths=[9.0, 8.5, 8.5, 8.5],
        col_aligns=["right", "right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00", "0.00"],
        auto_height=False,
    )
    write_table(
        ws, 27, 20,
        headers=["x (mm)", "S_{11}", "S_{22}", "S_{33}"],
        rows=stress_x,
        col_widths=[9.0, 8.5, 8.5, 8.5],
        col_aligns=["right", "right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00", "0.00"],
        auto_height=False,
    )
    write_table(
        ws, 38, 20,
        headers=["z (mm)", "S_{11}", "S_{22}", "S_{33}"],
        rows=stress_z,
        col_widths=[9.0, 8.5, 8.5, 8.5],
        col_aligns=["right", "right", "right", "right"],
        num_formats=["0.00", "0.00", "0.00", "0.00"],
        auto_height=False,
    )

    ws.merge_cells("O45:W47")
    note_s = ws["O45"]
    note_s.value = build_rich_text(
        "S_{11}(폭), S_{22}(인장), S_{33}(두께) 방향 수직응력 (적분점 평균). "
        "인장 방향 S_{22}가 지배적이며 가열 종료(10s) 시 중앙 S_{22} = -2.2 MPa(구속 압축 열응력).",
        size=8.5,
        color=COLOR_SUBTEXT,
    )
    note_s.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    note_s.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)

    ws.merge_cells("B49:W49")
    foot = ws["B49"]
    foot.value = "※ 시편 치수 및 물성치는 추정/대표값 적용. 9~10번 데이터는 tensile_dflux_v2.odb 추출값."
    foot.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)

    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.print_area = "B2:W49"

    wb.save(dst_path)
    print(f"정리 완료: {dst_path}")


if __name__ == "__main__":
    clean_dflux_workbook(
        src_path="인장시편_DFLUX_해석정리_한페이지 (1).xlsx",
        dst_path="인장시편_DFLUX_해석정리_수정본.xlsx",
    )
