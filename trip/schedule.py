"""하루 계획 → 시각표. LLM이 적은 시각은 쓰지 않고 여기서 계산한다.

숙소 출발 → (이동) → 장소 1 → … → 장소 N → (이동) → 숙소 도착.
도착했는데 아직 문을 열기 전이면 여는 시각까지 기다렸다가 방문한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from trip.models import Place, Segment, to_min
from trip.plan import DayPlan, Stop
from trip.provider import DataProvider


@dataclass
class Leg:
    segment: Segment
    depart_min: int
    arrive_min: int


@dataclass
class Visit:
    index: int
    stop: Stop
    place: Place
    arrive_min: int
    start_min: int   # 기다린 뒤 실제로 방문을 시작하는 시각
    end_min: int


@dataclass
class Schedule:
    plan: DayPlan
    lodging: Place
    visits: list[Visit]
    legs: list[Leg]   # legs[i]는 visits[i]로 가는 구간, 마지막은 숙소로 돌아가는 구간

    @property
    def end_min(self) -> int:
        return self.legs[-1].arrive_min

    @property
    def travel_min(self) -> int:
        return sum(leg.arrive_min - leg.depart_min for leg in self.legs)


def opening_wait(place: Place, plan: DayPlan, arrive_min: int) -> int:
    """도착 시각에 아직 안 열었고 그 날 나중에 여는 시간대가 있으면, 여는 시각을 돌려준다."""
    hours = place.hours_on(plan.date)
    if not hours:
        return arrive_min
    later_opens = [r.span_min()[0] for r in hours if r.span_min()[0] > arrive_min]
    already_open = any(r.span_min()[0] <= arrive_min < r.span_min()[1] for r in hours)
    if already_open or not later_opens:
        return arrive_min
    return min(later_opens)


def build_schedule(plan: DayPlan, provider: DataProvider) -> Schedule:
    lodging = provider.place(plan.lodging_id)
    now = to_min(plan.start_time)
    prev_id = plan.lodging_id
    visits: list[Visit] = []
    legs: list[Leg] = []

    for i, stop in enumerate(plan.stops):
        seg = provider.segment(prev_id, stop.place_id)
        arrive = now + seg.minutes
        legs.append(Leg(seg, now, arrive))
        place = provider.place(stop.place_id)
        start = opening_wait(place, plan, arrive)
        end = start + stop.stay_min
        visits.append(Visit(i, stop, place, arrive, start, end))
        now, prev_id = end, stop.place_id

    back = provider.segment(prev_id, plan.lodging_id)
    legs.append(Leg(back, now, now + back.minutes))
    return Schedule(plan, lodging, visits, legs)


def hhmm(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}" if h < 24 else f"다음날 {h - 24:02d}:{m:02d}"
