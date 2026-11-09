from tests.helpers import FRI, REF, SAT, TUE, WED, codes, day
from trip.fixer import check_and_fix, fix_allocation, judge


def names(result, provider):
    return [provider.place(s.place_id).name for s in result.plan.stops]


def test_clean_day_auto_confirms_without_fixing(provider):
    plan = day(FRI, ("a_gyeongbok", 120), ("r_tosokchon", 60, "점심"), ("a_bukchon", 60))
    r = check_and_fix(plan, provider, REF)
    assert r.verdict.decision == "auto_confirm"
    assert r.verdict.reasons == []
    assert r.actions == [] and r.rounds == 0
    assert r.plan == plan


def test_closed_restaurant_is_replaced(provider):
    # 북촌 11:00 끝 → 폐업 식당 대신 인사동 한정식(버스 10분, 11:30 오픈까지 대기)
    r = check_and_fix(day(FRI, ("a_bukchon", 60), ("r_closed", 60, "점심")), provider, REF)
    assert names(r, provider) == ["북촌한옥마을", "인사동 한정식(시험용)"]
    assert [a.action for a in r.actions] == ["replace"]
    assert "폐업 확인됨" in r.actions[0].detail
    assert r.plan.stops[1].meal == "점심"
    assert r.plan.stops[1].reason.startswith("자동 수정")
    assert r.verdict.decision == "auto_confirm"


def test_reorder_fixes_outside_hours(provider):
    # 경복궁 → 북촌 순서면 북촌이 17:00 이후까지 → 북촌을 먼저 가면 둘 다 영업시간 안
    r = check_and_fix(day(FRI, ("a_gyeongbok", 120), ("a_bukchon", 60), start="14:00"), provider, REF)
    assert names(r, provider) == ["북촌한옥마을", "경복궁"]
    assert [a.action for a in r.actions] == ["reorder"]
    assert all(s.reason.endswith("(자동 수정으로 순서가 바뀜)") for s in r.plan.stops)
    assert r.actions[0].fixed_codes == ["OUTSIDE_HOURS"]
    assert r.verdict.decision == "auto_confirm"


def test_unfixable_closed_attraction_goes_to_human(provider):
    # 관광지는 빼지 않는다. 다른 날로 옮기는 것은 여행 단위(fix_allocation)의 일이다
    r = check_and_fix(day(TUE, ("a_gyeongbok", 120)), provider, REF)
    assert r.verdict.decision == "needs_review"
    assert codes(r.verdict.reasons) == ["FIX_FAILED"]
    assert r.verdict.reasons[0].value == "11/24(화) 휴무일 방문 — 자동 수정으로 해결하지 못함"
    assert r.verdict.reasons[0].kind == "uncertain"


def test_uncertain_issues_are_not_fixed(provider):
    # 기준 2(리뷰 오래됨)는 에이전트가 판단하지 않고 사람에게 묻는다
    r = check_and_fix(day(FRI, ("a_bukchon", 60), ("r_sujebi", 60, "점심")), provider, REF)
    assert r.actions == []
    assert r.verdict.decision == "needs_review"
    assert "C2_STALE_REVIEW" in codes(r.verdict.reasons)


def test_fix_then_still_uncertain(provider):
    # 폐업 식당은 고치지만, 같은 날의 특별 운영일(기준 3)은 남아서 멈춘다
    r = check_and_fix(day(SAT, ("a_changdeok", 90), ("r_closed", 60, "점심")), provider, REF)
    assert "replace" in [a.action for a in r.actions]
    assert "r_closed" not in [s.place_id for s in r.plan.stops]
    assert r.verdict.decision == "needs_review"
    assert "C3_SPECIAL_DAY" in codes(r.verdict.reasons)


def test_round_limit(provider):
    r = check_and_fix(day(FRI, ("a_bukchon", 60), ("r_closed", 60, "점심")), provider, REF, max_rounds=0)
    assert r.actions == [] and r.rounds == 0
    assert "FIX_FAILED" in codes(r.verdict.reasons)


def test_judge_empty_is_auto_confirm():
    assert judge([]).decision == "auto_confirm"


def test_fix_allocation_moves_closed_day(provider):
    allocation = {TUE: ["a_gyeongbok", "a_folk"], WED: ["a_bukchon"]}
    fixed, actions = fix_allocation(allocation, provider, REF)
    assert fixed == {TUE: ["a_folk"], WED: ["a_bukchon", "a_gyeongbok"]}
    assert [a.action for a in actions] == ["move_day"]
    assert actions[0].detail == "경복궁: 11/24 정기 휴무 → 11/25로 이동"


def test_fix_allocation_leaves_unmovable(provider):
    # 여행 날짜가 하루뿐이면 옮길 곳이 없다 → 하루 검사에서 FIX_FAILED로 사람에게
    fixed, actions = fix_allocation({TUE: ["a_gyeongbok"]}, provider, REF)
    assert fixed == {TUE: ["a_gyeongbok"]} and actions == []


def test_fix_allocation_ignores_special_day_and_unknown(provider):
    # 특별 운영일(창덕궁 11/21)과 정보 없음(인사동)은 '확실한 휴무'가 아니라 옮기지 않는다
    fixed, actions = fix_allocation({SAT: ["a_changdeok", "a_insadong"], FRI: []}, provider, REF)
    assert actions == []
