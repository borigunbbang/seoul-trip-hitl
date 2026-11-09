"""테스트용 가짜 플래너. LLM 대신 정해 둔 계획을 돌려준다."""
from datetime import date

from trip.plan import DayPlan, Stop


def to_plan(d: date, stops: list[tuple], lodging: str = "h_myeongdong") -> DayPlan:
    return DayPlan(date=d, lodging_id=lodging,
                   stops=[Stop(place_id=s[0], stay_min=s[1], meal=s[2] if len(s) > 2 else None,
                               reason=f"가짜 플래너가 고름: {s[0]}") for s in stops])


class FakePlanner:
    def __init__(self, day_plans: dict[date, list[tuple]], replans: dict[date, list[tuple]] | None = None,
                 allocation: dict[date, list[str]] | None = None):
        self.day_plans = day_plans
        self.replans = replans or {}
        self.allocation = allocation
        self.feedback: list[tuple[date, str, list[str]]] = []

    def allocate(self, dates, wanted_ids, note):
        if self.allocation is not None:
            return {d: list(self.allocation.get(d, [])) for d in dates}, "가짜 배분"
        result = {d: [] for d in dates}
        for i, pid in enumerate(wanted_ids):
            result[dates[i % len(dates)]].append(pid)
        return result, "가짜 배분(차례대로)"

    def plan_day(self, d, lodging_id, attraction_ids, restaurant_ids, note):
        return to_plan(d, self.day_plans[d], lodging_id), f"가짜 요약 {d}"

    def replan_day(self, d, lodging_id, attraction_ids, restaurant_ids, note, previous, stop_reasons, feedback):
        self.feedback.append((d, feedback, stop_reasons))
        return to_plan(d, self.replans[d], lodging_id), f"가짜 재작성 {d}: {feedback}"
