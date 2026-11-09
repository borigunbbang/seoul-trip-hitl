from datetime import date

import pytest

from tests.helpers import FRI, SAT
from trip.candidates import find_restaurants
from trip.planner import (AllocationOut, DayAssignment, DayPlanOut, LLMPlanner, StopOut, normalize_allocation,
                          normalize_day)


def test_normalize_allocation_drops_unknown_and_adds_missing():
    out = AllocationOut(reason="r", days=[
        DayAssignment(date="2026-11-20", place_ids=["a_gyeongbok", "nope", "a_gyeongbok"]),
        DayAssignment(date="2030-01-01", place_ids=["a_bukchon"]),           # 없는 날짜
    ])
    result = normalize_allocation(out, [FRI, SAT], ["a_gyeongbok", "a_bukchon"])
    assert result == {FRI: ["a_gyeongbok"], SAT: ["a_bukchon"]}


def test_normalize_day(provider):
    out = DayPlanOut(summary="s", stops=[
        StopOut(place_id="a_gyeongbok", stay_min=999, meal="점심", reason="r"),   # 관광지 끼니 표시는 지움, 체류 상한
        StopOut(place_id="r_tosokchon", stay_min=10, meal="점심", reason="r"),    # 체류 하한
        StopOut(place_id="h_myeongdong", stay_min=10, reason="r"),                # 후보가 아닌 id
    ])
    plan = normalize_day(out, FRI, "h_myeongdong", ["a_gyeongbok", "a_bukchon"], ["r_tosokchon"], provider)
    assert [(s.place_id, s.stay_min, s.meal) for s in plan.stops] == [
        ("a_gyeongbok", 180, None), ("r_tosokchon", 40, "점심"), ("a_bukchon", 60, None)]
    assert "코드 보정" in plan.stops[2].reason


def test_replan_does_not_refill_removed_attraction(provider):
    out = DayPlanOut(summary="s", stops=[StopOut(place_id="a_gyeongbok", stay_min=90, reason="r")])
    plan = normalize_day(out, FRI, "h_myeongdong", ["a_gyeongbok", "a_bukchon"], [], provider, fill_missing=False)
    assert [s.place_id for s in plan.stops] == ["a_gyeongbok"]


def test_restaurant_candidates_sorted_by_rating(provider):
    ids = find_restaurants(["a_bukchon"], provider)
    ratings = [provider.place(i).rating for i in ids]
    assert ratings == sorted(ratings, reverse=True)
    assert "r_closed" in ids          # 상태로 거르지 않는다 (검사기가 잡는다)


class FakeChat:
    """with_structured_output(schema).invoke(messages) 흉내."""

    def __init__(self, response):
        self.response, self.calls = response, 0

    def with_structured_output(self, schema):
        chat = self

        class Runner:
            def invoke(self, messages):
                chat.calls += 1
                return schema.model_validate(chat.response)
        return Runner()


class NoChat:
    def with_structured_output(self, schema):
        raise AssertionError("캐시에 있으면 LLM을 부르면 안 된다")


def test_llm_cache_reuses_response(provider, tmp_path):
    response = {"summary": "경복궁 보고 점심", "stops": [
        {"place_id": "a_gyeongbok", "stay_min": 120, "meal": None, "reason": "아침에 여유"},
        {"place_id": "r_tosokchon", "stay_min": 60, "meal": "점심", "reason": "가까움"}]}
    chat = FakeChat(response)
    p1 = LLMPlanner(provider, model="m", cache_dir=tmp_path, llm=chat)
    plan1, summary1 = p1.plan_day(FRI, "h_myeongdong", ["a_gyeongbok"], ["r_tosokchon"], "")
    plan1b, _ = p1.plan_day(FRI, "h_myeongdong", ["a_gyeongbok"], ["r_tosokchon"], "")
    assert chat.calls == 1 and p1.calls == 1
    assert plan1 == plan1b

    # 새 플래너(=다음 실행)도 파일 캐시에서 같은 결과를 받는다
    p2 = LLMPlanner(provider, model="m", cache_dir=tmp_path, llm=NoChat())
    plan2, summary2 = p2.plan_day(FRI, "h_myeongdong", ["a_gyeongbok"], ["r_tosokchon"], "")
    assert (plan2, summary2) == (plan1, summary1) and p2.calls == 0

    # 입력이 다르면 캐시를 쓰지 않는다
    with pytest.raises(AssertionError):
        p2.plan_day(SAT, "h_myeongdong", ["a_gyeongbok"], ["r_tosokchon"], "")
