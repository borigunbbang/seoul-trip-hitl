"""장소와 이동 구간의 데이터 모델.

정보가 "없음"과 "휴무"를 반드시 구분한다. 승인 기준이 이 차이로 갈린다.
- 영업시간 None  → 정보 없음 (불확실 → 사람에게 묻는 쪽)
- 영업시간 []    → 그 날 휴무 (확실 → 에이전트가 고치는 쪽)
"""
from __future__ import annotations

import math
from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PlaceKind = Literal["관광지", "음식점", "숙소"]
BusinessStatus = Literal["OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY", "UNKNOWN"]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# 종류별 기본 체류 시간(분). 장소마다 따로 정할 수 있다.
DEFAULT_STAY_MIN = {"관광지": 90, "음식점": 60, "숙소": 0}


def weekday_of(d: date) -> Weekday:
    return WEEKDAYS[d.weekday()]


def to_min(t: time) -> int:
    return t.hour * 60 + t.minute


class TimeRange(BaseModel):
    """영업 시간대 하나. 마감이 시작보다 이르면 자정을 넘기는 영업으로 본다."""

    open: time
    close: time

    def span_min(self) -> tuple[int, int]:
        start, end = to_min(self.open), to_min(self.close)
        if end <= start:
            end += 24 * 60
        return start, end

    def covers(self, start_min: int, end_min: int) -> bool:
        s, e = self.span_min()
        return s <= start_min and end_min <= e


class SpecialDay(BaseModel):
    """특별 운영일. hours가 []이면 그 날 휴무(임시 휴관 포함)."""

    date: date
    hours: list[TimeRange]
    note: str = ""


class Overridable(BaseModel):
    """기준 시연을 위해 값을 일부러 바꾼 항목은 원래 값과 이유를 남긴다."""

    overridden: dict[str, Any] = Field(default_factory=dict, description="필드 이름 → 원래 값")
    override_reason: str | None = None

    @property
    def is_overridden(self) -> bool:
        return bool(self.overridden)


class Place(Overridable):
    id: str
    name: str
    kind: PlaceKind
    lat: float
    lng: float
    area: str = ""
    address: str = ""

    status: BusinessStatus = "UNKNOWN"
    weekly_hours: dict[Weekday, list[TimeRange]] | None = None
    special_days: list[SpecialDay] = Field(default_factory=list)
    notice: str | None = Field(None, description="임시 휴관·공사 등 운영 관련 공지")

    rating: float | None = None
    review_count: int | None = None
    last_review_date: date | None = None
    stay_min: int | None = None

    last_checked: date
    source: str = "fixture"

    @property
    def default_stay_min(self) -> int:
        return self.stay_min if self.stay_min is not None else DEFAULT_STAY_MIN[self.kind]

    def special_day(self, d: date) -> SpecialDay | None:
        return next((s for s in self.special_days if s.date == d), None)

    def hours_on(self, d: date) -> list[TimeRange] | None:
        """그 날의 영업 시간대. None = 정보 없음, [] = 휴무. 특별 운영일이 우선한다."""
        special = self.special_day(d)
        if special is not None:
            return special.hours
        if self.weekly_hours is None:
            return None
        return self.weekly_hours.get(weekday_of(d))  # 요일 키가 없으면 정보 없음

    def is_open_during(self, d: date, start_min: int, end_min: int) -> bool | None:
        """start~end(그 날 0시부터의 분) 동안 계속 열려 있는가. None = 판단할 정보 없음."""
        hours = self.hours_on(d)
        if hours is None:
            return None
        return any(r.covers(start_min, end_min) for r in hours)


TransitMode = Literal["SUBWAY", "BUS", "BUS_SUBWAY", "WALK", "ESTIMATE"]


class TransitOption(BaseModel):
    """한 구간을 가는 방법 하나."""

    mode: TransitMode
    minutes: int
    transfers: int = 0
    lines: list[str] = Field(default_factory=list, description="노선 이름, 예: ['3호선', '7212']")
    last_departure: time | None = Field(None, description="출발 정류장 기준 막차 시각. 모르면 None")
    fare: int | None = None


class Segment(Overridable):
    """두 장소 사이의 이동 구간. options[0]이 추천 경로다."""

    from_id: str
    to_id: str
    distance_m: int
    options: list[TransitOption]
    path: list[tuple[float, float]] = Field(default_factory=list, description="경로 좌표 (위도, 경도)")
    last_checked: date
    source: str = "fixture"

    @field_validator("options")
    @classmethod
    def _at_least_one(cls, v: list[TransitOption]) -> list[TransitOption]:
        if not v:
            raise ValueError("구간에는 이동 방법이 하나 이상 있어야 한다")
        return v

    @property
    def best(self) -> TransitOption:
        return self.options[0]

    @property
    def minutes(self) -> int:
        return self.best.minutes

    @property
    def alternatives(self) -> int:
        return len(self.options)

    @property
    def is_estimate(self) -> bool:
        return self.source == "추정"

    def reversed(self) -> Segment:
        """반대 방향 구간. 대중교통은 대체로 양방향이 비슷하므로 수집량을 줄이려고 쓴다."""
        return self.model_copy(update={
            "from_id": self.to_id,
            "to_id": self.from_id,
            "path": list(reversed(self.path)),
        })


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))
