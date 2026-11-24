"""
엑셀 차트(그래프) 넣기 — 보고서와 같은 톤(맑은 고딕, 무채색 위주, 축 제목은 진짜 아래첨자)

[언제 쓰나]
  1) 이미 만든 보고서 파일에 '그래프' 시트 추가
         python excel_chart.py 인장시편_DFLUX_해석정리_수정본_v3.xlsx [결과.xlsx]
  2) 보고서 만들 때 같이
         python excel_auto_cleaner.py 원본.xlsx 결과.xlsx --graph
  3) 코드에서 원하는 자리에 차트 하나 (표 데이터 참조 → 표 값을 고치면 그래프도 같이 바뀜)
         from excel_chart import add_xy_chart, table_series
         pos = find_axis_table_positions(ws)["temp_y"]
         add_xy_chart(ws, table_series(ws, pos), slot=(6, 13, 50, 60),
                      x_title="y (mm)", y_title="온도 (℃)")
  4) 파이썬 리스트(예: Abaqus 에서 뽑은 RF2-시간 이력) → '차트데이터' 시트에 표로 쓰고 차트
         data_ws, pos = write_chart_data(wb, ["시간 (s)", "RF2 (N)"], rows, title="반력 이력")
         add_xy_chart(ws, table_series(data_ws, pos), anchor="F50", width_px=320, height_px=200,
                      x_title="시간 (s)", y_title="RF2 (N)")
  5) 그래프가 이미 그림 파일(PNG/JPG)일 때
         add_image_graph(ws, "graph.png", c1=6, c2=9, top_row=50)

[왜 꺾은선(Line)이 아니라 분산형(Scatter)인가]
  꺾은선형은 X 값을 '항목'으로 취급해서 19.68, 25.58, 32, 35 … 처럼 간격이 다른 좌표도
  같은 간격으로 찍음 → 거리 축이 틀어짐. 좌표/시간처럼 숫자 X 는 분산형(직선+표식)이 맞음.

[첨자]
  축 제목·차트 제목 : 'S_{22} (MPa)', 'RF2 (N)' 처럼 쓰면 진짜 아래첨자 (셀과 같은 규칙 자동 적용)
  범례              : 엑셀이 범례 글자별 서식을 지원하지 않아서 유니코드 첨자(S₂₂)로 표시
"""
import io
import math
import sys

import openpyxl
from openpyxl.chart import Reference, ScatterChart, Series
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.data_source import NumFmt
from openpyxl.chart.marker import Marker
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText, Text
from openpyxl.chart.title import Title
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor, TwoCellAnchor
from openpyxl.drawing.text import (
    CharacterProperties,
    Font as DrawingFont,
    Paragraph,
    ParagraphProperties,
    RegularTextRun,
    RichTextProperties,
)
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from excel_auto_cleaner import (
    COLOR_SUBTEXT,
    COLOR_TEXT,
    FONT_NAME,
    IMG_PAD_PX,
    ROW_H,
    SIDE_DARK_MED,
    SIDE_DARK_THIN,
    TOKEN_PATTERN,
    _is_num,
    auto_mark_scripts,
    col_px,
    find_axis_table_positions,
    get_col_width,
    place_images_row,
    set_col_width,
    write_section_title,
    write_table,
)

# ============================================================
# 1. 차트 스타일
#    - 오피스 기본 파랑/주황(4472C4, ED7D31)은 '기본 차트' 티가 나서 안 씀
#    - 흑백 인쇄해도 구분되게 색 + 표식 모양 + 선 종류를 같이 바꿈
# ============================================================
SERIES_STYLES = [
    {"color": "262626", "marker": "circle", "dash": None},      # 진회색 실선 ●
    {"color": "2F5F8A", "marker": "square", "dash": "dash"},    # 남색 파선 ■
    {"color": "A23B32", "marker": "triangle", "dash": "sysDot"},  # 벽돌색 점선 ▲
    {"color": "7F7F7F", "marker": "diamond", "dash": "lgDash"},  # 회색 긴파선 ◆
]
LINE_W = 15875          # 1.25 pt (EMU)
AXIS_LINE = "7F7F7F"
GRID_LINE = "D9D9D9"
TICK_FONT_PT = 8
AXIS_TITLE_PT = 8.5
CHART_TITLE_PT = 9

