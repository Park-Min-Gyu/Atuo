"""
DFLUX 인장시편 해석 결과 한 장 정리 엑셀 자동 생성

사용법
    python excel_auto_cleaner.py [원본.xlsx] [결과.xlsx] [--graph]
      --graph : '그래프' 시트(엑셀 차트 4개)도 같이 만듦  → 차트 코드는 excel_chart.py

[3차 수정 내역]
 - 통합 문서 기본 글꼴(표준 스타일)을 Calibri → 맑은 고딕 11 로 변경. 빈 셀에 새로 입력해도 맑은 고딕
   (열 너비 단위가 '기본 글꼴 숫자 0 폭' 이라 7px → 8px 로 바뀜. 너비 값은 환산해서 화면 크기는 그대로)
 - 원본 읽기 함수가 표 위치도 돌려주도록 정리 (차트가 같은 표를 참조할 때 사용)

[2차 수정 내역]
 - 행 높이 통일: 왼쪽 표 자동 높이/제목 행 높이가 같은 행의 오른쪽 숫자표까지 늘리던 문제 해결
   (전 구간 19.5pt 고정, 대신 글자가 한 줄에 들어가도록 열 너비를 자동으로 맞춤)
 - 열 너비 버그: openpyxl 기본 열 너비가 13이라 max(13, 9.0) → 오른쪽 표가 전부 13으로 넓어지던 문제
 - 이미지: 345px 고정 → 실제 칸 폭(4열 = 320px)을 넘어 옆 그림을 25px 덮던 문제.
   칸 폭 기준으로 비율 유지 축소 + 가운데 정렬, 행 수도 그림 높이에서 계산
 - 9번 온도표 3열 / 10번 응력표 4열이라 오른쪽 끝선이 안 맞던 문제 → 온도표에 ΔT(수식) 열 추가
 - x/z 표 아래 끝선을 y 표 끝선과 맞춤, 캡션 2개 같은 행에 정렬
 - 첨자 규칙: \b 가 한글 조사 앞에서 안 먹던 문제(S22가, RF2와 …) 수정, mm2/mm3 규칙 추가
 - 숫자 오른쪽 여백(표시형식 _-), 테두리 겹침 정리, 문서 속성 작성자 'openpyxl' 제거
 - 원본 읽기: 고정 셀 범위 → 'y (mm)' 같은 머리글을 찾아서 읽고, 못 찾으면 기존 고정 범위 사용
"""
import getpass
import io
import math
import re
import sys
from datetime import date

import openpyxl
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Color, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.indexed_list import IndexedList
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from PIL import Image as PILImage

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

ROW_H = 19.5                   # 본문 전 구간 공통 행 높이(pt). 세 단이 같은 행을 쓰므로 반드시 통일
AUTHOR = None                  # 문서 속성 작성자. None 이면 PC 로그인 이름 사용 (엑셀과 동일한 방식)
PAPER = "A4"                   # 글자가 너무 작게 인쇄되면 "A3"
DEFAULT_FONT_SIZE = 11         # 통합 문서 기본 글꼴 크기 (한글 엑셀 기본값과 동일)


def apply_default_font(wb, name: str = FONT_NAME, size: float = DEFAULT_FONT_SIZE):
    """
    통합 문서 기본 글꼴(표준 스타일) 변경. 새 Workbook() 직후, 셀을 쓰기 전에 호출.
    openpyxl 기본값은 Calibri 11 이라 빈 셀에 입력하면 Calibri 로 찍힘 → 한글 엑셀에서 만든 파일과 다르게 보임.
    charset=129 는 한글 글꼴 표시(엑셀이 직접 저장할 때와 동일).
    """
    font = Font(name=name, size=size, family=2, charset=129, color=Color(theme=1))
    wb._fonts = IndexedList([font])            # 글꼴 0번 = 서식 없는 모든 셀의 글꼴
    wb._named_styles["Normal"].font = font     # '표준' 스타일


# ============================================================
# 2. 아래첨자 / 윗첨자 부분 서식(RichText) 자동 변환
#    - 셀 전체가 아니라 첨자 글자만 vertAlign 적용
#    - 유니코드(², ³) 대신 엑셀 네이티브 맑은 고딕 첨자로 통일
#    - \b 는 한글도 '단어 글자'로 봐서 "S22가", "RF2와" 에서 매칭이 안 됨
#      → 앞뒤가 '영문/숫자가 아닐 때'로 경계를 직접 지정
# ============================================================
_B = r"(?<![A-Za-z0-9])"
_E = r"(?![A-Za-z0-9]|\.\d)"          # U2.5 같은 소수는 제외
AUTO_SCRIPT_RULES = [
    (r"²", "^{2}"),
    (r"³", "^{3}"),
    (r"(?<![A-Za-z])mm([23])(?![0-9])", r"mm^{\1}"),
    (_B + r"Q0" + _E, "Q_{0}"),
    (_B + r"YC" + _E, "Y_{C}"),
    (_B + r"WB" + _E, "W_{B}"),
    (_B + r"S(11|22|33|12|13|23)" + _E, r"S_{\1}"),
    (_B + r"NT(11)" + _E, r"NT_{\1}"),
    (_B + r"RF([123])" + _E, r"RF_{\1}"),
    (_B + r"U([123])" + _E, r"U_{\1}"),
]
TOKEN_PATTERN = re.compile(r"(_\{[^}]+\}|\^\{[^}]+\})")
NBSP = " "


