"""API 키 없이 동작하는 '기본 정리' 모드.

AI 대신 정해진 규칙으로 메모에서 날짜·사람·할 일·숫자를 찾아 DocPlan을 만든다.
문장을 다듬거나 내용을 이해해서 재구성하지는 못한다.
"""
from __future__ import annotations

import calendar
import datetime as dt
import re

from planner import DocPlan

TITLES = "사원|주임|선임|책임|수석|대리|과장|차장|부장|팀장|실장|본부장|이사|상무|전무|대표|매니저"
WEEKDAYS = "월화수목금토일"
NUM = r"\d[\d,.]*\s*(?:억\s*(?:\d[\d,.]*\s*(?:천만|백만|만|천)?)?|천만|백만|만|천)?"
UNIT = {"천만": 1e7, "백만": 1e6, "만": 1e4, "천": 1e3}


# ───────────── 날짜 ─────────────

def _ymd(year: int, month: int, day: int) -> str | None:
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


def _guess_year(month: int, day: int, today: dt.date) -> str | None:
    """연도가 없는 날짜. 반년 넘게 지난 날짜면 내년으로 본다."""
    iso = _ymd(today.year, month, day)
    if iso and (today - dt.date.fromisoformat(iso)).days > 180:
        return _ymd(today.year + 1, month, day)
    return iso


def find_date(text: str, today: dt.date, relative: bool = True) -> str | None:
    """글 안에서 마지막으로 나오는 날짜 표현을 실제 날짜로 바꾼다."""
    found: list[tuple[int, str | None]] = []

    def add(m, value):
        found.append((m.start(), value))

    for m in re.finditer(r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", text):
        add(m, _ymd(int(m[1]), int(m[2]), int(m[3])))
    for m in re.finditer(r"(?<![\d./-])(\d{1,2})월\s*(\d{1,2})일", text):
        add(m, _guess_year(int(m[1]), int(m[2]), today))
    for m in re.finditer(r"(?<![\d./-])(\d{1,2})/(\d{1,2})(?![\d/])", text):
        add(m, _guess_year(int(m[1]), int(m[2]), today))
    if relative:
        for m in re.finditer(r"(\d{1,2})월\s*(첫|둘|셋|넷|[1-4])\s*째?\s*주", text):
            nth = {"첫": 1, "둘": 2, "셋": 3, "넷": 4}.get(m[2]) or int(m[2])
            month = int(m[1])
            year = today.year + (1 if month < today.month else 0)
            first = dt.date(year, month, 1)
            friday = first + dt.timedelta(days=(4 - first.weekday()) % 7 + 7 * (nth - 1))
            add(m, friday.isoformat())
        for m in re.finditer(r"(\d{1,2})월\s*말", text):
            month = int(m[1])
            year = today.year + (1 if month < today.month else 0)
            add(m, _ymd(year, month, calendar.monthrange(year, month)[1]))
        for m in re.finditer(r"(이번|다음|다다음)\s*주\s*(?:([월화수목금토일])요일)?", text):
            weeks = {"이번": 0, "다음": 1, "다다음": 2}[m[1]]
            weekday = WEEKDAYS.index(m[2]) if m[2] else 4  # 요일이 없으면 금요일
            monday = today - dt.timedelta(days=today.weekday())
            add(m, (monday + dt.timedelta(days=7 * weeks + weekday)).isoformat())
        for word, days in (("오늘", 0), ("내일", 1), ("모레", 2)):
            for m in re.finditer(word, text):
                add(m, (today + dt.timedelta(days=days)).isoformat())
        for m in re.finditer(r"(이번\s*달|월)\s*말", text):
            add(m, _ymd(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))
        for m in re.finditer(r"연말", text):
            add(m, _ymd(today.year, 12, 31))
    found = [f for f in found if f[1]]
    return max(found)[1] if found else None


# ───────────── 사람 ─────────────

class People:
    def __init__(self, text: str) -> None:
        self.full: dict[str, str] = {}  # 이름 -> "이름 직함"
        for m in re.finditer(rf"(?<![가-힣])([가-힣]{{2,3}})\s?({TITLES})(?![가-힣]{{2}})", text):
            self.full.setdefault(m[1], f"{m[1]} {m[2]}")

    def first_in(self, text: str) -> tuple[str, tuple[int, int]] | None:
        """글에서 가장 먼저 나오는 사람과 그 위치."""
        hits = []
        for name, display in self.full.items():
            m = re.search(rf"{name}(\s?(?:{TITLES}))?", text)
            if m:
                hits.append((m.start(), m.end(), display))
        # "김과장"처럼 성+직함만 쓴 경우
        for m in re.finditer(rf"(?<![가-힣])([가-힣])\s?({TITLES})", text):
            matches = [d for n, d in self.full.items() if n[0] == m[1] and d.endswith(m[2])]
            display = matches[0] if len(matches) == 1 else f"{m[1]} {m[2]}"
            hits.append((m.start(), m.end(), display))
        if not hits:
            return None
        start, end, display = min(hits)
        return display, (start, end)


# ───────────── 숫자 ─────────────

def parse_amount(text: str) -> tuple[float, bool] | None:
    """'1억2천' → (120000000, True). 두 번째 값은 단위가 분명한지 여부."""
    s = re.sub(r"[\s,]", "", text)
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)억)?(?:(\d+(?:\.\d+)?)(천만|백만|만|천)?)?", s)
    if not m or (m[1] is None and m[2] is None):
        return None
    if m[1] is not None:
        value = float(m[1]) * 1e8
        if m[2] is not None:
            # "1억 2천", "1억 5000"은 말할 때 만 단위를 생략한 것
            rest = float(m[2]) * UNIT.get(m[3], 1.0)
            value += rest if m[3] in ("천만", "백만", "만") else rest * 1e4
        return value, True
    if m[3] in ("천만", "백만", "만"):
        return float(m[2]) * UNIT[m[3]], True
    return float(m[2]) * UNIT.get(m[3], 1.0), m[3] is None and False