_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def to_unicode_scripts(text: str) -> str:
    """범례용: 'S_{22}' / 'S22' → 'S₂₂'. 숫자만 바꾸고 글자 첨자(Y_C 등)는 그대로 둠"""
    out = []
    for part in TOKEN_PATTERN.split(auto_mark_scripts(str(text))):
        if part.startswith("_{") and part.endswith("}"):
            inner = part[2:-1]
            out.append(inner.translate(_SUB) if inner.isdigit() else inner)
        elif part.startswith("^{") and part.endswith("}"):
            inner = part[2:-1]
            out.append(inner.translate(_SUP) if inner.isdigit() else inner)
        else:
            out.append(part)
    return "".join(out)


def _cp(size: float, bold: bool = False, color: str = COLOR_TEXT, baseline: int = None):
    """차트 글자 속성. 한글(ea)·영문(latin) 모두 맑은 고딕으로 지정해야 한글이 다른 글꼴로 안 빠짐"""
    return CharacterProperties(
        latin=DrawingFont(typeface=FONT_NAME),
        ea=DrawingFont(typeface=FONT_NAME),
        sz=int(round(size * 100)),
        b=bold,
        solidFill=color,
        baseline=baseline,
    )


def text_props(size: float = TICK_FONT_PT, color: str = COLOR_SUBTEXT, bold: bool = False):
    """눈금 숫자·범례 같은 '글자 전체' 서식"""
    return RichText(
        bodyPr=RichTextProperties(),
        p=[Paragraph(pPr=ParagraphProperties(defRPr=_cp(size, bold, color)), endParaRPr=_cp(size, bold, color),
                     r=[])],   # r=None 이면 openpyxl 이 빈 글자 조각을 하나 넣음
    )


def make_title(text: str, size: float = CHART_TITLE_PT, bold: bool = True, vertical: bool = False):
    """
    제목/축 제목. 'S_{22} (MPa)', 'mW/mm^{3}' 또는 'S22', 'RF2' 처럼 쓰면 해당 글자만 진짜 첨자
    (DrawingML baseline: -25000 = 아래첨자, 30000 = 위첨자)
    """
    runs = []
    for part in TOKEN_PATTERN.split(auto_mark_scripts(text)):
        if not part:
            continue
        baseline = None
        if part.startswith("_{") and part.endswith("}"):
            baseline, part = -25000, part[2:-1]
        elif part.startswith("^{") and part.endswith("}"):
            baseline, part = 30000, part[2:-1]
        runs.append(RegularTextRun(rPr=_cp(size, bold, COLOR_TEXT, baseline), t=part))
    body = RichTextProperties(rot=-5400000, vert="horz") if vertical else RichTextProperties()
    para = Paragraph(pPr=ParagraphProperties(defRPr=_cp(size, bold)), r=runs)
    return Title(tx=Text(rich=RichText(bodyPr=body, p=[para])), overlay=False)


# ============================================================
# 2. 축 범위 (엑셀 자동 범위는 온도도 0 부터 시작해서 그래프가 납작해짐 → 데이터에 맞춰 깔끔한 눈금)
# ============================================================
def nice_axis(lo: float, hi: float, ticks: int = 6):
    """(최소, 최대, 주 눈금 간격). 간격은 1·2·2.5·5 × 10ⁿ 중에서"""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw - 1e-12)
    return math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step, step


def _axis_format(step: float) -> str:
    if step >= 1:
        return "0"
    digits = max(0, -math.floor(math.log10(step) + 1e-9))
    return "0." + "0" * digits


def series_values(series: list, which: str) -> list:
    """시리즈들이 참조하는 셀 값. 수식 셀이 섞여 있으면 None (그때는 엑셀 자동 범위)"""
    vals = []
    for s in series:
        col = s["x_col"] if which == "x" else s["y_col"]
        for r in range(s["first_row"], s["last_row"] + 1):
            v = s["ws"].cell(r, col).value
            if not _is_num(v):
                return None
            vals.append(v)
    return vals


def auto_axis(series: list, which: str):
    vals = series_values(series, which)
    if not vals:
        return None
    lo, hi, step = nice_axis(min(vals), max(vals))
    return {"min": lo, "max": hi, "major": step}


# ============================================================
# 3. 시리즈 정의
# ============================================================
def xy_series(data_ws, x_col: int, y_col: int, first_row: int, last_row: int, name: str) -> dict:
    """한 줄(시리즈) = X 열 + Y 열 + 행 범위 + 범례 이름"""
    return {"ws": data_ws, "x_col": x_col, "y_col": y_col,
            "first_row": first_row, "last_row": last_row, "name": name}


