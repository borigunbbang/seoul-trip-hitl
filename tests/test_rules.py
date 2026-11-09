"""승인 기준 1~4와 '확실히 틀림' 검사. 기대값은 시험용 데이터 README의 사례에서 손으로 계산했다."""
from datetime import date

import pytest

from tests.helpers import FRI, REF, SAT, SUN, TUE, codes, day
from trip.rules import check
from trip.schedule import build_schedule


def run(plan, provider, ref=REF):
    return check(build_schedule(plan, provider), ref)


# ── 아무 문제 없는 날 ──────────────────────────────────────────────

def test_clean_day_has_no_issues(provider):
    # 경복궁 09:52~11:52 → 토속촌(도보) 12:02~13:02 → 북촌(버스) 13:17~14:17 → 숙소
    plan = day(FRI, ("a_gyeongbok", 120), ("r_tosokchon", 60, "점심"), ("a_bukchon", 60))
    assert run(plan, provider) == []


# ── 기준 1: 대안 없음 / 막차 이후 ───────────────────────────────────

def test_c1_single_option_segment(provider):
    issues = run(day(FRI, ("a_bukchon", 60), ("a_changdeok", 90)), provider)
    assert codes(issues) == ["C1_NO_ALTERNATIVE"]
    assert issues[0].target == "북촌한옥마을 → 창덕궁"
    assert "버스 종로02 하나뿐" in issues[0].value


def test_c1_after_last_departure(provider):
    # 19:30 출발 → 빈대떡 19:50~21:50 → 21:50 출발인데 추천 경로 막차 21:00
    issues = run(day(FRI, ("r_bindaetteok", 120, "저녁"), start="19:30"), provider)
    after = [i for i in issues if i.code == "C1_AFTER_LAST"]
    assert len(after) == 1
    assert after[0].value == "21:50 출발 예정, 추천 경로(버스 149) 막차 21:00"
    assert after[0].criterion == 1


def test_c1_before_last_departure_is_fine(provider):
    issues = run(day(FRI, ("r_bindaetteok", 60, "저녁"), start="18:00"), provider)
    assert "C1_AFTER_LAST" not in codes(issues)


def test_walk_first_segment_skips_c4(provider):
    # 추천 경로가 도보면 확인일이 오래돼도 묻지 않는다 (걷는 길은 바뀌지 않는다)
    from trip.rules import check_leg
    from trip.schedule import Leg

    seg = provider.segment("a_gyeongbok", "a_folk").model_copy(update={"last_checked": date(2026, 1, 1)})
    assert check_leg(Leg(seg, 600, 606), "경복궁 → 국립민속박물관", REF) == []


def test_walk_segments_are_never_flagged(provider):
    # 경복궁 → 민속박물관은 도보 하나뿐이지만 노선이 없어질 위험이 없다
    issues = run(day(FRI, ("a_gyeongbok", 60), ("a_folk", 60)), provider)
    assert not [i for i in issues if i.target == "경복궁 → 국립민속박물관"]


# ── 기준 2: 음식점 ─────────────────────────────────────────────────

def test_c2_stale_review_uses_override(provider):
    plan = day(FRI, ("a_bukchon", 60), ("r_sujebi", 60, "점심"))
    issue = next(i for i in run(plan, provider) if i.code == "C2_STALE_REVIEW")
    assert issue.value == "최근 리뷰 152일 전(2026-06-10)"
    assert issue.threshold == "90일 이내"
    assert issue.message == "[기준 2] 삼청동 수제비: 최근 리뷰 152일 전(2026-06-10) (기준: 90일 이내)"


def test_c2_review_boundary(provider):
    plan = day(FRI, ("a_bukchon", 60), ("r_sujebi", 60, "점심"))
    # 수제비 리뷰 2026-06-10: 90일째 되는 날은 통과, 91일째는 걸림
    assert "C2_STALE_REVIEW" not in codes(run(plan, provider, ref=date(2026, 9, 8)))
    assert "C2_STALE_REVIEW" in codes(run(plan, provider, ref=date(2026, 9, 9)))


def test_c2_no_hours_on_that_weekday(provider):
    # 인사동 한정식은 일요일 영업시간 정보가 없다. 인사동 쌈지길은 운영시간 정보 자체가 없다(기준 3)
    issues = run(day(SUN, ("a_insadong", 60), ("r_hanjeongsik", 60, "점심")), provider)
    assert codes(issues) == ["C2_NO_HOURS", "C3_NO_HOURS"]