def auto_mark_scripts(text: str) -> str:
    if not isinstance(text, str):
        return text
    result = text
    for pattern, repl in AUTO_SCRIPT_RULES:
        result = re.sub(pattern, repl, result)
    return result


def glue_units(text: str) -> str:
    """줄바꿈되는 캡션/메모에서 '67.9 ℃' 의 ℃ 만 다음 줄로 떨어지지 않게 숫자-단위 사이를 붙임"""
    return re.sub(r"(\d) (?=(?:℃|°C|mm|MPa|mW|N|s)(?![A-Za-z]))", r"\1" + NBSP, text)


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
# 3. 엑셀 픽셀 계산 (열 너비 / 행 높이 / 글자 폭)
#    - 열 너비 단위는 '표준 글꼴의 숫자 0 폭' 기준: 맑은 고딕 11 = 8px (Calibri 11 이면 7px)
#      (한글 엑셀 기본 열 너비가 8.43 이 아니라 8.38(=67/8) 로 보이는 이유)
#    - 저장값 w → 화면 px = trunc((256w + trunc(128/8)) / 256 × 8)   (100% 배율)
# ============================================================
MAX_DIGIT_PX = 8
CELL_PAD_PX = 8      # 셀 좌우 기본 여백 합
INDENT_PX = 10       # 들여쓰기 1단계
ROW_PX = round(ROW_H * 96 / 72)


def col_px(width: float) -> int:
    return int(((256 * width + int(128 / MAX_DIGIT_PX)) / 256) * MAX_DIGIT_PX)


def px_to_width(px: float) -> float:
    """px 이상이 되는 가장 작은 열 너비 (0.25 단위로 올림 → 손으로 맞춘 것 같은 값)"""
    return math.ceil((px / MAX_DIGIT_PX) * 4) / 4


def _char_em(ch: str) -> float:
    """맑은 고딕 기준 대략적인 글자 폭 (em). 모자라면 잘리므로 넉넉한 쪽으로 잡음"""
    o = ord(ch)
    if 0xAC00 <= o <= 0xD7A3 or 0x3130 <= o <= 0x318F or 0x1100 <= o <= 0x11FF:
        return 0.92
    if ch in (" ", NBSP):
        return 0.30
    if ch in "iljtfrI.,:;'!|":
        return 0.30
    if ch in "()[]{}/\\-":
        return 0.38
    if ch in "mwMW%":
        return 0.85
    if ch.isdigit():
        return 0.56
    if "A" <= ch <= "Z":
        return 0.66
    if "a" <= ch <= "z":
        return 0.54
    if o < 128:
        return 0.58
    return 0.95      # ℃, →, ×, ·, Δ, ν 등


def text_px(text, size: float = 9.0, bold: bool = False) -> float:
    """셀에 한 줄로 썼을 때의 폭(px) 추정. 첨자 글자는 작게 찍히므로 0.7배"""
    marked = auto_mark_scripts(str(text))
    em = 0.0
    for part in TOKEN_PATTERN.split(marked):
        if not part:
            continue
        if TOKEN_PATTERN.fullmatch(part):
            em += 0.7 * sum(_char_em(c) for c in part[2:-1])
        else:
            em += sum(_char_em(c) for c in part)
    return em * size * 96 / 72 * (1.06 if bold else 1.0) * 1.08


def calc_row_height(row_values, col_widths, size=9.0, base_height=ROW_H, line_height=13.5):
    """줄바꿈 허용 표(wrap=True)에서만 사용. 세 단이 행을 공유하는 구간에서는 쓰지 말 것"""
    max_lines = 1
    for val, width in zip(row_values, col_widths):
        if val is None:
            continue
        usable = max(col_px(width) - CELL_PAD_PX - INDENT_PX, 20)
        lines = sum(max(1, math.ceil(text_px(p, size) / usable)) for p in str(val).split("\n"))
        max_lines = max(max_lines, lines)
    return base_height if max_lines == 1 else max_lines * line_height + 5.0


def set_col_width(ws, col: int, width: float):
    """처음 지정하면 그 값, 이미 지정된 열이면 더 넓은 쪽.
    (ws.column_dimensions[x].width 를 그냥 읽으면 openpyxl 기본값 13 이 나와서 max 비교가 깨짐)"""
    letter = get_column_letter(col)
    if letter in ws.column_dimensions:
        width = max(ws.column_dimensions[letter].width, width)
    ws.column_dimensions[letter].width = width