def table_series(data_ws, pos: dict, value_idx: list = None, names: list = None) -> list:
    """
    표 위치(pos) → 시리즈 목록. pos 는 find_axis_table_positions() 또는 write_chart_data() 결과.
    value_idx: 값 열 중 몇 번째를 쓸지 (None = 전부), names: 범례 이름 (None = 머리글)
    """
    idx = list(range(len(pos["value_cols"]))) if value_idx is None else value_idx
    out = []
    for k, i in enumerate(idx):
        name = names[k] if names else pos["headers"][i]
        out.append(xy_series(data_ws, pos["x_col"], pos["value_cols"][i], pos["first_row"], pos["last_row"], name))
    return out


# ============================================================
# 4. 차트 만들기 / 배치
# ============================================================
def _style_axis(ax, title, rng, vertical, gridlines):
    ax.delete = False
    if title:
        ax.title = make_title(title, AXIS_TITLE_PT, bold=False, vertical=vertical)
    ax.majorTickMark = "out"
    ax.minorTickMark = "none"
    ax.tickLblPos = "low"                      # 음수 값이 있어도 눈금 숫자는 항상 바깥쪽
    ax.crosses = "min"                         # 축선은 항상 왼쪽/아래 (L자 테두리)
    ax.spPr = GraphicalProperties(ln=LineProperties(solidFill=AXIS_LINE, w=9525))
    ax.txPr = text_props(TICK_FONT_PT, COLOR_SUBTEXT)
    ax.majorGridlines = (
        ChartLines(spPr=GraphicalProperties(ln=LineProperties(solidFill=GRID_LINE, w=6350))) if gridlines else None
    )
    if rng:
        ax.scaling.min = rng["min"]
        ax.scaling.max = rng["max"]
        ax.majorUnit = rng["major"]
        ax.numFmt = NumFmt(formatCode=rng.get("fmt") or _axis_format(rng["major"]), sourceLinked=False)


def make_xy_chart(
    series: list,
    title: str = None,
    x_title: str = None,
    y_title: str = None,
    x_axis: dict = None,
    y_axis: dict = None,
    markers: bool = True,
    legend: str = "t",
):
    """
    분산형(직선+표식) 차트 객체만 만듦. 배치는 add_xy_chart / place_chart.
    x_axis / y_axis: {"min":, "max":, "major":, "fmt":} 직접 지정. None 이면 데이터 보고 자동,
                     "excel" 이면 엑셀 자동 범위.
    legend: "t"(위) / "r"(오른쪽) / "b" / None(숨김). 시리즈가 1개면 자동으로 숨김
    """
    ch = ScatterChart()
    ch.scatterStyle = "lineMarker"
    ch.roundedCorners = False
    ch.style = None
    ch.graphical_properties = GraphicalProperties(ln=LineProperties(noFill=True))   # 차트 바깥 테두리 없음
    ch.plot_area.spPr = GraphicalProperties(noFill=True, ln=LineProperties(noFill=True))
    if title:
        ch.title = make_title(title, CHART_TITLE_PT, bold=True)

    for i, s in enumerate(series):
        st = SERIES_STYLES[i % len(SERIES_STYLES)]
        xref = Reference(s["ws"], min_col=s["x_col"], min_row=s["first_row"], max_row=s["last_row"])
        yref = Reference(s["ws"], min_col=s["y_col"], min_row=s["first_row"], max_row=s["last_row"])
        ser = Series(yref, xref, title=to_unicode_scripts(s["name"]))
        ser.smooth = False
        ser.graphicalProperties = GraphicalProperties(
            ln=LineProperties(solidFill=st["color"], w=LINE_W, prstDash=st["dash"])
        )
        if markers:
            ser.marker = Marker(symbol=st["marker"], size=5)
            ser.marker.graphicalProperties = GraphicalProperties(
                solidFill=st["color"], ln=LineProperties(solidFill=st["color"], w=6350)
            )
        else:
            ser.marker = Marker(symbol="none")
        ch.series.append(ser)

    xr = None if x_axis == "excel" else (x_axis or auto_axis(series, "x"))
    yr = None if y_axis == "excel" else (y_axis or auto_axis(series, "y"))
    _style_axis(ch.x_axis, x_title, xr, vertical=False, gridlines=False)
    _style_axis(ch.y_axis, y_title, yr, vertical=True, gridlines=True)
    ch.x_axis.axPos, ch.y_axis.axPos = "b", "l"      # openpyxl 기본값은 둘 다 "l"

    if legend and len(series) > 1:
        ch.legend.position = legend
        ch.legend.overlay = False
        ch.legend.txPr = text_props(TICK_FONT_PT, COLOR_TEXT)
    else:
        ch.legend = None
    return ch


def _row_px(ws, r: int) -> int:
    return round((ws.row_dimensions[r].height or ROW_H) * 96 / 72)


