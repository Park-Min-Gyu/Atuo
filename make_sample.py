"""API 키 없이 결과물 모양을 확인하는 예시. 실행: python make_sample.py"""
from pathlib import Path

from hwpx_writer import write_hwpx
from planner import DocPlan
from xlsx_writer import write_xlsx

PLAN = DocPlan.model_validate({
    "title": "영업팀 주간회의 회의록",
    "doc_type": "회의록",
    "date": "2026-11-20",
    "author": "",
    "summary": "3분기 지역별 매출 실적을 점검함. 서울은 목표를 초과했으나 부산·대구는 미달하여 원인 분석을 진행하기로 함.",
    "sections": [
        {"heading": "회의 개요", "paragraphs": [], "bullets": [
            "일시: 2026-11-20(금)", "장소: 3층 회의실",
            "참석: 김민수 과장, 이지은 대리, 박준호 사원"], "table": None},
        {"heading": "3분기 매출 실적", "paragraphs": ["지역별 목표 대비 실적은 다음과 같음."],
         "bullets": [], "table": {"columns": ["지역", "목표", "실적", "달성률"], "rows": [
             ["서울", "1억 원", "1억 2,000만 원", "120%"], ["부산", "9,000만 원", "8,000만 원", "89%"],
             ["대구", "5,000만 원", "4,500만 원", "90%"], ["광주", "3,000만 원", "3,000만 원", "100%"]]}},
        {"heading": "논의 사항", "paragraphs": [], "bullets": [
            "부산 지역 목표 미달 원인 분석 필요", "신규 거래처 3곳 계약 진행 중",
            "연말 프로모션 기획안 준비"], "table": None},
    ],
    "action_items": [
        {"task": "부산 지역 매출 미달 원인 분석 보고", "owner": "이지은 대리", "due": "2026-11-27", "status": "예정"},
        {"task": "신규 거래처 3곳 견적서 발송", "owner": "박준호 사원", "due": "미정", "status": "예정"},
        {"task": "연말 프로모션 기획안 작성", "owner": "김민수 과장", "due": "2026-12-04", "status": "예정"},
    ],
    "sheets": [
        {"name": "3분기 매출", "columns": [
            {"name": "지역", "kind": "text"}, {"name": "목표", "kind": "currency"},
            {"name": "실적", "kind": "currency"}, {"name": "달성률", "kind": "percent"}],
         "rows": [["서울", "100000000", "120000000", "120"], ["부산", "90000000", "80000000", "88.9"],
                  ["대구", "50000000", "45000000", "90"], ["광주", "30000000", "30000000", "100"]],
         "add_total_row": True,
         "chart": {"kind": "bar", "title": "지역별 목표 대비 실적", "category_column": "지역",
                   "value_columns": ["목표", "실적"]}},
        {"name": "할 일", "columns": [
            {"name": "할 일", "kind": "text"}, {"name": "담당자", "kind": "text"},
            {"name": "기한", "kind": "date"}, {"name": "상태", "kind": "text"}],
         "rows": [["부산 지역 매출 미달 원인 분석 보고", "이지은 대리", "2026-11-27", "예정"],
                  ["신규 거래처 3곳 견적서 발송", "박준호 사원", "미정", "예정"],
                  ["연말 프로모션 기획안 작성", "김민수 과장", "2026-12-04", "예정"]],
         "add_total_row": False, "chart": None},
    ],
    "missing_info": ["견적서 발송 기한", "신규 거래처 3곳의 이름"],
})

if __name__ == "__main__":
    out = Path(__file__).parent / "samples"
    out.mkdir(exist_ok=True)
    print(write_hwpx(PLAN, str(out / "예시_회의록.hwpx")))
    print(write_xlsx(PLAN, str(out / "예시_회의록.xlsx")))