def get_col_width(ws, col: int) -> float:
    letter = get_column_letter(col)
    if letter in ws.column_dimensions:
        return ws.column_dimensions[letter].width
    return 9.0


def pad_number_format(fmt: str) -> str:
    """'0.00' → '0.00_-' : 오른쪽 정렬 숫자가 테두리에 붙지 않게 '-' 한 글자 폭만큼 띄움 (양수/음수 구역 모두)"""
    if not fmt or fmt == "General" or "_-" in fmt:
        return fmt
    return ";".join(part + "_-" for part in fmt.split(";"))


def _display_text(val, num_format):
    if isinstance(val, str) and val.startswith("="):
        return "+00.00"                        # 수식 결과 자리 (ΔT 등)
    if isinstance(val, (int, float)):
        m = re.fullmatch(r"\+?0\.(0+).*", num_format or "")
        return f"{val:.{len(m.group(1))}f}" if m else str(val)
    return val


# ============================================================
# 4. 범용 표 생성기 (3칸·4칸 열 구조를 뭉개지 않고 그대로 출력)
# ============================================================
def write_section_title(ws, row: int, start_col: int, end_col: int, title: str):
    """병합 없이 첫 칸에만 쓰고(빈 칸으로 자연스럽게 넘침), 밑줄만 범위 전체에"""
    cell = ws.cell(row, start_col)
    cell.value = build_rich_text(title, size=10.0, bold=True)
    cell.font = Font(name=FONT_NAME, size=10.0, bold=True, color=COLOR_TEXT)
    cell.alignment = Alignment(horizontal="left", vertical="center")
    for c in range(start_col, end_col + 1):
        ws.cell(row, c).border = Border(bottom=SIDE_DARK_MED)


def write_table(
    ws,
    start_row: int,
    start_col: int,
    headers: list,
    rows: list,
    col_widths: list,
    col_aligns: list = None,
    num_formats: list = None,
    wrap: bool = False,
    top_side: Side = SIDE_DARK_MED,
):
    """
    wrap=False (기본): 모든 칸을 한 줄로. 글자가 길면 열 너비를 넓혀서 맞추고 행 높이는 건드리지 않음
                      → 같은 행을 쓰는 옆 단 표가 들쭉날쭉해지지 않음
    wrap=True        : 줄바꿈 + 행 높이 자동. 표 혼자 행을 쓰는 경우에만 사용
    top_side         : 헤더 윗선. 바로 위가 섹션 제목(굵은 밑줄)이면 같은 굵기로 맞춰 선 겹침 방지
    반환값: 마지막 데이터 행 번호
    """
    n_cols = len(headers)
    col_aligns = col_aligns or ["center"] * n_cols
    num_formats = num_formats or [None] * n_cols
    key_bold = col_aligns[0] == "center"

    # --- 열 너비: 지정값 기준, 한 줄에 안 들어가면 넓힘 ---
    need_px = [text_px(h, 9.0, bold=True) + CELL_PAD_PX for h in headers]
    for row_data in rows:
        for idx in range(n_cols):
            val = row_data[idx] if idx < len(row_data) else None
            if val is None:
                continue
            indent = 1 if col_aligns[idx] in ("left", "right") else 0
            w_px = text_px(_display_text(val, num_formats[idx]), 9.0, bold=(idx == 0 and key_bold))
            need_px[idx] = max(need_px[idx], w_px + CELL_PAD_PX + indent * INDENT_PX)

    final_widths = []
    for idx, w in enumerate(col_widths):
        fit_w = w if wrap else max(w, px_to_width(need_px[idx]))
        if fit_w > max(w, get_col_width(ws, start_col + idx)):
            print(f"  [열 너비] {get_column_letter(start_col + idx)}: {w} → {fit_w} (한 줄에 맞춤)")
        set_col_width(ws, start_col + idx, fit_w)
        final_widths.append(get_col_width(ws, start_col + idx))

    def side_lr(idx):
        return (
            SIDE_DARK_THIN if idx == 0 else SIDE_LIGHT_THIN,
            SIDE_DARK_THIN if idx == n_cols - 1 else SIDE_LIGHT_THIN,
        )

    # --- 헤더 ---
    for idx, h in enumerate(headers):
        c = ws.cell(start_row, start_col + idx)
        c.value = build_rich_text(h, size=9.0, bold=True)
        c.font = Font(name=FONT_NAME, size=9.0, bold=True, color=COLOR_TEXT)
        c.fill = FILL_HEADER
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=wrap, shrink_to_fit=not wrap)
        left, right = side_lr(idx)
        c.border = Border(top=top_side, bottom=SIDE_DARK_THIN, left=left, right=right)

    # --- 데이터 ---
    for r_idx, row_data in enumerate(rows):
        curr_r = start_row + 1 + r_idx
        is_last = (r_idx == len(rows) - 1)
        for c_idx in range(n_cols):
            val = row_data[c_idx] if c_idx < len(row_data) else None
            cell = ws.cell(curr_r, start_col + c_idx)
            is_formula = isinstance(val, str) and val.startswith("=")

            is_number = isinstance(val, (int, float)) or is_formula
            if is_number:
                cell.value = val
                cell.font = Font(name=FONT_NAME, size=9.0, color=COLOR_TEXT)
                if num_formats[c_idx]:
                    cell.number_format = pad_number_format(num_formats[c_idx])
            else:
                is_key_col = (c_idx == 0 and key_bold)
                cell.value = build_rich_text(val, size=9.0, bold=is_key_col)
                cell.font = Font(name=FONT_NAME, size=9.0, bold=is_key_col, color=COLOR_TEXT)

            h_align = col_aligns[c_idx]
            cell.alignment = Alignment(
                horizontal=h_align,
                vertical="center",
                wrap_text=wrap,
                shrink_to_fit=not wrap,          # 추정이 빗나가도 잘리지 않고 살짝 줄어듦 (안전장치)
                # 숫자 오른쪽 여백은 들여쓰기 대신 표시형식 '_-' 로 (엑셀 회계형식과 같은 방식)
                indent=1 if (h_align == "left" or (h_align == "right" and not is_number)) else 0,
            )
            left, right = side_lr(c_idx)
            cell.border = Border(
                top=SIDE_DARK_THIN if r_idx == 0 else SIDE_LIGHT_THIN,   # 헤더 아랫선과 동일하게
                bottom=SIDE_DARK_THIN if is_last else SIDE_LIGHT_THIN,
                left=left,
                right=right,
            )

        if wrap:
            needed_h = calc_row_height(row_data, final_widths)
            ws.row_dimensions[curr_r].height = max(ws.row_dimensions[curr_r].height or ROW_H, needed_h)

    return start_row + len(rows)