def test_c2_not_operational(provider):
    # 폐업 표시인데 정보가 오래됐으면 '확실'이 아니라 기준 2로 묻는다
    issues = run(day(FRI, ("a_bukchon", 60), ("r_closed", 60, "점심")), provider, ref=date(2027, 1, 1))
    assert "C2_NOT_OPERATIONAL" in codes(issues)
    assert "CLOSED_PERMANENTLY" not in codes(issues)


# ── 기준 3: 관광지 ─────────────────────────────────────────────────

def test_c3_special_day(provider):
    issues = run(day(SAT, ("a_changdeok", 90)), provider)
    assert codes(issues) == ["C3_SPECIAL_DAY"]
    assert "11/21 특별 운영일(09:00~13:00)" in issues[0].value


def test_c3_no_hours(provider):
    assert codes(run(day(FRI, ("a_insadong", 60)), provider)) == ["C3_NO_HOURS"]


# ── 기준 4: 낡은 정보 ──────────────────────────────────────────────

def test_c4_stale_place(provider):
    issues = run(day(FRI, ("r_bindaetteok", 60, "저녁"), start="18:00"), provider)
    stale = next(i for i in issues if i.code == "C4_STALE_PLACE")
    assert stale.value == "마지막 확인 69일 전(2026-09-01)"


def test_c4_stale_segments(provider):
    issues = run(day(FRI, ("a_folk", 60), ("a_bukchon", 60), ("a_changdeok", 90), ("a_insadong", 60)), provider)
    stale = {i.target: i.value for i in issues if i.code == "C4_STALE_SEGMENT"}
    assert stale == {
        "국립민속박물관 → 북촌한옥마을": "마지막 확인 55일 전(2026-09-15)",
        "창덕궁 → 인사동 쌈지길": "마지막 확인 81일 전(2026-08-20)",   # 덮어쓰기 사례
    }


def test_c4_boundary(provider):
    # 기본 확인일 2026-11-05: 30일째(12/05)는 통과, 31일째(12/06)는 걸림
    plan = day(date(2026, 12, 4), ("a_gyeongbok", 60))
    assert "C4_STALE_PLACE" not in codes(run(plan, provider, ref=date(2026, 12, 5)))
    assert "C4_STALE_PLACE" in codes(run(plan, provider, ref=date(2026, 12, 6)))


# ── 확실히 틀림 ────────────────────────────────────────────────────

def test_closed_day_is_certain(provider):
    issues = run(day(TUE, ("a_gyeongbok", 120)), provider)
    assert [(i.kind, i.code) for i in issues] == [("certain", "CLOSED_DAY")]
    assert issues[0].value == "11/24(화) 휴무일 방문"


def test_outside_hours_is_certain(provider):
    # 14:00 출발 → 경복궁 14:22~16:22 → 북촌 16:40~17:40, 북촌은 17:00에 닫는다
    issues = run(day(FRI, ("a_gyeongbok", 120), ("a_bukchon", 60), start="14:00"), provider)
    assert [(i.kind, i.code, i.target) for i in issues] == [("certain", "OUTSIDE_HOURS", "북촌한옥마을")]


def test_closed_permanently_is_certain(provider):
    issues = run(day(FRI, ("a_bukchon", 60), ("r_closed", 60, "점심")), provider)
    assert ("certain", "CLOSED_PERMANENTLY") in [(i.kind, i.code) for i in issues]


def test_far_estimated_segment_is_certain(provider):
    # 숙소 ↔ 토속촌은 양방향 모두 수집하지 않은 약 1.9km 구간
    issues = run(day(FRI, ("r_tosokchon", 60, "점심"), start="11:30"), provider)
    assert [(i.kind, i.code) for i in issues] == [("certain", "ESTIMATED_SEGMENT")] * 2
    assert [i.target for i in issues] == ["숙소(명동) → 토속촌 삼계탕", "토속촌 삼계탕 → 숙소(명동)"]
    assert "직선" in issues[0].value


@pytest.mark.parametrize("stale_ref", [date(2027, 1, 1)])
def test_stale_info_is_not_treated_as_certain(provider, stale_ref):
    # 정보가 오래됐으면 휴무일이라도 '확실히 틀림'으로 고치지 않고 기준 4로 묻는다
    issues = run(day(TUE, ("a_gyeongbok", 120)), provider, ref=stale_ref)
    assert "CLOSED_DAY" not in codes(issues)
    assert "C4_STALE_PLACE" in codes(issues)