def place_chart(ws, chart, slot: tuple = None, anchor: str = None, width_px: int = 320, height_px: int = 200):
    """
    slot=(시작열, 끝열, 시작행, 끝행): 그 칸 영역에 딱 맞게 (네 모서리를 셀에 고정 → 열 너비 단위가 달라도 칸 밖으로 안 나감)
    anchor="F50" + width_px/height_px: 그 셀 왼쪽 위에서 고정 크기
    """
    if slot:
        c1, c2, r1, r2 = slot
        pad = IMG_PAD_PX
        a = TwoCellAnchor(editAs="oneCell")
        a._from = AnchorMarker(col=c1 - 1, colOff=pixels_to_EMU(pad), row=r1 - 1, rowOff=pixels_to_EMU(pad))
        a.to = AnchorMarker(
            col=c2 - 1, colOff=pixels_to_EMU(col_px(get_col_width(ws, c2)) - pad),
            row=r2 - 1, rowOff=pixels_to_EMU(_row_px(ws, r2) - pad),
        )
    else:
        cell = ws[anchor]
        a = OneCellAnchor(
            _from=AnchorMarker(col=cell.column - 1, colOff=0, row=cell.row - 1, rowOff=0),
            ext=XDRPositiveSize2D(pixels_to_EMU(width_px), pixels_to_EMU(height_px)),
        )
    ws.add_chart(chart, a)
    return chart


def add_xy_chart(ws, series: list, slot: tuple = None, anchor: str = None,
                 width_px: int = 320, height_px: int = 200, **chart_kwargs):
    """make_xy_chart + place_chart 한 번에. chart_kwargs 는 make_xy_chart 인자 그대로"""
    return place_chart(ws, make_xy_chart(series, **chart_kwargs), slot, anchor, width_px, height_px)


def add_image_graph(ws, image_path: str, c1: int, c2: int, top_row: int) -> int:
    """그래프가 그림 파일일 때: 칸(c1~c2) 폭에 맞춰 비율 유지로 넣음. 반환값 = 차지한 행 수"""
    with open(image_path, "rb") as f:
        buf = io.BytesIO(f.read())
    return place_images_row(ws, top_row, [(c1, c2)], [buf])


# ============================================================
# 5. 파이썬 데이터 → 차트용 표
# ============================================================
def write_chart_data(wb, headers: list, rows: list, title: str = None,
                     sheet_name: str = "차트데이터", col_width: float = 9.0, num_format: str = "0.00"):
    """
    데이터 시트(없으면 만듦)의 빈 곳(아래쪽)에 표를 쓰고 (시트, 표 위치)를 돌려줌.
    첫 열 = X, 나머지 열 = Y 시리즈. 표 서식은 보고서 표와 동일.
    num_format 은 셀 표시 형식만 바꿈 (차트는 원래 값 그대로 사용)
    """
    if sheet_name in wb.sheetnames:
        ds = wb[sheet_name]
        top = ds.max_row + 2
    else:
        ds = wb.create_sheet(sheet_name)
        ds.sheet_view.showGridLines = False
        set_col_width(ds, 1, 1.75)
        top = 2
    n = len(headers)
    if title:
        write_section_title(ds, top, 2, 1 + n, title)
        top += 1
    last = write_table(ds, top, 2, headers, rows, [col_width] * n,
                       col_aligns=["right"] * n, num_formats=[num_format] * n,
                       top_side=SIDE_DARK_MED if title else SIDE_DARK_THIN)
    for r in range(2, last + 1):
        ds.row_dimensions[r].height = ROW_H
    pos = {"header_row": top, "x_col": 2, "value_cols": list(range(3, 2 + n)),
           "headers": list(headers[1:]), "first_row": top + 1, "last_row": last}
    return ds, pos