def write_note(ws, row: int, c1: int, c2: int, text: str, size: float = 8.5, max_rows: int = 3):
    """표 아래 설명 메모. 필요한 줄 수만큼만 행을 병합. 반환값: 마지막 행 번호"""
    width_px = sum(col_px(get_col_width(ws, c)) for c in range(c1, c2 + 1)) - CELL_PAD_PX
    n_rows = min(max_rows, max(1, math.ceil(text_px(text, size) / width_px)))
    if n_rows > 1 or c2 > c1:
        ws.merge_cells(start_row=row, start_column=c1, end_row=row + n_rows - 1, end_column=c2)
    cell = ws.cell(row, c1)
    cell.value = build_rich_text(glue_units(text), size=size, color=COLOR_SUBTEXT)
    cell.font = Font(name=FONT_NAME, size=size, color=COLOR_SUBTEXT)
    cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    return row + n_rows - 1


# ============================================================
# 5. 이미지 배치 (칸 폭에 맞춰 비율 유지 축소 + 가운데 정렬)
# ============================================================
IMG_PAD_PX = 4


def _anchor_image(ws, buf, col: int, row: int, x_px: int, y_px: int, w_px: int, h_px: int):
    # 오프셋이 셀 하나를 넘으면 다음 열/행으로 넘겨서 지정 (엑셀이 셀 크기보다 큰 오프셋을 싫어함)
    while x_px >= col_px(get_col_width(ws, col)):
        x_px -= col_px(get_col_width(ws, col))
        col += 1
    row += y_px // ROW_PX
    y_px %= ROW_PX

    buf.seek(0)
    img = XLImage(buf)
    img.width, img.height = w_px, h_px
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(col=col - 1, colOff=pixels_to_EMU(x_px), row=row - 1, rowOff=pixels_to_EMU(y_px)),
        ext=XDRPositiveSize2D(pixels_to_EMU(w_px), pixels_to_EMU(h_px)),
    )
    ws.add_image(img)


def place_images_row(ws, top_row: int, slots: list, bufs: list) -> int:
    """
    slots: [(시작열, 끝열), ...] 한 행에 나란히 놓을 칸들
    각 그림을 칸 폭에 맞춰 비율 유지 축소, 가장 큰 그림 기준으로 필요한 행 수 계산,
    작은 그림은 세로 가운데. 반환값: 사용한 행 수
    """
    plan = []
    for (c1, c2), buf in zip(slots, bufs):
        buf.seek(0)
        w0, h0 = PILImage.open(buf).size
        slot_w = sum(col_px(get_col_width(ws, c)) for c in range(c1, c2 + 1))
        w = slot_w - 2 * IMG_PAD_PX
        plan.append((c1, buf, slot_w, w, round(w * h0 / w0)))

    n_rows = math.ceil((max(p[4] for p in plan) + 2 * IMG_PAD_PX) / ROW_PX)
    box_h = n_rows * ROW_PX
    for c1, buf, slot_w, w, h in plan:
        _anchor_image(ws, buf, c1, top_row, (slot_w - w) // 2, (box_h - h) // 2, w, h)
    return n_rows


def caption_rows(ws, c1: int, c2: int, text: str, size: float = 8.5) -> int:
    """캡션이 칸 폭에서 몇 줄이 되는지 (1~2). 한 쌍의 캡션은 큰 쪽에 맞춰 같은 높이로 씀"""
    width_px = sum(col_px(get_col_width(ws, c)) for c in range(c1, c2 + 1)) - CELL_PAD_PX
    return min(2, max(1, math.ceil(text_px(text, size) / width_px)))


def write_caption(ws, row: int, c1: int, c2: int, text: str, n_rows: int = 2):
    ws.merge_cells(start_row=row, start_column=c1, end_row=row + n_rows - 1, end_column=c2)
    cell = ws.cell(row, c1)
    cell.value = build_rich_text(glue_units(text), size=8.5, color=COLOR_SUBTEXT)
    cell.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)


