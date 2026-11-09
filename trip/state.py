"""그래프 State와 여행 요청.

State 값은 체크포인터(SQLite)에 저장되므로 JSON으로 바꿀 수 있는 값(문자열, 숫자, 목록, dict)만 둔다.
날짜는 'YYYY-MM-DD' 문자열, 계획·문제는 model_dump(mode="json") 결과를 넣는다.
"""
from __future__ import annotations

import operator
from datetime import date, timedelta
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field, model_validator

MAX_TRIP_DAYS = 4


class TripRequest(BaseModel):
    """여행자가 입력한 원문 요청."""

    start_date: date
    end_date: date
    wanted_ids: list[str] = Field(min_length=1, description="가고 싶은 관광지 id")
    lodging_id: str
    note: str = Field("", description="자유 입력 요청 (예: '걷는 건 적게')")

    @model_validator(mode="after")
    def _check_dates(self) -> TripRequest:
        if self.end_date < self.start_date:
            raise ValueError("종료일이 시작일보다 빠르다")
        if len(self.dates) > MAX_TRIP_DAYS:
            raise ValueError(f"여행은 최대 {MAX_TRIP_DAYS}일까지다")
        return self

    @property
    def dates(self) -> list[date]:
        n = (self.end_date - self.start_date).days + 1
        return [self.start_date + timedelta(days=i) for i in range(n)]


class TripState(TypedDict, total=False):
    trip_id: str
    request: dict                      # TripRequest
    restaurant_ids: list[str]          # 음식점 후보
    allocation: dict[str, list[str]]   # 'YYYY-MM-DD' → 관광지 id 목록
    allocation_reason: str             # LLM이 나눈 이유
    allocation_actions: list[dict]     # 정기 휴무일 피하기(move_day) 기록


DayStatus = Literal["planning", "waiting", "confirmed"]


class DayState(TypedDict, total=False):
    trip_id: str
    day_index: int                     # 1부터
    date: str
    request: dict                      # 요청 원문 (상세 화면에 그대로 보여 줌)
    lodging_id: str
    attraction_ids: list[str]
    restaurant_ids: list[str]

    plan: dict                         # DayPlan
    summary: str                       # 에이전트가 이 동선을 짠 이유 (하루 전체)
    schedule: list[dict]               # 화면용 시각표
    issues: list[dict]                 # 마지막 검사의 문제 목록
    verdict: dict                      # Verdict
    actions: Annotated[list[dict], operator.add]   # 자동 수정 기록 (누적)
    history: Annotated[list[dict], operator.add]   # 진행 기록 (누적)

    status: DayStatus
    confirmed_by: Literal["auto", "human"] | None
    human_decision: dict | None        # {"action": "approve"|"reject", "reason": str}
    reject_count: int