# ============================================================
# 6. 예시: 보고서 9~10번 표로 '그래프' 시트 만들기
# ============================================================
def add_chart_sheet(wb, report_ws=None, sheet_name: str = "그래프"):
    report_ws = report_ws or wb.worksheets[0]
    pos = find_axis_table_positions(report_ws)
    need = ["temp_y", "temp_x", "stress_y", "stress_x"]
    missing = [k for k in need if k not in pos]
    if missing:
        print(f"  [그래프] 표를 못 찾아서 건너뜀: {missing}")
        return None

    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    gs = wb.create_sheet(sheet_name)
    gs.sheet_view.showGridLines = False
    gs.sheet_view.zoomScale = 85
    gs.sheet_format.defaultRowHeight = 16.5

    # 열: A 여백 | B~G 차트 | H 간격 | I~N 차트   (10.0 × 6열 = 480 px)
    SLOT_N, SLOT_W, CHART_ROWS = 6, 10.0, 11
    L1, L2 = 2, 2 + SLOT_N + 1
    set_col_width(gs, 1, 1.75)
    set_col_width(gs, L1 + SLOT_N, 2.75)
    for c in list(range(L1, L1 + SLOT_N)) + list(range(L2, L2 + SLOT_N)):
        set_col_width(gs, c, SLOT_W)
    last_col = L2 + SLOT_N - 1

    t = gs.cell(2, L1)
    t.value = "해석 결과 그래프"
    t.font = Font(name=FONT_NAME, size=13.0, bold=True, color=COLOR_TEXT)
    t.alignment = Alignment(horizontal="left", vertical="center")
    gs.row_dimensions[1].height = 10.0
    gs.row_dimensions[2].height = 28.0
    gs.row_dimensions[3].height = 10.0

    # 같은 물리량·같은 방향은 같은 축 범위로 (위아래/좌우 그래프를 바로 비교할 수 있게)
    def shared(which, *series_lists):
        vals = []
        for sl in series_lists:
            v = series_values(sl, which)
            if v is None:
                return "excel"
            vals += v
        lo, hi, step = nice_axis(min(vals), max(vals))
        return {"min": lo, "max": hi, "major": step}

    ty = table_series(report_ws, pos["temp_y"])
    tx = table_series(report_ws, pos["temp_x"])
    sy = table_series(report_ws, pos["stress_y"])
    sx = table_series(report_ws, pos["stress_x"])
    temp_y_rng, stress_y_rng = shared("y", ty, tx), shared("y", sy, sx)
    len_x_rng, wid_x_rng = shared("x", ty, sy), shared("x", tx, sx)

    blocks = [
        (4, [("온도 분포 (℃) - 길이(y) 방향", ty, "y (mm)", "온도 (℃)", len_x_rng, temp_y_rng),
             ("온도 분포 (℃) - 폭(x) 방향", tx, "x (mm)", "온도 (℃)", wid_x_rng, temp_y_rng)]),
        (4 + CHART_ROWS + 2,
         [("응력 분포 (MPa) - 길이(y) 방향, 12.33 s", sy, "y (mm)", "응력 (MPa)", len_x_rng, stress_y_rng),
          ("응력 분포 (MPa) - 폭(x) 방향, 12.33 s", sx, "x (mm)", "응력 (MPa)", wid_x_rng, stress_y_rng)]),
    ]
    for top, pair in blocks:
        for (c1, (title, series, xt, yt, xr, yr)) in zip((L1, L2), pair):
            write_section_title(gs, top, c1, c1 + SLOT_N - 1, title)
            add_xy_chart(gs, series, slot=(c1, c1 + SLOT_N - 1, top + 1, top + CHART_ROWS),
                         x_title=xt, y_title=yt, x_axis=xr, y_axis=yr)

    foot_row = blocks[-1][0] + CHART_ROWS + 2
    f = gs.cell(foot_row, L1)
    f.value = f"※ '{report_ws.title}' 시트 9~10번 표를 직접 참조 (표 값을 고치면 그래프도 같이 바뀜). 범례 첨자는 유니코드 표기."
    f.font = Font(name=FONT_NAME, size=8.5, color=COLOR_SUBTEXT)
    for r in range(4, foot_row + 1):
        gs.row_dimensions[r].height = ROW_H

    gs.page_setup.orientation = gs.ORIENTATION_LANDSCAPE
    gs.page_setup.paperSize = gs.PAPERSIZE_A4
    gs.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    gs.page_setup.fitToWidth = 1
    gs.page_setup.fitToHeight = 1
    gs.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5, header=0.3, footer=0.3)
    gs.print_options.horizontalCentered = True
    gs.print_area = f"B2:{get_column_letter(last_col)}{foot_row}"
    print(f"  [그래프] '{sheet_name}' 시트에 차트 4개 추가")
    return gs


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python excel_chart.py 보고서.xlsx [결과.xlsx]")
        sys.exit(1)
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.replace(".xlsx", "_그래프.xlsx")
    wb = openpyxl.load_workbook(src, rich_text=True)      # rich_text=True 로 열어야 셀 첨자가 안 풀림
    add_chart_sheet(wb, wb.worksheets[0])
    wb.calculation.fullCalcOnLoad = True                  # 다시 저장하면 수식 결과값이 비므로 열 때 재계산
    wb.save(dst)
    print(f"저장: {dst}")