# ============================================================
# 6. 원본 읽기
#    1순위: 'y (mm)' / 'x (mm)' / 'z (mm)' 머리글을 찾아서 아래로 숫자가 이어지는 만큼 읽음
#           (머리글 오른쪽이 '10 s, 30 s' → 온도표, 'S11, S22, S33' → 응력표)
#    2순위: 못 찾은 표만 기존 고정 셀 범위로 읽음
# ============================================================
SRC_FIXED_RANGES = {            # (행1, 행2, 열1, 열2) - 최초 AI 생성 원본 기준
    "temp_y": (6, 22, 14, 16),
    "temp_x": (6, 14, 18, 20),
    "temp_z": (17, 21, 18, 20),
    "stress_y": (28, 44, 14, 17),
    "stress_x": (28, 35, 18, 21),
    "stress_z": (39, 42, 18, 21),
}
AXIS_HEADER = re.compile(r"^\s*([xyz])\s*[\(\[]\s*mm\s*[\)\]]\s*$", re.I)
_SUB_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _norm_header(v) -> str:
    s = str(v).translate(_SUB_DIGITS)
    s = re.sub(r"[_^]\{([^}]*)\}", r"\1", s)
    return re.sub(r"\s+", "", s).lower()


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v):
    """일반 문자열 / RichText(rich_text=True 로 연 파일) 모두 문자열로. 그 외 None"""
    return str(v) if isinstance(v, (str, CellRichText)) else None


def find_axis_table_positions(ws) -> dict:
    """
    'x/y/z (mm)' 머리글로 표를 찾아 위치를 돌려줌.
    {'temp_y': {'header_row', 'x_col', 'value_cols', 'headers', 'first_row', 'last_row'}, ...}
    """
    found = {}
    for row in ws.iter_rows():
        for cell in row:
            m = AXIS_HEADER.match(_text(cell.value) or "")
            if not m:
                continue
            hdr = {}
            c = cell.column + 1
            while _text(ws.cell(cell.row, c).value) and not AXIS_HEADER.match(_text(ws.cell(cell.row, c).value)):
                hdr[c] = _norm_header(_text(ws.cell(cell.row, c).value))
                c += 1
            stress_cols = [col for comp in ("s11", "s22", "s33") for col, h in hdr.items() if h == comp]
            time_cols = [col for col, h in hdr.items() if re.fullmatch(r"\d+(\.\d+)?s", h)]
            if len(stress_cols) == 3:
                kind, cols = "stress", stress_cols
            elif len(time_cols) >= 2:
                kind, cols = "temp", time_cols[:2]
            else:
                continue

            key = f"{kind}_{m.group(1).lower()}"
            if key in found:
                print(f"  [경고] {key} 표가 두 번 이상 있음 → {cell.coordinate} 는 무시")
                continue
            r = cell.row + 1
            while _is_num(ws.cell(r, cell.column).value):
                r += 1
            if r == cell.row + 1:
                continue
            found[key] = {
                "header_row": cell.row,
                "x_col": cell.column,
                "value_cols": cols,
                "headers": [_text(ws.cell(cell.row, cc).value) for cc in cols],
                "first_row": cell.row + 1,
                "last_row": r - 1,
            }
    return found


def find_axis_tables(ws) -> dict:
    data = {}
    for key, p in find_axis_table_positions(ws).items():
        data[key] = [
            [ws.cell(r, p["x_col"]).value] + [ws.cell(r, cc).value for cc in p["value_cols"]]
            for r in range(p["first_row"], p["last_row"] + 1)
        ]
    return data


def read_source_data(src_path: str) -> dict:
    src_wb = openpyxl.load_workbook(src_path, data_only=True)
    src_ws = src_wb.active

    def read_block(r1, r2, c1, c2):
        out = []
        for r in range(r1, r2 + 1):
            vals = [src_ws.cell(r, c).value for c in range(c1, c2 + 1)]
            if any(v is not None for v in vals):
                out.append(vals)
        return out

    data = find_axis_tables(src_ws)
    for key, rng in SRC_FIXED_RANGES.items():
        if not data.get(key):
            print(f"  [안내] {key}: 머리글을 못 찾아서 고정 범위 {rng} 로 읽음")
            data[key] = read_block(*rng)

    for key in SRC_FIXED_RANGES:
        bad = [row for row in data[key] if not all(_is_num(v) for v in row)]
        if bad:
            raise ValueError(f"{key} 에 숫자가 아닌 값이 있음 (원본 위치 확인 필요): {bad[0]}")
        print(f"  {key:9s}: {len(data[key])}행")

    data["images"] = [io.BytesIO(img._data()) for img in src_ws._images]
    print(f"  이미지   : {len(data['images'])}개")
    return data