def parse_series(line: str):
    """'3분기 매출 - 서울 1억2천, 부산 8천' → ('3분기 매출', [(서울, 값, 분명, 단위), ...])"""
    items = [p.strip() for p in re.split(r"[,，、]", line) if p.strip()]
    if len(items) < 2:
        return None
    first = re.search(rf"([가-힣A-Za-z]+)\s*({NUM})\s*(원|명|개|건|%)?\s*$", items[0])
    if not first:
        return None
    prefix = re.sub(r"\s*(?:[-–:=]|은|는|이|가)\s*$", "", items[0][: first.start()].strip())
    parsed = []
    for label, number, unit in [(first[1], first[2], first[3])] + [
        (m[1].strip(), m[2], m[3])
        for m in (re.match(rf"([가-힣A-Za-z ]+?)\s*({NUM})\s*(원|명|개|건|%)?", i) for i in items[1:])
        if m
    ]:
        amount = parse_amount(number)
        if amount:
            parsed.append((label, amount[0], amount[1], unit or ""))
    if len(parsed) < 2 or len(parsed) * 2 < len(items):
        return None
    return prefix or "값", parsed


def _fmt(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _raw(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# ───────────── 메모 → DocPlan ─────────────

ACTION_WORDS = r"하기$|하기로|기로(?:\s|$|\.)|할\s?것|해야|예정|제출|->|→|=>"
DOC_TYPES = [("회의|미팅", "회의록"), ("보고", "업무 보고서"), ("계획|일정", "업무 계획서"),
             ("공지|안내", "공지문")]


def _plan_from_notes(notes: str, today: dt.date) -> dict:
    lines = [l.strip(" \t-•*·") for l in notes.splitlines() if l.strip(" \t-•*·")]
    missing: list[str] = []
    people = People(notes)

    # 제목과 문서 종류
    first = lines[0]
    doc_type = next((t for pat, t in DOC_TYPES if re.search(pat, notes)), "업무 메모")
    doc_date = find_date(first, today, relative=False)
    title = re.split(r"[,，]", first)[0]
    title = re.sub(r"\d{4}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}일?|\d{1,2}월\s*\d{1,2}일|\d{1,2}/\d{1,2}",
                   "", title).strip(" -:.")
    if not title or len(title) > 40:
        title = doc_type
    elif doc_type == "회의록" and "회의록" not in title:
        title += " 회의록"
    if not doc_date:
        doc_date = today.isoformat()
        missing.append("문서 날짜 (메모에 날짜가 없어 오늘 날짜로 넣었습니다)")

    # 개요
    overview = [f"일시: {doc_date}"]
    place = re.search(r"(\d+층\s?[가-힣A-Za-z]+|[가-힣A-Za-z]*회의실)", first)
    if place:
        overview.append(f"장소: {place[1]}")
    attendee_line = next((l for l in lines if re.match(r"참석자?\b|참석", l)), None)
    if attendee_line:
        overview.append("참석: " + re.sub(r"^참석자?\s*[:：]?\s*", "", attendee_line))

    # 본문 문장 단위로 나누기
    sentences: list[str] = []
    for line in lines[1:]:
        if line is attendee_line:
            continue
        sentences += [s.strip() for s in re.split(r"(?<=[가-힣])\.\s+", line) if s.strip()]
    if not sentences and len(lines) == 1:
        sentences = [first]

    # 숫자 묶음
    groups: list[dict] = []  # {labels, series: [(name, values, explicit, unit)]}
    for s in sentences:
        parsed = parse_series(s)
        if not parsed:
            continue
        name, items = parsed
        labels = [i[0] for i in items]
        group = next((g for g in groups if g["labels"] == labels), None)
        if group is None:
            group = {"labels": labels, "series": []}
            groups.append(group)
        group["series"].append([name, [i[1] for i in items], [i[2] for i in items], items[0][3]])

    data_sections, sheets = [], []
    for g in groups:
        flat = [(v, e) for s in g["series"] for v, e in zip(s[1], s[2])]
        if any(e and v >= 1e7 for v, e in flat) and any(not e and v < 1e5 for v, e in flat):
            # "1억, 8천, 4500"처럼 섞여 있으면 작은 숫자는 만 단위를 생략한 것으로 본다
            for s in g["series"]:
                s[1] = [v if e else v * 1e4 for v, e in zip(s[1], s[2])]
            missing.append(
                "숫자 단위 확인 ('8천', '4500'처럼 단위가 없는 숫자는 만 단위로 보고 "
                "8,000만, 4,500만으로 넣었습니다)"
            )
        names = [s[0] for s in g["series"]]
        money = any(e for s in g["series"] for e in s[2]) or any(s[3] == "원" for s in g["series"])
        kind = "percent" if g["series"][0][3] == "%" else "currency" if money else "number"
        columns = ["항목"] + names
        data_sections.append({
            "heading": " / ".join(names), "paragraphs": [], "bullets": [],
            "table": {"columns": columns, "rows": [
                [label] + [_fmt(s[1][i]) for s in g["series"]] for i, label in enumerate(g["labels"])]},
        })
        sheets.append({
            "name": names[0],
            "columns": [{"name": "항목", "kind": "text"}] + [{"name": n, "kind": kind} for n in names],
            "rows": [[label] + [_raw(s[1][i]) for s in g["series"]] for i, label in enumerate(g["labels"])],
            "add_total_row": kind != "percent",
            "chart": {"kind": "bar", "title": " / ".join(names), "category_column": "항목",
                      "value_columns": names},
        })

    # 할 일
    actions = []
    for s in sentences:
        arrow = re.split(r"->|→|=>", s)
        clause = arrow[-1].strip()
        due_match = re.search(r"(.{1,20}?)까지", clause)
        due = find_date(due_match[1], today) if due_match else None
        if not (due or len(arrow) > 1 or re.search(ACTION_WORDS, s)):
            continue
        if parse_series(s):
            continue
        task, owner = clause, "미정"
        hit = people.first_in(clause)
        if hit:
            owner, (a, b) = hit
            task = task[:a] + re.sub(r"^(?:이|가|은|는|께서)?\s*", "", task[b:])
        if due_match and due:
            task = re.sub(r"\s*(?:\d{1,2}월\s*)?[^\s]*\s?[^\s]*까지", "", task, count=1) \
                if not re.search(re.escape(due_match[1].strip()) + r"\s*까지", task) \
                else task.replace(re.search(re.escape(due_match[1].strip()) + r"\s*까지", task)[0], "")
        task = re.sub(r"\s+", " ", task).strip(" .,")
        task = re.sub(r"(해서|하여)$", "", task)
        task = re.sub(r"기로(\s?함)?$", "기", task)
        task = re.sub(r"(?<=[가-힣]{2})(은|는|을|를)$", "", task).strip()
        if not task:
            continue
        actions.append({"task": task, "owner": owner, "due": due or "미정", "status": "예정"})
        if owner == "미정":
            missing.append(f"'{task}'의 담당자")
        if not due:
            missing.append(f"'{task}'의 기한")

    if actions:
        sheets.append({
            "name": "할 일",
            "columns": [{"name": "할 일", "kind": "text"}, {"name": "담당자", "kind": "text"},
                        {"name": "기한", "kind": "date"}, {"name": "상태", "kind": "text"}],
            "rows": [[a["task"], a["owner"], a["due"], a["status"]] for a in actions],
            "add_total_row": False, "chart": None,
        })

    sections = [{"heading": "개요", "paragraphs": [], "bullets": overview, "table": None}]
    if sentences:
        sections.append({"heading": "주요 내용", "paragraphs": [], "bullets": sentences, "table": None})
    sections += data_sections
    summary = f"메모 내용 {len(sentences)}건"
    summary += f", 할 일 {len(actions)}건" if actions else ""
    summary += f", 수치 표 {len(groups)}개" if groups else ""
    summary += " 정리."
    return {"title": title, "doc_type": doc_type, "date": doc_date, "author": "",
            "summary": summary, "sections": sections,
            "action_items": actions, "sheets": sheets, "missing_info": missing}


# ───────────── 올린 엑셀 정리 ─────────────

TOTAL_WORDS = re.compile(r"^\s*(합\s*계|총\s*계|소\s*계|계|총합|total|sum)\s*$", re.I)
NOT_A_NUMBER = re.compile(r"번호|순번|코드|연도|년도|전화|우편|^no\.?$|^id$", re.I)
MONEY = re.compile(r"금액|매출|가격|단가|비용|예산|급여|원가|수익|이익|지출|수입|결제|\(원\)|원$")
TIME_AXIS = re.compile(r"날짜|일자|월$|^월|분기|연도|년도|기간|주차")


def _as_number(text: str) -> float | None:
    cleaned = re.sub(r"[,\s원₩$%]", "", text)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _as_date(text: str) -> str | None:
    m = re.match(r"\s*(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})(?:[ T]00:00:00)?\.?\s*$", text)
    return _ymd(int(m[1]), int(m[2]), int(m[3])) if m else None


def _clean_sheet(name: str, rows: list[list[str]], notices: list[str]) -> dict | None:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    # 머리글 행: 채워진 칸이 2개 이상이고 표 너비의 절반 이상을 채운 첫 행
    header_at = next(
        (i for i, r in enumerate(rows)
         if sum(1 for c in r if c.strip()) >= max(2, (width + 1) // 2)
         and all(_as_number(c) is None for c in r if c.strip())),
        None,
    )
    if header_at is None:
        header, body = [f"열{i + 1}" for i in range(width)], rows
        notices.append(f"'{name}' 시트에서 머리글 행을 찾지 못해 '열1, 열2…'로 이름을 붙였습니다.")
    else:
        header, body = rows[header_at], rows[header_at + 1:]
    header = [(h.strip() or f"열{i + 1}") for i, h in enumerate(header + [""] * (width - len(header)))]
    seen: dict[str, int] = {}
    for i, h in enumerate(header):  # 같은 이름의 열 구분
        seen[h] = seen.get(h, 0) + 1
        if seen[h] > 1:
            header[i] = f"{h}{seen[h]}"

    body = [(r + [""] * width)[:width] for r in body]
    dropped = [r for r in body if any(TOTAL_WORDS.match(c) for c in r)]
    body = [r for r in body if r not in dropped]
    if dropped:
        notices.append(f"'{name}' 시트의 기존 합계 행 {len(dropped)}개는 빼고 수식으로 다시 넣었습니다.")
    keep = [i for i in range(width) if any(r[i].strip() for r in body) or not header[i].startswith("열")]
    header = [header[i] for i in keep]
    body = [[r[i].strip() for i in keep] for r in body]
    if not body or not header:
        return None

    columns = []
    for i, h in enumerate(header):
        values = [r[i] for r in body if r[i]]
        if values and all(_as_date(v) for v in values):
            kind = "date"
            for r in body:
                r[i] = _as_date(r[i]) or r[i]
        elif values and not NOT_A_NUMBER.search(h) and all(_as_number(v) is not None for v in values):
            if all(v.endswith("%") for v in values) or re.search(r"율$|률$|비율|%", h):
                kind = "percent"
                if not any(v.endswith("%") for v in values) and max(_as_number(v) for v in values) <= 1:
                    for r in body:  # 0.25 형태면 25로
                        if r[i]:
                            r[i] = _raw(round(_as_number(r[i]) * 100, 4))
            else:
                kind = "currency" if MONEY.search(h) else "number"
        else:
            kind = "text"
        columns.append({"name": h, "kind": kind})

    numeric = [c["name"] for c in columns if c["kind"] in ("number", "currency")]
    category = next((c for c in columns if c["kind"] in ("text", "date")), None)
    chart = None
    if category and numeric and 2 <= len(body) <= 40:
        timeline = category["kind"] == "date" or TIME_AXIS.search(category["name"])
        chart = {"kind": "line" if timeline else "bar", "title": f"{category['name']}별 {', '.join(numeric[:3])}",
                 "category_column": category["name"], "value_columns": numeric[:3]}
    return {"name": name, "columns": columns, "rows": body,
            "add_total_row": bool(numeric), "chart": chart}


def _sheets_from_table_text(table_text: str, notices: list[str]) -> list[dict]:
    sheets = []
    for i, block in enumerate(re.split(r"\n\n(?=\[시트: )", table_text)):
        m = re.match(r"\[시트: (.*)\]\n", block)
        name = m[1] if m else f"정리{i + 1}"
        rows = [line.split("\t") for line in block[m.end() if m else 0:].split("\n")]
        sheet = _clean_sheet(name, rows, notices)
        if sheet:
            sheets.append(sheet)
    return sheets


def make_plan_offline(notes: str, table_text: str = "", want_excel: bool = True,
                      today: dt.date | None = None) -> tuple[DocPlan, list[str]]:
    """(DocPlan, 사용자에게 알릴 말) 을 돌려준다."""
    today = today or dt.date.today()
    notices: list[str] = []
    file_sheets = _sheets_from_table_text(table_text, notices) if table_text else []

    if notes.strip():
        plan = _plan_from_notes(notes, today)
    else:
        plan = {"title": file_sheets[0]["name"] if file_sheets else "정리 문서", "doc_type": "자료 정리",
                "date": today.isoformat(), "author": "", "summary": "", "sections": [],
                "action_items": [], "sheets": [], "missing_info": []}
        plan["summary"] = f"올린 파일의 표 {len(file_sheets)}개를 정리함."

    for sheet in file_sheets:
        shown = sheet["rows"][:30]
        note = [] if len(shown) == len(sheet["rows"]) else [
            f"전체 {len(sheet['rows'])}행 중 30행만 표시함. 전체 내용은 엑셀 파일 참고."]
        plan["sections"].append({
            "heading": sheet["name"], "paragraphs": note, "bullets": [],
            "table": {"columns": [c["name"] for c in sheet["columns"]], "rows": shown}})
    plan["sheets"] = (file_sheets + plan["sheets"]) if want_excel else []
    return DocPlan.model_validate(plan), notices
