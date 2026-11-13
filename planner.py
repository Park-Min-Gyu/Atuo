"""메모/엑셀 원본을 Claude에게 보내 '문서 설계도(DocPlan)'로 정리받는 부분."""
from __future__ import annotations

import datetime as dt
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"


class Table(BaseModel):
    columns: List[str]
    rows: List[List[str]]


class Section(BaseModel):
    heading: str
    paragraphs: List[str] = Field(description="서술형 문장. 없으면 빈 배열")
    bullets: List[str] = Field(description="개조식 항목. 없으면 빈 배열")
    table: Optional[Table] = None


class ActionItem(BaseModel):
    task: str
    owner: str = Field(description="담당자. 모르면 '미정'")
    due: str = Field(description="기한 YYYY-MM-DD. 모르면 '미정'")
    status: str = Field(description="예: 예정 / 진행 중 / 완료")


class Column(BaseModel):
    name: str
    kind: Literal["text", "number", "currency", "percent", "date"]


class Chart(BaseModel):
    kind: Literal["bar", "line", "pie"]
    title: str
    category_column: str = Field(description="가로축(항목 이름)으로 쓸 열 이름")
    value_columns: List[str] = Field(description="값으로 쓸 숫자 열 이름. pie는 1개만")


class Sheet(BaseModel):
    name: str = Field(description="시트 이름, 31자 이하")
    columns: List[Column]
    rows: List[List[str]] = Field(
        description="각 행은 columns와 같은 길이. 숫자는 쉼표·단위 없이 '1200000', "
        "퍼센트는 '12.5', 날짜는 'YYYY-MM-DD'"
    )
    add_total_row: bool = Field(description="숫자 열 합계 행이 의미가 있으면 true")
    chart: Optional[Chart] = None


class DocPlan(BaseModel):
    title: str
    doc_type: str = Field(description="예: 회의록, 업무 보고서, 업무 계획서, 공지문")
    date: str = Field(description="문서 날짜 YYYY-MM-DD")
    author: str = Field(description="작성자. 모르면 빈 문자열")
    summary: str = Field(description="2~3문장 요약")
    sections: List[Section]
    action_items: List[ActionItem]
    sheets: List[Sheet] = Field(description="엑셀로 만들 표. 표로 만들 내용이 없으면 빈 배열")
    missing_info: List[str] = Field(
        description="문서를 완성하려면 사용자가 더 알려줘야 하는 것들"
    )


SYSTEM = """당신은 한국 회사의 문서 작성 담당자입니다. 사용자가 대충 적은 메모(날짜, 할 일, \
사람 이름 등)나 정리되지 않은 엑셀 내용을 받아, 바로 제출할 수 있는 문서와 엑셀 표의 설계도를 만듭니다.

사용자는 표나 그래프를 직접 만드는 데 익숙하지 않으므로, 어떤 구조가 좋을지는 당신이 판단합니다.

정리 원칙
- 메모의 성격을 보고 문서 종류(회의록, 업무 보고서, 계획서, 공지문 등)를 정하고, 그 종류의 \
문서에 보통 들어가는 항목을 갖춘 구성으로 만듭니다. 메모가 짧아도 문서다운 형태를 갖추게 합니다.
- 문체는 공문서식 개조식("~함", "~예정")을 기본으로 하고, 메모의 표현을 다듬어 씁니다.
- 사실은 지어내지 않습니다. 날짜·금액·이름·수치처럼 메모에 없는 사실은 '미정'으로 두고 \
missing_info에 "무엇을 알려주면 좋은지"를 적습니다. 메모에 있는 내용은 빠뜨리지 않습니다.
- "내일", "다음 주 금요일" 같은 상대 날짜는 오늘 날짜 기준으로 실제 날짜로 바꿉니다.
- 할 일은 모두 action_items에 담당자·기한과 함께 넣습니다.

엑셀(sheets)
- 표로 볼 만한 데이터(할 일 목록, 일정, 금액, 실적, 명단 등)가 있으면 시트로 만듭니다.
- 원본 엑셀이 주어지면 제목 행·빈 행·합계 행 같은 군더더기를 걷어내고 한 행에 한 건씩 \
들어가는 깔끔한 표로 고칩니다. 데이터 행은 하나도 빼지 않습니다.
- 숫자 비교나 추이가 있으면 chart를 넣습니다: 항목 비교는 bar, 시간 흐름은 line, 비중은 pie.
- 합계 행은 프로그램이 수식으로 넣으므로 rows에 합계 행을 직접 넣지 않습니다."""


class PlanError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류."""


def make_plan(notes: str, table_text: str = "", want_excel: bool = True) -> DocPlan:
    today = dt.date.today()
    weekday = "월화수목금토일"[today.weekday()]
    parts = [f"오늘 날짜: {today.isoformat()} ({weekday}요일)"]
    if notes.strip():
        parts.append(f"<메모>\n{notes.strip()}\n</메모>")
    if table_text:
        parts.append(f"<원본_엑셀>\n{table_text}\n</원본_엑셀>")
    if not want_excel:
        parts.append("이번에는 엑셀이 필요 없으니 sheets는 빈 배열로 둡니다.")

    client = anthropic.Anthropic()
    try:
        # 표가 길면 응답도 길어지므로 스트리밍으로 받는다.
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=64000,
            system=SYSTEM,
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            output_format=DocPlan,
            # 드물게 요청이 거절되면 다른 모델이 이어서 처리하도록 한다.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise PlanError("API 키가 올바르지 않습니다. ANTHROPIC_API_KEY를 확인해 주세요.")
    except anthropic.RateLimitError:
        raise PlanError("요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")
    except anthropic.APIStatusError as e:
        raise PlanError(f"AI 서버 오류({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise PlanError("인터넷 연결을 확인해 주세요.")
    except anthropic.AnthropicError as e:
        raise PlanError(f"AI 호출 실패: {e}")
    except TypeError as e:
        if "authentication" in str(e):
            raise PlanError(
                "API 키가 설정되지 않았습니다. README의 'API 키 넣기'를 따라 "
                "ANTHROPIC_API_KEY를 설정한 뒤 프로그램을 다시 실행해 주세요."
            )
        raise

    if message.stop_reason == "refusal":
        raise PlanError("AI가 이 내용의 처리를 거절했습니다. 내용을 바꿔 다시 시도해 주세요.")
    if message.stop_reason == "max_tokens":
        raise PlanError("내용이 너무 길어 한 번에 정리하지 못했습니다. 나눠서 넣어 주세요.")
    if message.parsed_output is None:
        raise PlanError("AI 응답을 해석하지 못했습니다. 다시 시도해 주세요.")
    return message.parsed_output