# ============================================================
# 7. 보고서 시트 작성 (세 단 레이아웃, 행 번호는 전부 데이터 길이로 계산)
# ============================================================
TITLE = "DFLUX 열원 서브루틴 적용 인장시편 해석 결과 정리"
SUBTITLE = "(Abaqus/Standard 2021, tensile_dflux_v2)"

LEFT_SECTIONS = [
    ("1. 서브루틴 조건", ["항목", "적용 내용", "비고"], ["center", "left", "left"], [
        ["서브루틴", "DFLUX (dflux_heat.for)", "위치별 체적 열원 정의"],
        ["열원 식", "q(y) = Q_{0}·exp(-((y-Y_{C})/W_{B})^{2})", "단위: mW/mm^{3}"],
        ["매개변수", "Q_{0} = 7 mW/mm^{3}, Y_{C} = 50 mm", "W_{B} = 6 mm"],
        ["적용 대상", "*Dflux: ALLE, BFNU", "Step 1 (가열 구간)만 적용"],
    ]),
    ("2. 시편 형상 및 메시", ["구분", "주요 치수 / 사양", "비고"], ["center", "left", "left"], [
        ["시편 치수", "길이 100, 그립 10, 두께 3 mm", "평행부 6×36, 필렛 R4 (추정)"],
        ["요소 타입", "C3D8T (열-구조 연성 8절점)", "변위 + 온도 자유도"],
        ["메시 규모", "요소 4,544개 / 절점 6,435개", "-"],
    ]),
    ("3. 주요 세트(Set)", ["세트명", "대상 영역", "부여 조건 / 용도"], ["center", "left", "left"], [
        ["BOT_GRIP", "하단 그립 20 mm 절점", "완전 고정"],
        ["TOP_GRIP", "상단 그립 20 mm 절점", "기준점(RP) 커플링"],
        ["RP", "상단 기준점", "인장 변위 입력 / RF2, U2 출력"],
        ["CENTER", "시편 정중앙 절점(3218)", "중앙 온도(NT11) 이력 출력"],
    ]),
    ("4. 물성치 (ABS 대표값)", ["물성 항목", "입력 값", "온도 조건 / 비고"], ["center", "left", "left"], [
        ["탄성계수(E)", "2200 / 1600 / 900 MPa", "23 / 60 / 90 ℃, ν = 0.35"],
        ["항복응력", "40 / 26 / 12 MPa", "23 / 60 / 90 ℃ (경화곡선 포함)"],
        ["밀도", "1.05E-09 tonne/mm^{3}", "상온 기준"],
        ["열팽창계수", "9.0E-05 /℃", "선팽창계수"],
        ["열전도/비열", "0.17 mW/(mm·℃)", "비열 1.4E+09 mJ/(tonne·℃)"],
        ["파손 조건", "변형률 0.45 / 변위 0.3 mm", "Ductile damage (요소 삭제)"],
    ]),
    ("5. 초기조건 및 해석 스텝", ["스텝 / 구분", "시간 구간", "해석 조건"], ["center", "center", "left"], [
        ["초기조건", "t = 0 s", "전 영역 초기온도 23 ℃"],
        ["해석 유형", "Coupled Temp-Disp", "열-구조 연성 해석"],
        ["Step 1 (가열)", "0 ~ 10 s", "양단 고정, 평행부 DFLUX 가열"],
        ["Step 2 (인장)", "10 ~ 30 s", "열원 OFF, RP +Y 12 mm (0.6 mm/s)"],
    ]),
]

# (섹션 제목 또는 None, [(이미지 번호, 캡션), (이미지 번호, 캡션)])  ※ 이미지 번호 = 원본 시트의 그림 순서
MID_BLOCKS = [
    ("6. History Output 이력", [
        (0, "중앙 온도(NT11): 가열 10 s 동안 23→69.4 ℃ 상승, 30 s 시점 67.9 ℃"),
        (1, "반력(RF2): 가열 중 압축 -31.1 N, 인장 12.33 s 최대 456.9 N 후 14 s경 파단"),
    ]),
    (None, [
        (2, "변위(U2): 가열 중 0 유지, 인장 구간 0→12 mm 일정 속도 증가"),
        (3, "CRPTIME: Abaqus 기본 출력 누적시간 (크리프 물성 미적용)"),
    ]),
    ("7. 주요 해석 결과 (응력 / 온도)", [
        (4, "Mises 응력 (14.0 s): 평행부 중앙 네킹 및 파단 개시"),
        (5, "온도 분포 NT11 (17.3 s): 고온부(약 69 ℃) 중심 파단, 그립부 23 ℃ 유지"),
    ]),
    ("8. 인장 시편 실물 사진", [
        (6, "시험 전 시편"),
        (7, "상: 인장 파단 후 / 하: 시험 전"),
    ]),
]

