"""하루 계획과 검사 결과 모델.

LLM은 DayPlan(방문 순서, 체류 시간, 끼니)만 만든다. 시각은 schedule.py가 계산하고,
rules.py가 Issue를 찾고, fixer.py가 확실한 것을 고치고, judge가 멈출지 정한다.
"""
from __future__ import annotations

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, Field

Meal = Literal["점심", "저녁"]


class Stop(BaseModel):
    place_id: str
    stay_min: int
    meal: Meal | None = None
    reason: str = Field("", description="이 장소를 고른 이유 (LLM 또는 자동 수정이 적음)")


class DayPlan(BaseModel):
    date: date
    lodging_id: str
    start_time: time = time(9, 30)
    stops: list[Stop]


IssueKind = Literal["certain", "uncertain"]
IssueCode = Literal[
    # 확실히 틀림 → 에이전트가 고친다
    "CLOSED_PERMANENTLY", "CLOSED_DAY", "OUTSIDE_HOURS", "ESTIMATED_SEGMENT",
    # 불확실 → 사람에게 묻는다 (기준 1~4)
    "C1_NO_ALTERNATIVE", "C1_AFTER_LAST",
    "C2_NOT_OPERATIONAL", "C2_STALE_REVIEW", "C2_NO_HOURS",
    "C3_NO_HOURS", "C3_SPECIAL_DAY", "C3_NOTICE",
    "C4_STALE_PLACE", "C4_STALE_SEGMENT",
    # 확실히 틀렸지만 자동 수정에 실패 → 사람에게
    "FIX_FAILED",
]


class Issue(BaseModel):
    kind: IssueKind
    code: IssueCode
    criterion: int | None = Field(None, description="승인 기준 번호 1~4 (불확실일 때)")
    target: str = Field(description="사람이 읽는 대상 이름. 예: '삼청동 수제비', '북촌 → 창덕궁'")
    target_id: str = Field(description="장소 id 또는 'from>to'")
    stop_index: int | None = Field(None, description="장소 문제면 stops에서의 위치")
    value: str = Field(description="걸린 값. 예: '최근 리뷰 152일 전'")
    threshold: str = Field("", description="기준값. 예: '90일 이내'")

    @property
    def message(self) -> str:
        tail = f" (기준: {self.threshold})" if self.threshold else ""
        label = f"기준 {self.criterion}" if self.criterion else ("자동 수정 실패" if self.code == "FIX_FAILED" else "확실한 오류")
        return f"[{label}] {self.target}: {self.value}{tail}"


class FixAction(BaseModel):
    round: int
    action: Literal["reorder", "replace", "move_day"]
    detail: str
    fixed_codes: list[IssueCode] = Field(default_factory=list)


class Verdict(BaseModel):
    decision: Literal["auto_confirm", "needs_review"]
    reasons: list[Issue] = Field(default_factory=list, description="멈춘 이유. 자동 확정이면 빈 목록")
