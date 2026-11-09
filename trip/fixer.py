"""자동 수정과 판정.

확실히 틀린 것만 고친다(계획 0.4). 고치는 방법은 세 가지다.
- replace  : 폐업·휴무·영업시간 밖 음식점 → 근처의 영업 중인 다른 음식점
- reorder  : 같은 날 방문 순서를 바꿔 영업시간·추정 구간 문제를 없앤다
- move_day : (여행 단위) 정기 휴무일에 배정된 관광지를 그 장소가 여는 다른 날로 옮긴다

관광지를 일정에서 빼는 수정은 하지 않는다. 여행자가 가고 싶다고 한 곳이기 때문이다.
고치지 못하면 FIX_FAILED로 사람에게 넘긴다. 수정은 MAX_FIX_ROUNDS번까지만 반복한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from itertools import permutations

import config
from trip.models import haversine_m
from trip.plan import DayPlan, FixAction, Issue, Stop, Verdict
from trip.provider import DataProvider
from trip.rules import check, days_since, is_fresh
from trip.schedule import Schedule, build_schedule

MEAL_WINDOWS = {"점심": (11 * 60, 14 * 60), "저녁": (17 * 60, 20 * 60 + 30)}
MAX_PERMUTE = 7   # 이보다 장소가 많으면 순서 바꾸기를 하지 않는다 (7! = 5040)


@dataclass
class DayResult:
    plan: DayPlan
    schedule: Schedule
    issues: list[Issue]
    actions: list[FixAction] = field(default_factory=list)
    rounds: int = 0
    verdict: Verdict | None = None


def certain(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.kind == "certain"]


def evaluate(plan: DayPlan, provider: DataProvider, ref: date) -> tuple[Schedule, list[Issue]]:
    schedule = build_schedule(plan, provider)
    return schedule, check(schedule, ref)


# ── 수정 방법 ───────────────────────────────────────────────────────

def meal_penalty(schedule: Schedule) -> int:
    """끼니 장소가 끼니 시간대를 벗어난 분 수의 합."""
    total = 0
    for v in schedule.visits:
        if v.stop.meal:
            lo, hi = MEAL_WINDOWS[v.stop.meal]
            total += max(0, lo - v.start_min) + max(0, v.start_min - hi)
    return total


def try_reorder(plan: DayPlan, provider: DataProvider, ref: date) -> DayPlan | None:
    """확실한 오류가 가장 적은 순서를 찾는다. 줄어들지 않으면 None."""
    n = len(plan.stops)
    if n < 2 or n > MAX_PERMUTE:
        return None
    _, base_issues = evaluate(plan, provider, ref)
    base_certain = len(certain(base_issues))
    best_key, best_plan = None, None
    for perm in permutations(range(n)):
        candidate = plan.model_copy(update={"stops": [plan.stops[i] for i in perm]})
        schedule, issues = evaluate(candidate, provider, ref)
        key = (len(certain(issues)), len(issues), meal_penalty(schedule),
               sum(1 for i, p in enumerate(perm) if i != p), schedule.travel_min)
        if best_key is None or key < best_key:
            best_key, best_plan = key, candidate
    if best_key[0] >= base_certain:
        return None
    # 순서가 바뀐 장소는 LLM이 적은 이유가 더 이상 맞지 않을 수 있으므로 표시해 둔다
    original = {s.place_id: i for i, s in enumerate(plan.stops)}
    note = " (자동 수정으로 순서가 바뀜)"
    stops = [s if original[s.place_id] == i or s.reason.endswith(note)
             else s.model_copy(update={"reason": s.reason + note})
             for i, s in enumerate(best_plan.stops)]
    return best_plan.model_copy(update={"stops": stops})


def replace_restaurant(plan: DayPlan, index: int, provider: DataProvider, ref: date) -> DayPlan | None:
    """index 위치의 음식점을 다른 음식점으로 바꾼다.

    후보 조건: 그 자리에서 아무 문제도 없고, 하루 전체의 확실한 오류가 늘지 않는다.
    후보가 여럿이면 (확실한 오류 수, 전체 문제 수, 직전 장소와의 거리) 순으로 고른다.
    """
    old = plan.stops[index]
    anchor = provider.place(plan.stops[index - 1].place_id if index > 0 else plan.lodging_id)
    used = {s.place_id for s in plan.stops}
    _, before = evaluate(plan, provider, ref)
    best_key, best_plan = None, None
    for p in provider.places("음식점"):
        if p.id in used or p.status != "OPERATIONAL" or not is_fresh(p.last_checked, ref):
            continue
        if p.last_review_date is None or days_since(p.last_review_date, ref) > config.REVIEW_STALE_DAYS:
            continue
        new_stop = Stop(place_id=p.id, stay_min=p.default_stay_min, meal=old.meal,
                        reason=f"자동 수정: {provider.place(old.place_id).name} 대신 가까운 영업 중 음식점")
        candidate = plan.model_copy(update={"stops": plan.stops[:index] + [new_stop] + plan.stops[index + 1:]})
        _, issues = evaluate(candidate, provider, ref)
        if any(i.stop_index == index for i in issues) or len(certain(issues)) > len(certain(before)):
            continue
        key = (len(certain(issues)), len(issues), haversine_m(anchor.lat, anchor.lng, p.lat, p.lng))
        if best_key is None or key < best_key:
            best_key, best_plan = key, candidate
    return best_plan


def fix_once(plan: DayPlan, issues: list[Issue], provider: DataProvider, ref: date,
             round_no: int) -> tuple[DayPlan, list[FixAction]]:
    actions: list[FixAction] = []

    def food_issues(iss: list[Issue], codes: set[str]) -> list[Issue]:
        return [i for i in certain(iss) if i.code in codes and i.stop_index is not None
                and provider.place(plan.stops[i.stop_index].place_id).kind == "음식점"]

    def replace_all(iss: list[Issue], codes: set[str]) -> None:
        nonlocal plan
        for issue in food_issues(iss, codes):
            new_plan = replace_restaurant(plan, issue.stop_index, provider, ref)
            if new_plan is not None:
                new_name = provider.place(new_plan.stops[issue.stop_index].place_id).name
                actions.append(FixAction(round=round_no, action="replace", fixed_codes=[issue.code],
                                         detail=f"{issue.target}({issue.value}) → {new_name}"))
                plan = new_plan

    # 1) 폐업 음식점은 순서를 바꿔도 소용없으니 먼저 바꾼다
    replace_all(issues, {"CLOSED_PERMANENTLY"})

    # 2) 순서 바꾸기
    _, issues = evaluate(plan, provider, ref)
    if certain(issues):
        reordered = try_reorder(plan, provider, ref)
        if reordered is not None:
            before = [provider.place(s.place_id).name for s in plan.stops]
            after = [provider.place(s.place_id).name for s in reordered.stops]
            actions.append(FixAction(round=round_no, action="reorder",
                                     fixed_codes=sorted({i.code for i in certain(issues)}),
                                     detail=f"{' → '.join(before)}  ⇒  {' → '.join(after)}"))
            plan = reordered

    # 3) 그래도 남은 음식점 휴무·영업시간 밖은 다른 음식점으로
    _, issues = evaluate(plan, provider, ref)
    replace_all(issues, {"CLOSED_DAY", "OUTSIDE_HOURS"})
    return plan, actions


# ── 검사 → 수정 반복 → 판정 ─────────────────────────────────────────

def judge(issues: list[Issue]) -> Verdict:
    reasons = [
        i.model_copy(update={"kind": "uncertain", "code": "FIX_FAILED",
                             "value": f"{i.value} — 자동 수정으로 해결하지 못함"})
        if i.kind == "certain" else i
        for i in issues
    ]
    return Verdict(decision="needs_review" if reasons else "auto_confirm", reasons=reasons)


def check_and_fix(plan: DayPlan, provider: DataProvider, ref: date | None = None,
                  max_rounds: int | None = None) -> DayResult:
    ref = ref or config.REFERENCE_DATE
    max_rounds = config.MAX_FIX_ROUNDS if max_rounds is None else max_rounds
    actions: list[FixAction] = []
    rounds = 0
    schedule, issues = evaluate(plan, provider, ref)
    while certain(issues) and rounds < max_rounds:
        rounds += 1
        plan, new_actions = fix_once(plan, issues, provider, ref, rounds)
        if not new_actions:          # 더 고칠 방법이 없음
            break
        actions += new_actions
        schedule, issues = evaluate(plan, provider, ref)
    return DayResult(plan, schedule, issues, actions, rounds, judge(issues))


# ── 여행 단위: 정기 휴무일 피해서 날짜 배정 ──────────────────────────

def fix_allocation(allocation: dict[date, list[str]], provider: DataProvider,
                   ref: date | None = None) -> tuple[dict[date, list[str]], list[FixAction]]:
    """관광지가 확실한 정기 휴무일에 배정됐으면 그 장소가 여는 다른 날(장소가 가장 적은 날)로 옮긴다."""
    ref = ref or config.REFERENCE_DATE
    result = {d: list(ids) for d, ids in allocation.items()}
    actions: list[FixAction] = []
    for d in sorted(result):
        for pid in list(result[d]):
            p = provider.place(pid)
            if not (is_fresh(p.last_checked, ref) and p.hours_on(d) == []):
                continue
            open_days = [o for o in sorted(result) if o != d and p.hours_on(o)]
            if not open_days:
                continue                       # 옮길 날이 없으면 하루 단위 검사에서 FIX_FAILED가 된다
            target = min(open_days, key=lambda o: (len(result[o]), abs((o - d).days)))
            result[d].remove(pid)
            result[target].append(pid)
            actions.append(FixAction(round=0, action="move_day", fixed_codes=["CLOSED_DAY"],
                                     detail=f"{p.name}: {d:%m/%d} 정기 휴무 → {target:%m/%d}로 이동"))
    return result, actions