STRESS_NOTE = (
    "S11(폭), S22(인장), S33(두께) 방향 수직응력 (적분점 평균). "
    "인장 방향 S22가 지배적이며 가열 종료(10 s) 시 중앙 S22 = -2.2 MPa(구속 압축 열응력)."
)
FOOTNOTE = "※ 시편 치수 및 물성치는 추정/대표값 적용. 9~10번 데이터는 tensile_dflux_v2.odb 추출값."


def write_axis_tables(ws, top_row, c_left, c_right, title, tables, headers, widths, note):
    """
    y 표(긴 표)는 왼쪽, x 표는 오른쪽 위, z 표는 오른쪽 아래.
    z 표의 마지막 행을 y 표의 마지막 행에 맞춰서 블록 아래 끝선을 일치시킴.
    headers 마지막이 'ΔT' 이면 ΔT = (30 s) - (10 s) 를 수식으로 넣음.
    반환값: 이 블록이 사용한 마지막 행
    """
    n = len(headers)
    c_last = c_right + n - 1
    with_dt = headers[-1] == "ΔT"
    write_section_title(ws, top_row, c_left, c_last, title)
    hdr = top_row + 1
    fmts = ["0.00"] * (n - 1) + ["+0.00;-0.00;0.00" if with_dt else "0.00"]

    def put(axis, col, header_row, top_side):
        c10, c30 = get_column_letter(col + 1), get_column_letter(col + 2)
        rows = []
        for i, vals in enumerate(tables[axis]):
            row = list(vals)
            if with_dt:
                r = header_row + 1 + i
                row.append(f"=ROUND({c30}{r}-{c10}{r},2)")   # 반올림 안 하면 0.0001 차이가 '+0.00' 으로 보임
            rows.append(row)
        return write_table(ws, header_row, col, [f"{axis} (mm)"] + headers[1:], rows, widths,
                           col_aligns=["right"] * n, num_formats=fmts, top_side=top_side)

    end_y = put("y", c_left, hdr, SIDE_DARK_MED)
    end_x = put("x", c_right, hdr, SIDE_DARK_MED)
    z_hdr = end_y - len(tables["z"])
    if z_hdr < end_x + 2:                              # 공간이 모자라면 x 표 바로 아래(한 줄 띄움)
        z_hdr = end_x + 2
    end_z = put("z", c_right, z_hdr, SIDE_DARK_THIN)

    block_end = max(end_y, end_z)
    return write_note(ws, block_end + 1, c_left, c_last, note)


def build_report(data: dict, dst_path: str, with_graph: bool = False):
    wb = openpyxl.Workbook()
    apply_default_font(wb)
    ws = wb.active
    ws.title = "해석 정리"
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = 85
    ws.sheet_format.defaultRowHeight = 16.5      # 맑은 고딕 11 기본 행 높이

    # --- 열 배치: A(여백) | B~D 왼쪽 표 | E | F~M 그림 | N | O~R 표 | S | T~W 표 ---
    #     (맑은 고딕 기준 너비. 괄호 안은 px — Calibri 시절 2차 수정본과 같은 크기)
    COL_L = 2
    LEFT_W = [12.25, 28.0, 25.5]         # 98 / 224 / 204 px (글자가 길면 자동으로 넓어짐)
    COL_M = COL_L + len(LEFT_W) + 1
    MID_N, MID_W = 8, 10.0               # 80 px × 4열 = 그림 한 칸 320 px
    COL_R = COL_M + MID_N + 1
    RIGHT_W = [8.0, 7.5, 7.5, 7.5]       # 64 / 60 px
    COL_R2 = COL_R + len(RIGHT_W) + 1
    COL_LAST = COL_R2 + len(RIGHT_W) - 1

    set_col_width(ws, 1, 1.75)
    set_col_width(ws, COL_M - 1, 2.75)
    set_col_width(ws, COL_R - 1, 2.75)
    set_col_width(ws, COL_R2 - 1, 1.75)
    for c in range(COL_M, COL_M + MID_N):
        set_col_width(ws, c, MID_W)

    # --- 제목 (병합 없이 B2 에서 오른쪽으로 넘쳐 보이게 / 작성일은 오른쪽 끝) ---
    t = ws.cell(2, COL_L)
    t.value = CellRichText(
        TextBlock(InlineFont(rFont=FONT_NAME, sz=13.0, b=True, color=COLOR_TEXT), TITLE + "  "),
        TextBlock(InlineFont(rFont=FONT_NAME, sz=9.5, color=COLOR_SUBTEXT), SUBTITLE),
    )
    t.font = Font(name=FONT_NAME, size=13.0, bold=True, color=COLOR_TEXT)
    t.alignment = Alignment(horizontal="left", vertical="center")
    d = ws.cell(2, COL_LAST)
    d.value = f"작성일 {date.today():%Y.%m.%d}"
    d.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    d.alignment = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[1].height = 10.0
    ws.row_dimensions[2].height = 28.0
    ws.row_dimensions[3].height = 10.0

    # --- 왼쪽 단: 1~5 ---
    r = 4
    for title, headers, aligns, rows in LEFT_SECTIONS:
        write_section_title(ws, r, COL_L, COL_L + 2, title)
        r = write_table(ws, r + 1, COL_L, headers, rows, LEFT_W, col_aligns=aligns)
        r += 2
    left_end = r - 2

    # --- 가운데 단: 6~8 (그림 + 캡션) ---
    images = data["images"]
    slots = [(COL_M, COL_M + 3), (COL_M + 4, COL_M + 7)]
    r = 4
    for title, pair in MID_BLOCKS:
        if title:
            write_section_title(ws, r, COL_M, COL_M + MID_N - 1, title)
            r += 1
        pair = [(i, cap) for i, cap in pair if i < len(images)]
        if not pair:
            continue
        r += place_images_row(ws, r, slots[:len(pair)], [images[i] for i, _ in pair])
        cap_rows = max(caption_rows(ws, c1, c2, cap) for (c1, c2), (_, cap) in zip(slots, pair))
        for (c1, c2), (_, cap) in zip(slots, pair):
            write_caption(ws, r, c1, c2, cap, cap_rows)
        r += cap_rows + 1                    # 캡션 + 빈 행 1
    mid_end = r - 2

    # --- 오른쪽 단: 9 온도 / 10 응력 ---
    temp = {a: data[f"temp_{a}"] for a in "xyz"}
    stress = {a: data[f"stress_{a}"] for a in "xyz"}
    peak = max(row[1] for row in temp["y"])
    grip = min(row[1] for row in temp["y"])
    temp_note = (
        f"Y(길이) 방향 온도 구배가 지배적(중앙 약 {peak:.0f} ℃, 그립부 {grip:.0f} ℃)이며 "
        f"X(폭)·Z(두께) 방향은 거의 균일함. "
        + "ΔT = T(30 s) − T(10 s).".replace(" ", NBSP)          # 식 중간에서 줄이 안 끊기게
    )
    r = write_axis_tables(ws, 4, COL_R, COL_R2, "9. 온도 분포 (℃) - 시편 중앙 X/Y/Z 방향",
                          temp, ["", "10 s", "30 s", "ΔT"], RIGHT_W, temp_note)
    r = write_axis_tables(ws, r + 2, COL_R, COL_R2, "10. 응력 분포 (MPa) - 최대 하중 시점 (12.33 s)",
                          stress, ["", "S_{11}", "S_{22}", "S_{33}"], RIGHT_W, STRESS_NOTE)
    right_end = r

    # --- 꼬리말 ---
    foot_row = max(left_end, mid_end, right_end) + 2
    foot = ws.cell(foot_row, COL_L)
    foot.value = FOOTNOTE
    foot.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    foot.alignment = Alignment(horizontal="left", vertical="center")

    # --- 행 높이: 본문 전 구간 동일 (줄바꿈 표가 늘린 행은 유지) ---
    for r_i in range(4, foot_row + 1):
        cur = ws.row_dimensions[r_i].height
        ws.row_dimensions[r_i].height = max(cur or ROW_H, ROW_H)

    # --- 인쇄: 한 페이지 가로, 여백 좁게, 가로 가운데 ---
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.paperSize = ws.PAPERSIZE_A3 if PAPER == "A3" else ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5, header=0.3, footer=0.3)
    ws.print_options.horizontalCentered = True
    ws.print_area = f"B2:{get_column_letter(COL_LAST)}{foot_row}"

    # --- 문서 속성: 기본값 'openpyxl' 그대로 두면 파일 정보에 그대로 찍힘 ---
    try:
        author = AUTHOR or getpass.getuser()
    except Exception:
        author = ""
    wb.properties.creator = author
    wb.properties.lastModifiedBy = author
    wb.properties.title = "인장시편 DFLUX 해석 정리"

    if with_graph:
        from excel_chart import add_chart_sheet      # 필요할 때만 불러옴
        add_chart_sheet(wb, ws)

    wb.save(dst_path)
    print(f"정리 완료: {dst_path}")


def clean_dflux_workbook(src_path: str, dst_path: str, with_graph: bool = False):
    print(f"원본 읽는 중: {src_path}")
    data = read_source_data(src_path)
    build_report(data, dst_path, with_graph=with_graph)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    src = args[0] if len(args) > 0 else "인장시편_DFLUX_해석정리_한페이지 (1).xlsx"
    dst = args[1] if len(args) > 1 else "인장시편_DFLUX_해석정리_수정본.xlsx"
    clean_dflux_workbook(src, dst, with_graph="--graph" in sys.argv)
